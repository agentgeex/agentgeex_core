#!/usr/bin/env python3
"""Generate flat-square README badges as local SVG files.

Why local files instead of img.shields.io:
  GitHub proxies remote images through camo, so shields.io works there even
  when the reader's browser cannot reach it. Gitee does NOT proxy — it loads
  the image in the browser directly. In mainland China shields.io is routinely
  unreachable, so remote badges degrade to broken-image icons.
  Committing the SVGs makes the README self-contained and offline-safe.

Usage:
    python3 scripts/gen_readme_badges.py          # write assets/badges/*.svg
    python3 scripts/gen_readme_badges.py --check  # exit 1 if anything is stale

Each badge is a pure function of (label, value, color) — output is byte-for-byte
reproducible, so a manual edit silently drifts and `--check` will catch it.
"""

from __future__ import annotations

import pathlib

import re
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "assets" / "badges"

HEIGHT = 20
FONT_SIZE = 11
PAD = 6          # horizontal padding inside each half
ASCII_W = 6.2    # avg advance for 11px Verdana digits/lowercase
CJK_W = 11.0     # CJK glyph advance ~= font-size
LABEL_BG = "#555"
TEXT = "#fff"


def _text_width(s: str) -> float:
    w = 0.0
    for ch in s:
        w += CJK_W if ord(ch) > 0x2E80 else ASCII_W
    return w


def _esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def build_svg(label: str, value: str, color: str) -> str:
    lw = round(_text_width(label)) + PAD * 2
    vw = round(_text_width(value)) + PAD * 2
    total = lw + vw
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="{HEIGHT}" role="img" aria-label="{_esc(label)}: {_esc(value)}">
  <title>{_esc(label)}: {_esc(value)}</title>
  <linearGradient id="s" x2="0" y2="100%">
    <stop offset="0" stop-color="#bbb" stop-opacity=".1"/>
    <stop offset="1" stop-opacity=".1"/>
  </linearGradient>
  <clipPath id="r">
    <rect width="{total}" height="{HEIGHT}" rx="3" fill="#fff"/>
  </clipPath>
  <g clip-path="url(#r)">
    <rect width="{lw}" height="{HEIGHT}" fill="{LABEL_BG}"/>
    <rect x="{lw}" width="{vw}" height="{HEIGHT}" fill="{color}"/>
    <rect width="{total}" height="{HEIGHT}" fill="url(#s)"/>
  </g>
  <g fill="{TEXT}" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="{FONT_SIZE}">
    <text x="{lw / 2:.1f}" y="15" fill="#010101" fill-opacity=".3">{_esc(label)}</text>
    <text x="{lw / 2:.1f}" y="14">{_esc(label)}</text>
    <text x="{lw + vw / 2:.1f}" y="15" fill="#010101" fill-opacity=".3">{_esc(value)}</text>
    <text x="{lw + vw / 2:.1f}" y="14">{_esc(value)}</text>
  </g>
</svg>
"""


def _count_tools() -> int:
    """Count the tools that are actually registered in mcp_server.py (@mcp.tool() directly followed by def).

    * Do not use `grep -c "@mcp.tool()"`: occurrences inside comments or docstrings are counted too.
      2026-11-23 measurement: grep reported 100 while the runtime tools/list returned 96, so the README was wrong.
      Use structural matching instead (consistent with the runtime result: 96).
    """
    src = (Path(__file__).resolve().parent.parent / "agx_mcp" / "mcp_server.py").read_text(
        encoding="utf-8"
    )
    return len(re.findall(r"@mcp\.tool\(\)\s*\n(?:@[^\n]*\n)*\s*def\s+[a-zA-Z0-9_]+", src))


# ── the badge set ────────────────────────────────────────────────────────────
# row 1: project identity
# row 2: stack actually imported by the kernel (see pyproject.toml / uv.lock)
BADGES: list[tuple[str, str, str, str]] = [
    ("版本", "v0.1.0", "orange", "badge-version.svg"),
    ("状态", "pre-1.0", "red", "badge-status.svg"),
    ("许可证", "Apache-2.0", "D22128", "badge-license.svg"),
    ("MCP 工具", str(_count_tools()), "0052FF", "badge-tools.svg"),
    ("系统 Agent", "4", "8B5CF6", "badge-agents.svg"),
    ("部署", "自托管", "00C853", "badge-selfhosted.svg"),
    ("Python", "3.12+", "3776AB", "badge-python.svg"),
    ("环境", "uv", "DE5FE9", "badge-uv.svg"),
    ("协议", "MCP 1.x", "000000", "badge-mcp.svg"),
    ("框架", "FastMCP 3.2", "1B1B1B", "badge-fastmcp.svg"),
    ("MySQL", "8.0", "4479A1", "badge-mysql.svg"),
    ("Redis", "6.2+", "DC382D", "badge-redis.svg"),
    # row 3: agent runtimes (versions are the combination we actually ran)
    ("OpenClaw", "2026.8.35", "1B1B1B", "badge-openclaw.svg"),
    ("Claude Code", "2.1.318", "D97757", "badge-claudecode.svg"),
    ("DSH", "0.2.x", "4D6BFE", "badge-dsh.svg"),
    ("Node.js", "22.22.3+", "5FA04E", "badge-node.svg"),
    ("Codex", "POC", "6E7681", "badge-codex.svg"),
]



def main() -> int:
    check = "--check" in sys.argv
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    stale: list[str] = []
    for label, value, color, filename in BADGES:
        target = OUT_DIR / filename
        svg = build_svg(label, value, color)
        if check:
            current = target.read_text(encoding="utf-8") if target.exists() else None
            if current != svg:
                stale.append(filename)
        else:
            target.write_text(svg, encoding="utf-8")
            print(f"  wrote {target.relative_to(OUT_DIR.parent.parent)}  ({len(svg.encode())} B)")

    if check:
        if stale:
            print("stale badges (run without --check to regenerate):")
            for name in stale:
                print(f"  {name}")
            return 1
        print(f"all {len(BADGES)} badges up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
