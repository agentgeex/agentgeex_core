#!/usr/bin/env python3
"""Task-list consistency check - agx_struct.json <-> tasks/*.md <-> code references.

* Why this script exists
    "Which tasks get installed on a customer machine" is decided in exactly one place: the tasks
    section of agx_struct.json (install.py's sync_global_struct walks it to create agx_task_base
    rows, taking x_desc from tasks/<task_labs>.md). That gives three kinds of drift, each of which
    only shows up on a customer machine:

      1. in struct, missing file  -> install silently writes the description (not the manual),
                                     or _ensure_task_row gets an empty x_desc
      2. in file, missing struct  -> the file never becomes a task, i.e. an orphan;
                                     but if code reads it by name via _load_task_desc,
                                     it is still a live file - the two must be distinguishable
      3. code reads by name, no file -> things like agx_note_main_session fail outright

    Nobody can remember these three. So make it an executable gate, run before release.

* Field semantics (verified, do not change from memory)
    . struct.tasks[].md_file      manual path, defaults to tasks/<task_labs>.md
    . struct.tasks[].is_private   0 = shared task (one row per agent)
                                  1 = private task (rows only for owner_agent)
    . install.py's _load_md() reads **verbatim**, it does not strip frontmatter
      -> x_desc carries `---\nreply_mode: ...\n---`, which is existing behaviour and not an error here
    . tasks/*.md frontmatter is **not parsed by any code** (reply_mode comes from struct),
      so this script can safely use frontmatter for a declarative marker:

          task_base: none      <- declares "this file is not registered as a task_base row"

      Use case: main_session_notes.md is the instruction text source for agx_note_main_session /
      agx_daily_log step1 (read by code via _load_task_desc), but execution uses the general task_id,
      so it should **not** have a task_base row. With this marker, orphan files and "intentionally
      unregistered" live files become machine-distinguishable.

Usage:
    python3 scripts/verify_tasks.py            # files and config only
    python3 scripts/verify_tasks.py --db       # also check the live task_base table (needs credentials)
    python3 scripts/verify_tasks.py --json     # machine readable

Exit codes: 0 = consistent, 1 = drift (usable directly as a CI / pre-release gate).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


# ---- Python version gate ----------------------------------------------------
# This script does not work on 3.8 (it dies on an unrelated error such as
# `unsupported operand type(s) for |` -- measured 2026-11-23 on the system python3 of Ubuntu 20.04).
# Rather than let the user guess at an unrelated TypeError, state plainly how to run it.
if sys.version_info < (3, 10):  # noqa: UP036 - guards the runtime environment, not the style of this file
    sys.exit(
        "ERROR: %s 需要 Python 3.10+（当前 %d.%d）。\n"
        "       请在项目虚拟环境里运行，例如：\n"
        "           uv run python3 scripts/%s"
        % (Path(__file__).name, sys.version_info[0], sys.version_info[1], Path(__file__).name)
    )

ROOT = Path(__file__).resolve().parent.parent
STRUCT = ROOT / "agx_struct.json"
TASKS_DIR = ROOT / "tasks"

# call sites that read a manual by name (these task_labs must have a file)
_CODE_REF_RE = re.compile(r'_load_task_desc\(\s*["\']([a-z0-9_]+)["\']\s*\)')
_CODE_SEARCH_DIRS = ["agx_mcp", "scripts", "tools", "install.py"]
_CODE_SEARCH_SUFFIX = {".py"}


# ------------------------- basic reading -------------------------


def load_struct() -> dict:
    if not STRUCT.exists():
        raise SystemExit(f"找不到 {STRUCT}")
    return json.loads(STRUCT.read_text(encoding="utf-8"))


def parse_frontmatter(path: Path) -> dict:
    """Take only key: value pairs between the top --- ... --- (good enough, no yaml dependency)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return meta
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip().strip("'\"")
    return meta


def collect_code_refs() -> dict[str, list[str]]:
    """Scan code for _load_task_desc("x") references and return {task_labs: [file:line]}."""
    refs: dict[str, list[str]] = {}
    for sub in _CODE_SEARCH_DIRS:
        p = ROOT / sub
        if p.is_file():
            files = [p]
        elif p.is_dir():
            files = [
                f
                for f in p.rglob("*")
                if f.suffix in _CODE_SEARCH_SUFFIX and "__pycache__" not in f.parts
            ]
        else:
            continue
        for f in files:
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                continue
            # skip this script itself: its own string examples contain _load_task_desc("x")
            if f.name == Path(__file__).name:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                for m in _CODE_REF_RE.finditer(line):
                    refs.setdefault(m.group(1), []).append(f"{f.relative_to(ROOT)}:{i}")
    return refs


def git_tracked(rel_path: str) -> bool | None:
    """True/False; None when git is unavailable (not counted as an error)."""
    try:
        r = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel_path],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
        )
        return r.returncode == 0
    except Exception:
        return None


def load_db_rows() -> dict[str, dict] | None:
    """Read the live task_base overview; return None when there are no credentials / no pymysql (skip, not an error).

    Credentials come from the project's own config_loader (which injects ~/.agx/agx_creds.env into
    os.environ automatically) - the same path as scripts/verify_schema.py, no second mechanism.
    This way the caller does not have to `source` the credentials file first.
    """
    try:
        sys.path.insert(0, str(ROOT))
        from config_loader import config  # noqa: E402
        import pymysql  # type: ignore  # noqa: E402
    except SystemExit:
        return None
    except Exception:
        return None

    try:
        conn = pymysql.connect(
            host=config.mysql.get("host", "127.0.0.1"),
            port=int(config.mysql.get("port", 3306)),
            user=config.mysql.get("user", "root"),
            password=config.mysql.get("password", ""),
            database=config.mysql.get("database", ""),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
    except Exception:
        return None
    out: dict[str, dict] = {}
    try:
        with conn.cursor() as c:
            c.execute(
                "SELECT task_labs, COUNT(*) AS n, SUM(active_status) AS active, "
                "SUM(CASE WHEN x_desc IS NULL OR x_desc='' THEN 1 ELSE 0 END) AS no_desc "
                "FROM agx_task_base GROUP BY task_labs"
            )
            for row in c.fetchall():
                out[row["task_labs"]] = row
    finally:
        conn.close()
    return out


# ------------------------- check body -------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="任务清单一致性校验")
    ap.add_argument("--db", action="store_true", help="额外核对活库 task_base")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    struct = load_struct()
    tasks = struct.get("tasks", [])
    struct_map = {t["task_labs"]: t for t in tasks}

    files = {p.stem: p for p in TASKS_DIR.glob("*.md")}
    code_refs = collect_code_refs()

    errors: list[str] = []
    warnings: list[str] = []

    # 1) struct internal consistency
    dup = [k for k in struct_map if [t["task_labs"] for t in tasks].count(k) > 1]
    if dup:
        errors.append(f"struct 里 task_labs 重复: {sorted(set(dup))}")
    for tl, t in struct_map.items():
        mf = t.get("md_file") or f"tasks/{tl}.md"
        if not (ROOT / mf).exists():
            errors.append(f"[struct→文件] {tl}: md_file 不存在 ({mf})")
        if t.get("is_private", 1) != 0 and not t.get("owner_agent"):
            errors.append(f"[struct] {tl}: is_private=1 但没有 owner_agent，装不出行")

    # 2) file -> struct / exemption marker
    declared_none: set[str] = set()
    for tl, p in files.items():
        meta = parse_frontmatter(p)
        if str(meta.get("task_base", "")).lower() == "none":
            declared_none.add(tl)
        if tl in struct_map and tl in declared_none:
            errors.append(f"[冲突] {tl}: 已标 task_base=none，却又注册在 struct 里")

    orphans = sorted(set(files) - set(struct_map) - declared_none)
    for tl in orphans:
        ref = code_refs.get(tl)
        hint = f"（代码引用: {ref[0]}）" if ref else "（无任何引用）"
        errors.append(
            f"[文件→struct] {tl}.md 既不在 struct 也未标 task_base: none {hint}"
        )

    for tl in sorted(declared_none - set(files)):
        errors.append(f"[声明残留] {tl} 标了 task_base: none，但文件不存在")

    # 3) code reads by name -> file must exist
    for tl, locs in sorted(code_refs.items()):
        if tl not in files:
            errors.append(
                f"[代码→文件] _load_task_desc(\"{tl}\") 引用的 tasks/{tl}.md 不存在"
                f"（{locs[0]}，共 {len(locs)} 处）"
            )
        elif tl not in struct_map and tl not in declared_none and tl not in orphans:
            # anything already reported by [file->struct] above is not repeated
            errors.append(
                f"[代码→struct] {tl} 被代码读取，但既不在 struct 也无 task_base: none"
            )

    # 4) read-only check: files not tracked by git cannot reach a customer machine
    for tl, p in sorted(files.items()):
        tracked = git_tracked(str(p.relative_to(ROOT)))
        if tracked is False:
            warnings.append(f"[git] tasks/{tl}.md 未被 git 跟踪，发布包里不会有")

    # 5) live DB check
    db_info = None
    if args.db:
        db_info = load_db_rows()
        if db_info is None:
            warnings.append("[db] 跳过：无可用凭据（source ~/.agx/agx_creds.env 后重试）")
        else:
            for tl in sorted(struct_map):
                row = db_info.get(tl)
                if not row:
                    errors.append(f"[struct→DB] {tl}: 活库没有 task_base 行")
                elif not row.get("active"):
                    warnings.append(f"[struct→DB] {tl}: 活库有行但 active_status 全为 0")
                elif row.get("no_desc"):
                    warnings.append(f"[struct→DB] {tl}: {row['no_desc']} 行 x_desc 为空")

    summary = {
        "struct_tasks": len(tasks),
        "md_files": len(files),
        "declared_none": sorted(declared_none),
        "code_refs": {k: v for k, v in sorted(code_refs.items())},
        "orphans": orphans,
        "errors": errors,
        "warnings": warnings,
        "db": db_info,
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if not errors else 1

    print("Task manifest check — agx_struct.json vs tasks/")
    print(f"  struct tasks : {len(tasks)}")
    print(f"  md files     : {len(files)}")
    print(f"  task_base:none: {len(declared_none)}  {sorted(declared_none) or ''}")
    print(f"  代码引用     : {', '.join(sorted(code_refs)) or '无'}")

    if errors:
        print(f"\n  ✗ {len(errors)} 处漂移:")
        for e in errors:
            print(f"    - {e}")
    if warnings:
        print(f"\n  ! {len(warnings)} 条提示:")
        for w in warnings:
            print(f"    - {w}")
    if not errors:
        print("\n  OK — 无漂移")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
