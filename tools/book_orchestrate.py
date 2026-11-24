"""
book_orchestrate.py - single-chapter orchestrator

Pure logic, zero LLM involvement:
- Git branch management
- Physical chapter splitting (md-section-splitter, flat H2 split only)
- Read manifest.json + TOC.txt to generate the plan JSON
- Create the workspace directory + status.json

Topology (strictly per book_chapter_orchestrate.md section 5):
    quality (entry, children: [writer1..N])
      |- writer_1 (parent: quality, no children -> the watchdog activates it immediately)
      |- writer_2 (parent: quality, no children -> the watchdog activates it immediately)
      |- writer_N (parent: quality, no children -> the watchdog activates it immediately)

Key semantics:
- quality is the entry (no parent_run_uuid) but declares children: [w1..N]
- when the watchdog activates quality it finds children running/pending -> skips quality
- w1..N (no children, no prev_task) -> activated in parallel by the watchdog -> parallel writing
- once w1..N are all finished -> all of quality's children are terminal -> the watchdog activates quality
- quality summarizes and judges -> returns the next action -> super_pm orchestrates the follow-up plan separately

The instruction is generated strictly from the writing-brief template in book_chapter_orchestrate.md section 4.

Usage:
    orch = ChapterOrchestrator(book_root, chapter_path, project_id)
    result = orch.run()
"""

import json
import os
import re
import subprocess
import sys
import uuid
from typing import Optional

# -- Project path injection (so common.mysql can be imported) --
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from common.mysql.mysqlservice import executeSql

# ============================================================
# Constants
# ============================================================
SPLIT_SCRIPT = os.path.join(_PROJECT_ROOT, "skills", "md-section-splitter", "scripts", "split_by_h2.py")

# Max characters of the writing brief (including the TOC + current file content)
MAX_INSTRUCTION_CHARS = 8000
# Truncation threshold for the current file content (lines)
TRUNCATE_AFTER_LINES = 30


# ============================================================
# ChapterOrchestrator
# ============================================================
class ChapterOrchestrator:
    """Single-chapter orchestrator -- strictly follows the book_chapter_orchestrate.md flow"""

    def __init__(
        self,
        book_root: str,
        chapter_path: str,
        project_id: str,
        stage: str = "write",
        round_num: int = 1,
        use_git: bool = False,
    ):
        self.book_root = os.path.abspath(book_root)
        self.chapter_path = os.path.abspath(chapter_path)
        self.project_id = project_id
        self.stage = stage
        self.round_num = round_num
        self.use_git = use_git

        # Delayed assignment
        self.chapter: str = ""
        self.plan_id: str = ""
        self.branch_name: str = ""
        self.split_dir: str = ""
        self.drafts_dir: str = ""
        self.plans_dir: str = ""
        self.manifest: dict = {}
        self.toc: str = ""
        self.plan_json: dict = {}
        self.agent_ids: dict = {}
        self.task_ids: dict = {}

        # Writing standard
        self.writing_standard: str = ""

        if self.stage not in ("split", "write", "review", "merge", "full"):
            self.stage = "write"

    # ============================================================
    # Public methods
    # ============================================================

    def validate(self) -> None:
        """Validate the input arguments. Raises ValueError on failure."""
        # 1. chapter_path exists
        if not os.path.isfile(self.chapter_path):
            parent = os.path.dirname(self.chapter_path)
            basename = os.path.basename(self.chapter_path)
            chapter_prefix = re.match(r"(ch\d{2,3})", basename)
            if chapter_prefix and os.path.isdir(parent):
                prefix = chapter_prefix.group(1) + "_"
                for fname in os.listdir(parent):
                    if fname.startswith(prefix) and fname.endswith(".md"):
                        candidate = os.path.join(parent, fname)
                        if os.path.isfile(candidate):
                            self.chapter_path = candidate
                            break
            if not os.path.isfile(self.chapter_path):
                raise ValueError(f"章源文件不存在: {self.chapter_path}")

        # 2. book_root exists and is writable
        if not os.path.isdir(self.book_root):
            raise ValueError(f"书稿根目录不存在: {self.book_root}")
        ws_dir = os.path.join(self.book_root, "workspace")
        try:
            os.makedirs(ws_dir, exist_ok=True)
        except Exception as e:
            raise ValueError(f"无法创建 workspace 目录: {ws_dir} — {e}")

        # 2.1 Make sure the empty standards files exist (the writing standard / glossary are produced by the book_plan_structure task; this only prevents read/write errors)
        standards_dir = os.path.join(self.book_root, "standards")
        try:
            os.makedirs(standards_dir, exist_ok=True)
        except Exception as e:
            raise ValueError(f"无法创建 standards 目录: {standards_dir} — {e}")
        for fname in ("writing_standard.md", "glossary.md"):
            fp = os.path.join(standards_dir, fname)
            if not os.path.isfile(fp):
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(f"# {fname.replace('.md', '').replace('_', ' ').title()}\n\n（待 book_plan_structure task 填充）\n")

        # 3. project_id exists
        row = executeSql(
            "SELECT 1 FROM agx_project WHERE project_id = %s AND active_status = 1",
            (self.project_id,),
        )
        if not row:
            raise ValueError(f"项目不存在或已停用: {self.project_id}")

        # 4. the split script exists
        if not os.path.isfile(SPLIT_SCRIPT):
            raise ValueError(f"拆分脚本不存在: {SPLIT_SCRIPT}")

        # 5. Extract the chapter number
        self.chapter = self._chapter_from_path()

    def setup_git(self) -> str:
        """Git operations: check -> switch to master -> create the branch."""
        if not self.use_git:
            return ""

        r = subprocess.run(
            ["git", "-C", self.book_root, "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            return ""

        subprocess.run(
            ["git", "-C", self.book_root, "checkout", "master"],
            capture_output=True,
            text=True,
        )

        if not self.plan_id:
            self.plan_id = f"plan_{uuid.uuid4().hex[:12]}"

        self.branch_name = self.plan_id

        r2 = subprocess.run(
            ["git", "-C", self.book_root, "checkout", "-b", self.branch_name],
            capture_output=True,
            text=True,
        )
        if r2.returncode != 0 and "already exists" not in r2.stderr:
            raise RuntimeError(f"Git 创建分支失败: {r2.stderr.strip()}")

        if r2.returncode != 0:
            subprocess.run(
                ["git", "-C", self.book_root, "checkout", self.branch_name],
                capture_output=True,
                text=True,
            )

        return self.branch_name

    def split_chapter(self) -> dict:
        """Call split_by_h2.py for a flat split (H2 only).

                Idempotent: skips splitting when manifest.json already exists.
        """
        self.split_dir = os.path.join(self.book_root, "workspace", "split", self.chapter)
        manifest_path = os.path.join(self.split_dir, "manifest.json")

        if os.path.isfile(manifest_path):
            toc_path = os.path.join(self.split_dir, "TOC.txt")
            return {
                "split_dir": self.split_dir,
                "chapter": self.chapter,
                "manifest_path": manifest_path,
                "toc_path": toc_path,
                "reused": True,
            }

        os.makedirs(self.split_dir, exist_ok=True)

        r = subprocess.run(
            [
                sys.executable,
                SPLIT_SCRIPT,
                self.chapter_path,
                self.split_dir + "/",
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"章节拆分失败:\nSTDERR: {r.stderr}\nSTDOUT: {r.stdout}")

        if not os.path.isfile(manifest_path):
            raise RuntimeError(f"拆分完成但 manifest.json 缺失: {manifest_path}")

        toc_path = os.path.join(self.split_dir, "TOC.txt")
        return {
            "split_dir": self.split_dir,
            "chapter": self.chapter,
            "manifest_path": manifest_path,
            "toc_path": toc_path,
            "reused": False,
        }

    def read_manifest(self) -> dict:
        """Read manifest.json + TOC.txt."""
        manifest_path = os.path.join(self.split_dir, "manifest.json")
        if not os.path.isfile(manifest_path):
            raise RuntimeError(f"manifest.json 不存在: {manifest_path}")

        with open(manifest_path, encoding="utf-8") as f:
            self.manifest = json.load(f)

        toc_path = os.path.join(self.split_dir, "TOC.txt")
        if os.path.isfile(toc_path):
            with open(toc_path, encoding="utf-8") as f:
                self.toc = f.read()
        else:
            self.toc = self._build_toc_from_manifest()

        self._load_book_info()

        return self.manifest

    def generate_plan_json(self) -> dict:
        """Generate the plan JSON from manifest.json.

                Topology (strictly per book_chapter_orchestrate.md section 5):
                    quality (entry, children: [writer1..N])
                      |- writer_1 (parent: quality)
                      |- writer_2 (parent: quality)
                      |- writer_N (parent: quality)

                Once quality has all writer receipts it summarizes and judges -> super_pm composes the follow-up plan.
        """
        if not self.manifest:
            raise RuntimeError("manifest 为空（请先调用 read_manifest）")

        if not self.plan_id:
            self.plan_id = f"plan_{uuid.uuid4().hex[:12]}"

        self._resolve_agent_ids()

        sections = self.manifest.get("sections", [])
        steps = self._build_quality_writer_steps(sections)

        chapter_title = self.manifest.get("source_name", self.chapter)

        self.plan_json = {
            "plan_id": self.plan_id,
            "project_id": self.project_id,
            "description": f"{self.chapter} {self.stage}计划 - {chapter_title}",
            "mode": self.stage,
            "chapter": self.chapter,
            "split_dir": self.split_dir,
            "drafts_dir": self.drafts_dir,
            "steps": steps,
        }
        return self.plan_json

    def generate_plan_full(self) -> dict:
        """Generate the full pipeline plan JSON (all stages pre-declared).

                Topology (chain start):
                    quality_write (entry, children: [w1..N])
                      |- writer_1..N (parallel writing, the watchdog converges into quality_write)

                    quality_write finish -> activate review_round1
                      quality_review_1 (entry for the review round)
                        |- reviewer_1..N (parallel review)
                      integrate_1 (reviewers' feedback integration)
                      quality_revise_1 (entry for the revise round)
                        |- writer_1..N (parallel revision)
                      beta_1 (entry for the beta round)
                        |- beta_reader_1..N (parallel beta reading)
                      quality_judge_1 (judgement: pass -> merge, fail -> round2)

                    review_round2 (same as round1)
                    review_round3 (same as round1; the final quality_judge_3 forces finalize)

                    merge (final merge)

                Each stage entry sets next_task to the next stage.
                On finish, quality_judge_N activates next_task itself (next round) or skips it (activating merge).
        """
        if not self.manifest:
            raise RuntimeError("manifest 为空（请先调用 read_manifest）")

        if not self.plan_id:
            self.plan_id = f"plan_{uuid.uuid4().hex[:12]}"

        self._resolve_agent_ids()

        sections = self.manifest.get("sections", [])
        # _00 intro first
        sections_sorted = sorted(
            sections,
            key=lambda s: 0 if self._is_intro_section(s.get("section_id", "")) else 1,
        )

        chapter_title = self.manifest.get("source_name", self.chapter)
        workspace_base = self.book_root

        # Short suffix prevents run_uuid collisions (repeated orchestration of the same chapter)
        self.suffix = self.plan_id.split("_")[-1][:6]

        steps = []

        # ═══════════════════════════════════════════════════
        # Phase 1: writing (quality + writer*N, converged by the watchdog)
        # ═══════════════════════════════════════════════════
        quality_write_run = f"run_quality_write_{self.chapter}_{self.suffix}"
        writer_uuids = []

        for sec in sections_sorted:
            sec_id = sec.get("section_id", "")
            w_run = f"run_w_{self.chapter}_{sec_id}_{self.suffix}"
            writer_uuids.append(w_run)

        quality_task_id = self._get_task_id("planner", "book_manage_quality")
        steps.append({
            "run_uuid": quality_write_run,
            "task_id": quality_task_id,
            "agent_id": self.agent_ids["planner"],
            "instruction": (f"【写作阶段 quality】{self.chapter}。book_root={self.book_root}/、plan_id={self.plan_id}。等待所有 writer 完成并回执后，汇总判定。split={self.split_dir}/。共{len(sections_sorted)}节。"),
            "timeout_seconds": 1800,
            "children": writer_uuids,
        })

        for idx, sec in enumerate(sections_sorted):
            is_first = idx == 0
            steps.append(self._build_writer_step(sec, quality_write_run, idx, is_first))

        # ═══════════════════════════════════════════════════
        # Phase 2-4: review - integrate - revise - beta loop (up to 3 rounds)
        # ═══════════════════════════════════════════════════
        prev_phase_end = quality_write_run  # End of the previous stage

        for round_num in range(1, 4):  # Round 1/2/3
            r = str(round_num)
            _focus = self._round_focus(round_num)   # * This round's focus (three layered tiers)

            # --- Review ---
            review_entry_run = f"run_review_r{r}_entry_{self.chapter}_{self.suffix}"
            reviewer_uuids = []
            for sec in sections_sorted:
                sec_id = sec.get("section_id", "")
                rr = f"run_review_r{r}_{self.chapter}_{sec_id}_{self.suffix}"
                reviewer_uuids.append(rr)

            review_task_id = self._get_task_id("planner", "book_manage_quality")
            steps.append({
                "run_uuid": review_entry_run,
                "task_id": review_task_id,
                "agent_id": self.agent_ids["planner"],
                "instruction": (
                    f"【审校阶段 quality·第{r}轮】{self.chapter}。book_root={self.book_root}/、plan_id={self.plan_id}。等待所有 reviewer 完成审校并回执后汇总判定。判定后走下一步（review→integrate→revise→beta→judge）。"
                ),
                "children": reviewer_uuids,
                "timeout_seconds": 1800,
            })

            for sec in sections_sorted:
                sec_id = sec.get("section_id", "")
                file_name = sec.get("file", "")
                title = sec.get("title", "")
                review_single_run = f"run_review_r{r}_{self.chapter}_{sec_id}_{self.suffix}"
                review_content_task_id = self._get_task_id("reviewer", "book_review_content")
                steps.append({
                    "run_uuid": review_single_run,
                    "task_id": review_content_task_id,
                    "agent_id": self.agent_ids["reviewer"],
                    "instruction": (
                        f"审校 {self.chapter}/{file_name}（{title}）第{r}轮。\n"
                        f"book_root={self.book_root}/、plan_id={self.plan_id}。\n"
                        f"drafts={self.drafts_dir}/。\n"
                        f"★ 本轮只做「{_focus['layer']}」这一层：{_focus['review']}\n"
                        f"★ 产出组织：{_focus['review_output']}\n"
                        f"审校当前最新版本 _vN.md，产出 _vN_review.md。"
                    ),
                    "parent_run_uuid": review_entry_run,
                    "needs_reply": 1,
                    "timeout_seconds": 3600,
                })

            # --- Feedback integration ---
            integrate_run = f"run_integrate_r{r}_{self.chapter}_{self.suffix}"
            integrate_task_id = self._get_task_id("reviewer", "book_integrate_feedback")
            steps.append({
                "run_uuid": integrate_run,
                "task_id": integrate_task_id,
                "agent_id": self.agent_ids["reviewer"],
                "instruction": (f"整合 {self.chapter} 第{r}轮审校反馈。\nbook_root={self.book_root}/、plan_id={self.plan_id}。\n收集所有 _vN_review.md 审校报告，合并输出 _vN_review_integration.md。"),
                "timeout_seconds": 1800,
            })

            # --- Revision ---
            revise_entry_run = f"run_revise_r{r}_entry_{self.chapter}_{self.suffix}"
            revisor_uuids = []
            for sec in sections_sorted:
                sec_id = sec.get("section_id", "")
                rv = f"run_revise_r{r}_{self.chapter}_{sec_id}_{self.suffix}"
                revisor_uuids.append(rv)

            revise_task_id_q = self._get_task_id("planner", "book_manage_quality")
            steps.append({
                "run_uuid": revise_entry_run,
                "task_id": revise_task_id_q,
                "agent_id": self.agent_ids["planner"],
                "instruction": (f"【修订阶段 quality·第{r}轮】{self.chapter}。book_root={self.book_root}/、plan_id={self.plan_id}。等待所有 writer 修订完成并回执后汇总判定。"),
                "children": revisor_uuids,
                "timeout_seconds": 1800,
            })

            for sec in sections_sorted:
                sec_id = sec.get("section_id", "")
                file_name = sec.get("file", "")
                title = sec.get("title", "")
                revise_single_run = f"run_revise_r{r}_{self.chapter}_{sec_id}_{self.suffix}"
                revise_task_id = self._get_task_id("writer", "book_revise_section")
                steps.append({
                    "run_uuid": revise_single_run,
                    "task_id": revise_task_id,
                    "agent_id": self.agent_ids["writer"],
                    "instruction": (
                        f"修订 {self.chapter}/{file_name}（{title}）第{r}轮。\n"
                        f"book_root={self.book_root}/、plan_id={self.plan_id}。\n"
                        f"drafts={self.drafts_dir}/。\n"
                        f"★ 本轮只改「{_focus['layer']}」：{_focus['revise']}\n"
                        f"根据 _vN_review_integration.md 逐条处理，产出 _v(N+1).md（实际版本号由 book_revise_section task 自动计算）。"
                    ),
                    "parent_run_uuid": revise_entry_run,
                    "needs_reply": 1,
                    "timeout_seconds": 3600,
                })

            # --- Beta reading ---
            beta_entry_run = f"run_beta_r{r}_entry_{self.chapter}_{self.suffix}"
            beta_reader_uuids = []
            for sec in sections_sorted:
                sec_id = sec.get("section_id", "")
                br = f"run_beta_r{r}_{self.chapter}_{sec_id}_{self.suffix}"
                beta_reader_uuids.append(br)

            beta_task_id_q = self._get_task_id("planner", "book_manage_quality")
            steps.append({
                "run_uuid": beta_entry_run,
                "task_id": beta_task_id_q,
                "agent_id": self.agent_ids["planner"],
                "instruction": (f"【试读阶段 quality·第{r}轮】{self.chapter}。book_root={self.book_root}/、plan_id={self.plan_id}。等待所有 beta_reader 试读完成并回执后汇总判定。"),
                "children": beta_reader_uuids,
                "timeout_seconds": 1800,
            })

            for sec in sections_sorted:
                sec_id = sec.get("section_id", "")
                file_name = sec.get("file", "")
                title = sec.get("title", "")
                beta_single_run = f"run_beta_r{r}_{self.chapter}_{sec_id}_{self.suffix}"
                beta_read_task_id = self._get_task_id("beta_reader", "book_beta_read")
                steps.append({
                    "run_uuid": beta_single_run,
                    "task_id": beta_read_task_id,
                    "agent_id": self.agent_ids["beta_reader"],
                    "instruction": (
                        f"试读 {self.chapter}/{file_name}（{title}）第{r}轮。\n"
                        f"book_root={self.book_root}/、plan_id={self.plan_id}。\n"
                        f"drafts={self.drafts_dir}/。\n"
                        f"★ 本轮阅读视角：{_focus['beta']}\n"
                        f"★ 笔记组织：{_focus['beta_output']}\n"
                        f"沉浸式阅读修订后的最新版本，输出 _vN_beta.md 阅读笔记。"
                    ),
                    "parent_run_uuid": beta_entry_run,
                    "needs_reply": 1,
                    "timeout_seconds": 3600,
                })

            # --- Quality judgement ---
            judge_run = f"run_quality_judge_r{r}_{self.chapter}_{self.suffix}"
            judge_task_id = self._get_task_id("planner", "book_manage_quality")
            next_phase = f"run_review_r{round_num + 1}_entry_{self.chapter}_{self.suffix}" if round_num < 3 else ""
            steps.append({
                "run_uuid": judge_run,
                "task_id": judge_task_id,
                "agent_id": self.agent_ids["planner"],
                "instruction": (
                    f"【质量判定·第{r}轮·{_focus['layer']}】{self.chapter}。\n"
                    f"book_root={self.book_root}/、plan_id={self.plan_id}。\n"
                    f"★ 本轮判据只覆盖「{_focus['layer']}」：{_focus['judge']}。\n"
                    f"其它层的问题（若发现）只记录、不作为本轮不通过的理由 —— 那是别的轮次的职责。\n"
                    f"读取审校报告 + 试读笔记，判定：通过→激活合并，不通过→激活下一轮。"
                    f"{'这是最后一轮，不通过也要强制 finalize 或写 notice 人工介入。' if round_num == 3 else f'最多还有{3 - round_num}轮。'}"
                ),
                "timeout_seconds": 1800,
            })

            # --- Chaining: link this round's nodes via prev_task/next_task ---
            # review_entry ← integrate ← revise_entry ← beta_entry ← judge
            prev_node = review_entry_run
            for current_node in [integrate_run, revise_entry_run, beta_entry_run, judge_run]:
                # Set prev_task on the steps already added
                for s in steps:
                    if s["run_uuid"] == current_node:
                        s["prev_task"] = prev_node
                        break
                # Set next_task on the preceding node
                for s in steps:
                    if s["run_uuid"] == prev_node:
                        s["next_task"] = current_node
                        break
                prev_node = current_node

            # Link: end of the previous stage -> this round's review_entry
            if prev_phase_end:
                for s in steps:
                    if s["run_uuid"] == prev_phase_end:
                        s["next_task"] = review_entry_run
                        break
                for s in steps:
                    if s["run_uuid"] == review_entry_run:
                        s["prev_task"] = prev_phase_end
                        break

            prev_phase_end = judge_run


        # ═══════════════════════════════════════════════════
        # Phase 5: merge and finalize
        # ═══════════════════════════════════════════════════
        merge_run = f"run_merge_{self.chapter}_{self.suffix}"
        merge_task_id = self._get_task_id("planner", "book_merge_chapter")
        steps.append({
            "run_uuid": merge_run,
            "task_id": merge_task_id,
            "agent_id": self.agent_ids["planner"],
            "instruction": (f"合并 {self.chapter} 全部小节定稿。\nbook_root={self.book_root}/、plan_id={self.plan_id}。\n确认 status.json 全部 final 后，cat 拼接清理覆盖源文件。chapter_path={self.chapter_path}。"),
            "timeout_seconds": 600,
        })

        # merge's prev_task = the last judge
        for s in steps:
            if s["run_uuid"] == prev_phase_end:
                s["next_task"] = merge_run
                break
        for s in steps:
            if s["run_uuid"] == merge_run:
                s["prev_task"] = prev_phase_end
                break

        self.plan_json = {
            "plan_id": self.plan_id,
            "project_id": self.project_id,
            "description": f"{self.chapter} 全管线计划 - {chapter_title}",
            "activate_mode": "chain",
            "start_task_run_uuid": quality_write_run,
            "mode": self.stage,
            "chapter": self.chapter,
            "split_dir": self.split_dir,
            "drafts_dir": self.drafts_dir,
            "steps": steps,
        }
        return self.plan_json

    def create_workspace(self) -> dict:
        """Create the drafts directory + status.json. Idempotent: skipped when status.json already exists."""
        self.drafts_dir = os.path.join(self.book_root, "workspace", "drafts", self.chapter)
        status_path = os.path.join(self.drafts_dir, "status.json")

        if os.path.isfile(status_path):
            return {
                "drafts_dir": self.drafts_dir,
                "status_path": status_path,
                "reused": True,
            }

        os.makedirs(self.drafts_dir, exist_ok=True)

        status = self._build_status_json()
        with open(status_path, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)

        return {
            "drafts_dir": self.drafts_dir,
            "status_path": status_path,
            "reused": False,
        }

    def run(self) -> dict:
        """Dispatch by stage. split = split only; anything else = generate the matching plan."""
        errors = []

        try:
            self.validate()
        except ValueError as e:
            errors.append(f"[validate] {e}")
            return {"success": False, "errors": errors}

        try:
            self.setup_git()
        except Exception as e:
            errors.append(f"[git] {e}")
            return {"success": False, "errors": errors}

        try:
            self.split_chapter()
        except Exception as e:
            errors.append(f"[split] {e}")
            return {"success": False, "errors": errors}

        try:
            self.read_manifest()
        except Exception as e:
            errors.append(f"[manifest] {e}")
            return {"success": False, "errors": errors}

        if self.stage == "split":
            try:
                self.create_workspace()
            except Exception as e:
                errors.append(f"[workspace] {e}")
                return {"success": False, "errors": errors}
            manifest_path = os.path.join(self.split_dir, "manifest.json")
            toc_path = os.path.join(self.split_dir, "TOC.txt")
            return {
                "success": True,
                "stage": "split",
                "chapter": self.chapter,
                "git_branch": self.branch_name or "(none)",
                "split_dir": self.split_dir,
                "drafts_dir": self.drafts_dir,
                # Uniform return shape: split produces no plan, so plan_json_path is explicitly empty
                # (fixed 2026-10-25: the key used to be absent, so the MCP side read it empty and reported "plan JSON file missing:")
                "plan_json_path": "",
                "manifest_path": manifest_path,
                "toc_path": toc_path,
                "sections_count": len(self.manifest.get("sections", [])),
            }

        if not self.plan_id:
            self.plan_id = f"plan_{uuid.uuid4().hex[:12]}"

        try:
            self.create_workspace()
        except Exception as e:
            errors.append(f"[workspace] {e}")
            return {"success": False, "errors": errors}

        try:
            if self.stage == "full":
                self.generate_plan_full()
            elif self.stage == "write":
                self.generate_plan_write_only()
            elif self.stage == "review":
                self.generate_plan_review_only(round_num=self.round_num)
            elif self.stage == "merge":
                self.generate_plan_merge_only()
            else:
                self.generate_plan_write_only()
        except Exception as e:
            errors.append(f"[plan_json] {e}")
            return {"success": False, "errors": errors}

        try:
            plan_path = self._write_plan_file()
        except Exception as e:
            errors.append(f"[write_plan_file] {e}")
            return {"success": False, "errors": errors}

        result = {
            "success": True,
            "stage": self.stage,
            "plan_id": self.plan_id,
            "chapter": self.chapter,
            "git_branch": self.branch_name or "(none)",
            "split_dir": self.split_dir,
            "drafts_dir": self.drafts_dir,
            "plan_json_path": plan_path,
            "steps_count": len(self.plan_json.get("steps", [])),
        }
        if self.stage == "review":
            result["round_num"] = self.round_num
        return result

    # ============================================================
    # Book info loading (Step 3, degraded)
    # ============================================================

    def _load_book_info(self) -> None:
        """Load the writing standard. The book title and description are fetched at runtime by the Agent via agx_get_project_config."""
        standards_dir = os.path.join(self.book_root, "standards")
        ws_path = os.path.join(standards_dir, "writing_standard.md")
        try:
            with open(ws_path, encoding="utf-8") as f:
                self.writing_standard = f.read().strip()
        except Exception:
            self.writing_standard = ""

    def _get_prev_chapter_toc(self) -> str:
        """Read the previous chapter's TOC.txt. Returns '(none, this is chapter 1)' when absent."""
        ch_num = self._chapter_number()
        if ch_num is None or ch_num <= 1:
            return "（无，此为第1章）"

        prev_ch = f"ch{ch_num - 1:02d}"
        result = self._find_chapter_toc(prev_ch)
        if result is None:
            prev_file = self._find_chapter_md_filename(prev_ch)
            return f"（{prev_file or prev_ch} — TOC 尚未生成）"
        return result

    def _get_next_chapter_toc(self) -> str:
        """Read the next chapter's TOC.txt. Returns '(none, this is the last chapter)' when absent."""
        ch_num = self._chapter_number()
        if ch_num is None:
            return "（无，此为末章）"

        next_ch = f"ch{ch_num + 1:02d}"
        result = self._find_chapter_toc(next_ch)
        if result is None:
            return "（无，此为末章）"
        return result

    def _find_chapter_toc(self, ch_name: str) -> Optional[str]:
        """Find the TOC.txt of the given chapter under the workspace directory. Returns its content or None."""
        ws_dir = os.path.join(self.book_root, "workspace")
        if not os.path.isdir(ws_dir):
            return None

        # Walk the subdirectories under the workspace looking for split/{ch_name}/TOC.txt
        for entry in os.listdir(ws_dir):
            toc_path = os.path.join(ws_dir, entry, "split", ch_name, "TOC.txt")
            if os.path.isfile(toc_path):
                with open(toc_path, encoding="utf-8") as f:
                    return f.read().strip()
        return None

    def _chapter_number(self) -> Optional[int]:
        """Extract the number from chXX, e.g. ch07 -> 7."""
        m = re.match(r"ch(\d+)", self.chapter)
        return int(m.group(1)) if m else None

    def _find_chapter_md_filename(self, ch_name: str) -> Optional[str]:
        """Find the given chapter's .md file name under book_root, e.g. ch06 -> ch06_chapter6-llm-tech-ecosystem.md"""
        prefix = f"{ch_name}_"
        try:
            for fname in os.listdir(self.book_root):
                if fname.startswith(prefix) and fname.endswith(".md"):
                    return fname
        except Exception:
            pass
        return None

    def _build_toc_from_manifest(self) -> str:
        """Rebuild the TOC from the manifest (degraded path when TOC.txt is missing)."""
        sections = self.manifest.get("sections", [])
        if not sections:
            return ""
        lines = []
        for sec in sections:
            sec_id = sec.get("section_id", "")
            title = sec.get("title", "")
            indent = "  " * (len(sec_id.split(".")) - 1) if sec_id else ""
            lines.append(f"{indent}- {title}")
        return "\n".join(lines)

    # ============================================================
    # Writing brief generation (Step 4)
    # ============================================================

    def _build_creative_brief(self, section: dict, section_index: int, is_first: bool) -> str:
        """Generate the full writing brief for a single section.

                Strictly per the book_chapter_orchestrate.md section 4 template:
                +------------------------------------+
                | <book title>
                | Book introduction
                | Previous chapter TOC
                | This chapter TOC (marking the current task)
                | Next chapter TOC
                | Current task: write {file name}
                | Type: {H2 intro / H3 subsection}
                | Current content
                | ---
                | Writing requirements
                +------------------------------------+
        """
        sec_id = section.get("section_id", "")
        title = section.get("title", "")
        file_name = section.get("file", "")

        # Type detection
        section_type = self._judge_section_type(sec_id)

        # Current content
        section_content = self._read_section_content(file_name)

        # This chapter's TOC (marking the current task)
        toc_annotated = self._annotate_toc(sec_id, title)

        # Adjacent chapters
        prev_toc = self._get_prev_chapter_toc()
        next_toc = self._get_next_chapter_toc()

        # Writing requirements
        creative_requirements = self._build_creative_requirements(section, section_type, section_index, is_first)

        # Assemble the brief
        # The book title and introduction are not hardcoded by the orchestrator; the Agent fetches them at runtime via agx_get_project_config
        brief = f"""┌──────────────────────────────────────────┐
│ 调用`agx_get_project_config`在返回结果的x_desc字段中有本书的详细信息
│
│ 前一章目录：
│ {prev_toc}
│
│ 本章目录：
│ {toc_annotated}
│
│ 下一章目录：
│ {next_toc}
│
│ 当前任务：撰写 {file_name}
│
│ 类型：{section_type}
│
│ 当前内容：
│ {section_content}
│
│ ---
│
│ 创作要求：
{creative_requirements}
└──────────────────────────────────────────┘"""

        # Truncate to MAX_INSTRUCTION_CHARS
        if len(brief) > MAX_INSTRUCTION_CHARS:
            brief = brief[: MAX_INSTRUCTION_CHARS - 3] + "..."

        return brief

    def _judge_section_type(self, sec_id: str) -> str:
        """Classify the section. A name starting with _00_ = H2 intro; anything else = H3 subsection."""
        if sec_id.endswith("_00") or "_00_" in sec_id:
            return "H2 导语（承上启下，概括要点，不展开细节）"
        return "H3 小节（围绕一点深入展开）"

    def _annotate_toc(self, current_section_id: str, section_title: str = "") -> str:
        """Mark the current task's line in the TOC (<- CURRENT TASK).

                Match priority:
                1. exact section_id match (e.g. _00, s01)
                2. section_title keyword match
        """
        if not self.toc:
            return "（无 TOC）"

        lines = self.toc.split("\n")
        annotated = []
        section_num = section_title.split(maxsplit=1)[0] if section_title else ""

        for line in lines:
            stripped = line.strip()
            # Exact section_id match
            if current_section_id in stripped:
                annotated.append(f"{line}  ← 📝 当前任务")
            elif section_num and section_num in stripped:
                # Match by section number (e.g. "7.1")
                annotated.append(f"{line}  ← 📝 当前任务")
            else:
                annotated.append(line)
        return "\n".join(annotated)

    def _read_section_content(self, file_name: str) -> str:
        """Read the section file content under the split directory. Empty or missing files are handled by degradation."""
        split_file = os.path.join(self.split_dir, file_name)
        try:
            with open(split_file, encoding="utf-8") as f:
                raw = f.read()
            if not raw.strip():
                return "（新建文件，开篇创作）"
            if len(raw) > 5000:
                # Keep only the first TRUNCATE_AFTER_LINES lines
                raw_lines = raw.split("\n")
                return "\n".join(raw_lines[:TRUNCATE_AFTER_LINES]) + "\n...（省略）"
            return raw
        except Exception:
            return "（新建文件，开篇创作）"

    def _build_creative_requirements(
        self,
        section: dict,
        section_type: str,
        section_index: int,
        is_first: bool,
    ) -> str:
        """Generate bespoke writing requirements from the section's position and type.

                Derived from writing_standard.md + section_plan.json + position information.
        """
        reqs = []

        # Take it from writing_standard (when present)
        if self.writing_standard:
            # Use the first 5 lines as the summary requirement
            ws_lines = self.writing_standard.split("\n")[:5]
            for line in ws_lines:
                line = line.strip()
                if line and not line.startswith("#"):
                    reqs.append(line)

        # Type-related requirements
        if "导语" in section_type:
            reqs.append("这是章节导语，承上启下概括本节要点，说明本节与全书的关系")
            reqs.append("篇幅控制在 500-800 字，不要展开技术细节")
        else:
            reqs.append("围绕本节主题深入展开，保持与前后小节的衔接")
            reqs.append("代码示例必须可运行，附必要的环境说明")

        # Position-related requirements
        if is_first:
            reqs.append("这是本章第一节正文，开篇需要自然承接章导语")

        # General requirements
        reqs.append("尽量写出通俗易懂、举例生动的文字")
        reqs.append("输出文件放在 drafts/{chapter}/ 目录")

        return "\n".join(f"  {i}. {r}" for i, r in enumerate(reqs, 1))

    # ============================================================
    # Plan construction (Step 5 + Step 6)
    # ============================================================

    # ══════════════════════════════════════════════════════════════
    # Per-round focus -- each of the three rounds owns one layer; no duplicated work
    # ══════════════════════════════════════════════════════════════
    # 2026-10-27 the maintainer's decision: keep 3 rounds, but each round must do a different thing.
    #
    # Evidence (book "On Chain", ch03, night of 2026-10-26): the three review instructions were 99.6% similar
    # (227 chars / 227 chars / 227 chars; the only difference was "round 1" -> "round 2" -> "round 3"),
    # and the beta instructions likewise -- the same prompt, the same person, run three times.
    # Worse, what round 2 caught (em dashes, "wan ge / mei ether") belongs to style,
    # which shows the first two rounds were not layered at all; round 1 should have caught it.
    #
    # Fix: tiers "structure -> facts -> language", each round stating explicitly
    #   - what this round looks at (focus)
    #   - what this round does **not** look at (left to other rounds, to avoid overlap)
    # Inside each tier, use the four viewpoints review / revise / beta / judge.
    #
    # Why these three tiers and not others:
    #   - a structural error makes word polishing a waste (hence structure first)
    #   - a factual error misleads no matter how good the rhetoric (hence facts second)
    #   - style goes last, because the earlier edits introduce new punctuation/word inconsistencies
    _ROUND_FOCUS = {
        1: {
            "layer": "结构与逻辑",
            "review": (
                "只看章节骨架：结构是否完整、论证链是否闭合、每段职责是否清晰、"
                "有无跑题段落或重复论述、小节之间是否衔接得上。"
                "★ 不要提错别字、标点、用词统一（属第 3 轮），"
                "也不要提具体数据是否有误（属第 2 轮）。"
            ),
            "revise": (
                "只改结构与逻辑问题（增删段落、调整顺序、补论证缺口）。"
                "措辞、标点、术语一律不动 —— 后两轮分别处理。"
            ),
            "beta": "关注「读不读得下去」：哪里卡住、哪里需要回读上文、哪一段让你走神。",
            "judge": "只判结构是否成立（论证链闭合、段落职责清晰、无跑题）",
            "review_output": (
                "报告以「结构诊断表」为主线组织：小节 → 结构问题 → 建议动作（删/移/补）。"
                "属于事实或体例的问题，单列在末尾「移交后续轮次」小节，一行一条，不展开。"
            ),
            "beta_output": (
                "笔记以「阅读顺畅度地图」为主线：按阅读顺序列出卡点（位置 → 卡在哪 → 建议怎么改）。"
                "对内容真伪、文字体例的感受只记一句话，不展开。"
            ),
        },
        2: {
            "layer": "事实与术语",
            "review": (
                "只看事实层：数据与引文是否可核验、技术论断是否成立、"
                "同一概念是否始终同一叫法（术语一致性）、前后有无自相矛盾。"
                "★ 不要提结构问题（第 1 轮已定），也不要提标点体例（第 3 轮）。"
            ),
            "revise": (
                "只改事实与术语问题（订正数据、统一术语、消除矛盾）。"
                "不改结构（第 1 轮已定），不改标点与句式。"
            ),
            "beta": "关注「信不信」：哪句话你觉得空、哪个数字你想追问、哪里像在绕。",
            "judge": "只判事实是否准确、术语是否前后一致",
            "review_output": (
                "报告以「事实核对表」为主线：论断/数据 → 依据（或指出去哪查）→ 结论（成立/存疑/错误）。"
                "术语不统一另列一张对照表（术语 → 建议统一为）。"
                "结构与体例问题只列「移交后续轮次」，不展开。"
            ),
            "beta_output": (
                "笔记以「可信度审计」为主线：逐条列出让你产生怀疑的地方（原句 → 你的疑问 → 希望补什么证据）。"
                "阅读体验与文字好坏只记一句话。"
            ),
        },
        3: {
            "layer": "语言与体例",
            "review": (
                "只看文字层：标点、用词统一（如「万个以太币」/「枚以太币」须择一）、"
                "重复表述、句式冗长、可读性。"
                "★ 不要提结构（第 1 轮）与事实论断（第 2 轮）。"
            ),
            "revise": (
                "只改语言与体例（统一用词、修标点、删重复、拆长句）。"
                "不动结构与事实论断 —— 那两层已经定稿。"
            ),
            "beta": "关注「记不记得住」：读完能复述哪三句、哪句最想划走、哪里读起来别扭。",
            "judge": "只判文字是否干净（体例统一、无重复表述、可读）",
            "review_output": (
                "报告以「体例统一清单」为主线：问题（原词/原标点）→ 建议统一为 → 出现位置。"
                "重复表述、冗长句式各列一张表。"
                "结构与事实问题只列「移交人工确认」，不展开（那两层已定稿）。"
            ),
            "beta_output": (
                "笔记以「记忆点提取」为主线：读完能复述的三句话（原文引用）→ 哪句最想划走及原因 → 哪里读起来别扭。"
                "不建议改结构、不质疑事实。"
            ),
        },
    }

    def _round_focus(self, round_num: int) -> dict:
        """Get this round's focus. Rounds beyond 1-3 reuse round 3 (language tier) to avoid a KeyError."""
        try:
            return self._ROUND_FOCUS.get(int(round_num), self._ROUND_FOCUS[3])
        except Exception:
            return self._ROUND_FOCUS[3]

    def _resolve_agent_ids(self) -> dict:
        """Fetch the real agent_id and task_id from the project."""
        rows = executeSql(
            """SELECT r.agent_id, r.agent_labs, r.agent_role
               FROM agx_agent_registry r
               WHERE r.project_id = %s AND r.active_status = 1""",
            (self.project_id,),
        )
        lab_map = {}
        role_map = {}
        for row in rows:
            labs = row.get("agent_labs", "")
            if labs:
                lab_map.setdefault(labs, []).append(row["agent_id"])
            role = row.get("agent_role", "")
            if role:
                role_map.setdefault(role, []).append(row["agent_id"])

        # PM resolution (three-level fallback):
        #   1) agent_labs == "super_pm"  -> a PM cloned from the super_pm template (traditional form, preferred)
        #   2) agent_role == "project_manager" -> a PM created standalone without cloning (e.g. book_up_chain)
        #   3) agx_project.admin_agent_id -> the platform-registered project admin (the most authoritative fallback)
        # Fixed 2026-10-26: the original only recognized (1), so projects "with a PM that is not a super_pm clone" were judged as missing a PM,
        # and single-chapter orchestration raised an error at the _resolve_agent_ids stage (change authorized by the maintainer).
        pm_agent_id = lab_map.get("super_pm", [None])[0]
        if not pm_agent_id:
            pm_agent_id = role_map.get("project_manager", [None])[0]
        if not pm_agent_id:
            admin_rows = executeSql(
                "SELECT admin_agent_id FROM agx_project WHERE project_id = %s",
                (self.project_id,),
            )
            if admin_rows:
                pm_agent_id = admin_rows[0].get("admin_agent_id") or None

        # The 5 core agents
        self.agent_ids = {
            "planner": lab_map.get("book_planner", [None])[0],
            "writer": lab_map.get("book_writer", [None])[0],
            "reviewer": lab_map.get("book_reviewer", [None])[0],
            "beta_reader": lab_map.get("book_beta_reader", [None])[0],
            "super_pm": pm_agent_id,
        }

        # Look up task_id
        self.task_ids = {}
        for role, agent_id in self.agent_ids.items():
            if agent_id:
                tasks = executeSql(
                    """SELECT t.task_id, t.task_labs
                       FROM agx_task_base t
                       WHERE t.agent_id = %s AND t.active_status = 1""",
                    (agent_id,),
                )
                for t in tasks:
                    labs = t.get("task_labs", "")
                    self.task_ids.setdefault(agent_id, {})[labs] = t["task_id"]

        # Validate the required agents
        # super_pm is not part of the hard validation: none of generate_plan_full/write/review/merge's steps depend on it,
        # only master-plan activation needs it (see _resolve_super_pm_task, which reports the error explicitly there).
        # Fixed 2026-10-26: all 5 roles used to be validated together, so a missing super_pm took single-chapter orchestration down with it.
        missing = [k for k, v in self.agent_ids.items() if v is None and k != "super_pm"]
        if missing:
            raise ValueError(
                f"项目 {self.project_id} 缺少必要 agent: {missing}。"
                "请确认已部署 book_planner + book_writer + book_reviewer + book_beta_reader 四个团队角色。"
            )
        return self.agent_ids

    def _build_quality_writer_steps(self, sections: list) -> list:
        """Build the quality + writer*N plan steps.

                Topology (per book_chapter_orchestrate.md section 5):
                    quality (entry, children: [w1..N])
                      |- writer_1 (parent: quality)
                      |- writer_2 (parent: quality)
                      |- writer_N (parent: quality)

                The _00 intro goes first (book_chapter_orchestrate.md key principle 1).
        """
        steps = []

        # Order: _00 intro first
        sections_sorted = sorted(sections, key=lambda s: 0 if self._is_intro_section(s.get("section_id", "")) else 1)

        quality_run = f"run_quality_{self.chapter}"

        # Collect every writer run_uuid
        writer_uuids = []
        for sec in sections_sorted:
            sec_id = sec.get("section_id", "")
            writer_uuids.append(f"run_w_{self.chapter}_{sec_id}_{self.suffix}")

        # --- quality hub ---
        quality_task_id = self._get_task_id("planner", "general")
        steps.append({
            "run_uuid": quality_run,
            "task_id": quality_task_id,
            "agent_id": self.agent_ids["planner"],
            "instruction": (f"质量判定中枢 {self.chapter}。workspace={os.path.join(self.book_root, 'workspace')}/、plan_id={self.plan_id}。{len(sections_sorted)} 节。等待所有 writer 完成后汇总判定下一步动作。"),
            "timeout_seconds": 1800,
            "children": writer_uuids,
        })

        # --- writer * N (in sorted order) ---
        for idx, sec in enumerate(sections_sorted):
            is_first = idx == 0
            steps.append(self._build_writer_step(sec, quality_run, idx, is_first))

        return steps

    def _is_intro_section(self, sec_id: str) -> bool:
        """Whether this is the _00 intro section."""
        return sec_id.endswith("_00") or "_00_" in sec_id

    def _build_writer_step(
        self,
        section: dict,
        quality_run_uuid: str,
        section_index: int,
        is_first: bool,
    ) -> dict:
        """Generate the writer execution step for one section.

                The instruction uses the full writing-brief template from section Step 4.
        """
        sec_id = section.get("section_id", "")
        title = section.get("title", "")
        file_name = section.get("file", "")

        run_uuid = f"run_w_{self.chapter}_{sec_id}_{self.suffix}"
        write_task_id = self._get_task_id("writer", "book_write_section")

        # Generate the writing brief from the section Step 4 template
        creative_brief = self._build_creative_brief(section, section_index, is_first)

        # Attach extra context (workspace path and the like)
        instruction = (
            f"{creative_brief}\n\n"
            f"---\n"
            f"**执行上下文**:\n"
            f"- 书稿根目录: {self.book_root}/\n"
            "- 工作区: workspace/\n"
            f"- 源码文件: workspace/split/{self.chapter}/{file_name}\n"
            f"- 输出文件: workspace/drafts/{self.chapter}/{file_name.replace('.md', '_v1.md')}\n"
            f"- manifest: workspace/split/{self.chapter}/manifest.json\n"
            f"- TOC: workspace/split/{self.chapter}/TOC.txt"
        )

        return {
            "run_uuid": run_uuid,
            "task_id": write_task_id,
            "agent_id": self.agent_ids["writer"],
            "instruction": instruction,
            "parent_run_uuid": quality_run_uuid,
            "needs_reply": 1,
            "timeout_seconds": 3600,
            "plan_section": sec_id,
        }

    # ============================================================
    # Helper methods
    # ============================================================

    def _chapter_from_path(self) -> str:
        """Extract the chapter number from chapter_path, always returning chNN.

                Naming forms accepted (relaxed 2026-10-25; only chNN before):
                    ch07_xxx.md / chapter13_xxx.md / chapter 13 xxx.md / 01_intro.md / 1.2_xxx.md
        """
        basename = os.path.basename(self.chapter_path)

        # 1) Explicit chapter number: ch07 / chapter13 / chapter 13
        m = re.match(r"(?:ch(?:apter)?|第)\s*0*(\d{1,3})", basename, re.I)
        if m:
            return "ch%02d" % int(m.group(1))

        # 2) Pure numeric prefix: 01_intro.md / 1.2_xxx.md / 13-xxx.md / 13.xxx.md / 13 xxx.md
        m = re.match(r"0*(\d{1,3})[_\-.、\s]", basename)
        if m:
            return "ch%02d" % int(m.group(1))

        raise ValueError(
            f"无法从文件名提取章号: {basename}"
            "（支持 ch07 / chapter13 / 第13章 / 01_ / 1.2_ 等命名）"
        )

    def _get_task_id(self, agent_labs: str, task_labs: str) -> str:
        """Look up task_id from agent_labs + task_labs. agent_labs may be planner / writer / reviewer / beta_reader."""
        agent_id = self.agent_ids.get(agent_labs)
        if not agent_id:
            return ""
        for task_agent_id, tasks in self.task_ids.items():
            if task_agent_id == agent_id and task_labs in tasks:
                return tasks[task_labs]
        return ""

    def _build_status_json(self) -> dict:
        """Generate the initial status.json (one record per section)."""
        sections_status = {}
        if self.manifest:
            for sec in self.manifest.get("sections", []):
                sec_id = sec.get("section_id", "")
                sections_status[f"{self.chapter}/{sec_id}"] = {
                    "status": "pending",
                    "title": sec.get("title", ""),
                    "split_file": os.path.join(self.split_dir, sec.get("file", "")),
                }

        return {
            "plan": self.plan_id,
            "chapter": self.chapter,
            "mode": self.stage,
            "sections": sections_status,
        }

    def _write_plan_file(self) -> str:
        """Write plan_json into workspace/plans/{chapter}/plan_{stage}_{suffix}.json."""
        self.plans_dir = os.path.join(self.book_root, "workspace", "plans", self.chapter)
        os.makedirs(self.plans_dir, exist_ok=True)

        suffix = self.plan_id.split("_")[-1][:6] if self.plan_id else uuid.uuid4().hex[:6]
        stage_tag = self.stage
        if self.stage == "review":
            stage_tag = f"review_r{self.round_num}"
        plan_filename = f"plan_{stage_tag}_{suffix}.json"
        plan_path = os.path.join(self.plans_dir, plan_filename)

        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(self.plan_json, f, ensure_ascii=False, indent=2)
        return plan_path

    # ═══════════════════════════════════════════════════════════════
    # Atomic methods: split, writing plan, review plan (added 2026-06-26)
    # The existing generate_plan_json / generate_plan_full are untouched
    # ═══════════════════════════════════════════════════════════════

    def generate_split_only(self) -> dict:
        """[Method 1] Split the chapter only; generate no plan at all.

                Precondition: validate() has passed.
                Returns the manifest content.
        """
        if not self.manifest:
            self.split_chapter()
            self.read_manifest()
        if not self.split_dir:
            self.create_workspace()
        return {
            "chapter": self.chapter,
            "split_dir": self.split_dir,
            "manifest": self.manifest,
            "drafts_dir": self.drafts_dir,
        }

    def generate_plan_write_only(self, activate_mode: str = "chain") -> dict:
        """[Method 2] Generate the pure writing-stage plan JSON (no review/revision/beta reading).

                Topology (chain mode by default):
                    quality_write (entry, children: [w1..N])
                      |- writer_1..N (parallel writing)

                activate_mode="chain": activate quality_write -> the watchdog scans children and activates the writers -> converge.
                activate_mode="watchdog": quality_write declares children; the watchdog activates it once all are done.
        """
        if not self.manifest:
            raise RuntimeError("manifest 为空（请先调用 split_chapter + read_manifest）")
        self._resolve_agent_ids()
        sections = self.manifest.get("sections", [])
        chapter_title = self.manifest.get("source_name", self.chapter)
        workspace_base = self.book_root
        self.suffix = self.plan_id.split("_")[-1][:6]

        steps = []
        quality_write_run = f"run_quality_write_{self.chapter}_{self.suffix}"
        writer_runs = []

        # writer children (parallel)
        for idx, sec in enumerate(sections):
            sec_id = sec.get("section_id", f"s{idx + 1:02d}")
            w_run = f"run_w_{self.chapter}_{sec_id}_{self.suffix}"
            writer_runs.append(w_run)
            steps.append(self._build_writer_step(sec, quality_write_run, idx, idx == 0))

        # quality_write entry (waits for all writers)
        steps.append({
            "run_uuid": quality_write_run,
            "agent_id": self.agent_ids.get("planner", ""),
            "task_id": self._get_task_id("planner", "book_manage_quality"),
            "description": f"写作阶段 quality gate — {self.chapter}",
            "instruction": (
                f"【写作阶段 quality】{self.chapter}。book_root={self.book_root}/、plan_id={self.plan_id}。等待所有 writer 完成并回执后，汇总判定。split={self.split_dir}。共{len(sections)}节。children: {writer_runs}"
            ),
            "children": writer_runs,
            "parent_run_uuid": "",
            "prev_task": "",
            "next_task": "",
        })

        self.plan_json = {
            "plan_id": self.plan_id,
            "project_id": self.project_id,
            "description": f"{self.chapter} 纯写作计划 - {chapter_title}",
            "activate_mode": activate_mode,
            "start_task_run_uuid": quality_write_run if activate_mode == "chain" else "",
            "chapter": self.chapter,
            "split_dir": self.split_dir,
            "drafts_dir": self.drafts_dir,
            "steps": steps,
        }
        return self.plan_json

    def generate_plan_review_only(self, round_num: int = 1) -> dict:
        """[Method 3] Generate a single-round review pipeline plan JSON (review -> integrate -> revise -> beta -> judge).

                round_num: round number (1/2/3).

                Topology (chain):
                    review_entry (children: [r1..N])
                      |- reviewer_1..N (parallel review)
                    integrate (integrate feedback)
                    revise_entry (children: [rv1..N])
                      |- writer_1..N (parallel revision)
                    beta_entry (children: [b1..N])
                      |- beta_reader_1..N (parallel beta reading)
                    judge (quality judgement -> pass means merge, fail activates the next round)

                Returns the plan_json dict.
        """
        if not self.manifest:
            raise RuntimeError("manifest 为空（请先调用 split_chapter + read_manifest）")
        self._resolve_agent_ids()
        sections = self.manifest.get("sections", [])
        chapter_title = self.manifest.get("source_name", self.chapter)
        workspace_base = self.book_root
        self.suffix = self.plan_id.split("_")[-1][:6]
        r = round_num
        _focus = self._round_focus(r)   # * This round's focus (three layered tiers)

        steps = []

        # ---- review entry + reviewer children ----
        review_entry_run = f"run_review_r{r}_entry_{self.chapter}_{self.suffix}"
        review_runs = []
        for sec in sections:
            sec_id = sec.get("section_id", "")
            rr = f"run_review_r{r}_{self.chapter}_{sec_id}_{self.suffix}"
            review_runs.append(rr)
            steps.append({
                "run_uuid": rr,
                "agent_id": self.agent_ids.get("reviewer", ""),
                "task_id": self._get_task_id("reviewer", "book_review_content"),
                "description": f"审校 {self.chapter}/{sec.get('file', '')} 第{r}轮",
                "instruction": (
                    f"审校 {self.chapter}/{sec.get('file', '')}（{sec.get('title', '')}）第{r}轮。\n"
                    f"book_root={self.book_root}/、plan_id={self.plan_id}。\n"
                    f"drafts={self.drafts_dir}。\n"
                    f"★ 本轮只做「{_focus['layer']}」这一层：{_focus['review']}\n"
                    f"★ 产出组织：{_focus['review_output']}\n"
                    f"审校当前最新版本 _vN.md，产出 _vN_review.md。"
                ),
                "children": [],
                "parent_run_uuid": review_entry_run,
                "prev_task": "",
                "next_task": "",
            })
        steps.append({
            "run_uuid": review_entry_run,
            "agent_id": self.agent_ids.get("planner", ""),
            "task_id": self._get_task_id("planner", "book_manage_quality"),
            "description": f"审校阶段 entry·第{r}轮 — {self.chapter}",
            "instruction": (f"【审校阶段 quality·第{r}轮】{self.chapter}。book_root={self.book_root}/、plan_id={self.plan_id}。等待所有 reviewer 完成审校并回执后汇总判定。children: {review_runs}"),
            "children": review_runs,
            "parent_run_uuid": "",
            "prev_task": "",
            "next_task": "",
        })

        # ---- integrate ----
        integrate_run = f"run_integrate_r{r}_{self.chapter}_{self.suffix}"
        steps.append({
            "run_uuid": integrate_run,
            "agent_id": self.agent_ids.get("reviewer", ""),
            "task_id": self._get_task_id("reviewer", "book_integrate_feedback"),
            "description": f"整合审校反馈·第{r}轮 — {self.chapter}",
            "instruction": (f"整合第{r}轮审校反馈：{self.chapter}。book_root={self.book_root}/、plan_id={self.plan_id}。drafts={self.drafts_dir}。读取所有 _vN_review.md，整合成统一修订清单。"),
            "children": [],
            "parent_run_uuid": "",
            "prev_task": review_entry_run,
            "next_task": "",
        })

        # ---- revise entry + writer children ----
        revise_entry_run = f"run_revise_r{r}_entry_{self.chapter}_{self.suffix}"
        revise_runs = []
        for sec in sections:
            sec_id = sec.get("section_id", "")
            rv = f"run_revise_r{r}_{self.chapter}_{sec_id}_{self.suffix}"
            revise_runs.append(rv)
            steps.append({
                "run_uuid": rv,
                "agent_id": self.agent_ids.get("writer", ""),
                "task_id": self._get_task_id("writer", "book_revise_section"),
                "description": f"修订 {self.chapter}/{sec.get('file', '')} 第{r}轮",
                "instruction": (
                    f"修订 {self.chapter}/{sec.get('file', '')}（{sec.get('title', '')}）第{r}轮。\n"
                    f"book_root={self.book_root}/、plan_id={self.plan_id}。\n"
                    f"drafts={self.drafts_dir}。\n"
                    f"★ 本轮只改「{_focus['layer']}」：{_focus['revise']}\n"
                    f"读取整合后的修订清单，产出 _vN_revised.md。"
                ),
                "children": [],
                "parent_run_uuid": revise_entry_run,
                "prev_task": "",
                "next_task": "",
            })
        steps.append({
            "run_uuid": revise_entry_run,
            "agent_id": self.agent_ids.get("planner", ""),
            "task_id": self._get_task_id("planner", "book_manage_quality"),
            "description": f"修订阶段 entry·第{r}轮 — {self.chapter}",
            "instruction": (f"【修订阶段 quality·第{r}轮】{self.chapter}。book_root={self.book_root}/、plan_id={self.plan_id}。等待所有 writer 修订完成并回执后汇总判定。children: {revise_runs}"),
            "children": revise_runs,
            "parent_run_uuid": "",
            "prev_task": integrate_run,
            "next_task": "",
        })

        # ---- beta entry + beta_reader children ----
        beta_entry_run = f"run_beta_r{r}_entry_{self.chapter}_{self.suffix}"
        beta_runs = []
        for sec in sections:
            sec_id = sec.get("section_id", "")
            br = f"run_beta_r{r}_{self.chapter}_{sec_id}_{self.suffix}"
            beta_runs.append(br)
            steps.append({
                "run_uuid": br,
                "agent_id": self.agent_ids.get("beta_reader", ""),
                "task_id": self._get_task_id("beta_reader", "book_beta_read"),
                "description": f"试读 {self.chapter}/{sec.get('file', '')} 第{r}轮",
                "instruction": (
                    f"试读 {self.chapter}/{sec.get('file', '')}（{sec.get('title', '')}）第{r}轮。\n"
                    f"book_root={self.book_root}/、plan_id={self.plan_id}。\n"
                    f"drafts={self.drafts_dir}。\n"
                    f"★ 本轮阅读视角：{_focus['beta']}\n"
                    f"★ 笔记组织：{_focus['beta_output']}\n"
                    f"沉浸式阅读修订后的最新版本，输出 _vN_beta.md 阅读笔记。"
                ),
                "children": [],
                "parent_run_uuid": beta_entry_run,
                "prev_task": "",
                "next_task": "",
            })
        steps.append({
            "run_uuid": beta_entry_run,
            "agent_id": self.agent_ids.get("planner", ""),
            "task_id": self._get_task_id("planner", "book_manage_quality"),
            "description": f"试读阶段 entry·第{r}轮 — {self.chapter}",
            "instruction": (f"【试读阶段 quality·第{r}轮】{self.chapter}。book_root={self.book_root}/、plan_id={self.plan_id}。等待所有 beta_reader 完成试读并回执后汇总判定。children: {beta_runs}"),
            "children": beta_runs,
            "parent_run_uuid": "",
            "prev_task": revise_entry_run,
            "next_task": "",
        })

        # ---- judge ----
        judge_run = f"run_quality_judge_r{r}_{self.chapter}_{self.suffix}"
        steps.append({
            "run_uuid": judge_run,
            "agent_id": self.agent_ids.get("planner", ""),
            "task_id": self._get_task_id("planner", "book_manage_quality"),
            "description": f"质量判定·第{r}轮 — {self.chapter}",
            "instruction": (
                f"【质量判定·第{r}轮·{_focus['layer']}】{self.chapter}。\n"
                f"book_root={self.book_root}/、plan_id={self.plan_id}。\n"
                f"★ 本轮判据只覆盖「{_focus['layer']}」：{_focus['judge']}。\n"
                f"其它层的问题只记录、不作为本轮不通过的理由。\n"
                f"读取审校报告 + 试读笔记，判定：通过→可合并。不通过→需下一轮修订。"
            ),
            "children": [],
            "parent_run_uuid": "",
            "prev_task": beta_entry_run,
            "next_task": "",
        })

        self.plan_json = {
            "plan_id": self.plan_id,
            "project_id": self.project_id,
            "description": f"{self.chapter} 审校管线·第{r}轮 - {chapter_title}",
            "activate_mode": "chain",
            "start_task_run_uuid": review_entry_run,
            "chapter": self.chapter,
            "round": r,
            "split_dir": self.split_dir,
            "drafts_dir": self.drafts_dir,
            "steps": steps,
        }
        return self.plan_json

    def generate_plan_merge_only(self) -> dict:
        """[Method 4] Generate the pure merge-stage plan JSON.

                Precondition: all subsections are final under drafts_dir and every entry in status.json is final.

                Returns the plan_json dict.
        """
        if not self.manifest:
            raise RuntimeError("manifest 为空（请先调用 split_chapter + read_manifest）")
        self._resolve_agent_ids()
        workspace_base = self.book_root
        self.suffix = self.plan_id.split("_")[-1][:6]

        merge_run = f"run_merge_{self.chapter}_{self.suffix}"
        steps = [
            {
                "run_uuid": merge_run,
                "agent_id": self.agent_ids.get("planner", ""),
                "task_id": self._get_task_id("planner", "book_merge_chapter"),
                "description": f"合并 {self.chapter} 全部小节定稿",
                "instruction": (f"合并 {self.chapter} 全部小节定稿。\nbook_root={self.book_root}/、plan_id={self.plan_id}。\n确认 status.json 全部 final 后，cat 拼接清理覆盖源文件。chapter_path={self.chapter_path}。"),
                "children": [],
                "parent_run_uuid": "",
                "prev_task": "",
                "next_task": "",
            }
        ]

        self.plan_json = {
            "plan_id": self.plan_id,
            "project_id": self.project_id,
            "description": f"{self.chapter} 合并计划",
            "activate_mode": "chain",
            "start_task_run_uuid": merge_run,
            "chapter": self.chapter,
            "split_dir": self.split_dir,
            "drafts_dir": self.drafts_dir,
            "steps": steps,
        }
        return self.plan_json


# ============================================================
# MultiChapterOrchestrator -- plans within plans
# ============================================================


class MultiChapterOrchestrator:
    """
        Multi-chapter orchestrator: N chapters -> N sub-plans + 1 master plan.

        Each step in the master plan uses the plan_execute task to activate the matching sub-plan.
        Both serial (prev_task/next_task chain) and parallel (no dependency) modes are supported.
    """

    def __init__(
        self,
        book_root: str,
        chapters: list[dict],
        parallel: bool = False,
    ):
        """
                Args:
                    book_root: absolute path of the book root directory.
                    chapters: chapter list, each {chapter_path, project_id, stage?, round_num?, sub_plan_id?}.
                stage defaults to "full"; the allowed values match agx_book_orchestrate_chapter:
                - "split": split into H2 sections only, generating manifest.json + TOC.txt but no plan
                - "write": generate the pure writing plan (quality gate + N parallel writers), no review
                - "review": generate the review plan; round_num controls the rounds (default 1)
                - "merge": generate the merge plan, combining all writer output into the final chapter
                - "full": the complete pipeline (writing -> 3 review rounds -> 3 beta rounds -> revision -> merge) in one go
                round_num applies only when stage="review"; default 1.
                    parallel: False = serial (prev_task chain), True = parallel (no dependencies).
        """
        self.book_root = book_root
        self.chapters = chapters
        self.parallel = parallel

        # Validation
        if not chapters:
            raise ValueError("chapters 不能为空")
        for i, ch in enumerate(chapters):
            if "chapter_path" not in ch:
                raise ValueError(f"chapters[{i}] 缺少 chapter_path")
            if "project_id" not in ch:
                raise ValueError(f"chapters[{i}] 缺少 project_id")

    def _resolve_plan_execute(self, project_id: str) -> tuple:
        """Find the agent_id + task_id under the project that can execute plan_execute.

                Three-level fallback (2026-10-27, aligned with _resolve_agent_ids)
                -------------------------------------------------
                  1) agent_labs == 'super_pm'         -> a PM cloned from the super_pm template (traditional form, preferred)
                  2) agent_role == 'project_manager'  -> a PM created standalone without cloning (e.g. book_up_chain)
                  3) agx_project.admin_agent_id       -> the platform-registered project admin (the most authoritative fallback)

                Why this fix: the original only recognized (1), while _resolve_agent_ids (:954) had already gained
                the three-level fallback on 10-26 -> two different rule sets in one file: "single-chapter full runs
                (through the inline loop, not this function), but the moment you use plan-within-plan to chain stages
                it raises ValueError: no deployed super_pm clone instance".

                Difference from (2)(3): (1)(2) require deploy_status='deployed'; (3) takes the platform-registered
                admin_agent_id without requiring deployment status (it may never have gone through deployment),
                but all three **must hold the plan_execute task** to be adopted --
                otherwise "we found a PM but cannot dispatch to it", which equals not found.
        """
        rows = executeSql(
            """SELECT r.agent_id, r.agent_labs, r.agent_role
               FROM agx_agent_registry r
               WHERE r.project_id = %s AND r.active_status = 1
                 AND r.deploy_status = 'deployed'""",
            (project_id,),
        )

        candidates = []
        candidates += [r["agent_id"] for r in rows if r.get("agent_labs") == "super_pm"]
        for r in rows:
            if r.get("agent_role") == "project_manager" and r["agent_id"] not in candidates:
                candidates.append(r["agent_id"])

        admin_rows = executeSql(
            "SELECT admin_agent_id FROM agx_project WHERE project_id = %s", (project_id,)
        )
        if admin_rows and admin_rows[0].get("admin_agent_id"):
            _admin = admin_rows[0]["admin_agent_id"]
            if _admin not in candidates:
                candidates.append(_admin)

        if not candidates:
            raise ValueError(
                f"项目 {project_id} 无可用于 plan_execute 的 PM"
                f"（super_pm / agent_role=project_manager / admin_agent_id 均为空）"
            )

        for agent_id in candidates:
            task_rows = executeSql(
                """SELECT task_id FROM agx_task_base
                   WHERE agent_id = %s AND task_labs = 'plan_execute' AND active_status = 1
                   LIMIT 1""",
                (agent_id,),
            )
            if task_rows:
                return agent_id, task_rows[0]["task_id"]

        raise ValueError(
            f"项目 {project_id} 的候选 PM 均缺少 plan_execute task：{candidates}"
        )

    def build_master_plan(self) -> dict:
        """
                Build the master plan JSON.

                Sub-plans are generated by the caller (the MCP layer) before build_master_plan,
                and each chapter's sub_plan_id field carries its sub plan_id.

                Returns:
                    dict: the master plan JSON, ready to pass to agx_plan_create + agx_plan_deploy.
        """
        N = len(self.chapters)
        plan_id = f"plan_multi_{N}chapters_{uuid.uuid4().hex[:8]}"
        project_id = self.chapters[0].get("project_id", "")

        # Resolve the agent_id + task_id for plan_execute
        pm_agent_id, pm_task_id = self._resolve_plan_execute(project_id)

        steps = []
        for i, ch in enumerate(self.chapters):
            chapter_path = ch["chapter_path"]
            sub_plan_id = ch.get("sub_plan_id", "")
            chapter_name_full = os.path.splitext(os.path.basename(chapter_path))[0]
            # Extract the bare chapter number (e.g. ch10) so no Chinese reaches run_uuid/session_id
            # m = re.match(r"(ch\d+)", chapter_name_full)
            # chapter_tag = m.group(1) if m else chapter_name_full

            step_run = f"run_plan_execute_{uuid.uuid4().hex[:6]}"
            step = {
                "run_uuid": step_run,
                "agent_id": pm_agent_id,
                "task_id": pm_task_id,
                "task_type": "plan",
                "task_labs": "plan_execute",
                "step_name": f"编排 {chapter_name_full}",
                "description": f"激活子计划 {sub_plan_id}（{chapter_name_full}）",
                "instruction": (f"激活子计划并等待完成。\nplan_id: {sub_plan_id}\n章节: {chapter_name_full}\nbook_root: {self.book_root}"),
                "children": [],
                "parent_run_uuid": "",
                "prev_task": "",
                "next_task": "",
                "timeout_seconds": 7200,
                "needs_reply": 1,
                "x_desc": json.dumps(
                    {"plan_id": sub_plan_id, "chapter": chapter_name_full},
                    ensure_ascii=False,
                ),
            }
            steps.append(step)

        # -- Serial: prev_task / next_task chain --
        if not self.parallel and N > 1:
            for i in range(N - 1):
                steps[i]["next_task"] = steps[i + 1]["run_uuid"]
                steps[i + 1]["prev_task"] = steps[i]["run_uuid"]

        master_plan = {
            "plan_id": plan_id,
            "project_id": self.chapters[0].get("project_id", ""),
            "description": f"多章编排（{N} 章，{'并行' if self.parallel else '串行'}）",
            "activate_mode": "watchdog",
            "parallel": self.parallel,
            "steps": steps,
            "chapter_list": [
                {
                    "chapter_path": ch["chapter_path"],
                    "chapter_name": os.path.splitext(os.path.basename(ch["chapter_path"]))[0],
                    "sub_plan_id": ch.get("sub_plan_id", ""),
                    "project_id": ch.get("project_id", ""),
                    "stage": ch.get("stage", "full"),
                    "round_num": ch.get("round_num", 1),
                }
                for ch in self.chapters
            ],
        }
        return master_plan
