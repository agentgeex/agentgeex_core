#!/usr/bin/env python3
"""
json_ops.py - general-purpose JSON file operations

Usage:
  python3 json_ops.py <action> <file> [--path JSONPATH] [--data JSON] [--deep]

  When --data is omitted, JSON data is read from stdin.

Actions:
  read      - Read a JSON file, with optional JSONPath filtering
  write     - Write / overwrite a JSON file
  merge     - Merge new data into existing JSON (shallow or deep)
  append    - Append elements to an array field
  set       - Set a value by JSONPath (creating intermediate nodes)
  delete    - Delete a key by JSONPath
  validate  - Validate that a JSON file is well-formed
  query     - JSONPath query, returning matched results

Examples:
  python3 json_ops.py read plan.json
  python3 json_ops.py read plan.json --path '$.steps[0]'
  python3 json_ops.py write plan.json --data '{"steps":[]}'
  python3 json_ops.py merge plan.json --data '{"steps":[{"name":"step1"}]}'
  echo '{"name":"step1","task_id":"task_xxx"}' | python3 json_ops.py append plan.json --path '$.steps'
  echo '{"author":"pm"}' | python3 json_ops.py set plan.json --path '$.metadata'
  python3 json_ops.py delete plan.json --path '$.tmp'
  python3 json_ops.py validate plan.json
  python3 json_ops.py query plan.json --path '$.steps[?(@.status=="pending")]'
"""

import argparse
import json
import os
import re
import sys


def _get_data(args) -> str:
    """Read data from --data or from stdin"""
    if args.data:
        return args.data
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return None


def read_json(path: str, jsonpath: str = None) -> int:
    if not os.path.exists(path):
        print(json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False))
        return 1
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"JSON 解析失败: {e}"}, ensure_ascii=False))
        return 1

    if jsonpath:
        try:
            from jsonpath_ng import parse

            expr = parse(jsonpath)
            matches = [m.value for m in expr.find(data)]
            if len(matches) == 1:
                print(json.dumps(matches[0], ensure_ascii=False, indent=2))
            else:
                print(json.dumps(matches, ensure_ascii=False, indent=2))
        except ImportError:
            result = _simple_jsonpath(data, jsonpath)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as e:
            print(json.dumps({"error": f"JSONPath 查询失败: {e}"}, ensure_ascii=False))
            return 1
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def write_json(path: str, raw: str) -> int:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"输入不是合法 JSON: {e}"}, ensure_ascii=False))
        return 1
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "path": path, "size": os.path.getsize(path)}, ensure_ascii=False))
    return 0


def merge_json(path: str, raw: str, deep: bool = False) -> int:
    try:
        incoming = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"输入不是合法 JSON: {e}"}, ensure_ascii=False))
        return 1

    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = json.load(f)
    else:
        existing = {} if isinstance(incoming, dict) else []

    if deep and isinstance(existing, dict) and isinstance(incoming, dict):
        merged = _deep_merge(existing, incoming)
    elif isinstance(existing, dict) and isinstance(incoming, dict):
        existing.update(incoming)
        merged = existing
    elif isinstance(existing, list) and isinstance(incoming, list):
        existing.extend(incoming)
        merged = existing
    else:
        merged = incoming

    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "path": path}, ensure_ascii=False))
    return 0


def append_to_array(path: str, jsonpath: str, raw: str) -> int:
    if not os.path.exists(path):
        print(json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False))
        return 1
    try:
        item = json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"输入不是合法 JSON: {e}"}, ensure_ascii=False))
        return 1

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    target = _resolve_jsonpath(data, jsonpath, create_missing=True)
    if not isinstance(target, list):
        print(json.dumps({"error": f"路径 '{jsonpath}' 不是数组"}, ensure_ascii=False))
        return 1
    target.append(item)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "path": path, "new_length": len(target)}, ensure_ascii=False))
    return 0


def set_value(path: str, jsonpath: str, raw: str) -> int:
    if not os.path.exists(path):
        print(json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False))
        return 1
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        val = raw

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    _set_by_jsonpath(data, jsonpath, val)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "path": path, "jsonpath": jsonpath}, ensure_ascii=False))
    return 0


def delete_key(path: str, jsonpath: str) -> int:
    if not os.path.exists(path):
        print(json.dumps({"error": f"文件不存在: {path}"}, ensure_ascii=False))
        return 1

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    parts = _parse_jsonpath_parts(jsonpath)
    if not parts:
        print(json.dumps({"error": f"无效 JSONPath: {jsonpath}"}, ensure_ascii=False))
        return 1

    current = data
    for i, part in enumerate(parts[:-1]):
        current = _navigate(current, part, create_missing=False)
        if current is None:
            print(json.dumps({"error": f"路径 '{jsonpath}' 不存在"}, ensure_ascii=False))
            return 1

    last = parts[-1]
    if isinstance(current, dict) and last["key"] in current:
        del current[last["key"]]
    elif isinstance(current, list) and last["index"] is not None:
        current.pop(last["index"])
    else:
        print(json.dumps({"error": f"路径 '{jsonpath}' 不存在"}, ensure_ascii=False))
        return 1

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(json.dumps({"ok": True, "path": path, "deleted": jsonpath}, ensure_ascii=False))
    return 0


def validate_json(path: str) -> int:
    if not os.path.exists(path):
        print(json.dumps({"valid": False, "error": f"文件不存在: {path}"}, ensure_ascii=False))
        return 1
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        info = {
            "valid": True,
            "path": path,
            "size": os.path.getsize(path),
            "type": type(data).__name__,
        }
        if isinstance(data, dict):
            info["key_count"] = len(data.keys())
            info["keys"] = list(data.keys())[:20]
        elif isinstance(data, list):
            info["length"] = len(data)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    except json.JSONDecodeError as e:
        print(json.dumps({"valid": False, "error": str(e), "path": path}, ensure_ascii=False))
        return 1


def query_json(path: str, jsonpath: str) -> int:
    return read_json(path, jsonpath)


# --- Minimal JSONPath implementation ---


def _parse_jsonpath_parts(expr: str) -> list:
    parts = []
    if expr.startswith("$"):
        expr = expr[1:]
    tokens = re.findall(r'\.([a-zA-Z_]\w*)|\[(\d+)\]|\["([^"]+)"\]|\[\'([^\']+)\'\]', expr)
    for token in tokens:
        key = token[0] or token[2] or token[3]
        index = int(token[1]) if token[1] else None
        parts.append({"key": key, "index": index})
    if not tokens and expr:
        for segment in expr.lstrip(".").split("."):
            if segment:
                m = re.match(r"([a-zA-Z_]\w*)(?:\[(\d+)\])?", segment)
                if m:
                    parts.append({"key": m.group(1), "index": int(m.group(2)) if m.group(2) else None})
    return parts


def _navigate(data, part: dict, create_missing: bool = False):
    if part["index"] is not None:
        if isinstance(data, list) and part["index"] < len(data):
            return data[part["index"]]
        return None
    elif part["key"]:
        if isinstance(data, dict):
            if part["key"] in data:
                return data[part["key"]]
            elif create_missing:
                data[part["key"]] = {}
                return data[part["key"]]
        return None
    return data


def _resolve_jsonpath(data, expr: str, create_missing: bool = False):
    parts = _parse_jsonpath_parts(expr)
    if not parts:
        return data
    current = data
    for part in parts:
        current = _navigate(current, part, create_missing=create_missing)
        if current is None:
            return None
    return current


def _simple_jsonpath(data, expr: str):
    return _resolve_jsonpath(data, expr)


def _set_by_jsonpath(data, expr: str, value):
    parts = _parse_jsonpath_parts(expr)
    if not parts:
        return
    current = data
    for i, part in enumerate(parts[:-1]):
        next_part = parts[i + 1]
        if part["index"] is not None:
            current = current[part["index"]]
        else:
            if part["key"] not in current:
                current[part["key"]] = [] if next_part["index"] is not None else {}
            current = current[part["key"]]
    last = parts[-1]
    if last["index"] is not None:
        current[last["index"]] = value
    else:
        current[last["key"]] = value


def _deep_merge(base: dict, incoming: dict) -> dict:
    result = base.copy()
    for key, value in incoming.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# --- CLI ---


def main():
    parser = argparse.ArgumentParser(description="JSON 文件操作工具")
    parser.add_argument("action", choices=["read", "write", "merge", "append", "set", "delete", "validate", "query"])
    parser.add_argument("file", help="JSON 文件路径")
    parser.add_argument("--data", help="JSON 数据字符串（如未传则从 stdin 读取）")
    parser.add_argument("--path", help="JSONPath 表达式")
    parser.add_argument("--deep", action="store_true", help="深度合并 (merge 时使用)")

    args = parser.parse_args()

    needs_data = args.action in ("write", "merge", "append", "set")
    data = _get_data(args) if needs_data else None

    if needs_data and data is None:
        print(json.dumps({"error": f"action '{args.action}' 需要 --data 或 stdin 输入"}, ensure_ascii=False))
        sys.exit(1)

    if args.action in ("append", "set", "delete", "query") and not args.path:
        print(json.dumps({"error": f"action '{args.action}' 需要 --path 参数"}, ensure_ascii=False))
        sys.exit(1)

    action_map = {
        "read": lambda: read_json(args.file, args.path),
        "write": lambda: write_json(args.file, data),
        "merge": lambda: merge_json(args.file, data, args.deep),
        "append": lambda: append_to_array(args.file, args.path, data),
        "set": lambda: set_value(args.file, args.path, data),
        "delete": lambda: delete_key(args.file, args.path),
        "validate": lambda: validate_json(args.file),
        "query": lambda: query_json(args.file, args.path),
    }

    sys.exit(action_map[args.action]())


if __name__ == "__main__":
    main()
