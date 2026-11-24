#!/usr/bin/env python3
"""
sync_runtime.py - keep the code in the runtime directory (work_dir) in step with the repo

* Why this script exists
  Two sets of code are "running" on this host:
    . agx-mcp-gateway   cwd=repo root, args=agx_mcp/mcp_gateway.py  -> runs the *repo* copy
    . agx_daemon    work_dir/scripts/agx_daemon.py       -> runs the *work_dir* copy
    . agx_watchdog  work_dir/scripts/agx_watchdog.py     -> runs the *work_dir* copy
  Once the repo changes, the work_dir copy silently falls behind. It raises nothing, warns nothing,
  and just keeps working with the old logic - a classic silent failure. Hence an explicit "align"
  action instead of relying on someone remembering to cp by hand.

* Where the directories come from (no path is hard-coded, so 118 / 188 work as-is)
    runtime dir : config.work_dir                    (agx_config.json)
    repo dir    : config.sync.source_dir  -> falls back to this script's repo root when unset
    exclude list: config.sync.exclude     -> falls back to the built-in default
  Each machine's agx_config.json already differs (host_id / work_dir differ), so "read dirs from
  config" is the only portable option; hard-coding the install root would only work on the Mac.

* Default semantics: only keep files that already exist in the target in sync (in-place)
  work_dir never had repo-only directories such as webtools / test / exams; force-feeding them
  only creates copies that should not be there and makes it impossible to tell which one is in use.
  Add --add-missing explicitly to fill in missing files too (still subject to exclude).

* agx_config.json is not synced by default
  It is environment-specific configuration (host_id / work_dir / DB credentials differ), not code.
  Overwriting a remote copy with the repo's would write someone else's identity onto this machine.
  Overwriting requires an explicit --include-config.

Usage:
    python scripts/sync_runtime.py                # report only (dry run)
    python scripts/sync_runtime.py --apply        # actually sync, leaving .bak_<timestamp> per file
    python scripts/sync_runtime.py --apply --add-missing
    python scripts/sync_runtime.py --check        # report only; exit 1 when out of sync
    python scripts/sync_runtime.py --path agx_mcp --path scripts
Exit codes:
    0 in sync / sync succeeded    1 out of sync (--check) or leftovers after sync    2 argument or env error
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
from fnmatch import fnmatch

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config_loader import config  # noqa: E402

# -- built-in defaults (overridable via config.sync.*) ------------------------
DEFAULT_EXCLUDE = [
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    "webtools", "agx_agents",        # frontend and agent workspaces are not runtime copies
    "logs", "backups", "bak",        # logs and historical backups
    "exams", "data", "outputs",      # repo-only material
    "*.bak_*", "*.pyc", ".DS_Store",
]
# environment-specific config: untouched by default, needs --include-config
CONFIG_PROTECTED = ["agx_config.json"]


def _expand(p: str) -> str:
    return os.path.abspath(os.path.expandvars(os.path.expanduser(str(p))))


def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _cfg(name: str, default=None):
    """Read config.sync.<name>; return default when absent."""
    section = config.get("sync")
    if section is None:
        return default
    return section.get(name, default)


def _excluded(rel: str, patterns) -> bool:
    parts = rel.split(os.sep)
    for pat in patterns:
        if any(ch in pat for ch in "*?["):
            if fnmatch(rel, pat) or any(fnmatch(p, pat) for p in parts):
                return True
        elif pat in parts:
            return True
    return False


def _list_repo_files(source: str, patterns):
    """Prefer git ls-files (only what the repo actually tracks); fall back to a directory walk without git."""
    git_dir = os.path.join(source, ".git")
    if os.path.exists(git_dir):
        try:
            out = subprocess.run(
                # -c core.quotepath=false: otherwise non-ASCII file names are escaped as
                # "docs/2026-09-14-PM\350\241\214....md" (quoted + octal),
                # and using that directly as a path raises FileNotFoundError - measured on 118 on 2026-11-11.
                ["git", "-c", "core.quotepath=false", "-C", source, "ls-files"],
                capture_output=True, text=True, check=True,
            ).stdout
            files = [f for f in out.split("\n") if f.strip()]
            return files, "git ls-files"
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
    exts = (".py", ".sh", ".sql", ".json", ".md", ".toml", ".lock",
            ".txt", ".cfg", ".ini", ".yaml", ".yml")
    files = []
    for root, dirs, names in os.walk(source):
        dirs[:] = [d for d in dirs if not _excluded(
            os.path.relpath(os.path.join(root, d), source), patterns)]
        for n in names:
            if n.endswith(exts):
                files.append(os.path.relpath(os.path.join(root, n), source))
    return files, "目录遍历"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="让运行时目录（work_dir）里的代码与仓库一致")
    ap.add_argument("--apply", action="store_true", help="真同步（默认只报告）")
    ap.add_argument("--check", action="store_true", help="只报告；不一致退出码 1")
    ap.add_argument("--add-missing", action="store_true",
                    help="连目标缺失的文件也补上（默认只处理已存在的）")
    ap.add_argument("--include-config", action="store_true",
                    help="连 agx_config.json 也同步（环境专属，默认跳过）")
    ap.add_argument("--no-backup", action="store_true", help="不留 .bak 备份（不推荐）")
    ap.add_argument("--source", default="", help="覆盖仓库目录（默认取配置/脚本所在仓库）")
    ap.add_argument("--target", default="", help="覆盖运行时目录（默认取 config.work_dir）")
    ap.add_argument("--path", action="append", default=[],
                    help="只处理指定路径前缀（可重复），如 --path agx_mcp")
    args = ap.parse_args()

    # -- 1. directory source: config first, arguments may override --
    source = _expand(args.source or _cfg("source_dir") or PROJECT_ROOT)
    target = _expand(args.target or config.get("work_dir") or "")
    if not target:
        print("✗ 配置里没有 work_dir，且未传 --target", file=sys.stderr)
        return 2
    if not os.path.isdir(source):
        print(f"✗ 仓库目录不存在: {source}", file=sys.stderr)
        return 2
    if not os.path.isdir(target):
        print(f"✗ 运行时目录不存在: {target}", file=sys.stderr)
        return 2
    if os.path.realpath(source) == os.path.realpath(target):
        print("✗ 仓库目录与运行时目录相同，无需同步", file=sys.stderr)
        return 2

    patterns = list(_cfg("exclude") or DEFAULT_EXCLUDE)
    if not args.include_config:
        patterns = patterns + CONFIG_PROTECTED
    prefixes = [p.strip("/") for p in args.path if p.strip("/")]

    # -- 2. list candidates --
    all_files, how = _list_repo_files(source, patterns)
    files = []
    for f in all_files:
        if _excluded(f, patterns):
            continue
        if prefixes and not any(f == p or f.startswith(p + "/") for p in prefixes):
            continue
        files.append(f)

    print(f"仓库目录 : {source}")
    print(f"运行时目录: {target}")
    print(f"文件来源 : {how}  → 候选 {len(files)} 个"
          + (f"（前缀过滤: {', '.join(prefixes)}）" if prefixes else ""))
    print()

    # -- 3. compare one by one --
    same, update, add, missing, target_only_error = [], [], [], [], []
    for rel in sorted(files):
        sp = os.path.join(source, rel)
        tp = os.path.join(target, rel)
        if not os.path.exists(tp):
            (add if args.add_missing else missing).append(rel)
            continue
        if not os.path.isfile(sp):
            continue
        try:
            if _md5(sp) == _md5(tp):
                same.append(rel)
            else:
                update.append(rel)
        except OSError as exc:
            target_only_error.append(f"{rel}: {exc}")

    print(f"  一致        : {len(same)}")
    print(f"  不一致(待同步): {len(update)}")
    if args.add_missing:
        print(f"  目标缺失(待补): {len(add)}")
    else:
        print(f"  目标缺失(跳过): {len(missing)}   ← 加 --add-missing 才补")
    if target_only_error:
        print(f"  读取失败    : {len(target_only_error)}")
    print()

    show = update + (add if args.add_missing else [])
    if show:
        label = "将同步" if args.apply else "待同步"
        print(f"{label}（{len(show)} 个）:")
        for rel in show:
            sp = os.path.join(source, rel)
            tp = os.path.join(target, rel)
            if os.path.exists(tp):
                print(f"  ~ {rel}   repo={_md5(sp)[:8]}  run={_md5(tp)[:8]}")
            else:
                print(f"  + {rel}   repo={_md5(sp)[:8]}  (目标不存在)")
        print()

    if not show:
        print("✓ 已是完全一致的状态")
        return 0
    if not args.apply:
        print("（只报告，未写入。加 --apply 执行同步）")
        # exit-code semantics must be predictable: only --check uses the exit code to signal "drift",
        # otherwise a dry run prints a pile of red text yet returns 0, and scripts cannot tell "crashed" from "differs".
        return 1 if args.check else 0

    # -- 4. perform the sync --
    stamp = time.strftime("%Y%m%d_%H%M%S")
    done, failed = [], []
    for rel in show:
        sp = os.path.join(source, rel)
        tp = os.path.join(target, rel)
        try:
            os.makedirs(os.path.dirname(tp) or target, exist_ok=True)
            if os.path.exists(tp) and not args.no_backup:
                shutil.copy2(tp, f"{tp}.bak_{stamp}")
            shutil.copy2(sp, tp)
            done.append(rel)
        except OSError as exc:
            failed.append((rel, str(exc)))

    print(f"已同步 {len(done)} 个" + (f"，失败 {len(failed)} 个" if failed else ""))
    for rel, err in failed:
        print(f"  ✗ {rel}: {err}")

    # -- 5. verify: every file must have an equal md5 after the sync --
    still = [rel for rel in done
             if _md5(os.path.join(source, rel)) != _md5(os.path.join(target, rel))]
    print("✓ 同步后 md5 全部一致" if not still else f"✗ 仍有 {len(still)} 个不一致")
    for rel in still:
        print(f"  ✗ {rel}")

    # -- 6. syntax self-check: every modified .py must compile --
    py = [rel for rel in done if rel.endswith(".py")]
    if py:
        bad = []
        for rel in py:
            r = subprocess.run([sys.executable, "-m", "py_compile",
                                os.path.join(target, rel)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                bad.append((rel, r.stderr.strip().splitlines()[-1:]))
        print(f"py_compile: {len(py) - len(bad)}/{len(py)} 通过"
              + ("" if not bad else "  ✗ 有失败"))
        for rel, err in bad:
            print(f"  ✗ {rel}: {err}")

    if not args.no_backup and done:
        print(f"备份后缀: .bak_{stamp}（与目标文件同目录）")
    if failed or still:
        return 1
    print("\n提醒：daemon / watchdog 跑在运行时目录，需重启才生效"
          "（<install>/daemon.sh restart，默认 ~/.agx）；mcp_gateway 也从安装目录起，需一并重启。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
