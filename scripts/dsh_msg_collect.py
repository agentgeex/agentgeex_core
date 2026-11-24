#!/usr/bin/env python3
"""DSH session message collector - archives the DSH web session event stream into ``agx_user_message``.

────────────────────────────────────────────────────────────────────────────
Why it is needed (2026-11-17)
────────────────────────────────────────────────────────────────────────────
The existing collector ``tools/user_msg_collect.py`` reads **OpenClaw's SQLite
``transcript_events``**. DSH agents run inside ``dsh web`` and have no transcript on the OpenClaw
side at all - so the DB held zero DSH messages and the front end could not show any conversation history.

The data on the DSH side is complete, just shaped differently:
  * ``session/list``     -> each item carries ``running`` / ``updatedAt`` / ``cwd`` / projections
  * ``session/page``     -> event by event (user/message, assistant/message, tool/call...)
  * ``session/projections`` -> sessionStats / tokenUsage / turnOutline / plan

This collector aggregates the "event stream" into "question and answer" pairs and writes them into the
existing table ``agx_user_message`` (platform='dsh'), **without creating a new table** - rationale:
the BFF already has queries that work as-is (``webtools/server/app/realdata.py`` queries exactly this
table), so there is no extra front-end work and no "two message tables telling different stories".

────────────────────────────────────────────────────────────────────────────
Aggregation rules (aligned with the OpenClaw side)
────────────────────────────────────────────────────────────────────────────
One turn = one human/task input plus all assistant text blocks after it.

  user/message (source.kind == 'user')  -> opens a new turn
  assistant/message                     -> extract blocks with type=='text', accumulate in order
                                          (skip reasoning and tool-call:
                                           the front end wants "what was said", not "what was thought")
  user/message (any other kind)         -> system injection (time-context / agent-instructions
                                          / skill-catalog / runtime-context)
                                         stored on its own with msg_kind='system', does not break the current turn

* Why not simply drop system injections: the owner decided on 2026-11-14 that "system instructions
  must not be dropped"; the collector only has to capture everything, classification belongs to
  classify(), and display filtering belongs to the UI. This collector follows the same rule.

────────────────────────────────────────────────────────────────────────────
Incremental position
────────────────────────────────────────────────────────────────────────────
``session/page`` can only page **backwards** via ``beforeSeq``; there is no afterSeq.
So incremental collection = page backwards from the tail until ``seq <= last_seq`` and then truncate.
Paging too far loses nothing, it just reads a few extra pages (200 per page, negligible cost).

The position is stored in ``agx_user_message_cursor.last_seq``, sharing the same cursor table as the
OpenClaw side (distinguished by the platform column).

Usage::

    # collect sessions of all dsh agents
    python tools/dsh_msg_collect.py --host 10.0.0.13 --port 8791

    # only one agent, and do not write to the DB
    python tools/dsh_msg_collect.py --agent dshlab_pm --dry-run

    # full rebuild (ignore the position)
    python tools/dsh_msg_collect.py --full
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
from datetime import datetime, timedelta, timezone

LOCAL_TZ = timezone(timedelta(hours=8))  # Asia/Shanghai, consistent with the agx_* DATETIME columns
PLATFORM = "dsh"
FLUSH_EVERY = 200

CONFIG_CANDIDATES = (
    os.path.expanduser("~/.agx/agx_config.json"),
    os.path.expanduser("~/.agx/config/agx_config.json"),
)
CREDS_CANDIDATES = (
    os.path.expanduser("~/.agx/agx_creds.env"),
)

# DSH cwd looks like /home/owner/.agx/agx_agents/<agent_id>/workspace
_AGENT_IN_CWD = re.compile(r"/agx_agents/([^/]+)/")

# System-injection prefix. * Do not add "<!-- agx:session-ctx -->" here - that marks the start of
#   an AgentGeeX-dispatched *task instruction*, which carries a real answer and belongs to envelope (see classify).
SYS_PREFIXES = (
    "<system-reminder>",
    "Current runtime context",
    "Time sampled while preparing",
    "[System]",
)
AGX_ENVELOPE_PREFIXES = ("<!-- agx:session-ctx -->",)
NOTICE_PREFIXES = ("【",)


# --------------------------------------------------------------------------- #
# small helper
# --------------------------------------------------------------------------- #
def text_of(content) -> str:
    """Join DSH's content block array into body text.

    * Skip tool-call / reasoning, keep text only - same rule as text_of on the OpenClaw side.
      A measured assistant/message has content like:
        [{"type":"reasoning","text":"..."},
         {"type":"text","text":"Let me check first."},
         {"type":"tool-call","id":"call_x","name":"mcp__...","arguments":"{...}"}]
      Only the middle block is the answer; the other two are not.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        b.get("text", "")
        for b in content
        if isinstance(b, dict) and b.get("type") == "text"
    )


def classify(content: str, from_owner: int | None = None, source_kind: str = "") -> str:
    """human / envelope / notice / system / command.

    * Trust DSH's source.kind first, then look at the content shape (corrected on a real box, 2026-11-17):
      DSH labels the source of every user/message - user / time-context /
      runtime-context / agent-instructions / skill-catalog.
      That is the **authoritative marker**, far more reliable than a content prefix.

      Measured pitfall: a message starting with `<!-- agx:session-ctx -->` was once classified as
      system by prefix, but it is actually a task instruction dispatched by AgentGeeX, carrying a full
      real answer in the DB (reply_len=1372 / 5 text blocks). Guessing by content -> a task degraded
      into noise, and when the front end filters by msg_kind the human/task conversation disappeared.
      Therefore: any source.kind other than 'user' is system (content no longer inspected), and only
      'user' is further split into envelope / notice / command / human.
    """
    if source_kind and source_kind != "user":
        return "system"

    text = (content or "").lstrip()
    if text.startswith(AGX_ENVELOPE_PREFIXES):
        return "envelope"
    if text.startswith("{"):
        # * raw_decode is mandatory, not json.loads (hit on a real box 2026-11-17):
        #   the bridge appends a handling hint after the JSON envelope of receipt/alert messages
        #   (_notice_hint: tells the model to call agx_ack_receipt rather than finish_task).
        #   So the body looks like:
        #     {"task_run_uuid": "...", "msg_type": "receipt"}
        #
        #     Receipt handling: ... please call agx_ack_receipt(...) ...
        #     Note: a receipt is not a task, do **not** call agx_finish_task.
        #   json.loads raises `Extra data` on the trailing content,
        #   and a bare except swallows it -> the whole row is judged human (the front end then shows a receipt as a human message).
        #   raw_decode parses only the leading object; trailing content does not affect the decision.
        try:
            obj, _ = json.JSONDecoder().raw_decode(text)
            if isinstance(obj, dict) and ("task_run_uuid" in obj or "msg_type" in obj):
                return "envelope"
        except (ValueError, TypeError):
            pass
    if text.startswith(NOTICE_PREFIXES):
        return "notice"
    if text.startswith(SYS_PREFIXES):
        return "system"
    if text.startswith("/"):
        return "command"
    if from_owner == 0:
        return "system"
    return "human"


def make_msg_uid(host_id: str, session_id: str, ts_ms: int, content: str) -> str:
    """Stable identity = <host>:<md5(session|ts|content)[:16]>.

    Same rule as the OpenClaw side (its comment explains why event id is not used:
    compaction replays the same history under new ids, so keying by id stores two rows).
    """
    digest = hashlib.md5(
        f"{session_id}|{ts_ms}|{content}".encode("utf-8")
    ).hexdigest()[:16]
    return f"{host_id}:{digest}"


def md5_hex(text: str) -> str:
    return hashlib.md5((text or "").encode("utf-8")).hexdigest()


def to_local(ms: int | None) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def agent_from_cwd(cwd: str) -> str:
    m = _AGENT_IN_CWD.search(cwd or "")
    return m.group(1) if m else ""


# --------------------------------------------------------------------------- #
# config / connection
# --------------------------------------------------------------------------- #
def load_config(explicit: str | None = None) -> dict:
    paths = [explicit] if explicit else list(CONFIG_CANDIDATES)
    for p in paths:
        if p and os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
    sys.exit("找不到 agx_config.json（用 --config 指定）")


def load_creds_env() -> None:
    """Load AGX_DB_* from ~/.agx/agx_creds.env into the environment (without overwriting existing values)."""
    for p in CREDS_CANDIDATES:
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k.startswith("AGX_DB_") and k not in os.environ:
                        os.environ[k] = v
        except OSError:
            pass


def connect_mysql(cfg: dict):
    load_creds_env()
    import pymysql

    m = cfg.get("mysql") or {}
    if not m:
        sys.exit("agx_config.json 里没有 mysql 段")
    return pymysql.connect(
        host=m.get("host") or os.environ.get("AGX_DB_HOST", "127.0.0.1"),
        port=int(m.get("port") or os.environ.get("AGX_DB_PORT", 3306)),
        user=m.get("user") or os.environ.get("AGX_DB_USER", ""),
        password=m.get("password") or os.environ.get("AGX_DB_PASS", ""),
        database=m.get("database") or os.environ.get("AGX_DB_NAME", "agentgeex"),
        charset=m.get("charset", "utf8mb4"),
        autocommit=False,
    )


def resolve_session_keys(con_mysql, to_dsh_sid) -> dict[str, str]:
    """dsh_sid -> session_key, derived forward from agx_session.

    * Why ``to_session_key()`` alone is not enough (hit on a real box 2026-11-17):
      it is the **inverse of ``to_dsh_sid()`` but not a bijection** - ``to_dsh_sid`` collapses every
      illegal character in the session name (including colons) into ``-``:

        agent:dshlab_pm:universal:run_xxx
          --to_dsh_sid-->  dshlab_pm-universal-run_xxx
          --to_session_key-->  agent:dshlab_pm:universal-run_xxx   <- the middle colon is gone

      The result is that the same DSH session no longer matches its original session_key in the DB,
      so state write-back and message attribution all fall through.

      Correct approach: **compute forward from the DB**. agx_session.session_id is the source of truth;
      compute to_dsh_sid once per row and the dsh_sid -> session_key mapping is deterministic -
      a forward mapping is deterministic and needs no invertibility.
    """
    out: dict[str, str] = {}
    with con_mysql.cursor() as cur:
        cur.execute("SELECT session_id FROM agx_session WHERE COALESCE(active_status,1) = 1")
        for (session_id,) in cur.fetchall():
            sid = to_dsh_sid(session_id or "")
            if sid:
                out[sid] = session_id
    return out


def resolve_host_ids(con_mysql) -> dict[str, str]:
    """agent_id -> host_id (the host a DSH agent actually belongs to).

    * Why agx_config.json's host_id cannot be used directly (hit on a real box 2026-11-17):
      the 188 machine has **two** records in agx_host_inventory -
        test_host_srv   runs the OpenClaw main instance
        test_host_srv  DSH-only logical host (same IP; all agent_type=dsh agents hang off it)
      while agx_config.json writes host_id=test_host_srv.
      Copying the config -> collected data is filed under test_host_srv, does not match the agent records,
      and the BFF finds nothing when filtering by host.
      So follow the agent's own registration here; the config value is only a fallback.
    """
    out: dict[str, str] = {}
    with con_mysql.cursor() as cur:
        cur.execute(
            "SELECT agent_id, host_id FROM agx_agent_registry "
            "WHERE agent_type = %s AND COALESCE(host_id,'') <> ''",
            (PLATFORM,),
        )
        for agent_id, host_id in cur.fetchall():
            out[agent_id] = host_id
    return out


def load_cursors(con_mysql, host_id: str) -> dict[tuple[str, str], int]:
    """(agent_id, session_id) -> last_seq, only rows with platform=dsh."""
    out: dict[tuple[str, str], int] = {}
    with con_mysql.cursor() as cur:
        cur.execute(
            "SELECT agent_id, session_id, last_seq FROM agx_user_message_cursor "
            "WHERE host_id=%s AND platform=%s",
            (host_id, PLATFORM),
        )
        for agent_id, session_id, last_seq in cur.fetchall():
            out[(agent_id, session_id)] = int(last_seq or 0)
    return out


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #
def extract_turns(events: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split the event stream into "question and answer" pairs.

    Returns ``(turns, system_rows)``:
      turns       -- each with {seq, ts_ms, content, reply_parts: [(ts_ms, text)]}
      system_rows -- single system injections ({seq, ts_ms, content}), not part of turn splitting
    """
    turns: list[dict] = []
    system_rows: list[dict] = []
    cur: dict | None = None

    def close():
        nonlocal cur
        if cur is not None:
            turns.append(cur)
            cur = None

    for ev in events:
        etype = ev.get("type")
        data = ev.get("data") or {}
        seq = ev.get("seq") or 0
        ts = ev.get("time") or 0

        if etype == "user/message":
            src = data.get("source") or {}
            kind = src.get("kind") or ""
            text = text_of(data.get("content"))
            if not text.strip():
                continue
            if kind and kind != "user":
                # system injection: stored, but does not break the current turn
                system_rows.append({"seq": seq, "ts_ms": int(ts), "content": text,
                                    "source_kind": kind})
                continue
            close()
            cur = {"seq": seq, "ts_ms": int(ts), "content": text, "reply_parts": [],
                   "source_kind": kind or "user"}

        elif etype == "assistant/message":
            msg = data.get("message") or {}
            text = text_of(msg.get("content"))
            if text.strip() and cur is not None:
                cur["reply_parts"].append((int(ts), text))

    close()
    return turns, system_rows


# --------------------------------------------------------------------------- #
# DB write
# --------------------------------------------------------------------------- #
INSERT_SQL = """
INSERT IGNORE INTO agx_user_message
  (msg_uid, host_id, platform, agent_id, session_id, session_key, session_label,
   role, msg_kind, from_owner, content, content_len, content_hash, msg_at, source,
   reply_content, reply_len, reply_hash, reply_at, reply_parts)
VALUES (%s,%s,%s,%s,%s,%s,%s,'user',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
"""

CURSOR_SQL = """
INSERT INTO agx_user_message_cursor
  (host_id, platform, agent_id, session_id, last_seq, msg_count)
VALUES (%s,%s,%s,%s,%s,%s)
ON DUPLICATE KEY UPDATE
  last_seq  = GREATEST(last_seq, VALUES(last_seq)),
  msg_count = msg_count + VALUES(msg_count)
"""


def build_row(host_id, agent_id, dsh_sid, session_key, label, ts_ms, content,
              msg_kind, from_owner, reply_parts) -> tuple:
    reply_text = "".join(t for _, t in (reply_parts or []))
    reply_at = to_local(reply_parts[0][0]) if reply_parts else None
    return (
        make_msg_uid(host_id, dsh_sid, ts_ms, content),
        host_id, PLATFORM, agent_id, dsh_sid, session_key, label,
        msg_kind, from_owner, content, len(content), md5_hex(content), to_local(ts_ms),
        "dsh",
        reply_text, len(reply_text), (md5_hex(reply_text) if reply_text else None),
        reply_at, len(reply_parts or []),
    )


def flush(con_mysql, buffer: list[tuple], cursors: list[tuple]) -> int:
    with con_mysql.cursor() as cur:
        inserted = 0
        if buffer:
            cur.executemany(INSERT_SQL, buffer)
            inserted = cur.rowcount
        if cursors:
            cur.executemany(CURSOR_SQL, cursors)
    con_mysql.commit()
    return inserted


# --------------------------------------------------------------------------- #
# session state write-back (agx_session)
# --------------------------------------------------------------------------- #
def sync_session_state(con_mysql, host_id: str, states: list[dict]) -> int:
    """Write the DSH runtime state back into agx_session.

    Updates existing rows only (never inserts) - creating agx_session rows belongs to ensure_session;
    the collector must not conjure sessions. Columns written:
      last_active_at  <- updatedAt (last activity on the DSH side)
      run_count       <- sessionStats.turns (cumulative turns)
    """
    if not states:
        return 0
    hit = 0
    with con_mysql.cursor() as cur:
        for st in states:
            if not st.get("exists"):
                continue
            last_active = to_local(st.get("updated_at_ms"))
            turns = (st.get("stats") or {}).get("turns")
            if last_active is None and turns is None:
                continue
            # * Cannot filter by host_id (measured 2026-11-17): agx_session.host_id is
            #   NULL for all of these DSH sessions (table comment: NULL = historical, not backfilled), so the condition matches nothing.
            #   session_id itself is globally unique (stated in the table comment); it alone is enough to locate the row.
            #   Also fill in host_id on the way - it is not a predicate but a factual record of
            #   which host materializes the session entity; leaving it empty makes per-host aggregation miss these sessions.
            cur.execute(
                "UPDATE agx_session SET "
                "last_active_at = COALESCE(%s, last_active_at), "
                "run_count = GREATEST(COALESCE(run_count,0), COALESCE(%s,0)), "
                "host_id = COALESCE(host_id, %s) "
                "WHERE session_id = %s",
                (last_active, turns, host_id, st.get("session_key")),
            )
            hit += cur.rowcount
    con_mysql.commit()
    return hit


# --------------------------------------------------------------------------- #
# main flow
# --------------------------------------------------------------------------- #
def collect(dsh_client, con_mysql, host_id: str, *, only_agent: str = "",
            full: bool = False, dry_run: bool = False, verbose: bool = False,
            sync_state: bool = True) -> dict:
    from agx_dsh_client import to_dsh_sid, to_session_key

    sessions = dsh_client.list_sessions()
    host_of = resolve_host_ids(con_mysql)
    key_of = resolve_session_keys(con_mysql, to_dsh_sid)
    cursor_cache: dict[str, dict[tuple[str, str], int]] = {}

    def cursors_for(hid: str) -> dict[tuple[str, str], int]:
        if hid not in cursor_cache:
            cursor_cache[hid] = load_cursors(con_mysql, hid)
        return cursor_cache[hid]

    stats = {"sessions": 0, "scanned": 0, "rows": 0, "state_rows": 0,
             "errors": [], "unknown_agents": []}

    states: list[dict] = []
    buffer: list[tuple] = []
    cursor_rows: list[tuple] = []

    for item in sessions:
        dsh_sid = item.get("sessionId") or ""
        if not dsh_sid:
            continue
        agent_id = agent_from_cwd(item.get("cwd") or "")
        if not agent_id:
            continue  # not a session under agx_agents (DSH built-in / test session), skip
        if only_agent and agent_id != only_agent:
            continue

        hid = host_of.get(agent_id) or host_id
        stats["sessions"] += 1
        # look up the authoritative mapping built from the DB first; only fall back to reverse conversion (which is lossy for multi-segment names)
        session_key = key_of.get(dsh_sid) or to_session_key(dsh_sid)
        label = ((item.get("projections") or {}).get("values") or {}).get("title")
        if isinstance(label, dict):
            label = label.get("val")
        since = 0 if full else cursors_for(hid).get((agent_id, dsh_sid), 0)

        # * State collection runs *before* the "are there new messages" check (corrected 2026-11-17):
        #   the old implementation put session_state after `if not events: continue`,
        #   so a session with no new messages never had its state updated. But "busy or not / last activity time"
        #   is exactly what most needs refreshing when there are no new messages (the idle state has to be discoverable).
        #   with_stats=False only reads the projections already present in session/list, zero extra RPC.
        if sync_state:
            try:
                st = dsh_client.session_state(dsh_sid, with_stats=False)
                st["session_key"] = session_key
                st["host_id"] = hid
                states.append(st)
            except Exception:  # noqa: BLE001
                pass

        try:
            events = dsh_client.all_events(dsh_sid, since_seq=since)
        except Exception as e:  # noqa: BLE001
            stats["errors"].append(f"{dsh_sid}: {str(e)[:120]}")
            continue
        if not events:
            continue

        stats["scanned"] += 1
        turns, sysrows = extract_turns(events)
        max_seq = max((e.get("seq") or 0) for e in events)

        for t in turns:
            kind = classify(t["content"], source_kind=t.get("source_kind", "user"))
            # from_owner: only a human-classified message may claim "the owner himself said this".
            # everything else (envelope/notice/command) has no owner marker on the DSH side -> leave NULL.
            buffer.append(build_row(
                hid, agent_id, dsh_sid, session_key, label,
                t["ts_ms"], t["content"],
                kind, (1 if kind == "human" else None), t["reply_parts"],
            ))
        for sr in sysrows:
            buffer.append(build_row(
                hid, agent_id, dsh_sid, session_key, label,
                sr["ts_ms"], sr["content"], "system", None, [],
            ))

        cursor_rows.append((hid, PLATFORM, agent_id, dsh_sid, max_seq,
                            len(turns) + len(sysrows)))

        # spend one extra RPC on the full projections (sessionStats.turns) only when there really are new messages,
        # because agx_session.run_count wants the cumulative turn count, which the trimmed list projection lacks.
        if sync_state and states and states[-1].get("session_id") == dsh_sid:
            try:
                full = dsh_client.session_state(dsh_sid, with_stats=True)
                full["session_key"] = session_key
                full["host_id"] = hid
                states[-1] = full
            except Exception:  # noqa: BLE001
                pass

        if verbose:
            print(f"  {agent_id:<14} {dsh_sid:<34} +{len(turns)}turns "
                  f"+{len(sysrows)}sys (seq<={max_seq})")

        if len(buffer) >= FLUSH_EVERY:
            if dry_run:
                stats["rows"] += len(buffer)
            else:
                stats["rows"] += flush(con_mysql, buffer, cursor_rows)
            buffer, cursor_rows = [], []

    if buffer or cursor_rows:
        if dry_run:
            stats["rows"] += len(buffer)
        else:
            stats["rows"] += flush(con_mysql, buffer, cursor_rows)

    if sync_state and states and not dry_run:
        for hid in {s.get("host_id") for s in states if s.get("host_id")}:
            subset = [s for s in states if s.get("host_id") == hid]
            stats["state_rows"] += sync_session_state(con_mysql, hid, subset)

    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="DSH 会话消息采集器")
    ap.add_argument("--config", help="agx_config.json 路径")
    ap.add_argument("--host", default="192.168.0.188", help="DSH web 主机")
    ap.add_argument("--port", type=int, default=8791, help="DSH web 端口")
    ap.add_argument("--agent", default="", help="只采某个 agent")
    ap.add_argument("--full", action="store_true", help="忽略位点，拉全量")
    ap.add_argument("--dry-run", action="store_true", help="不写库")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--no-state", action="store_true", help="不回写 agx_session")
    args = ap.parse_args()

    cfg = load_config(args.config)
    host_id = (cfg.get("host_id") or socket.gethostname()).strip()

    scripts_dir = os.environ.get("AGX_SCRIPTS_DIR") or os.path.expanduser("~/.agx/scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from agx_dsh_client import DshClient  # noqa: E402

    dsh = DshClient(host=args.host, port=args.port, autostart=False, check_version=False)
    dsh.list_sessions()   # liveness: list working means reachable (DshClient itself has no probe; only the backend does)

    con = connect_mysql(cfg)
    try:
        stats = collect(
            dsh, con, host_id,
            only_agent=args.agent, full=args.full,
            dry_run=args.dry_run, verbose=args.verbose,
            sync_state=not args.no_state,
        )
    finally:
        con.close()

    print(
        f"[{datetime.now(LOCAL_TZ):%Y-%m-%d %H:%M:%S}] host={host_id} platform={PLATFORM} "
        f"会话={stats['sessions']} 有新增的={stats['scanned']} "
        f"写入={stats['rows']} 状态={stats['state_rows']}"
        + (" (dry-run)" if args.dry_run else "")
    )
    if stats["errors"]:
        print("  错误:")
        for e in stats["errors"][:10]:
            print("   ", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
