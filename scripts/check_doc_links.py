#!/usr/bin/env python3
"""Markdown relative-link check - catch 404s before the readers do.

* Why this script exists
    Every doc in this repo links to another doc by relative path. Moving a file, renaming a
    directory, or writing a link against the wrong base directory breaks it silently: GitHub
    renders a 404 only when somebody clicks. Nobody clicks every link before a release.

    2026-11-25: exactly that happened. docs/README.md said
        [Console operations manual](webtools/docs/manual.md)
    from inside docs/, which resolves to docs/webtools/docs/manual.md -- a path that has never
    existed. The correct target is ../webtools/docs/manual.md. The link was dead from the day
    it was written and nobody noticed until a manual audit.

* What it checks
    . inline links     [text](path) and [text](path#anchor)
    . image sources    ![alt](path)
    . skips            http://, https://, mailto:, and pure #anchor targets
    . skips            fenced code blocks (``` and ~~~), so sample links in docs are not flagged

* What it does NOT check
    . links written inside backticks (too many false positives in prose)
    . in-page anchors (#section) -- GitHub's slug rules are not worth re-implementing here
    . anything outside the repository

Usage
    python3 scripts/check_doc_links.py                # whole repo
    python3 scripts/check_doc_links.py docs           # one directory
    python3 scripts/check_doc_links.py docs README.md
    python3 scripts/check_doc_links.py --quiet        # errors only
    python3 scripts/check_doc_links.py --json         # machine-readable

Exit code
    0 = every relative link resolves
    1 = at least one broken link
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Directories never worth walking.
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next"}

# [text](target) or ![alt](target)
LINK_RE = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")

# Fenced code blocks: ``` ... ``` or ~~~ ... ~~~ (allowing info strings after the fence)
FENCE_RE = re.compile(r"^(```+|~~~+)", re.M)

EXTERNAL_PREFIXES = ("http://", "https://", "mailto:", "tel:", "data:", "#")


def strip_code_fences(text: str) -> str:
    """Blank out fenced code blocks while keeping line numbers stable."""
    out = []
    in_fence = None
    for line in text.split("\n"):
        m = FENCE_RE.match(line)
        if in_fence:
            if m and m.group(1)[0] == in_fence[0] and len(m.group(1)) >= len(in_fence):
                in_fence = None
            out.append("")
            continue
        if m:
            in_fence = m.group(1)
            out.append("")
            continue
        out.append(line)
    return "\n".join(out)


def iter_markdown(targets: list[str], root: Path):
    for t in targets:
        p = (root / t).resolve() if not os.path.isabs(t) else Path(t).resolve()
        if p.is_file() and p.suffix.lower() == ".md":
            yield p
            continue
        if not p.is_dir():
            print(f"warning: {t} is neither a markdown file nor a directory", file=sys.stderr)
            continue
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if fn.lower().endswith(".md"):
                    yield Path(dirpath) / fn


def check_file(md: Path, repo_root: Path) -> list[dict]:
    broken = []
    try:
        raw = md.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"warning: {md} is not valid UTF-8, skipped", file=sys.stderr)
        return broken

    text = strip_code_fences(raw)
    line_starts = [0]
    for i, ch in enumerate(raw):
        if ch == "\n":
            line_starts.append(i + 1)

    for m in LINK_RE.finditer(text):
        label, target = m.group(1), m.group(2)
        if target.startswith(EXTERNAL_PREFIXES):
            continue
        path_part = target.split("#", 1)[0]
        if not path_part:
            continue
        # URL-decode %20 etc. so "my%20file.md" resolves the way a browser would
        try:
            from urllib.parse import unquote
            path_part = unquote(path_part)
        except Exception:
            pass
        resolved = (md.parent / path_part).resolve()
        if resolved.exists():
            continue
        # Absolute-from-repo-root links (starting with /) are not used in this repo,
        # but treat them as repo-relative if they are written that way.
        if path_part.startswith("/"):
            alt = (repo_root / path_part.lstrip("/")).resolve()
            if alt.exists():
                continue
        line_no = text[: m.start()].count("\n") + 1
        broken.append({
            "file": str(md.relative_to(repo_root)),
            "line": line_no,
            "label": label[:60],
            "target": target,
            "resolved": str(resolved),
        })
    return broken


def main() -> int:
    ap = argparse.ArgumentParser(description="Check relative links in markdown files.")
    ap.add_argument("targets", nargs="*", default=[], help="files or directories (default: repo root)")
    ap.add_argument("--quiet", action="store_true", help="print only broken links")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    targets = args.targets or ["."]

    files = list(iter_markdown(targets, repo_root))
    all_broken = []
    for md in files:
        all_broken.extend(check_file(md, repo_root))

    if args.json:
        print(json.dumps({
            "files_checked": len(files),
            "broken": all_broken,
        }, ensure_ascii=False, indent=2))
    else:
        if not args.quiet:
            print(f"checked {len(files)} markdown file(s) under {repo_root.name}")
        if all_broken:
            print(f"\n{len(all_broken)} broken link(s):\n")
            for b in all_broken:
                print(f"  {b['file']}:{b['line']}")
                print(f"    [{b['label']}]({b['target']})")
                print(f"    -> {b['resolved']}")
        elif not args.quiet:
            print("OK - every relative link resolves")

    return 1 if all_broken else 0


if __name__ == "__main__":
    sys.exit(main())
