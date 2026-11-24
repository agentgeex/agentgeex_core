#!/usr/bin/env python3
"""md-section-splitter - split a Markdown file by heading level

Split modes:
    flat (default):     split on ## (H2) only, output flat
    tree  (--deep):     start at H2, split recursively down to --level (default 3)
                        --level 3: H2 -> H3
                        --level 4: H2 -> H3 -> H4

File naming:
    00 = intro for that level (the body between the heading and the first subheading)
    01/02/... = subsections (files starting at a subheading)
    e.g. 01_01_00_understanding-llms.md -> 01_01_01_what-llms-look-like.md -> 01_01_01_01_diving-deeper.md

Dependencies: standard library only (Python 3.8+)
"""

import json
import re
import sys
from pathlib import Path

# -- Helpers ---------------------------------------------------


def extract_heading_number(title: str) -> str | None:
    """Extract the number: '1.1 title' -> '1.1', 'Chapter 1 title' -> '1'"""
    m = re.match(r"第(\d+)[章節节](?:\s+|)", title)
    if m:
        return m.group(1)
    m = re.match(r"(\d+(?:\.\d+)*)\s+", title)
    return m.group(1) if m else None


def number_to_prefix(num_str: str) -> str:
    return "_".join(f"{int(p):02d}" for p in num_str.split("."))


def sanitize_filename(title: str, max_len: int = 60) -> str:
    body = re.sub(r"^\d+(?:\.\d+)*\s+", "", title).strip() or title.strip()
    safe = re.sub(r"[^\w\s\u4e00-\u9fff\uff08\uff09\u300a\u300b\u00b7-]", "", body, flags=re.UNICODE)
    safe = re.sub(r"\s+", "_", safe.strip())
    return safe[:max_len] if len(safe) > max_len else safe or "untitled"


def extract_frontmatter(lines: list[str]) -> tuple[list[str], int]:
    total = len(lines)
    fm, bs = [], 0
    if total > 0 and lines[0].strip() == "---":
        for i in range(1, total):
            if lines[i].strip() == "---":
                fm = lines[: i + 1]
                bs = i + 1
                break
        else:
            bs = 0
    while bs < total and lines[bs].strip() == "":
        bs += 1
    return fm, bs


def scan_headings(lines: list[str], body_start: int, max_level: int = 6) -> list[dict]:
    hp = re.compile(r"^(#{1,6})\s+(.*)")
    fp = re.compile(r"^```")
    headings, in_fence = [], False
    for i in range(body_start, len(lines)):
        raw = lines[i].rstrip("\n").rstrip("\r")
        if fp.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = hp.match(raw)
        if m:
            lv = len(m.group(1))
            if lv <= max_level:
                headings.append(
                    {
                        "line_idx": i,
                        "level": lv,
                        "title": m.group(2).strip(),
                        "num": extract_heading_number(m.group(2).strip()),
                    }
                )
    return headings


# -- Flat mode -------------------------------------------------


def split_flat(input_path: str, output_dir: str) -> dict:
    src, out = Path(input_path), Path(output_dir)
    if not src.exists():
        raise FileNotFoundError(f"文件不存在: {input_path}")
    out.mkdir(parents=True, exist_ok=True)
    with open(src, encoding="utf-8") as f:
        lines = f.readlines()
    total = len(lines)
    fm_lines, body_start = extract_frontmatter(lines)
    H2, FENCE = re.compile(r"^##\s+(.*)"), re.compile(r"^```")
    sections, cur_title, cur_start, in_fence = [], None, body_start, False
    for i in range(body_start, total):
        line = lines[i].rstrip("\n").rstrip("\r")
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = H2.match(line)
        if m:
            title = m.group(1).strip()
            if cur_title is not None:
                sections.append((cur_title, cur_start, i))
                cur_start = i
            cur_title = title
    if cur_title is not None:
        sections.append((cur_title, cur_start, total))
    elif body_start < total:
        sections.append(("__full__", body_start, total))
    manifest_secs = []
    for idx, (title, start, end) in enumerate(sections):
        seq = f"{idx + 1:02d}"
        safe = sanitize_filename(title)
        fname = f"{seq}_{safe}.md"
        fpath = out / fname
        content: list[str] = []
        if idx == 0 and fm_lines:
            content.extend(fm_lines)
            if content and content[-1].strip() == "---" and start < total and lines[start].strip() != "":
                content.append("\n")
        content.extend(lines[start:end])
        fpath.write_text("".join(content), encoding="utf-8")
        manifest_secs.append(
            {
                "section_id": f"s{seq}",
                "title": title,
                "file": fname,
                "heading_level": 2 if title != "__full__" else 0,
                "line_start": start + 1,
                "line_end": end,
            }
        )
    manifest = {
        "mode": "flat",
        "split_level": 2,
        "source": str(src.resolve()),
        "source_name": src.name,
        "output_dir": str(out.resolve()),
        "total_sections": len(sections),
        "sections": manifest_secs,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # Generate TOC.txt (flat mode: chapter titles + section list)
    _write_toc_txt_flat(manifest_secs, src.name, out)
    return manifest


def _write_toc_txt_flat(sections: list[dict], chapter_title: str, out_dir: Path):
    """Compact table of contents for flat mode"""
    lines = [chapter_title, ""]
    for sec in sections:
        title = sec.get("title", "")
        file = sec.get("file", "")
        lines.append(f"  {title}  ({file})")
    (out_dir / "TOC.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


# -- Tree mode -------------------------------------------------


def _build_tree_nodes(
    headings: list[dict], start: int, end: int, target_level: int, max_level: int, fallback: list[int]
):
    """
    Recursively build the node tree.
    Returns list[dict]: {title, num, level, line_start, line_end, children}
    """
    nodes = []
    i = start
    while i < end:
        h = headings[i]
        if h["level"] != target_level:
            i += 1
            continue

        node = {
            "title": h["title"],
            "num": h["num"],
            "level": target_level,
            "line_start": h["line_idx"],
            "line_end": None,
            "children": [],
        }

        # Find the next sibling
        next_i = i + 1
        while next_i < end and headings[next_i]["level"] != target_level:
            next_i += 1

        if target_level < max_level and i + 1 < next_i:
            node["children"] = _build_tree_nodes(headings, i + 1, next_i, target_level + 1, max_level, fallback)

        # line_end
        if node["children"]:
            node["line_end"] = node["children"][0]["line_start"]
        elif next_i < end:
            node["line_end"] = headings[next_i]["line_idx"]
        elif end < len(headings):
            # end points at the parent's next sibling heading
            node["line_end"] = headings[end]["line_idx"]
        else:
            node["line_end"] = None  # The globally last leaf is fixed up by _fix_ends

        nodes.append(node)
        i = next_i
    return nodes


def _assign_prefixes(nodes: list[dict], parent_prefix: str, seq_start: int):
    """
    Assign prefixes top-down.
    parent_prefix: the parent prefix (without _00), e.g. '01_01'
    Has children -> this node becomes the `_00` intro, children get `_seq`
    No children -> this node is the content file
    """
    for idx, node in enumerate(nodes):
        seq = f"{seq_start + idx:02d}"
        # Extract the sequence number from num
        if node["num"]:
            parts = node["num"].split(".")
            seq = f"{int(parts[-1]):02d}"

        if node["children"]:
            # Has children: the current file is the _00 intro
            node["prefix"] = f"{parent_prefix}_{seq}"
            node["file"] = f"{node['prefix']}_00_{sanitize_filename(node['title'])}.md"
            _assign_prefixes(node["children"], node["prefix"], 1)
        else:
            # Leaf node
            node["prefix"] = f"{parent_prefix}_{seq}"
            node["file"] = f"{node['prefix']}_{sanitize_filename(node['title'])}.md"


def _write_node(
    node: dict,
    parent: dict | None,
    lines: list[str],
    out: Path,
    total: int,
    fm_lines: list[str],
    body_start: int,
    is_first: bool,
):
    """Write files recursively; return a JSON dict"""
    # Compute the line range (1-indexed)
    ls = node["line_start"] + 1
    le = node["line_end"] if node["line_end"] is not None else total

    content: list[str] = []
    # The _00 file of the first H2 includes the frontmatter + H1 preface
    if is_first and node.get("_is_first_h2") is not False and "_00_" in node["file"]:
        is_first = False  # Only triggers on the first _00
        if fm_lines:
            content.extend(fm_lines)
        for k in range(body_start, node["line_start"]):
            content.append(lines[k])

    content.extend(lines[node["line_start"] : le])
    (out / node["file"]).write_text("".join(content), encoding="utf-8")

    children_json = []
    for child in node.get("children", []):
        child["_is_first_h2"] = False
        children_json.append(_write_node(child, node, lines, out, total, fm_lines, body_start, False))

    result = {
        "section_id": f"s{node['prefix']}",
        "title": node["title"],
        "file": node["file"],
        "heading_level": node["level"],
        "line_start": ls,
        "line_end": le,
    }
    if children_json:
        result["children"] = children_json
    return result


def _count_subs(nodes: list[dict]) -> int:
    total = 0
    for n in nodes:
        total += len(n["children"])
        total += _count_subs(n["children"])
    return total


def _fix_ends(nodes: list[dict], total: int):
    """Fix the global last leaf node's line_end to the end of the file.
    Only the deepest single leaf is fixed; intermediate levels are not touched recursively.
    """
    if not nodes:
        return
    last = nodes[-1]
    while last.get("children"):
        last = last["children"][-1]
    last["line_end"] = total


def split_tree(input_path: str, output_dir: str, max_level: int = 3) -> dict:
    src, out = Path(input_path), Path(output_dir)
    if not src.exists():
        raise FileNotFoundError(f"文件不存在: {input_path}")
    out.mkdir(parents=True, exist_ok=True)
    with open(src, encoding="utf-8") as f:
        lines = f.readlines()
    total = len(lines)
    fm_lines, body_start = extract_frontmatter(lines)
    all_headings = scan_headings(lines, body_start, max_level)

    if not all_headings:
        p = out / "01_full.md"
        p.write_text("".join(lines), encoding="utf-8")
        m = {
            "mode": "tree",
            "split_level": max_level,
            "source": str(src.resolve()),
            "source_name": src.name,
            "output_dir": str(out.resolve()),
            "total_sections": 1,
            "sections": [{"section_id": "s01", "title": "__full__", "file": "01_full.md"}],
        }
        (out / "manifest.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
        return m

    fallback = [0]
    h2_nodes = _build_tree_nodes(all_headings, 0, len(all_headings), 2, max_level, fallback)
    _fix_ends(h2_nodes, total)

    # Chapter title + expanded TOC
    chapter_title = ""
    toc_entries = []
    for h in all_headings:
        entry = {"level": h["level"], "prefix": "", "title": h["title"], "line": h["line_idx"] + 1}
        if h["num"]:
            entry["prefix"] = number_to_prefix(h["num"])
        elif h["level"] == 1:
            m = re.match(r"第(\d+)[章節节]", h["title"])
            if m:
                entry["prefix"] = f"{int(m.group(1)):02d}"
            else:
                entry["prefix"] = "--"
        else:
            entry["prefix"] = "--"
        toc_entries.append(entry)
        if h["level"] == 1:
            chapter_title = h["title"]

    # Assign prefixes
    for idx, node in enumerate(h2_nodes):
        base = number_to_prefix(node["num"]) if node["num"] else f"{idx + 1:02d}"
        if node["children"]:
            node["prefix"] = base
            node["file"] = f"{base}_00_{sanitize_filename(node['title'])}.md"
            _assign_prefixes(node["children"], base, 1)
        else:
            node["prefix"] = base
            node["file"] = f"{base}_{sanitize_filename(node['title'])}.md"

    # Write out
    manifest_secs = []
    for idx, node in enumerate(h2_nodes):
        node["_is_first_h2"] = idx == 0
        manifest_secs.append(_write_node(node, None, lines, out, total, fm_lines, body_start, idx == 0))

    total_subs = _count_subs(h2_nodes)
    manifest = {
        "mode": "tree",
        "split_level": max_level,
        "source": str(src.resolve()),
        "source_name": src.name,
        "output_dir": str(out.resolve()),
        "chapter": chapter_title,
        "toc": toc_entries,
        "total_sections": len(h2_nodes),
        "total_subsections": total_subs,
        "sections": manifest_secs,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_toc_txt(h2_nodes, chapter_title, out)
    return manifest


def _write_toc_txt(nodes: list[dict], chapter_title: str, out_dir: Path):
    """Generate the compact plain-text TOC.txt"""
    lines = []
    label = chapter_title or "文档结构"
    lines.append(label)
    lines.append("")

    def _walk(node_list: list[dict], prefix_chars: str, is_last: list[bool]):
        for idx, node in enumerate(node_list):
            last = idx == len(node_list) - 1
            # Build the current-line prefix: the is_last history decides between a bar and a space
            conn = "└── " if last else "├── "
            indent = ""
            for p in is_last:
                indent += "    " if p else "│   "
            lines.append(f"{indent}{conn}{node['file']}")

            children = node.get("children", [])
            if children:
                _walk(children, "", [*is_last, last])

    _walk(nodes, "", [])
    (out_dir / "TOC.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


# -- Compose ---------------------------------------------------


def merge_from_manifest(manifest_path: str, output_path: str) -> str:
    m = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    out_dir = Path(m["output_dir"])
    with open(output_path, "w", encoding="utf-8") as out:
        for sec in m["sections"]:
            out.write((out_dir / sec["file"]).read_text(encoding="utf-8"))
            for child in sec.get("children", []) or []:
                out.write((out_dir / child["file"]).read_text(encoding="utf-8"))
    return output_path


# ── CLI ────────────────────────────────────────────────────


def _print_tree(sec, indent=""):
    ln = sec["line_end"] - sec["line_start"]
    print(f"{indent}{sec['file']}  ←  {sec['title']}  ({ln} 行)")
    for child in sec.get("children", []) or []:
        _print_tree(child, indent + "  ")


def toc(input_path: str, max_level: int = 3) -> dict:
    """
    Extract the chapter outline (read-only, no splitting).
    Returns {headings: [{indent, prefix, title, line}]}
    and prints an indented tree.
    """
    src = Path(input_path)
    if not src.exists():
        raise FileNotFoundError(f"文件不存在: {input_path}")
    with open(src, encoding="utf-8") as f:
        lines = f.readlines()
    _, body_start = extract_frontmatter(lines)
    headings = scan_headings(lines, body_start, max_level)

    if not headings:
        print("(无标题)")
        return {"headings": []}

    entries = []
    for h in headings:
        level = h["level"]
        indent = "  " * (level - 1)
        num = h["num"]
        prefix = number_to_prefix(num) if num else "--"
        print(f"{indent}{prefix}  {h['title']}")
        entries.append(
            {
                "level": level,
                "prefix": prefix,
                "title": h["title"],
                "line": h["line_idx"] + 1,
            }
        )
    return {"source": str(src.resolve()), "headings": entries}


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\n用法:")
        print("  uv run python split_by_h2.py in.md [out/]")
        print("  uv run python split_by_h2.py in.md [out/] --deep [--level N]")
        print("  uv run python split_by_h2.py in.md --toc [--level N]")
        print("  uv run python split_by_h2.py --merge manifest.json [output.md]")
        sys.exit(1)
    if sys.argv[1] == "--merge":
        mp, op = sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "merged.md"
        print(f"✅ 已合成: {merge_from_manifest(mp, op)}")
        return

    # --toc mode (read-only outline)
    if "--toc" in sys.argv:
        level = 3
        try:
            li = sys.argv.index("--level")
            level = int(sys.argv[li + 1])
        except (ValueError, IndexError):
            pass
        clean = []
        skip = False
        for a in sys.argv[1:]:
            if a == "--toc":
                continue
            if a == "--level":
                skip = True
                continue
            if skip:
                skip = False
                continue
            clean.append(a)
        input_path = clean[0] if clean else None
        if not input_path or input_path.startswith("-"):
            print("❌ 需要指定输入文件，如: split_by_h2.py chapter.md --toc")
            sys.exit(1)
        toc(input_path, max_level=level)
        return

    input_path, deep = sys.argv[1], "--deep" in sys.argv
    level = 3
    try:
        li = sys.argv.index("--level")
        level = int(sys.argv[li + 1])
    except (ValueError, IndexError):
        pass
    other = [a for a in sys.argv[2:] if a not in ("--deep", "--level") and not (a.startswith("--level"))]
    # Drop the value of --level
    clean = []
    skip = False
    for a in other:
        if a == "--level":
            skip = True
            continue
        if skip:
            skip = False
            continue
        clean.append(a)
    out_dir = clean[0] if clean else input_path.rsplit(".", 1)[0] + "_sections"

    if deep:
        m = split_tree(input_path, out_dir, max_level=level)
        print(f"✅ Tree 拆分完成: H2 → H{level}")
        print(f"   {m['total_sections']} H2 + {m['total_subsections']} 子节")
        print(f"   输出: {m['output_dir']}")
        for sec in m["sections"]:
            _print_tree(sec, "  ")
    else:
        m = split_flat(input_path, out_dir)
        print(f"✅ Flat: {m['total_sections']} sections → {m['output_dir']}")
        for s in m["sections"]:
            print(f"   {s['file']}  ({s['line_end'] - s['line_start']} 行)")


if __name__ == "__main__":
    main()
