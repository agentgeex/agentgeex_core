#!/usr/bin/env python3
"""Verify that agx.sql (the DDL truth source) matches the live MySQL schema.

WHY THIS EXISTS
---------------
`agx.sql` is the only schema source a fresh install has: `install.py` imports it
into an empty database and that is the customer's starting point. If the file
drifts from what the code actually needs, every new install breaks while the
developer's machine keeps working — the worst kind of bug, because it is
invisible until someone else installs.

The rule this script enforces: **the file is the truth, the database is the
product.** Any column that exists in the live DB but not in agx.sql is a column
a new install will be missing.

USAGE
-----
    python3 scripts/verify_schema.py              # human report, exit 1 on drift
    python3 scripts/verify_schema.py --json        # machine readable
    python3 scripts/verify_schema.py --strict      # also compare defaults + comments
    python3 scripts/verify_schema.py --all         # include non-agx_* live tables

Exit codes: 0 = consistent, 1 = drift, 2 = cannot run (config/connection).

CREDENTIALS
-----------
Read through `config_loader`, exactly like the rest of the codebase:
  ~/.agx/agx_config.json  +  ~/.agx/agx_creds.env (600)
Nothing is printed. Passwords never appear in output.
"""

from __future__ import annotations

import argparse
import json
import re
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
DDL_PATH = ROOT / "agx.sql"

# Tables that exist in a developer database but are NOT part of the product:
#   _bak_*          ad-hoc backups of business data
#   non agx_*       another project sharing the same MySQL instance
IGNORE_SUFFIX = ("_bak_",)
IGNORE_PREFIXES: tuple[str, ...] = ()


def _split_defs(body: str) -> list[str]:
    """Split a CREATE TABLE body into individual definitions at top-level commas.

    Column definitions can span several lines — enum values are usually listed
    one per line — so splitting on newlines produces garbage. Track parentheses
    depth and quote state instead, and only split on commas at depth 0.
    """
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    quote: str | None = None
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:
                buf.append(body[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf))
    return parts


def _norm_type(raw: str) -> str:
    """Normalise a column type so MySQL 5.7 dumps and MySQL 8 metadata compare equal.

    `int (11)` -> `int`, `enum('a', 'b')` -> `enum('a','b')`,
    `tinyint (1)` -> `tinyint(1)` (kept: it is the conventional boolean marker).
    """
    t = re.sub(r"\s+", " ", raw.strip().lower())
    t = t.replace(" (", "(")
    # collapse whitespace inside parentheses, e.g. enum( 'a', 'b' ) -> enum('a','b')
    def _tight(m: re.Match) -> str:
        inner = re.sub(r"\s*,\s*", ",", m.group(2)).strip()
        return f"{m.group(1)}({inner})"
    t = re.sub(r"(\w+)\((.*?)\)", _tight, t)
    # MySQL 8 no longer reports integer display widths -> drop them,
    # except tinyint(1), the conventional boolean marker.
    im = re.match(r"^(tinyint|smallint|mediumint|int|bigint)(?:\((\d+)\))?(.*)$", t)
    if im:
        kind, width, rest = im.groups()
        keep = width if (kind == "tinyint" and width == "1") else None
        t = kind + (f"({keep})" if keep else "") + rest
    return t


def parse_ddl(path: Path) -> dict[str, dict[str, dict]]:
    """Parse agx.sql into {table: {column: {type, nullable, default, comment}}}.

    The dump format is:
        CREATE TABLE
          `name` (
            `col` type ...,
            ...
          ) ENGINE = ...
    with an optional `IF NOT EXISTS` after CREATE TABLE.
    """
    text = path.read_text(encoding="utf-8")
    pat = re.compile(
        r"CREATE TABLE[ \t]*\r?\n[ \t]*(?:IF NOT EXISTS[ \t]*)?`([A-Za-z0-9_]+)`[ \t]*\((.*?)\r?\n[ \t]*\)[ \t]*ENGINE",
        re.S,
    )
    out: dict[str, dict[str, dict]] = {}
    for m in pat.finditer(text):
        table, body = m.group(1), m.group(2)
        cols: dict[str, dict] = {}
        for chunk in _split_defs(body):
            line = chunk.strip()
            cm = re.match(r"`([A-Za-z0-9_]+)`\s+(.+)$", line, re.S)
            if not cm:
                continue          # PRIMARY KEY / KEY / CONSTRAINT …
            name, rest = cm.group(1), cm.group(2)
            # Cut off the COMMENT part first: comment text may itself contain the
            # word DEFAULT or NULL, which would corrupt the attribute parsing.
            cmnt = re.search(r"\bCOMMENT\s+'((?:[^'\\\\]|\\\\.)*)'", rest)
            head = rest[: cmnt.start()] if cmnt else rest
            upper = head.upper()
            # type = leading type word + optional parenthesised part + optional modifiers
            tm = re.match(r"([A-Za-z]+)\s*\(([^)]*)\)", head)
            if tm:
                ctype = f"{tm.group(1)}({tm.group(2)})"
                remainder = head[tm.end():]
            else:
                tm = re.match(r"([A-Za-z]+)", rest)
                ctype = tm.group(1) if tm else rest
                remainder = head[tm.end():] if tm else ""
            for mod in ("unsigned", "zerofill"):
                if re.search(rf"\b{mod}\b", remainder, re.I):
                    ctype += f" {mod}"
            nullable = "NOT NULL" not in upper
            dm = re.search(r"\bDEFAULT\s+('(?:[^'\\]|\\.)*'|[A-Za-z_]+(?:\(\d*\))?|\S+)", head, re.I)
            default = None
            if dm:
                v = dm.group(1)
                default = v[1:-1] if v.startswith("'") else v
                if default.upper() == "NULL":
                    default = None   # DEFAULT NULL == no default, matches MySQL metadata
            cols[name] = {
                "type": _norm_type(ctype),
                "nullable": nullable,
                "default": default,
                "comment": cmnt.group(1) if cmnt else "",
            }
        out[table] = cols
    return out


def fetch_live() -> tuple[dict[str, dict[str, dict]], str]:
    """Read column metadata for all agx_* tables from the live database."""
    sys.path.insert(0, str(ROOT))
    from config_loader import config  # noqa: E402  (needs credential injection first)

    import pymysql  # noqa: E402

    db_name = config.mysql.get("database", "")
    conn = pymysql.connect(
        host=config.mysql.get("host", "127.0.0.1"),
        port=int(config.mysql.get("port", 3306)),
        user=config.mysql.get("user", "root"),
        password=config.mysql.get("password", ""),
        charset="utf8mb4",
        autocommit=True,
    )
    sql = (
        "SELECT table_name, column_name, column_type, is_nullable, "
        "IFNULL(column_default,''), IFNULL(column_comment,'') "
        "FROM information_schema.columns WHERE table_schema=%s "
        "ORDER BY table_name, ordinal_position"
    )
    out: dict[str, dict[str, dict]] = {}
    with conn.cursor() as cur:
        cur.execute(sql, (db_name,))
        for table, col, ctype, nullable, default, comment in cur.fetchall():
            out.setdefault(table, {})[col] = {
                "type": _norm_type(ctype),
                "nullable": nullable == "YES",
                "default": default,
                "comment": comment,
            }
    conn.close()
    return out, db_name


def _skip(table: str) -> bool:
    if any(s in table for s in IGNORE_SUFFIX):
        return True
    return any(table.startswith(p) for p in IGNORE_PREFIXES)


def compare(ddl: dict, live: dict, strict: bool, include_foreign: bool = False) -> dict:
    """Diff the two schemas. Only tables present in the DDL are required."""
    report: dict = {"tables_only_in_ddl": [], "tables_only_in_live": [], "drift": []}

    for t in sorted(ddl):
        lv = live.get(t)
        if lv is None:
            report["tables_only_in_ddl"].append(t)
            continue
        d = ddl[t]
        miss = [c for c in lv if c not in d]
        extra = [c for c in d if c not in lv]
        mismatched = []
        for c in sorted(set(d) & set(lv)):
            a, b = d[c], lv[c]
            problems = []
            if a["type"] != b["type"]:
                problems.append(f"type {a['type']} != {b['type']}")
            if a["nullable"] != b["nullable"]:
                problems.append(f"nullable {a['nullable']} != {b['nullable']}")
            if strict:
                if (a["default"] or "") != (b["default"] or ""):
                    problems.append(f"default {a['default']!r} != {b['default']!r}")
                if (a["comment"] or "").strip() != (b["comment"] or "").strip():
                    problems.append("comment differs")
            if problems:
                mismatched.append({"column": c, "problems": problems})
        if miss or extra or mismatched:
            report["drift"].append(
                {
                    "table": t,
                    "missing_from_ddl": [{"column": c, "live_type": lv[c]["type"],
                                          "nullable": lv[c]["nullable"]} for c in miss],
                    "extra_in_ddl": [{"column": c, "ddl_type": d[c]["type"]} for c in extra],
                    "mismatched": mismatched,
                }
            )

    for t in sorted(live):
        if t in ddl or _skip(t):
            continue
        # A developer database may hold tables from other projects. They are not
        # part of the product, so they are noise here — show them only on request.
        if not include_foreign and not t.startswith("agx_"):
            continue
        report["tables_only_in_live"].append(t)
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Compare agx.sql against the live schema")
    ap.add_argument("--json", action="store_true", help="machine readable output")
    ap.add_argument("--strict", action="store_true", help="also compare defaults and comments")
    ap.add_argument("--all", action="store_true", help="also list non-agx_* tables found in the DB")
    ap.add_argument("--ddl", default=str(DDL_PATH), help="path to agx.sql")
    args = ap.parse_args()

    ddl_path = Path(args.ddl)
    if not ddl_path.exists():
        print(f"DDL not found: {ddl_path}", file=sys.stderr)
        return 2

    ddl = parse_ddl(ddl_path)
    if not ddl:
        print(f"No CREATE TABLE blocks parsed from {ddl_path}", file=sys.stderr)
        return 2

    try:
        live, db_name = fetch_live()
    except Exception as exc:  # noqa: BLE001 — any connection/config failure means "cannot verify"
        print(f"Cannot read live schema: {exc}", file=sys.stderr)
        return 2

    if not args.all:
        live = {t: v for t, v in live.items() if not _skip(t)}

    report = compare(ddl, live, args.strict, include_foreign=args.all)
    drift = report["drift"]
    only_live = report["tables_only_in_live"]
    only_ddl = report["tables_only_in_ddl"]
    clean = not (drift or only_live or only_ddl)

    if args.json:
        print(json.dumps(
            {
                "database": db_name,
                "ddl_tables": len(ddl),
                "live_tables": len(live),
                "clean": clean,
                **report,
            },
            ensure_ascii=False, indent=2,
        ))
        return 0 if clean else 1

    print(f"Schema check — agx.sql vs `{db_name}`")
    print(f"  DDL  : {len(ddl)} tables / {sum(len(v) for v in ddl.values())} columns")
    print(f"  live : {len(live)} tables / {sum(len(v) for v in live.values())} columns")

    if clean:
        print("\n  OK — no drift")
        return 0

    print()
    for item in only_ddl:
        print(f"  [missing table] {item} — declared in agx.sql, absent from the database")
    for item in only_live:
        print(f"  [extra table]   {item} — exists in the database, not in agx.sql")
        print("                  (a fresh install will not have it; add it to agx.sql or ignore it)")
    for d in drift:
        print(f"  [{d['table']}]")
        for c in d["missing_from_ddl"]:
            print(f"    + {c['column']}  {c['live_type']}"
                  f"{' NULL' if c['nullable'] else ' NOT NULL'}"
                  f"  ← live only; a fresh install will be missing this column")
        for c in d["extra_in_ddl"]:
            print(f"    - {c['column']}  {c['ddl_type']}  ← in agx.sql but not in the database")
        for c in d["mismatched"]:
            print(f"    ~ {c['column']}: {'; '.join(c['problems'])}")

    print(f"\n  DRIFT — {len(drift)} table(s) differ")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
