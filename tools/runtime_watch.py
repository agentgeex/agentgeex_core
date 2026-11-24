#!/usr/bin/env python3
"""Read-only runtime anomaly collector for OpenClaw (prototype, collect only).

Two local sources:

  1. trajectory events
       ~/.openclaw/agents/<agent_id>/agent/openclaw-agent.sqlite
       table `trajectory_runtime_events` (session_id, seq, run_id, event_json, created_at)
       created_at is milliseconds since epoch; sessionKey lives inside event_json.

  2. stability bundles
       ~/.openclaw/logs/stability/openclaw-stability-<iso>-<pid>-<reason>.json

Design rules (v1 collects, never intervenes):

  * Every sqlite handle is opened with `?mode=ro`. These databases belong to a
    live gateway; a write here would corrupt a running process' state.
  * No CLI calls, no restart, no message sending, no daemon/ws_client changes.
  * One unreadable database must never abort the scan - it is counted and skipped.
  * stdlib only (sqlite3 / json / argparse / pathlib) so system python3 works.
  * No auto-heal, no resume messages: an intervention policy is a product
    decision, not something a collector should decide on its own.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

OPENCLAW_HOME = Path.home() / ".openclaw"
AGENTS_DIR = OPENCLAW_HOME / "agents"
STABILITY_DIR = OPENCLAW_HOME / "logs" / "stability"

# * The runtime root is fixed at ~/.agx.
def _runtime_root() -> Path:
    for _n in (".agx",):
        _p = Path.home() / _n
        if _p.is_dir():
            return _p
    return Path.home() / ".agx"


OUT_DIR = _runtime_root() / "runtime_watch"
EVENTS_PATH = OUT_DIR / "events.jsonl"
STABILITY_PATH = OUT_DIR / "stability.jsonl"
STATE_PATH = OUT_DIR / "state.json"

TRAJECTORY_TABLE = "trajectory_runtime_events"

# Types we keep. `context.compiled` is excluded on purpose: it embeds the full
# compiled prompt and is by far the bulkiest payload, with no failure signal
# that the other two types do not already carry. It is still counted.
EXTRACT_TYPES = ("session.ended", "trace.artifacts", "session.started")
JUDGED_TYPES = ("session.ended", "trace.artifacts")

OK_STATUS = {"success", "done"}

FLAG_KEYS = (
    "timedOutDuringCompaction",
    "idleTimedOut",
    "timedOut",
    "aborted",
    "externalAbort",
    "timedOutByRunBudget",
    "timedOutDuringToolExecution",
)

SQLITE_TIMEOUT_S = 3.0
MAX_ROWS_PER_DB = 200_000      # guard against a runaway table; truncation is reported
BOUNDARY_KEY_CAP = 500         # cursor keys pinned at the newest millisecond
STABILITY_SEEN_CAP = 5_000
ASSISTANT_TAIL_CHARS = 300


# ── helpers ──────────────────────────────────────────────────────────────


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def ms_to_iso(ms: int) -> str:
    try:
        return datetime.fromtimestamp(ms / 1000, timezone.utc).astimezone().isoformat(
            timespec="seconds"
        )
    except (OverflowError, OSError, ValueError):
        return str(ms)


def connect_ro(db_path: Path) -> sqlite3.Connection:
    """Open a gateway database strictly read-only.

    as_uri() percent-encodes the path, so agent ids with unusual characters do
    not break the URI. `mode=ro` makes the driver refuse writes at the file
    level rather than trusting this script to behave.
    """
    uri = db_path.as_uri() + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=SQLITE_TIMEOUT_S)


def table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def classify(data: dict, evt_type: str) -> tuple[str | None, list[str], str | None, bool]:
    """Return (status, flags, promptErrorSource, is_anomaly).

    Normal = status in (success, done) AND no timeout/abort flag AND no
    prompt-level error. Everything else counts as an anomaly, including a
    missing status - an event we cannot read is not evidence of health.
    """
    status = data.get("status")
    if status is None:
        status = data.get("finalStatus")
    flags = [k for k in FLAG_KEYS if data.get(k) is True]
    prompt_err = data.get("promptErrorSource")
    is_ok = status in OK_STATUS and not flags and not prompt_err
    return status, flags, prompt_err, not is_ok


def count_tool_errors(data: dict) -> int:
    metas = data.get("toolMetas")
    if not isinstance(metas, list):
        return 0
    return sum(1 for m in metas if isinstance(m, dict) and m.get("isError") is True)


def _usage_subset(data: dict) -> dict | None:
    """Token/cost numbers only - enough to see truncation and cost burn."""
    usage = data.get("usage")
    if not isinstance(usage, dict):
        return None
    cost = usage.get("cost")
    return {
        "input": usage.get("input"),
        "output": usage.get("output"),
        "cacheRead": usage.get("cacheRead"),
        "reasoningTokens": usage.get("reasoningTokens"),
        "total": usage.get("total"),
        "cost": cost.get("total") if isinstance(cost, dict) else None,
    }


def assistant_tail(data: dict) -> str | None:
    texts = data.get("assistantTexts")
    if not isinstance(texts, list) or not texts:
        return None
    last = texts[-1]
    if not isinstance(last, str):
        return None
    last = " ".join(last.split())
    return last[:ASSISTANT_TAIL_CHARS]


# ── trajectory scan ──────────────────────────────────────────────────────


def scan_agent_db(db_path: Path, agent_id: str, from_ms: int, cursor: dict) -> dict:
    """Scan one agent database. Never raises; failures come back in the result."""
    res = {
        "db": str(db_path),
        "agent_id": agent_id,
        "ok": False,
        "error": None,
        "type_counts": Counter(),
        "anomalies": [],
        "max_created_at": None,
        "rows_at_max": [],
        "skipped_boundary": 0,
        "truncated": False,
    }

    try:
        con = connect_ro(db_path)
    except Exception as exc:  # unreadable / locked / not a database
        res["error"] = f"open: {exc}"
        return res

    try:
        if not table_exists(con, TRAJECTORY_TABLE):
            res["error"] = f"missing table {TRAJECTORY_TABLE}"
            return res

        # Old cursor and the --since-hours floor both apply: the cursor wins
        # once it is newer, so a big window only matters on the first run.
        query_from = from_ms
        last_ms = cursor.get("last_created_at")
        if isinstance(last_ms, int) and last_ms > query_from:
            query_from = last_ms
        boundary = set(cursor.get("boundary_keys") or [])

        rows = con.execute(
            f"SELECT session_id, seq, run_id, created_at, event_json "  # noqa: S608 - fixed SQL
            f"FROM {TRAJECTORY_TABLE} WHERE created_at >= ? "
            f"ORDER BY created_at, seq LIMIT ?",
            (query_from, MAX_ROWS_PER_DB),
        ).fetchall()

        if len(rows) >= MAX_ROWS_PER_DB:
            res["truncated"] = True

        for session_id, seq, run_id, created_at, event_json in rows:
            key = f"{session_id}|{seq}"
            # Re-deliver the newest millisecond next run; skip what we already
            # emitted from that same millisecond.
            if created_at == query_from and key in boundary:
                res["skipped_boundary"] += 1
                continue

            try:
                event = json.loads(event_json)
            except (TypeError, ValueError):
                res["type_counts"]["<unparsable>"] += 1
                continue

            evt_type = event.get("type") or "<no-type>"
            res["type_counts"][evt_type] += 1

            if created_at is not None and (
                res["max_created_at"] is None or created_at > res["max_created_at"]
            ):
                res["max_created_at"] = created_at

            if evt_type not in JUDGED_TYPES:
                continue

            data = event.get("data") or {}
            status, flags, prompt_err, is_anomaly = classify(data, evt_type)
            if not is_anomaly:
                continue

            res["anomalies"].append(
                {
                    "collected_at": now_iso(),
                    "source": "trajectory",
                    "agent_id": agent_id,
                    "type": evt_type,
                    "sessionKey": event.get("sessionKey"),
                    "sessionId": session_id or event.get("sessionId"),
                    "runId": run_id or event.get("runId"),
                    "seq": seq,
                    "ts": event.get("ts"),
                    "ts_ms": created_at,
                    "status": status,
                    "flags": flags,
                    "promptErrorSource": prompt_err,
                    "stopReason": data.get("stopReason"),
                    "terminalError": data.get("terminalError"),
                    "compactionCount": data.get("compactionCount"),
                    "provider": event.get("provider"),
                    "modelId": event.get("modelId"),
                    "toolErrorCount": count_tool_errors(data),
                    "assistantTextCount": len(data.get("assistantTexts") or []),
                    "usage": _usage_subset(data),
                    "assistantTail": assistant_tail(data),
                }
            )

        # Rows that share the newest millisecond become the next boundary.
        max_ms = res["max_created_at"]
        if max_ms is not None:
            res["rows_at_max"] = [
                f"{r[0]}|{r[1]}" for r in rows if r[3] == max_ms
            ][:BOUNDARY_KEY_CAP]

        res["ok"] = True
        return res
    except Exception as exc:
        res["error"] = f"scan: {exc}"
        return res
    finally:
        try:
            con.close()
        except Exception:
            pass


# ── stability bundles ────────────────────────────────────────────────────


def scan_stability(seen: set[str]) -> tuple[list[dict], list[str], int]:
    """Return (new_records, new_names, total_files)."""
    if not STABILITY_DIR.is_dir():
        return [], [], 0

    paths = sorted(STABILITY_DIR.glob("*.json"))
    new_records: list[dict] = []
    new_names: list[str] = []

    for path in paths:
        if path.name in seen:
            continue
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            new_records.append(
                {
                    "collected_at": now_iso(),
                    "source": "stability",
                    "file": path.name,
                    "parse_error": str(exc),
                }
            )
            new_names.append(path.name)
            continue

        snap = bundle.get("snapshot") or {}
        summary = snap.get("summary") or {}
        by_type = summary.get("byType") if isinstance(summary, dict) else None
        new_records.append(
            {
                "collected_at": now_iso(),
                "source": "stability",
                "file": path.name,
                "reason": bundle.get("reason"),
                "generatedAt": bundle.get("generatedAt"),
                "version": bundle.get("version"),
                "process": bundle.get("process"),
                "host": bundle.get("host"),
                "error": bundle.get("error"),
                "snapshot": {
                    "count": snap.get("count"),
                    "dropped": snap.get("dropped"),
                    "capacity": snap.get("capacity"),
                    "byType": by_type,
                },
            }
        )
        new_names.append(path.name)

    return new_records, new_names, len(paths)


# ── state ────────────────────────────────────────────────────────────────


def load_state() -> dict:
    if not STATE_PATH.is_file():
        return {"version": 1, "agents": {}, "stability_seen": []}
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        # A corrupt cursor must not stop collection; start over and say so.
        print(f"[WARN] state.json 读取失败，按首次运行处理: {exc}")
        return {"version": 1, "agents": {}, "stability_seen": [], "corrupt": True}
    state.setdefault("agents", {})
    state.setdefault("stability_seen", [])
    return state


def save_state(state: dict) -> None:
    state["version"] = 1
    state["updated_at"] = now_iso()
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def append_jsonl(path: Path, records: list[dict]) -> int:
    if not records:
        return 0
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(records)


def count_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


# ── main ─────────────────────────────────────────────────────────────────


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="OpenClaw 运行时异常采集器（只读原型，只采集不干预）"
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="单轮扫描后退出（v1 仅支持这一种模式）",
    )
    p.add_argument(
        "--since-hours",
        type=float,
        default=24.0,
        help="首次扫描回看窗口（小时），默认 24；有游标后按游标增量",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印摘要，不写 events/stability/state 任何文件",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    if not args.once:
        print("v1 只支持 --once（单轮扫描）。轮询/常驻模式待采集口径定下来再加。")
        return 2

    if not AGENTS_DIR.is_dir():
        print(f"[ERROR] 找不到 OpenClaw agents 目录: {AGENTS_DIR}")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    state = load_state()
    agent_state = state["agents"]
    seen_stability = set(state.get("stability_seen") or [])

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    window_ms = int(now_ms - args.since_hours * 3600 * 1000)

    db_paths = sorted(AGENTS_DIR.glob("*/agent/openclaw-agent.sqlite"))
    results: list[dict] = []
    for db_path in db_paths:
        agent_id = db_path.parent.parent.name
        cursor = agent_state.get(agent_id) or {}
        results.append(scan_agent_db(db_path, agent_id, window_ms, cursor))

    ok_results = [r for r in results if r["ok"]]
    failed_results = [r for r in results if not r["ok"]]

    type_counts: Counter = Counter()
    for r in ok_results:
        type_counts.update(r["type_counts"])

    anomalies: list[dict] = []
    for r in ok_results:
        anomalies.extend(r["anomalies"])

    stability_records, stability_new, stability_total = scan_stability(seen_stability)

    # ── persist ──
    if args.dry_run:
        ev_lines = st_lines = 0
        print("[DRY RUN] 不写文件")
    else:
        ev_lines = append_jsonl(EVENTS_PATH, anomalies)
        st_lines = append_jsonl(STABILITY_PATH, stability_records)

        for r in ok_results:
            if r["max_created_at"] is None:
                continue
            prev = agent_state.get(r["agent_id"]) or {}
            prev_ms = prev.get("last_created_at")
            if isinstance(prev_ms, int) and prev_ms > r["max_created_at"]:
                continue
            agent_state[r["agent_id"]] = {
                "db": r["db"],
                "last_created_at": r["max_created_at"],
                "boundary_keys": r["rows_at_max"],
                "updated_at": now_iso(),
            }

        merged = list(dict.fromkeys(list(seen_stability) + stability_new))
        state["stability_seen"] = merged[-STABILITY_SEEN_CAP:]
        save_state(state)

    # ── summary ──
    cursor_count = sum(1 for a in agent_state.values() if a.get("last_created_at"))
    total_new_events = sum(type_counts.values())
    truncated = [r["agent_id"] for r in ok_results if r["truncated"]]

    print(
        f"OpenClaw 运行时异常采集 — {now_iso()}  window={args.since_hours:g}h"
        f"  cursor={cursor_count}/{len(db_paths)} db"
    )
    print(f"扫描库: {len(db_paths)} 个  成功 {len(ok_results)}  跳过/失败 {len(failed_results)}")
    print(f"窗口内事件: {total_new_events}")
    if type_counts:
        parts = " | ".join(f"{k}={v}" for k, v in type_counts.most_common())
        print(f"  按类型: {parts}")
    print(f"异常: {len(anomalies)}")

    if anomalies:
        by_status = Counter(str(a["status"]) for a in anomalies)
        print("  按 status: " + ", ".join(f"{k}={v}" for k, v in by_status.most_common()))
        by_flags = Counter(
            ",".join(a["flags"]) if a["flags"] else "(no-flag)" for a in anomalies
        )
        print("  按 flags : " + ", ".join(f"[{k}]={v}" for k, v in by_flags.most_common()))
        by_term = Counter(
            str(a["terminalError"]) for a in anomalies if a.get("terminalError")
        )
        if by_term:
            print(
                "  terminalError: "
                + ", ".join(f"{k}={v}" for k, v in by_term.most_common())
            )
        empty_text = sum(1 for a in anomalies if a.get("assistantTextCount") == 0)
        if empty_text:
            print(f"  空回复(assistantTexts 为空): {empty_text}")
        by_agent = Counter(a["agent_id"] for a in anomalies)
        print(
            "  按 agent : "
            + ", ".join(f"{k}={v}" for k, v in by_agent.most_common(5))
        )
        print("  样本(最多5条):")
        for a in anomalies[:5]:
            print(
                f"    - {a['sessionKey']}  {a['type']}  status={a['status']}"
                f"  flags={a['flags']}  promptErr={a['promptErrorSource']}"
            )

    if failed_results:
        print(f"  跳过的库({len(failed_results)}):")
        for r in failed_results[:5]:
            print(f"    - {r['agent_id']}: {r['error']}")
        if len(failed_results) > 5:
            print(f"    ... 其余 {len(failed_results) - 5} 个同类跳过")

    if truncated:
        print(f"  [WARN] 命中行数上限 {MAX_ROWS_PER_DB}: {', '.join(truncated)}")

    print(
        f"stability bundle: 共 {stability_total} 份  新增 {len(stability_records)}"
    )
    if stability_records:
        reasons = Counter(str(r.get("reason")) for r in stability_records)
        print("  按 reason: " + ", ".join(f"{k}={v}" for k, v in reasons.most_common()))

    print(
        f"落盘: events.jsonl +{ev_lines} 行 (共 {count_lines(EVENTS_PATH)})  "
        f"stability.jsonl +{st_lines} 行 (共 {count_lines(STABILITY_PATH)})"
    )
    print(f"状态: {STATE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
