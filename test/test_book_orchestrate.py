"""
book_orchestrate E2E tests
Covers: ChapterOrchestrator -> run() dispatching by stage

Refactored 2026-06-27:
- plan_type -> stage (split / write / review / merge / full)
- split_dir / drafts_dir / plans_dir all live under workspace/, organised per chapter
- split and workspace creation are idempotent (skipped when they already exist)
"""

import json
import os
import sys
import tempfile
import unittest.mock

import pytest

# ── path injection ──
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from tools.book_orchestrate import ChapterOrchestrator


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def book_root():
    # Point this at any local manuscript tree; the default is a placeholder so
    # nothing machine-specific ships with the repository.
    return os.environ.get("AGX_TEST_BOOK_ROOT", "/home/test/booknew/effective_gpt")


@pytest.fixture
def chapter_path(book_root):
    return os.environ.get(
        "AGX_TEST_CHAPTER_PATH",
        os.path.join(
            book_root,
            "ch07_\u7b2c7\u7ae0  MCP\u534f\u8bae\uff1a\u5927\u6a21\u578b\u7684\u201c\u795e\u7ecf\u7a81\u89e6\u201d.md",
        ),
    )


@pytest.fixture
def project_id():
    # A real project id must exist in the environment under test.
    return os.environ.get("AGX_TEST_PROJECT_ID", "proj_test_000000000000")


@pytest.fixture
def orch(book_root, chapter_path, project_id):
    """Default orchestrator (stage=write, use_git=False)."""
    return ChapterOrchestrator(
        book_root=book_root,
        chapter_path=chapter_path,
        project_id=project_id,
        stage="write",
        use_git=False,
    )


# ============================================================
# test classes
# ============================================================

class TestValidate:
    """validate() tests"""

    def test_validate_ok(self, orch):
        orch.validate()
        assert orch.chapter == "ch07"

    def test_validate_file_not_found(self, book_root, project_id):
        orch = ChapterOrchestrator(
            book_root=book_root,
            chapter_path="/nonexistent/ch01_test.md",
            project_id=project_id,
            use_git=False,
        )
        with pytest.raises(ValueError, match="章源文件不存在"):
            orch.validate()

    def test_validate_book_root_not_found(self, project_id, chapter_path):
        orch = ChapterOrchestrator(
            book_root="/nonexistent/book_root",
            chapter_path=chapter_path,
            project_id=project_id,
            use_git=False,
        )
        with pytest.raises(ValueError, match="书稿根目录不存在"):
            orch.validate()

    def test_validate_project_not_found(self, book_root, chapter_path):
        orch = ChapterOrchestrator(
            book_root=book_root,
            chapter_path=chapter_path,
            project_id="proj_nonexistent",
            use_git=False,
        )
        with pytest.raises(ValueError, match="项目不存在"):
            orch.validate()

    def test_chapter_extraction(self, orch):
        orch.validate()
        assert orch.chapter == "ch07"


class TestSplitChapter:
    """split_chapter() tests — idempotent splitting"""

    def test_split_creates_manifest(self, orch):
        orch.validate()
        orch.setup_git()
        result = orch.split_chapter()
        assert os.path.isfile(result["manifest_path"])
        assert os.path.isfile(result["toc_path"])

    def test_split_dir_structure(self, orch):
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        # directory layout: workspace/split/ch07/
        assert orch.split_dir.endswith(f"split/{orch.chapter}")
        assert os.path.isdir(orch.split_dir)

    def test_split_idempotent(self, orch):
        """Calling split twice should succeed (idempotent)"""
        orch.validate()
        orch.setup_git()
        result1 = orch.split_chapter()
        result2 = orch.split_chapter()
        assert result1["manifest_path"] == result2["manifest_path"]
        # the second call should reuse the existing split
        assert result2.get("reused") is True

    def test_split_no_plan_id_generated(self, orch):
        """split should not generate a plan_id on its own"""
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        assert orch.plan_id == ""


class TestReadManifest:
    """read_manifest() tests"""

    def test_read_manifest_ok(self, orch):
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        m = orch.read_manifest()
        assert "sections" in m
        assert "source_name" in m
        assert len(m["sections"]) > 0

    def test_manifest_section_structure(self, orch):
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        orch.read_manifest()
        for sec in orch.manifest["sections"]:
            assert "section_id" in sec
            assert "title" in sec
            assert "file" in sec


class TestCreateWorkspace:
    """create_workspace() tests — idempotent"""

    def test_create_workspace_ok(self, book_root, chapter_path, project_id):
        """Create a workspace and verify the status.json structure"""
        orch = ChapterOrchestrator(
            book_root=book_root,
            chapter_path=chapter_path,
            project_id=project_id,
            stage="write",
            use_git=False,
        )
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        orch.read_manifest()
        orch.plan_id = "plan_ws_test_ok"
        # force-remove the old status.json to start clean (avoids idempotency pollution)
        status_path = os.path.join(orch.drafts_dir, "status.json")
        if os.path.isfile(status_path):
            os.remove(status_path)
        result = orch.create_workspace()
        assert os.path.isdir(result["drafts_dir"])
        assert os.path.isfile(result["status_path"])
        # verify the status.json structure
        with open(result["status_path"]) as f:
            status = json.load(f)
        assert status["plan"] == orch.plan_id
        assert status["chapter"] == orch.chapter
        for key, val in status["sections"].items():
            assert val["status"] == "pending"

    def test_workspace_idempotent(self, orch):
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        orch.read_manifest()
        orch.plan_id = "plan_test_222222"
        result1 = orch.create_workspace()
        result2 = orch.create_workspace()
        assert result1["drafts_dir"] == result2["drafts_dir"]
        assert result2.get("reused") is True

    def test_drafts_dir_per_chapter(self, orch):
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        orch.read_manifest()
        orch.plan_id = "plan_test_333333"
        result = orch.create_workspace()
        assert "drafts" in result["drafts_dir"]
        assert orch.chapter in result["drafts_dir"]


# ============================================================
# atomic-method tests
# ============================================================

class TestGeneratePlanWriteOnly:
    """generate_plan_write_only() tests"""

    def test_write_only_plan_structure(self, orch):
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        orch.read_manifest()
        orch.plan_id = "plan_write_test_01"
        plan = orch.generate_plan_write_only()
        assert plan["activate_mode"] == "chain"
        assert plan["plan_id"] == "plan_write_test_01"
        assert len(plan["steps"]) > 0
        # there should be a quality_write entry plus N writers
        run_ids = [s["run_uuid"] for s in plan["steps"]]
        assert any("quality_write" in r for r in run_ids)

    def test_write_only_default_chain(self, orch):
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        orch.read_manifest()
        orch.plan_id = "plan_write_test_02"
        plan = orch.generate_plan_write_only()
        assert plan["activate_mode"] == "chain"

    def test_write_only_watchdog_mode(self, orch):
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        orch.read_manifest()
        orch.plan_id = "plan_write_test_03"
        plan = orch.generate_plan_write_only(activate_mode="watchdog")
        assert plan["activate_mode"] == "watchdog"
        assert plan["start_task_run_uuid"] == ""


class TestGeneratePlanReviewOnly:
    """generate_plan_review_only() tests"""

    def test_review_only_round1(self, orch):
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        orch.read_manifest()
        orch.plan_id = "plan_review_r1_test"
        plan = orch.generate_plan_review_only(round_num=1)
        assert plan["activate_mode"] == "chain"
        assert plan["round"] == 1
        run_ids = [s["run_uuid"] for s in plan["steps"]]
        assert any("review_r1" in r for r in run_ids)
        assert any("revise_r1" in r for r in run_ids)
        assert any("beta_r1" in r for r in run_ids)
        assert any("integrate_r1" in r for r in run_ids)
        assert any("judge_r1" in r for r in run_ids)

    def test_review_only_round3(self, orch):
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        orch.read_manifest()
        orch.plan_id = "plan_review_r3_test"
        plan = orch.generate_plan_review_only(round_num=3)
        assert plan["round"] == 3

    def test_review_only_chain_default(self, orch):
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        orch.read_manifest()
        orch.plan_id = "plan_review_test_chain"
        plan = orch.generate_plan_review_only()
        assert plan["activate_mode"] == "chain"
        assert plan["start_task_run_uuid"] != ""


class TestGeneratePlanMergeOnly:
    """generate_plan_merge_only() tests"""

    def test_merge_only_plan(self, orch):
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        orch.read_manifest()
        orch.plan_id = "plan_merge_test"
        plan = orch.generate_plan_merge_only()
        assert plan["activate_mode"] == "chain"
        assert len(plan["steps"]) == 1
        assert "merge" in plan["steps"][0]["run_uuid"]

    def test_merge_only_default_chain(self, orch):
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        orch.read_manifest()
        orch.plan_id = "plan_merge_test_2"
        plan = orch.generate_plan_merge_only()
        assert plan["activate_mode"] == "chain"
        assert plan["start_task_run_uuid"] != ""


class TestGeneratePlanFull:
    """generate_plan_full() tests"""

    def test_full_plan_chain(self, orch):
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        orch.read_manifest()
        orch.plan_id = "plan_full_test"
        plan = orch.generate_plan_full()
        assert plan["activate_mode"] == "chain"
        # full should include every stage
        run_ids = [s["run_uuid"] for s in plan["steps"]]
        assert any("quality_write" in r for r in run_ids)
        assert any("review_r" in r for r in run_ids)
        assert any("beta_r" in r for r in run_ids)
        assert any("merge" in r for r in run_ids)

    def test_full_plan_many_steps(self, orch):
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        orch.read_manifest()
        orch.plan_id = "plan_full_big"
        plan = orch.generate_plan_full()
        assert len(plan["steps"]) > 10


# ============================================================
# run() dispatch-by-stage tests
# ============================================================

class TestRunByStage:
    """run() dispatching by stage"""

    def test_run_stage_split(self, book_root, chapter_path, project_id):
        orch = ChapterOrchestrator(
            book_root=book_root,
            chapter_path=chapter_path,
            project_id=project_id,
            stage="split",
            use_git=False,
        )
        result = orch.run()
        assert result["success"]
        assert result["stage"] == "split"
        assert "plan_id" not in result  # split does not generate a plan
        assert result["sections_count"] > 0

    def test_run_stage_write(self, orch):
        orch.stage = "write"
        result = orch.run()
        assert result["success"]
        assert result["stage"] == "write"
        assert result["plan_id"].startswith("plan_")
        assert result["steps_count"] > 0
        assert os.path.isfile(result["plan_json_path"])

    def test_run_stage_review(self, orch):
        orch.stage = "review"
        orch.round_num = 1
        result = orch.run()
        assert result["success"]
        assert result["stage"] == "review"
        assert result["round_num"] == 1
        assert result["plan_id"].startswith("plan_")

    def test_run_stage_merge(self, orch):
        orch.stage = "merge"
        result = orch.run()
        assert result["success"]
        assert result["stage"] == "merge"
        assert os.path.isfile(result["plan_json_path"])

    def test_run_stage_full(self, orch):
        orch.stage = "full"
        result = orch.run()
        assert result["success"]
        assert result["stage"] == "full"
        assert result["steps_count"] > 10

    def test_multiple_stages_reuse_split(self, book_root, chapter_path, project_id):
        """Calling different stages repeatedly reuses the same split directory"""
        # stage=split, first time
        orch1 = ChapterOrchestrator(
            book_root=book_root,
            chapter_path=chapter_path,
            project_id=project_id,
            stage="split",
            use_git=False,
        )
        r1 = orch1.run()
        assert r1["success"]
        split_dir = r1["split_dir"]

        # stage=write reuses the split
        orch2 = ChapterOrchestrator(
            book_root=book_root,
            chapter_path=chapter_path,
            project_id=project_id,
            stage="write",
            use_git=False,
        )
        r2 = orch2.run()
        assert r2["success"]
        # the split directory is identical
        assert r2["split_dir"] == split_dir

    def test_multiple_stages_share_drafts(self, book_root, chapter_path, project_id):
        """Different stages share the same drafts directory"""
        orch_w = ChapterOrchestrator(
            book_root=book_root,
            chapter_path=chapter_path,
            project_id=project_id,
            stage="write",
            use_git=False,
        )
        rw = orch_w.run()
        assert rw["success"]

        orch_r = ChapterOrchestrator(
            book_root=book_root,
            chapter_path=chapter_path,
            project_id=project_id,
            stage="review",
            use_git=False,
        )
        rr = orch_r.run()
        assert rr["success"]
        # the drafts directory is identical
        assert rr["drafts_dir"] == rw["drafts_dir"]


class TestPlanOutputFiles:
    """plan JSON file persistence tests"""

    def test_plan_file_per_stage(self, orch):
        """Each stage generates its own plan JSON file"""
        orch.stage = "write"
        rw = orch.run()
        assert rw["success"]
        write_plan = rw["plan_json_path"]

        orch.stage = "review"
        orch.round_num = 1
        orch.plan_id = ""  # reset to trigger a new plan_id
        rr = orch.run()
        assert rr["success"]
        review_plan = rr["plan_json_path"]

        # the two file paths differ
        assert write_plan != review_plan
        assert "write" in os.path.basename(write_plan)
        assert "review" in os.path.basename(review_plan)

    def test_plans_dir_structure(self, orch):
        orch.stage = "write"
        result = orch.run()
        assert result["success"]
        # confirm the plans directory layout
        assert os.path.isdir(orch.plans_dir)
        assert orch.chapter in orch.plans_dir


class TestAgentResolution:
    """agent resolution tests"""

    def test_resolve_agent_ids_ok(self, orch):
        orch.validate()
        orch.setup_git()
        orch.split_chapter()
        orch.read_manifest()
        orch.plan_id = "plan_agent_test"
        orch.create_workspace()
        ids = orch._resolve_agent_ids()
        assert ids["writer"] is not None
        assert ids["reviewer"] is not None
        assert ids["beta_reader"] is not None


class TestBackwardCompat:
    """Backward-compatibility tests"""

    def test_default_stage_is_write(self, book_root, chapter_path, project_id):
        orch = ChapterOrchestrator(
            book_root=book_root,
            chapter_path=chapter_path,
            project_id=project_id,
            use_git=False,
        )
        orch.validate()
        assert orch.stage == "write"

    def test_invalid_stage_falls_back(self, book_root, chapter_path, project_id):
        orch = ChapterOrchestrator(
            book_root=book_root,
            chapter_path=chapter_path,
            project_id=project_id,
            stage="invalid_stage",
            use_git=False,
        )
        orch.validate()
        assert orch.stage == "write"  # fall back to the default


# ============================================================
# MultiChapterOrchestrator tests
# ============================================================

from tools.book_orchestrate import MultiChapterOrchestrator


class TestMultiChapterOrchestrator:
    """build_master_plan() pure-logic tests (no MCP/DB)."""

    def test_serial_default(self):
        """Default serial mode: steps are chained through prev_task/next_task."""
        chapters = [
            {"chapter_path": "/book/ch03.md", "project_id": "proj_test",
             "sub_plan_id": "plan_ch03"},
            {"chapter_path": "/book/ch04.md", "project_id": "proj_test",
             "sub_plan_id": "plan_ch04"},
            {"chapter_path": "/book/ch05.md", "project_id": "proj_test",
             "sub_plan_id": "plan_ch05"},
        ]
        orch = MultiChapterOrchestrator(
            book_root="/book", chapters=chapters, parallel=False
        )
        plan = orch.build_master_plan()

        assert plan["plan_id"] == "plan_multi_3chapters"
        assert plan["parallel"] is False
        assert plan["activate_mode"] == "watchdog"
        assert len(plan["steps"]) == 3
        assert len(plan["chapter_list"]) == 3

        # serial chain
        steps = plan["steps"]
        assert steps[0]["next_task"] == steps[1]["run_uuid"]
        assert steps[1]["prev_task"] == steps[0]["run_uuid"]
        assert steps[1]["next_task"] == steps[2]["run_uuid"]
        assert steps[2]["prev_task"] == steps[1]["run_uuid"]
        assert steps[0]["prev_task"] == ""
        assert steps[-1]["next_task"] == ""

        # every step is a plan_execute task
        for s in steps:
            assert s["task_labs"] == "plan_execute"
            assert s["task_type"] == "plan"
            assert s["needs_reply"] == 1

    def test_parallel_mode(self):
        """Parallel mode: no prev_task/next_task dependency between steps."""
        chapters = [
            {"chapter_path": "/book/ch03.md", "project_id": "proj_test",
             "sub_plan_id": "plan_ch03"},
            {"chapter_path": "/book/ch04.md", "project_id": "proj_test",
             "sub_plan_id": "plan_ch04"},
        ]
        orch = MultiChapterOrchestrator(
            book_root="/book", chapters=chapters, parallel=True
        )
        plan = orch.build_master_plan()

        assert plan["parallel"] is True
        assert len(plan["steps"]) == 2

        for s in plan["steps"]:
            assert s["prev_task"] == ""
            assert s["next_task"] == ""

    def test_single_chapter(self):
        """Single chapter: the step has no predecessor or successor."""
        chapters = [
            {"chapter_path": "/book/ch03.md", "project_id": "proj_test",
             "sub_plan_id": "plan_ch03"}
        ]
        orch = MultiChapterOrchestrator(
            book_root="/book", chapters=chapters, parallel=False
        )
        plan = orch.build_master_plan()

        assert len(plan["steps"]) == 1
        s = plan["steps"][0]
        assert s["prev_task"] == ""
        assert s["next_task"] == ""

    def test_step_instruction_contains_plan_id(self):
        """Each step instruction contains the corresponding sub-plan plan_id."""
        chapters = [
            {"chapter_path": "/book/ch03.md", "project_id": "proj_test",
             "sub_plan_id": "plan_ch03_abc"},
            {"chapter_path": "/book/ch04.md", "project_id": "proj_test",
             "sub_plan_id": "plan_ch04_def"},
        ]
        orch = MultiChapterOrchestrator(
            book_root="/book", chapters=chapters, parallel=True
        )
        plan = orch.build_master_plan()

        for s in plan["steps"]:
            inst = s["instruction"]
            assert "plan_id:" in inst
            xd = json.loads(s["x_desc"])
            assert "plan_id" in xd
            assert "chapter" in xd

    def test_chapter_list_preserved(self):
        """chapter_list records the original parameters of every chapter."""
        chapters = [
            {"chapter_path": "/book/ch01.md", "project_id": "proj_a",
             "sub_plan_id": "plan_ch01", "stage": "full", "round_num": 3},
            {"chapter_path": "/book/ch02.md", "project_id": "proj_a",
             "sub_plan_id": "plan_ch02", "stage": "review", "round_num": 2},
        ]
        orch = MultiChapterOrchestrator(
            book_root="/book", chapters=chapters, parallel=False
        )
        plan = orch.build_master_plan()

        cl = plan["chapter_list"]
        assert cl[0]["chapter_name"] == "ch01"
        assert cl[0]["stage"] == "full"
        assert cl[0]["round_num"] == 3
        assert cl[1]["chapter_name"] == "ch02"
        assert cl[1]["stage"] == "review"
        assert cl[1]["round_num"] == 2

    def test_empty_chapters_raises(self):
        """An empty chapter list raises ValueError."""
        import pytest
        with pytest.raises(ValueError, match="不能为空"):
            MultiChapterOrchestrator(
                book_root="/book", chapters=[], parallel=False
            )

    def test_missing_chapter_path_raises(self):
        """A missing chapter_path raises ValueError."""
        import pytest
        with pytest.raises(ValueError, match="缺少 chapter_path"):
            MultiChapterOrchestrator(
                book_root="/book", chapters=[{"project_id": "x"}], parallel=False
            )

    def test_missing_project_id_raises(self):
        """A missing project_id raises ValueError."""
        import pytest
        with pytest.raises(ValueError, match="缺少 project_id"):
            MultiChapterOrchestrator(
                book_root="/book", chapters=[{"chapter_path": "/x.md"}], parallel=False
            )

    def test_default_stage_and_round_num(self):
        """Default values are used when stage/round_num are omitted."""
        chapters = [
            {"chapter_path": "/book/ch03.md", "project_id": "proj_test",
             "sub_plan_id": "plan_ch03"},
        ]
        orch = MultiChapterOrchestrator(
            book_root="/book", chapters=chapters, parallel=False
        )
        plan = orch.build_master_plan()

        cl = plan["chapter_list"][0]
        assert cl["stage"] == "full"
        assert cl["round_num"] == 1
