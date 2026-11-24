#!/usr/bin/env python3
"""Sync code from the repo into the install (runtime) tree.

Why this exists
---------------
The repo is a code base, not a working directory. Running the daemon inside the
repo makes it write logs, pid files and caches next to the sources; one careless
`git add -A` and the repo is polluted. The install tree exists exactly for that:
repo = code, install dir = runtime (logs, config, data).

This script is the bridge. Run it after every change, then restart the daemon.

Safety model — a WHITELIST, never a blanket copy
-----------------------------------------------
Copying the whole tree is wrong and has broken things before:
  * agx_config.json   — host specific (host_id, paths). Overwriting it
                            re-points the machine at the wrong identity.
  * standards/*           — the install copy holds real documents; the repo copy
                            is a template with {{placeholders}}.
  * tasks/, agents/       — runtime data with more entries than the repo.
Protected paths are asserted, not merely skipped, so a future edit that widens
the whitelist fails loudly instead of silently clobbering data.

Usage
-----
    python scripts/deploy_install.py --dry-run     # show what would change
    python scripts/deploy_install.py               # copy + verify
    python scripts/deploy_install.py --prune       # also delete orphans
    python scripts/deploy_install.py --to ~/.agx   # explicit install dir

Exit codes: 0 = in sync, 1 = mismatch after copy, 2 = refused/error.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys

# Code only. Anything not listed here is never written by this script.
CODE_DIRS = ("agx_mcp", "scripts", "common")
CODE_ROOT_FILES = (
    "config_loader.py",
    "log_setup.py",
    "install.py",
    "daemon.sh",
    "agx.sh",
)
CODE_EXTS = (".py", ".sh")

# Data / config / runtime. Asserted, never written.
PROTECTED = (
    "agx_config.json",
    "standards",
    "tasks",
    "agents",
    "docs",
    "logs",
    "config",
    ".env",
    ".git",
    ".venv",
)

SKIP_DIR_NAMES = {"__pycache__", ".git", ".venv", "node_modules", ".ruff_cache", ".pytest_cache"}


def md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_install_dir() -> str:
    env = os.environ.get("AGX_INSTALL_DIR", "").strip()
    if env:
        return os.path.expanduser(env)
    home = os.path.expanduser("~")
    for name in (".agx",):
        cand = os.path.join(home, name)
        if os.path.isdir(cand):
            return cand
    return ""


def is_protected(rel: str) -> bool:
    parts = rel.split(os.sep)
    if parts[0] in PROTECTED:
        return True
    return any(p in PROTECTED for p in parts)


def collect(repo: str) -> list[str]:
    """Relative paths of every file this script is allowed to write."""
    out: list[str] = []

    for name in CODE_ROOT_FILES:
        p = os.path.join(repo, name)
        if os.path.isfile(p) and name.endswith(CODE_EXTS):
            out.append(name)

    for d in CODE_DIRS:
        base = os.path.join(repo, d)
        if not os.path.isdir(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if x not in SKIP_DIR_NAMES]
            for fn in files:
                if not fn.endswith(CODE_EXTS):
                    continue
                full = os.path.join(root, fn)
                out.append(os.path.relpath(full, repo))
    return sorted(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync code from repo into the install tree.")
    ap.add_argument("--to", default="", help="install dir (default: $AGX_INSTALL_DIR or ~/.agx)")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--prune", action="store_true", help="delete whitelisted orphans in the install tree")
    args = ap.parse_args()

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dst = os.path.abspath(os.path.expanduser(args.to)) if args.to else detect_install_dir()

    if not dst or not os.path.isdir(dst):
        print(f"ERROR: install dir not found (tried --to / $AGX_INSTALL_DIR / ~/.agx)")
        return 2

    # Refuse to sync into the repo itself or into a directory that is not an install.
    if os.path.realpath(dst) == os.path.realpath(repo):
        print("ERROR: install dir equals the repo dir — refuse to overwrite the code base.")
        return 2
    if not os.path.isfile(os.path.join(dst, "agx_config.json")):
        print(f"ERROR: {dst} does not look like an AgentGeeX install (no agx_config.json).")
        return 2

    files = collect(repo)
    if not files:
        print("ERROR: no code files collected — wrong repo root?")
        return 2

    copied, same, orphan = [], [], []
    for rel in files:
        if is_protected(rel):
            print(f"REFUSE: {rel} is a protected path (whitelist bug?)")
            return 2
        src = os.path.join(repo, rel)
        tgt = os.path.join(dst, rel)
        if os.path.isfile(tgt) and md5(src) == md5(tgt):
            same.append(rel)
            continue
        copied.append(rel)
        if args.dry_run:
            continue
        os.makedirs(os.path.dirname(tgt) or dst, exist_ok=True)
        shutil.copy2(src, tgt)

    # Orphans: whitelisted files in the install tree with no repo counterpart.
    for d in CODE_DIRS:
        base = os.path.join(dst, d)
        if not os.path.isdir(base):
            continue
        for root, dirs, fs in os.walk(base):
            dirs[:] = [x for x in dirs if x not in SKIP_DIR_NAMES]
            for fn in fs:
                if not fn.endswith(CODE_EXTS):
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, dst)
                if rel not in files:
                    orphan.append(rel)
                    if args.prune and not args.dry_run and not is_protected(rel):
                        os.remove(full)

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(f"[{mode}] repo    : {repo}")
    print(f"[{mode}] install : {dst}")
    print(f"[{mode}] files   : {len(files)} whitelisted | changed {len(copied)} | unchanged {len(same)}")
    for rel in copied:
        print(f"   -> {rel}")
    if orphan:
        verb = "pruned" if (args.prune and not args.dry_run) else "orphan"
        print(f"[{mode}] {verb} {len(orphan)} (exist in install, not in repo):")
        for rel in orphan:
            print(f"   - {rel}")
        if not args.prune:
            print("   (use --prune to delete them)")

    if args.dry_run:
        return 0

    # Verify: every whitelisted file must now be byte identical.
    bad = [rel for rel in files if md5(os.path.join(repo, rel)) != md5(os.path.join(dst, rel))]
    if bad:
        print(f"\nMISMATCH after copy: {len(bad)}")
        for rel in bad:
            print(f"   ! {rel}")
        return 1

    print(f"\nOK: {len(files)} files identical in both trees.")
    print("Restart the daemon to load the new code: cd %s && ./daemon.sh stop && ./daemon.sh start --core" % dst)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        sys.exit(2)
