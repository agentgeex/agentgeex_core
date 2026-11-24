#!/usr/bin/env python3
"""Collect owner ("me") messages from OpenClaw gateway SQLite stores into MySQL.

Why this exists
---------------
OpenClaw keeps every conversation inside per-agent SQLite files
(``~/.openclaw/agents/<agent>/agent/openclaw-agent.sqlite``).  Those stores are
recycled by the gateway itself: sessions get compacted, archived and deleted, so
a message that matters today may be gone tomorrow.  This collector keeps a
durable, cross-host copy of the messages *I* sent, so they can later be mined
for ideas and preferences.

Design notes
------------
* One process per host.  It walks every agent store on this machine; no hook,
  no gateway restart, no changes to OpenClaw config.
* Incremental by ``transcript_events.seq`` (the store's primary key is
  ``(session_id, seq)``), which makes the "anything new?" check a primary-key
  probe rather than a table scan.
* Idempotent by ``msg_uid = <host_id>:<event_id>``.  Re-running, re-deploying or
  catching up after an offline period never duplicates rows.
* Every ``role=user`` row is taken (owner, non-owner and unmarked alike).
  ``msg_kind`` records what it was; filtering is a display concern, not a
  collection one.  Until 2026-11-14 an ``senderIsOwner`` gate silently dropped
  34.7% of user rows - including real human messages injected without that
  marker and all ``[System]`` / ``[cron:]`` instructions.
  The owner flag is the same one the gateway uses to mark first-party input, so
  injected system/plugin messages never leak in.

Usage
-----
    python3 tools/user_msg_collect.py --once          # backfill then exit
    python3 tools/user_msg_collect.py --dry-run       # report only, write nothing
    python3 tools/user_msg_collect.py --watch         # stay resident, poll loop

Exit codes: 0 ok, 1 error.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    import pymysql
except ImportError:  # pragma: no cover - dependency hint
    sys.exit("pymysql is required: pip install pymysql")

LOCAL_TZ = timezone(timedelta(hours=8))  # Asia/Shanghai, matches agx_* DATETIME columns
PLATFORM = "openclaw"
FLUSH_EVERY = 200
# Change-detection state for archives / trajectories.  Advisory only: losing it
# costs one full scan, never a lost row (every write path is idempotent).
FP_FILE = os.path.expanduser("~/.agx/runtime_watch/user_msg_collect.fp.json")
FP_VERSION = 1
CONFIG_CANDIDATES = (
    "~/.agx/agx_config.json",
)
OPENCLAW_BASE_CANDIDATES = ("~/.openclaw/agents",)
# One machine may have several OpenClaw profiles (.openclaw / .openclaw-<gw>b / -c ...),
# each with its own agents/ directory and store. Recognizing only the first would miss the sessions
# of the other gateways entirely (measured: test_srv_a missed 4/6, test_srv_b missed 6/19), so all are scanned by default.
OPENCLAW_BASE_GLOB = "~/.openclaw*/agents"
CREDS_CANDIDATES = ("~/.agx/agx_creds.env",)


# --------------------------------------------------------------------------- #
# config / connection
# --------------------------------------------------------------------------- #
def find_config(explicit: str | None) -> str:
    if explicit:
        path = os.path.expanduser(explicit)
        if not os.path.exists(path):
            sys.exit(f"config not found: {path}")
        return path
    env = os.environ.get("AGX_CONFIG")
    if env and os.path.exists(os.path.expanduser(env)):
        return os.path.expanduser(env)
    for cand in CONFIG_CANDIDATES:
        path = os.path.expanduser(cand)
        if os.path.exists(path):
            return path
    sys.exit("no agx_config.json found (tried %s)" % ", ".join(CONFIG_CANDIDATES))


def _creds_file() -> str:
    explicit = os.environ.get("AGX_CREDS_ENV", "")
    if explicit:
        return os.path.expanduser(explicit)
    for cand in CREDS_CANDIDATES:
        p = os.path.expanduser(cand)
        if os.path.isfile(p):
            return p
    return ""


def _load_creds_env() -> int:
    """Inject the creds file into os.environ (never overwrites existing keys).

    Why this exists: on 2026-11-13 the MySQL/Redis credentials were moved out of
    ``agx_config.json`` into ``~/.agx/agx_creds.env`` (the json now carries only
    host/port/database).  This collector kept reading the json, so ``m["user"]``
    raised ``KeyError('user')`` and every run died before writing anything.  The
    table sat frozen at 2026-11-10 22:48 for 3.5 days while the source SQLite
    kept accumulating.  Same root cause as the 2026-11-13 state-relay bug: the
    credential location moved, one reader did not follow.
    """
    p = _creds_file()
    if not p:
        return 0
    injected = 0
    try:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                if key.startswith("export "):
                    key = key[7:].strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                    val = val[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = val
                    injected += 1
    except OSError:
        return 0
    return injected


def load_mysql(cfg_path: str) -> dict:
    _load_creds_env()
    with open(cfg_path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if "mysql" not in cfg:
        sys.exit(f"{cfg_path} has no 'mysql' section")
    return cfg


def connect(cfg: dict):
    m = cfg["mysql"]
    # host/port/database still live in agx_config.json; user/password do not
    # (see _load_creds_env) — fall back to the AGX_DB_* env keys.
    return pymysql.connect(
        host=m.get("host") or os.environ.get("AGX_DB_HOST", "127.0.0.1"),
        port=int(m.get("port") or os.environ.get("AGX_DB_PORT", 3306)),
        user=m.get("user") or os.environ.get("AGX_DB_USER", ""),
        password=m.get("password") or os.environ.get("AGX_DB_PASS", ""),
        database=m.get("database") or os.environ.get("AGX_DB_NAME", "agentgeex"),
        charset=m.get("charset", "utf8mb4"),
        autocommit=False,
    )


def openclaw_bases(explicit: str | None) -> list[str]:
    """All OpenClaw ``agents/`` roots on this host, in a stable order.

    Multi-profile is the normal case, not the exception: one gateway per profile
    means ``~/.openclaw`` plus ``~/.openclaw-<name>`` for every extra instance.
    Scanning only the first root silently drops whole gateways' conversations,
    and "silently" is the part that hurts — the table still fills up, just with
    part of the host missing.
    """
    if explicit:
        return [os.path.expanduser(p.strip()) for p in explicit.split(",") if p.strip()]
    found = sorted(p for p in glob.glob(os.path.expanduser(OPENCLAW_BASE_GLOB)) if os.path.isdir(p))
    if found:
        return found
    for cand in OPENCLAW_BASE_CANDIDATES:
        path = os.path.expanduser(cand)
        if os.path.isdir(path):
            return [path]
    sys.exit("no openclaw agents dir found")


def openclaw_base(explicit: str | None) -> str:
    """First root only (kept for callers that still take a single base)."""
    return openclaw_bases(explicit)[0]


# --------------------------------------------------------------------------- #
# classification
# ---------------------------------------------------------------------------
# The gateway marks every message pushed over the owner channel with
# ``senderIsOwner``, including messages that the AgentGeeX daemon injects into a
# session (dispatch envelopes, watchdog alerts, receipts).  Roughly two thirds of
# the raw rows are those machine templates, so they must be separated from
# anything I actually typed.  The rules below are deliberately textual and
# re-runnable: raw content is kept verbatim, only ``msg_kind`` is derived.
SYS_PREFIXES = ("<<<BEGIN_OPENCLAW", "[System]", "[OpenClaw", "[Queue overflow]", "系统提醒",
                "[cron:", "[Cron:", "[Subagent Context]",
                # Session openers generated by OpenClaw itself. They arrive via internal injection without
                # the senderIsOwner marker, so they land as from_owner=NULL -- and NULL must stay
                # reserved for "human speech before 2026-10 that carries no marker", so only the raw text can identify them.
                # Measured (2026-11-14, three hosts combined): dream 559 / runtime 49.
                "Write a dream diary entry from these memory fragments",
                "Continue the OpenClaw runtime event.")


# Among system injections, the few that **trigger a round of their own answer** still count as a turn start.
# All other system injections (heartbeat / watchdog continuation reminders / queue overflow / previous-turn-interrupted notices /
# internal context blocks) merely "interleave mid-turn"; they ask nothing and should not consume an answer.
#
# * 2026-11-15: of 827 system-class user messages measured, 145 received more than 100 characters of
#   assistant text -- text that really belonged to the preceding human question or task envelope.
#   Most typical: the maintainer reported a bug at 15:42:23 (d9c10f1fefe110ea) -> tools ran with no output ->
#   a [OpenClaw heartbeat poll] was inserted into the transcript at 15:45:17 -> the final answer
#   at 15:47:14 ("you are right, both problems are real") was recorded under the heartbeat,
#   so the maintainer's row kept reply_content = NULL forever, and the UI shows "question has no reply".
SYS_TURN_OPENERS = (
    "Write a dream diary entry from these memory fragments",
    "[cron:", "[Cron:", "[Subagent Context]",
)


def _looks_like_envelope(text: str) -> bool:
    """Whether a text starting with ``{`` is really a dispatch envelope.

        * 2026-11-14: while checking 7 rows of invalid JSON, ui_lead found that pkid=423847 was actually
          the maintainer's own words -- `{pipe004_worker, pipe005_pm, ...}` followed by "I have already deleted these from openclaw...".
          It merely **starts with `{`**; it is not JSON. The old rule judged any `{` as an envelope,
          which is "it looks like one" -- the same flaw as guessing by content.

        Measured over the whole table: only 7 rows start with `{` but are not valid JSON --
          6 are real envelopes (two objects concatenated, so JSON parsing must fail)
          1 is the maintainer's own words (the example above)
        Telling them apart needs no semantic understanding: a real envelope always contains "task_run_uuid".
    """
    try:
        obj = json.loads(text)
        return isinstance(obj, dict)
    except (ValueError, TypeError):
        return '"task_run_uuid"' in text[:400]


# * 2026-11-17: when "a message arrives before the previous turn has finished", OpenClaw wraps the human
#   words before storing them in the form [Queued user message from a previous active turn; preserved
#   as context only. Continue with the active prompt below.] followed by the original words
#   It matches SYS_PREFIXES -> judged system -> **human speech treated as system noise**.
#   Measured 280 rows, all from the maintainer himself ("do you still remember your project", "got it, go ahead as you said" ...).
#   senderIsOwner is always None (internal injection writes no marker), so only prefix recognition plus stripping works.
_QUEUED_PREFIX_RE = re.compile(
    r"^\[Queued user message[^\]]*\]\s*", re.IGNORECASE)


def strip_queued_prefix(content: str) -> str:
    """Strip OpenClaw's queued-message wrapper and return the human words inside.
    The stored raw content is never modified (raw content stays verbatim); this is used **only for classification**.
    """
    stripped = _QUEUED_PREFIX_RE.sub("", content.lstrip(), count=1)
    return stripped if stripped.strip() else content


# * 2026-11-17: "Continue the OpenClaw runtime event." is also a **concatenable prefix**.
#   Measured 320 rows with that prefix: 252 are the opener alone (len=36, all replies empty = pure noise),
#   the other 68 are "opener + human words" concatenated -- for example
#     "Continue the OpenClaw runtime event. now let me think about it, you have confused me a bit ..."
#     "Continue the OpenClaw runtime event. 1) project-side active_status delegation ..."
#   These are the maintainer's words, yet SYS_PREFIXES judged the whole line system.
#   Note that stripping blindly is wrong: if what remains is "system reminder ..." (watchdog injection) it is still system,
#   and if nothing remains (bare opener) it must stay system -- so after stripping, let classify decide.
_RUNTIME_PREFIX = "Continue the OpenClaw runtime event."


def strip_runtime_prefix(content: str) -> str:
    """Strip the runtime-event opener; return the input unchanged when no substance follows (stays system)."""
    text = content.lstrip()
    if not text.startswith(_RUNTIME_PREFIX):
        return content
    rest = text[len(_RUNTIME_PREFIX):].lstrip()
    return rest if rest.strip() else content


def classify(content: str, from_owner: int | None = None) -> str:
    """Return human / envelope / notice / system / command.

        * 2026-11-14: ``from_owner=0`` is pressed straight to system instead of guessing from content shape.
          Cause: ui_lead found 8 ``[Subagent Context]`` rows treated as human.
          Both sides had to be fixed --
            1) the prefix was missing -> added to ``SYS_PREFIXES`` (fixes the known one of that kind)
            2) **the rule level**: once the gateway says "this is not first-party input" (``senderIsOwner=False``),
               content shape must not override it. Source markers take precedence over content guessing.
          Measured: plain text never produced ``False`` (0/3,805 rows), so this cannot kill real human messages.
    """
    # First strip the two layers of "concatenable prefix" -- what remains inside is what gets classified
    #   1) [Queued user message ...]            (queued while busy)
    #   2) Continue the OpenClaw runtime event. (session opener; human words may follow)
    text = strip_runtime_prefix(strip_queued_prefix(content)).lstrip()
    if text.startswith("{") and _looks_like_envelope(text):
        return "envelope"
    if text.startswith("【"):
        return "notice"
    if text.startswith(SYS_PREFIXES):
        return "system"
    if text.startswith("/"):
        return "command"
    if from_owner == 0:
        return "system"
    return "human"


# --------------------------------------------------------------------------- #
# store reading
# --------------------------------------------------------------------------- #
def load_fingerprints() -> dict[str, str]:
    try:
        with open(FP_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in files.items()} if isinstance(files, dict) else {}


def save_fingerprints(fp: dict[str, str]) -> None:
    try:
        os.makedirs(os.path.dirname(FP_FILE), exist_ok=True)
        tmp = f"{FP_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": FP_VERSION, "files": fp}, fh, ensure_ascii=False)
        os.replace(tmp, FP_FILE)
    except OSError:
        pass


def file_signature(path: str) -> str:
    try:
        st = os.stat(path)
    except OSError:
        return ""
    return f"{st.st_mtime_ns}:{st.st_size}"


def iter_stores(base: str):
    """Yield (agent_id, sqlite_path) for every agent store on this host."""
    for path in sorted(glob.glob(os.path.join(base, "*", "agent", "openclaw-agent.sqlite"))):
        yield path.split(os.sep)[-3], path


def open_store(path: str) -> sqlite3.Connection:
    """Read-only connection; falls back to a normal one if the URI form is refused."""
    def ro():
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)

    try:
        con = ro()
        con.execute("SELECT 1 FROM transcript_events LIMIT 1").fetchone()
        return con
    except sqlite3.Error:
        try:
            con.close()
        except Exception:
            pass
        return sqlite3.connect(path, timeout=5)


def load_session_meta(con: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """session_id -> (session_key, label). Archives cover sessions already rotated out.

        * 2026-11-14: ``session_windows`` added. The old implementation read only ``session_nodes`` (which stores
          only the [current] session_id of each session_key) and archives (which store only deleted ones). Once a session
          rotates, the old session_id is in neither -- measured across 1,369 transcript sessions,
          ``session_windows`` covers 100%, while session_nodes alone misses 2.
          Without it -> session_key is blank on insert -> the UI cannot see those conversations by session.
    """
    meta: dict[str, tuple[str, str]] = {}
    try:
        for cid, skey, label, dname in con.execute(
            "SELECT current_session_id, session_key, label, display_name FROM session_nodes"
        ):
            if cid:
                meta[cid] = (skey or "", label or dname or "")
    except sqlite3.Error:
        pass
    try:
        for sid, skey in con.execute(
            "SELECT session_id, session_key FROM session_windows"
        ):
            if sid and sid not in meta:
                meta[sid] = (skey or "", "")
    except sqlite3.Error:
        pass
    try:
        for sid, skey in con.execute(
            "SELECT session_id, session_key FROM session_transcript_archives"
        ):
            if sid and sid not in meta:
                meta[sid] = (skey or "", "")
    except sqlite3.Error:
        pass
    return meta


def session_watermark(con: sqlite3.Connection) -> dict[str, int]:
    """session_id -> max(seq). One indexed group-by; no JSON parsing."""
    try:
        return dict(con.execute("SELECT session_id, MAX(seq) FROM transcript_events GROUP BY session_id"))
    except sqlite3.Error:
        return {}


def load_cursors(con_mysql, host_id: str) -> dict[tuple[str, str], int]:
    """(agent_id, session_id) -> last_seq for this host."""
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


def make_msg_uid(host_id: str, event_id: str, session_id: str, seq: int,
                 ts_ms: int = 0, content: str = "") -> str:
    """Stable identity for one logical message.

        * 2026-11-14: event_id is no longer the unique key.
          Measured: compaction re-appends the same batch of history with [new event ids] into
          transcript_events -- in one session seq=185 and seq=518 are both "keep working" with
          identical timestamps, but different ids (f7c810e0 / c9851b98, across two compactions).
          An event id is a storage implementation detail, not message identity; using it as the unique key
          stores one human message twice.

          Now a digest of (session, timestamp, body): none of the three change when compaction copies,
          so duplicate keys hit INSERT IGNORE and dedupe naturally. Measured across the whole DB: 969 duplicate groups / 1,110 redundant rows.
    """
    if ts_ms and content:
        digest = hashlib.md5(
            f"{session_id}|{ts_ms}|{content}".encode("utf-8")
        ).hexdigest()[:16]
        return f"{host_id}:{digest}"
    return f"{host_id}:{event_id or f'{session_id}:{seq}'}"


def text_of(msg: dict) -> str:
    """Concatenate the text blocks of a message, skipping tool calls and thinking."""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def extract_turns(con: sqlite3.Connection, session_id: str):
    """Yield one record per turn: the owner message plus every reply block after it.

    A turn runs from an owner message to the next owner message.  Assistant steps
    that only carry tool calls emit no text and contribute nothing; several text
    blocks in one turn (narration -> tool -> narration) are kept in order so the
    reader sees how the answer was built, not just its final sentence.
    """
    current = None
    parts: list[tuple[int, str]] = []

    def close():
        if current is None:
            return None
        seq, event_id, ts_ms, kind, content = current
        return seq, event_id, ts_ms, kind, content, list(parts)

    rows = con.execute(
        "SELECT seq, event_json FROM transcript_events WHERE session_id=? ORDER BY seq",
        (session_id,),
    )
    for seq, raw in rows:
        try:
            event = json.loads(raw)
        except (ValueError, TypeError):
            continue
        msg = event.get("message") or {}
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")

        if role == "user":
            # * 2026-11-14: no longer filter on senderIsOwner (the maintainer decided on the spot: system instructions must not be dropped).
            #   The field is tri-state True / False / None (field absent); the old code
            #   `if not ...senderIsOwner` dropped None along with False --
            #   measured 7,808 user messages across 82 DBs, 2,706 wrongly dropped (34.7%):
            #     - ow=None 2,178: of which 355 plain-text rows were the maintainer's own words
            #       (injected via send_text / note_main_session / watchdog wake-ups, paths that
            #        write no __openclaw marker), and 1,318 were task-dispatch / plan_completed envelopes
            #     - ow=False  528: all 528 are [System] / [cron:] / heartbeat system instructions
            #   Both system and human rows must be archived. The collector only "collects everything";
            #   classification is up to classify(), display filtering is up to the UI -- consistent with the archives path
            #   (extract_turns_loose, which never looked at owner).
            # * 2026-11-17: the code used to require content to be a str, while OpenClaw stores
            #   messages like a queued-user-message wrapper followed by human words, or "Continue the OpenClaw
            #   runtime event.", with content as a block array
            #   ([{"type":"text","text":"..."}]) -> the whole row was dropped.
            #   Measured: of 83 DBs / 7,937 user messages, 508 were like this, 280 of them
            #   the maintainer's own words (and he happens to speak only while the agent is busy).
            #   The archive path always had a text_of fallback; these live paths missed it -- now added.
            content = text_of(msg)
            if not content.strip():
                continue
            kind = classify(content)
            # * 2026-11-15: interleaved system injections neither open a new turn nor cut the current one.
            #   Skipping them lets later assistant text keep attaching to the previous human question / task envelope.
            #   Its own line is still stored separately by the live stage (extract_owner_messages does not look here).
            if kind == "system" and not content.lstrip().startswith(SYS_TURN_OPENERS):
                continue
            done = close()
            if done:
                yield done
            ts = msg.get("timestamp")
            if ts is None:
                ts = parse_iso_ms(event.get("timestamp"))
            current = (seq, str(event.get("id") or ""), int(ts) if ts else 0, kind, content)
            parts = []
        elif role == "assistant":
            if current is None:
                continue
            text = text_of(msg)
            if not text.strip():
                continue
            ts = msg.get("timestamp")
            if ts is None:
                ts = parse_iso_ms(event.get("timestamp"))
            parts.append((int(ts) if ts else 0, text))

    done = close()
    if done:
        yield done


def owner_flag(msg: dict) -> int | None:
    """Tri-state owner marker straight from the gateway: 1 owner / 0 not / None unknown.

        * 2026-11-14: this is the single source of truth for ``__openclaw.senderIsOwner``; do not guess from content.
          Measured across 82 DBs, the 4,409 plain-text role=user rows:
            senderIsOwner=True   3,235 (the maintainer himself)
            senderIsOwner=False      0  <- plain text never yields False
            __openclaw absent      570
          while all 528 False rows fall into bracketed forms like ``[System]`` / ``[cron:]`` / heartbeat.
          In other words: **False only appears in system injections; plain text can never be misjudged as False**.

          ``None`` does not mean "unsure, so treat as yes"; it genuinely has three sources:
            1) the gateway did not write the field before 2026-10 (monthly: 100% missing before October,
               12% in October, 6.2% in November)
            2) plain text injected via send_text / note_main_session carries no marker
            3) ``__openclaw`` is stripped in archived JSONL
          so NULL must be preserved as-is and never defaulted to 1 or 0.
    """
    meta = msg.get("__openclaw")
    if not isinstance(meta, dict) or "senderIsOwner" not in meta:
        return None
    return 1 if meta.get("senderIsOwner") is True else 0


def extract_owner_messages(con: sqlite3.Connection, session_id: str, since_seq: int):
    """Yield (seq, event_id, timestamp_ms, content, from_owner) past since_seq."""
    rows = con.execute(
        "SELECT seq, event_json FROM transcript_events WHERE session_id=? AND seq>? ORDER BY seq",
        (session_id, since_seq),
    )
    for seq, raw in rows:
        try:
            event = json.loads(raw)
        except (ValueError, TypeError):
            continue
        msg = event.get("message") or {}
        if msg.get("role") != "user":
            continue
        # * 2026-11-14: same as extract_turns -- drop the senderIsOwner filter;
        #   both system instructions and human messages are collected. Rationale in the comment at extract_turns.
        # * 2026-11-17: the code used to require content to be a str, while OpenClaw stores
        #   messages like a queued-user-message wrapper followed by human words, or "Continue the OpenClaw
        #   runtime event.", with content as a block array
        #   ([{"type":"text","text":"..."}]) -> the whole row was dropped.
        #   Measured: of 83 DBs / 7,937 user messages, 508 were like this, 280 of them
        #   the maintainer's own words (and he happens to speak only while the agent is busy).
        #   The archive path always had a text_of fallback; these live paths missed it -- now added.
        content = text_of(msg)
        if not content.strip():
            continue
        ts = msg.get("timestamp")
        if ts is None:
            ts = parse_iso_ms(event.get("timestamp"))
        if ts is None:
            continue
        yield seq, str(event.get("id") or ""), int(ts), content, owner_flag(msg)


def parse_iso_ms(value) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return int(dt.timestamp() * 1000)


def to_local(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


# --------------------------------------------------------------------------- #
# sink
# --------------------------------------------------------------------------- #
INSERT_SQL = """
INSERT IGNORE INTO agx_user_message
  (msg_uid, host_id, platform, agent_id, session_id, session_key, session_label,
   role, msg_kind, from_owner, content, content_len, content_hash, msg_at, source)
VALUES (%s,%s,%s,%s,%s,%s,%s,'user',%s,%s,%s,%s,%s,%s,%s)
"""

CURSOR_SQL = """
INSERT INTO agx_user_message_cursor
  (host_id, platform, agent_id, session_id, last_seq, msg_count)
VALUES (%s,%s,%s,%s,%s,%s)
ON DUPLICATE KEY UPDATE
  last_seq  = GREATEST(last_seq, VALUES(last_seq)),
  msg_count = msg_count + VALUES(msg_count)
"""


def flush(con_mysql, buffer: list[tuple], cursors: list[tuple]) -> int:
    """Write buffered rows and advance cursors in one transaction.

    Cursors are written even when the buffer is empty: a session whose new rows
    were all assistant messages still has to move its watermark, otherwise it is
    rescanned forever.
    """
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
# collection
# --------------------------------------------------------------------------- #
def collect_once(cfg: dict, host_id: str, base: str, dry_run: bool, verbose: bool,
                 replay: bool = False) -> dict:
    con_mysql = None if dry_run else connect(cfg)
    # * When replay=True the cursor is treated as empty and the scan restarts from seq=0.
    #   Used to correct the historical collection policy (e.g. backfill history after dropping the senderIsOwner filter on 2026-11-14).
    #   Safety: msg_uid contains (host, event_id, session, seq), so old rows hit INSERT IGNORE
    #   and are skipped, never duplicated; the cursor is written back with GREATEST, so it never moves backwards.
    cursors = {} if replay else (load_cursors(con_mysql, host_id) if con_mysql else {})

    stats = {"agents": 0, "sessions": 0, "kept": 0, "inserted": 0, "errors": 0}
    per_agent: list[tuple[str, int]] = []

    for agent_id, path in iter_stores(base):
        stats["agents"] += 1
        try:
            store = open_store(path)
        except sqlite3.Error as exc:
            stats["errors"] += 1
            print(f"  ! {agent_id}: cannot open ({exc})", file=sys.stderr)
            continue

        try:
            meta = load_session_meta(store)
            marks = session_watermark(store)
            agent_kept = 0
            buffer: list[tuple] = []
            pending_cursors: list[tuple] = []

            for session_id, max_seq in marks.items():
                since = cursors.get((agent_id, session_id), 0)
                if max_seq <= since:
                    continue
                stats["sessions"] += 1
                session_key, label = meta.get(session_id, ("", ""))
                session_kept = 0

                for seq, event_id, ts_ms, content, f_owner in extract_owner_messages(store, session_id, since):
                    agent_kept += 1
                    session_kept += 1
                    buffer.append((
                        make_msg_uid(host_id, event_id, session_id, seq, ts_ms, content),
                        host_id,
                        PLATFORM,
                        agent_id,
                        session_id,
                        session_key,
                        label,
                        classify(content, f_owner),
                        f_owner,
                        content,
                        len(content),
                        hashlib.md5(content.encode("utf-8")).hexdigest(),
                        to_local(ts_ms),
                        "transcript_events",
                    ))
                    if not dry_run and len(buffer) >= FLUSH_EVERY:
                        stats["inserted"] += flush(con_mysql, buffer, [])
                        buffer = []

                pending_cursors.append(
                    (host_id, PLATFORM, agent_id, session_id, max_seq, session_kept)
                )
                if not dry_run and len(pending_cursors) >= FLUSH_EVERY:
                    stats["inserted"] += flush(con_mysql, buffer, pending_cursors)
                    buffer, pending_cursors = [], []

            if not dry_run and (buffer or pending_cursors):
                stats["inserted"] += flush(con_mysql, buffer, pending_cursors)

            stats["kept"] += agent_kept
            if agent_kept:
                per_agent.append((agent_id, agent_kept))
        finally:
            store.close()

    if con_mysql:
        check_invariants(con_mysql, verbose=verbose)
        con_mysql.close()

    stats["per_agent"] = per_agent
    return stats


def reclassify(con_mysql, dry_run: bool = False) -> int:
    """Re-derive msg_kind for every stored row. Raw content is never touched."""
    with con_mysql.cursor() as cur:
        cur.execute("SELECT pkid, msg_kind, content, from_owner FROM agx_user_message")
        rows = cur.fetchall()
        updates = [(kind, pkid) for pkid, old, content, fo in rows
                   if (kind := classify(content, fo)) != old]
        counts: dict[str, int] = {}
        for kind, _ in updates:
            counts[kind] = counts.get(kind, 0) + 1
        if updates and not dry_run:
            cur.executemany("UPDATE agx_user_message SET msg_kind=%s WHERE pkid=%s", updates)
    if not dry_run:
        con_mysql.commit()
    print(f"  scanned={len(rows)} changed={len(updates)} {'(dry-run)' if dry_run else ''}")
    for kind, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"    -> {kind:<10} {n}")
    return 0


REPLY_SQL = """
UPDATE agx_user_message
   SET reply_content=%s, reply_len=%s, reply_hash=%s, reply_at=%s, reply_parts=%s
 WHERE msg_uid=%s AND (reply_content IS NULL OR reply_hash IS NULL OR reply_hash<>%s)
"""


REPLY_SEPARATOR = "\n\n"


def collect_replies(cfg: dict, host_id: str, base: str, dry_run: bool, verbose: bool) -> dict:
    """Attach the assistant's reply to every stored question.

    Deliberately a replay instead of an incremental pass: a reply arrives *after*
    the question it answers, so by the time the cursor has moved past a question
    its answer may still be streaming.  Replaying whole sessions removes that
    ordering hazard, costs a few seconds per host, and stays idempotent because
    rows are only written when the reply hash changes.
    """
    stats = {"turns": 0, "with_reply": 0, "updated": 0, "unchanged": 0,
             "orphan": 0, "errors": 0}

    con_mysql = None
    known: set[str] = set()
    if not dry_run:
        con_mysql = connect(cfg)
        with con_mysql.cursor() as cur:
            cur.execute("SELECT msg_uid FROM agx_user_message")
            known = {row[0] for row in cur.fetchall()}

    pending: list[tuple] = []
    for agent_id, path in iter_stores(base):
        try:
            store = open_store(path)
        except sqlite3.Error as exc:
            stats["errors"] += 1
            print(f"  ! {agent_id}: cannot open ({exc})", file=sys.stderr)
            continue
        try:
            sessions = [r[0] for r in store.execute(
                "SELECT DISTINCT session_id FROM transcript_events")]
            for session_id in sessions:
                for seq, event_id, ts_ms, kind, _question, parts in extract_turns(store, session_id):
                    stats["turns"] += 1
                    uid = make_msg_uid(host_id, event_id, session_id, seq, ts_ms, _question)
                    if parts:
                        stats["with_reply"] += 1
                    if known and uid not in known:
                        stats["orphan"] += 1
                        continue
                    if not parts:
                        continue
                    body = REPLY_SEPARATOR.join(text for _ts, text in parts)
                    first_ts = parts[0][0] or ts_ms
                    pending.append((
                        body,
                        len(body),
                        hashlib.md5(body.encode("utf-8")).hexdigest(),
                        to_local(first_ts) if first_ts else None,
                        len(parts),
                        uid,
                        "",  # placeholder compared against reply_hash in SQL
                    ))
                    if not dry_run and len(pending) >= FLUSH_EVERY:
                        stats["updated"] += _flush_replies(con_mysql, pending)
                        pending = []
        finally:
            store.close()

    if pending and not dry_run:
        stats["updated"] += _flush_replies(con_mysql, pending)
    if con_mysql:
        con_mysql.close()

    if verbose:
        print(f"  replies: turns={stats['turns']} with_reply={stats['with_reply']} "
              f"orphans={stats['orphan']}")
    return stats


def _flush_replies(con_mysql, rows: list[tuple]) -> int:
    """Write reply columns; the trailing empty string disables the self-compare.

    ``executemany`` cannot reuse a parameter twice in one statement, so the last
    placeholder is filled with the row's own hash by rebuilding it here.
    """
    fixed = [row[:6] + (row[2],) for row in rows]
    with con_mysql.cursor() as cur:
        cur.executemany(REPLY_SQL, fixed)
        changed = cur.rowcount
    con_mysql.commit()
    return max(changed, 0)


_ZSTD_PROBE: list[str | None] = []  # Lazy detection result (empty list = not probed yet)


def zstd_available() -> str:
    """Return how zstd can be inflated: "cmd", "py" or "" (neither).

    Two hosts, two situations: test_srv_b ships /usr/bin/zstd but 118 has no
    zstd binary at all (and its glibc is too old to accept one copied over),
    while 118 does have the ``zstandard`` wheel installed.  Hard-coding the CLI
    made archive recovery a silent no-op there — the stage logged one OSError
    per pass and everything else looked healthy.
    """
    if not _ZSTD_PROBE:
        from shutil import which
        if which("zstd"):
            _ZSTD_PROBE.append("cmd")
        else:
            try:
                import zstandard  # noqa: F401
                _ZSTD_PROBE.append("py")
            except ImportError:
                _ZSTD_PROBE.append("")
    return _ZSTD_PROBE[0] or ""


def decompress_archive(blob, encoding: str) -> bytes:
    """Inflate an archive payload.  ``zstd`` is the only encoding seen so far."""
    if encoding == "zstd":
        how = zstd_available()
        if how == "cmd":
            proc = subprocess.run(["zstd", "-dc"], input=blob, capture_output=True)
            return proc.stdout if proc.returncode == 0 else b""
        if how == "py":
            import zstandard
            try:
                return zstandard.ZstdDecompressor().decompress(blob, max_output_size=1 << 30)
            except zstandard.ZstdError:
                # A frame that wrote no content size: can only be read as a stream
                with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(blob)) as r:
                    return r.read()
        print("  ! zstd 解压不可用：无 zstd 命令，且未安装 python zstandard", file=sys.stderr)
        return b""
    return blob if isinstance(blob, (bytes, bytearray)) else b""


def decompress_archive_file(path: str) -> bytes | None:
    """Same as :func:`decompress_archive` but reading from a rotated file."""
    how = zstd_available()
    if how == "cmd":
        try:
            proc = subprocess.run(["zstd", "-dc", path], capture_output=True)
        except OSError:
            return None
        return proc.stdout if proc.returncode == 0 else None
    if how == "py":
        import zstandard
        try:
            with open(path, "rb") as fh:
                with zstandard.ZstdDecompressor().stream_reader(fh) as r:
                    return r.read()
        except (OSError, zstandard.ZstdError):
            return None
    return None


def iter_archive_documents(base: str, fp: dict | None = None, seen: dict | None = None,
                           counters: dict | None = None):
    """Yield (agent_id, session_id, session_key, label, lines) for every rotated transcript.

    Three sources hold the same thing in different states of decay: files rotated
    by the gateway (``*.jsonl.deleted.*``, ``*.jsonl.reset.*``, ``*.zst``), still
    live ``*.jsonl`` files that predate collection, and ``archive_blob`` rows whose
    file may already be gone.  Overlap is expected and harmless: rows are keyed by
    event id, so the second copy of an event simply matches the first.

    ``fp`` / ``seen`` implement change detection.  Archives are append-only: once
    rotated out, a transcript never changes again.  Measured 2026-11-14: 1,242
    files / 843.8 MB / 99,876 lines re-parsed every 60 s cost 13.6 s of CPU per
    pass while reading **zero** bytes from disk.  A stat signature makes the
    steady state a no-op.  ``seen`` collects signatures for this pass only; the
    caller merges them into ``fp`` after the pass succeeds, so a pass that raises
    retries those files next time instead of silently skipping them.
    """
    for agent_id, store_path in iter_stores(base):
        keys: dict[str, tuple[str, str]] = {}
        blobs: list[tuple[str, str, bytes]] = []
        try:
            con = open_store(store_path)
        except sqlite3.Error:
            con = None
        if con is not None:
            try:
                for sid, key, label in con.execute(
                        "SELECT current_session_id, session_key, label FROM session_nodes"):
                    if sid:
                        keys.setdefault(sid, (key or "", label or ""))
                # * 2026-11-14: session_windows -- the only place the old session_id survives a rotation.
                #   Without it -> archive rows have a blank session_key -> the UI cannot see them by session.
                try:
                    for sid, key in con.execute(
                            "SELECT session_id, session_key FROM session_windows"):
                        if sid:
                            keys.setdefault(sid, (key or "", ""))
                except sqlite3.Error:
                    pass
                for sid, key in con.execute(
                        "SELECT session_id, session_key FROM session_transcript_archives"):
                    if sid:
                        keys.setdefault(sid, (key or "", ""))
                # The blob sits inside a SQLite file the gateway rewrites
                # constantly, so its mtime says nothing about blob content.
                # Row count + total byte length is a stable identity, and costs
                # 0.05 s for the whole host.
                try:
                    bcount, bbytes = con.execute(
                        "SELECT COUNT(*), COALESCE(SUM(LENGTH(archive_blob)),0)"
                        " FROM session_transcript_archives").fetchone()
                except sqlite3.Error:
                    bcount, bbytes = 0, 0
                bkey = f"archive::{store_path}#archive"
                bsig = f"{bcount}:{bbytes}"
                if fp is not None and fp.get(bkey) == bsig:
                    if counters is not None:
                        counters["blobs_skipped"] = counters.get("blobs_skipped", 0) + 1
                else:
                    for name, enc, blob in con.execute(
                            "SELECT archive_name, encoding, archive_blob FROM session_transcript_archives"):
                        blobs.append((name or "", enc or "", blob))
                    if seen is not None:
                        seen[bkey] = bsig
            except sqlite3.Error as exc:
                print(f"  ! {agent_id}: archive metadata unreadable ({exc})", file=sys.stderr)
            finally:
                con.close()

        sessions_dir = os.path.join(os.path.dirname(os.path.dirname(store_path)), "sessions")
        for path in sorted(glob.glob(os.path.join(sessions_dir, "*"))):
            name = os.path.basename(path)
            if not os.path.isfile(path) or "jsonl" not in name:
                continue
            if counters is not None:
                counters["files"] = counters.get("files", 0) + 1
            sig = file_signature(path)
            if fp is not None and sig and fp.get(f"archive::{path}") == sig:
                if counters is not None:
                    counters["files_skipped"] = counters.get("files_skipped", 0) + 1
                continue
            session_id = name.split(".")[0]
            key, label = keys.get(session_id, ("", ""))
            if path.endswith(".zst"):
                raw = decompress_archive_file(path)
                if raw is None:
                    continue
                lines = raw.decode("utf-8", "replace").splitlines()
            else:
                try:
                    with open(path, encoding="utf-8", errors="replace") as fh:
                        lines = fh.read().splitlines()
                except OSError:
                    continue
            if seen is not None and sig:
                seen[f"archive::{path}"] = sig
            yield agent_id, session_id, key, label, lines

        for name, enc, blob in blobs:
            text = decompress_archive(blob, enc)
            if not text:
                continue
            session_id = (name or "archive").split(".")[0]
            key, label = keys.get(session_id, ("", ""))
            yield agent_id, session_id, key, label, text.decode("utf-8", "replace").splitlines()


def extract_turns_loose(lines):
    """Same turn shape as :func:`extract_turns`, for JSONL that lost its metadata.

    Rotated transcripts strip ``__openclaw`` entirely, so ``senderIsOwner`` is not
    available.  Every user message is therefore taken, and classification decides
    afterwards what it was - the raw text is never altered either way.
    """
    current = None
    parts: list[tuple[int, str]] = []

    def close():
        if current is None:
            return None
        event_id, ts_ms, kind, content = current
        return event_id, ts_ms, kind, content, list(parts)

    for line_no, raw in enumerate(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        msg = event.get("message")
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        ts = msg.get("timestamp")
        if ts is None:
            ts = parse_iso_ms(event.get("timestamp"))

        if role == "user":
            # * 2026-11-17: the code used to require content to be a str, while OpenClaw stores
            #   messages like a queued-user-message wrapper followed by human words, or "Continue the OpenClaw
            #   runtime event.", with content as a block array
            #   ([{"type":"text","text":"..."}]) -> the whole row was dropped.
            #   Measured: of 83 DBs / 7,937 user messages, 508 were like this, 280 of them
            #   the maintainer's own words (and he happens to speak only while the agent is busy).
            #   The archive path always had a text_of fallback; these live paths missed it -- now added.
            content = text_of(msg)
            if not content.strip():
                continue
            kind = classify(content)
            # * 2026-11-15: same as extract_turns -- interleaved system injections open no new turn.
            #   Archives carry no senderIsOwner, so only prefixes work; the criteria match live.
            if kind == "system" and not content.lstrip().startswith(SYS_TURN_OPENERS):
                continue
            done = close()
            if done:
                yield done
            event_id = str(event.get("id") or f"line{line_no}")
            current = (event_id, int(ts) if ts else 0, kind, content)
            parts = []
        elif role == "assistant":
            if current is None:
                continue
            text = text_of(msg)
            if not text.strip():
                continue
            parts.append((int(ts) if ts else 0, text))

    done = close()
    if done:
        yield done


def collect_archives(cfg: dict, host_id: str, base: str, dry_run: bool, verbose: bool,
                     fp: dict | None = None, seen: dict | None = None,
                     counters: dict | None = None) -> dict:
    """Recover turns the gateway has already rotated out of its live stores.

    This is the only path that reaches months the live collector cannot see: the
    gateway compacts and deletes sessions, and what survives is these JSONL
    archives.  They are read once, keyed exactly like live rows, so re-running is a
    no-op and nothing is counted twice.
    """
    stats = {"documents": 0, "turns": 0, "inserted": 0, "updated": 0, "errors": 0}
    con_mysql = None if dry_run else connect(cfg)
    rows: list[tuple] = []
    replies: list[tuple] = []

    for agent_id, session_id, session_key, label, lines in iter_archive_documents(
            base, fp=fp, seen=seen, counters=counters):
        stats["documents"] += 1
        for event_id, ts_ms, kind, content, parts in extract_turns_loose(lines):
            stats["turns"] += 1
            uid = make_msg_uid(host_id, event_id, session_id, 0, ts_ms, content)
            rows.append((
                uid, host_id, PLATFORM, agent_id, session_id, session_key, label,
                kind, content, len(content),
                hashlib.md5(content.encode("utf-8")).hexdigest(),
                to_local(ts_ms) if ts_ms else None, "archive",
            ))
            if parts:
                body = REPLY_SEPARATOR.join(text for _ts, text in parts)
                first_ts = parts[0][0] or ts_ms
                replies.append((
                    body, len(body), hashlib.md5(body.encode("utf-8")).hexdigest(),
                    to_local(first_ts) if first_ts else None, len(parts), uid, "",
                ))

    if not dry_run and con_mysql is not None:
        stats["inserted"] = _write_archive_rows(con_mysql, rows)
        stats["updated"] = _flush_replies(con_mysql, [r[:6] + (r[2],) for r in replies]) \
            if replies else 0

    if verbose:
        extra = ""
        if counters:
            extra = (f" files={counters.get('files', 0)} "
                     f"skipped={counters.get('files_skipped', 0)}"
                     f" blobs_skipped={counters.get('blobs_skipped', 0)}")
        print(f"  archives: documents={stats['documents']} turns={stats['turns']} "
              f"inserted={stats['inserted']} replies_updated={stats['updated']}{extra}")
    if con_mysql is not None:
        con_mysql.close()
    return stats


ARCHIVE_INSERT_SQL = """
INSERT IGNORE INTO agx_user_message
  (msg_uid, host_id, platform, agent_id, session_id, session_key, session_label,
   role, msg_kind, content, content_len, content_hash, msg_at, source)
VALUES (%s,%s,%s,%s,%s,%s,%s,'user',%s,%s,%s,%s,%s,%s)
"""


def _write_archive_rows(con_mysql, rows: list[tuple]) -> int:
    inserted = 0
    with con_mysql.cursor() as cur:
        for start in range(0, len(rows), FLUSH_EVERY):
            chunk = rows[start:start + FLUSH_EVERY]
            cur.executemany(ARCHIVE_INSERT_SQL, chunk)
            inserted += max(cur.rowcount, 0)
    con_mysql.commit()
    return inserted


def collect_trajectories(cfg: dict, host_id: str, base: str, dry_run: bool, verbose: bool,
                         fp: dict | None = None, seen: dict | None = None,
                         counters: dict | None = None) -> dict:
    """Recover turns from ``*.trajectory.jsonl`` prompt snapshots.

    A snapshot is the compiled message list that was actually sent to the model, so
    it holds turns that compaction has since replaced with a summary.  The same
    history appears in every snapshot of a session, and the same turn already exists
    from the live store or an archive - so a turn is skipped when its question text
    is already known for that session.  That dedupe is by text, which means two
    identical questions inside one session collapse into one row; the alternative
    (re-importing every snapshot) would multiply every turn by its snapshot count.

    Snapshots carry no per-message timestamp, so ``msg_at`` is the snapshot time -
    an upper bound.  Rows are marked ``source='trajectory'`` precisely so that
    approximation stays visible and can be re-queried rather than trusted blindly.
    """
    stats = {"documents": 0, "snapshots": 0, "turns": 0, "inserted": 0, "updated": 0,
             "errors": 0}
    con_mysql = None if dry_run else connect(cfg)
    known: set[tuple[str, str]] = set()
    if con_mysql is not None:
        with con_mysql.cursor() as cur:
            cur.execute("SELECT session_id, content_hash FROM agx_user_message")
            known = {(row[0], row[1]) for row in cur.fetchall()}

    rows: list[tuple] = []
    replies: list[tuple] = []
    keys: dict[str, str] = {}
    for agent_id, store_path in iter_stores(base):
        sessions_dir = os.path.join(os.path.dirname(os.path.dirname(store_path)), "sessions")
        for path in sorted(glob.glob(os.path.join(sessions_dir, "*.trajectory.jsonl"))):
            if counters is not None:
                counters["files"] = counters.get("files", 0) + 1
            sig = file_signature(path)
            if fp is not None and sig and fp.get(f"traj::{path}") == sig:
                if counters is not None:
                    counters["files_skipped"] = counters.get("files_skipped", 0) + 1
                continue
            if seen is not None and sig:
                seen[f"traj::{path}"] = sig
            stats["documents"] += 1
            try:
                handle = open(path, encoding="utf-8", errors="replace")
            except OSError:
                stats["errors"] += 1
                continue
            with handle as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(event, dict) or event.get("type") != "prompt.submitted":
                        continue
                    snapshot = event.get("data") or {}
                    messages = snapshot.get("messages")
                    if not isinstance(messages, list):
                        continue
                    stats["snapshots"] += 1
                    session_id = event.get("sessionId") or ""
                    ts_ms = parse_iso_ms(event.get("ts"))
                    if session_id not in keys:
                        keys[session_id] = ""
                    counters: dict[str, int] = {}

                    current = None
                    parts: list[str] = []

                    def close():
                        if current is None:
                            return
                        question, kind, digest = current
                        key = (session_id, digest)
                        if key in known:
                            return
                        occurrence = counters.get(digest, 0)
                        counters[digest] = occurrence + 1
                        known.add(key)
                        stats["turns"] += 1
                        uid = f"{host_id}:traj:{session_id}:{digest[:12]}:{occurrence}"
                        rows.append((
                            uid, host_id, PLATFORM, agent_id, session_id, keys[session_id], "",
                            kind, question, len(question), digest,
                            to_local(ts_ms) if ts_ms else None, "trajectory",
                        ))
                        if parts:
                            body = REPLY_SEPARATOR.join(parts)
                            replies.append((body, len(body),
                                            hashlib.md5(body.encode("utf-8")).hexdigest(),
                                            to_local(ts_ms) if ts_ms else None,
                                            len(parts), uid, ""))

                    for msg in messages:
                        if not isinstance(msg, dict):
                            continue
                        role = msg.get("role")
                        if role == "user":
                            content = msg.get("content")
                            if not isinstance(content, str):
                                content = text_of(msg)
                            if not content.strip():
                                continue
                            kind = classify(content)
                            # * 2026-11-15: same as extract_turns -- interleaved system injections open no new turn.
                            if (kind == "system"
                                    and not content.lstrip().startswith(SYS_TURN_OPENERS)):
                                continue
                            close()
                            current = (content, kind,
                                       hashlib.md5(content.encode("utf-8")).hexdigest())
                            parts = []
                        elif role == "assistant" and current is not None:
                            text = text_of(msg)
                            if text.strip():
                                parts.append(text)
                    close()

    if con_mysql is not None and rows:
        stats["inserted"] = _write_archive_rows(con_mysql, rows)
        if replies:
            stats["updated"] = _flush_replies(con_mysql, [r[:6] + (r[2],) for r in replies])
    if con_mysql is not None:
        con_mysql.close()

    if verbose:
        print(f"  trajectories: documents={stats['documents']} snapshots={stats['snapshots']} "
              f"turns={stats['turns']} inserted={stats['inserted']}")
    return stats


def backfill_session_keys(cfg, base: str, dry_run: bool = False) -> int:
    """Fill ``agx_user_message.session_key`` for rows written before a mapping existed.

        ``session_key`` is written once at INSERT and ``INSERT IGNORE`` never updates it,
        so a row collected while the source store had no mapping stays blank forever.
        Measured 2026-11-14: of 6,144 human rows, 3,033 (49.4%) had a blank session_key,
        and the UI's history list filters by session_key -> half of the human conversations are invisible in every session.

        Mapping sources: session_nodes (current sessions) + session_windows (old session_ids after rotation,
        measured at 100% coverage) + session_transcript_archives (deleted sessions).
    """
    con_mysql = connect(cfg)
    with con_mysql.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT agent_id, session_id FROM agx_user_message "
            "WHERE session_key='' OR session_key IS NULL"
        )
        orphans = list(cur.fetchall())
    if not orphans:
        con_mysql.close()
        print("  backfill: 无孤儿行")
        return 0

    wanted: dict[str, set[str]] = {}
    for agent_id, sid in orphans:
        wanted.setdefault(agent_id, set()).add(sid)

    updates: list[tuple[str, str, str]] = []
    for agent_id, path in iter_stores(base):
        sids = wanted.get(agent_id)
        if not sids:
            continue
        try:
            store = open_store(path)
        except sqlite3.Error:
            continue
        try:
            meta = load_session_meta(store)
        finally:
            store.close()
        for sid in sids:
            key = meta.get(sid, ("", ""))[0]
            if key:
                updates.append((key, agent_id, sid))

    resolved = sum(1 for _ in updates)
    print(f"  backfill: 孤儿会话组 {len(orphans)}，可解析 {resolved}，不可解析 {len(orphans)-resolved}")
    if dry_run or not updates:
        con_mysql.close()
        return 0

    with con_mysql.cursor() as cur:
        cur.executemany(
            "UPDATE agx_user_message SET session_key=%s "
            "WHERE agent_id=%s AND session_id=%s "
            "AND (session_key='' OR session_key IS NULL)",
            updates,
        )
    con_mysql.commit()

    with con_mysql.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM agx_user_message "
            "WHERE session_key='' OR session_key IS NULL"
        )
        left = cur.fetchone()[0]
    con_mysql.close()
    print(f"  backfill: 已回填 {resolved} 组，剩余无 session_key 的行 {left}（源库已无映射）")
    return resolved


def backfill_from_owner(cfg, host_id: str, base: str, dry_run: bool = False) -> int:
    """Fill ``from_owner`` for rows collected before the column existed.

        * 2026-11-14: only the live source (``transcript_events``) can be backfilled -- the archived/trace JSONL
          had ``__openclaw`` stripped by the gateway, so that part stays NULL forever; that is reality, not laziness.

        Matching uses ``msg_uid`` (= ``{host}:{md5(session|ts|content)[:16]}``),
        so it depends on neither the cursor nor ``seq`` -- the same message at several seqs lands on one uid.
    """
    con = connect(cfg)
    with con.cursor() as cur:
        cur.execute(
            "SELECT msg_uid FROM agx_user_message "
            "WHERE source='transcript_events' AND from_owner IS NULL"
        )
        pending = {r[0] for r in cur.fetchall()}
    if not pending:
        con.close()
        print("  backfill-from-owner: 无待补行")
        return 0

    updates: list[tuple[int, str]] = []
    seen: set[str] = set()
    for _agent_id, path in iter_stores(base):
        try:
            store = open_store(path)
        except sqlite3.Error:
            continue
        try:
            for session_id, raw in store.execute(
                    "SELECT session_id, event_json FROM transcript_events"
                    " WHERE event_json LIKE '%\"role\":\"user\"%'"):
                try:
                    event = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                msg = event.get("message") or {}
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                # * 2026-11-17: the code used to require content to be a str, while OpenClaw stores
                #   messages like a queued-user-message wrapper followed by human words, or "Continue the OpenClaw
                #   runtime event.", with content as a block array
                #   ([{"type":"text","text":"..."}]) -> the whole row was dropped.
                #   Measured: of 83 DBs / 7,937 user messages, 508 were like this, 280 of them
                #   the maintainer's own words (and he happens to speak only while the agent is busy).
                #   The archive path always had a text_of fallback; these live paths missed it -- now added.
                content = text_of(msg)
                if not content.strip():
                    continue
                ts = msg.get("timestamp")
                if ts is None:
                    ts = parse_iso_ms(event.get("timestamp"))
                if ts is None:
                    continue
                uid = make_msg_uid(host_id, str(event.get("id") or ""),
                                   session_id, 0, int(ts), content)
                if uid in pending and uid not in seen:
                    flag = owner_flag(msg)
                    if flag is not None:
                        seen.add(uid)
                        updates.append((flag, uid))
        finally:
            store.close()

    known = sum(1 for _ in updates)
    print(f"  backfill-from-owner: 待补 {len(pending)}，可判定 {known}，无标记 {len(pending)-known}")
    if dry_run or not updates:
        con.close()
        return 0

    with con.cursor() as cur:
        cur.executemany(
            "UPDATE agx_user_message SET from_owner=%s "
            "WHERE msg_uid=%s AND from_owner IS NULL",
            updates,
        )
    con.commit()
    with con.cursor() as cur:
        cur.execute(
            "SELECT from_owner, COUNT(*) FROM agx_user_message GROUP BY from_owner"
        )
        dist = {str(k): int(v) for k, v in cur.fetchall()}
    con.close()
    print(f"  回填完成，分布 1={dist.get('1',0)} 0={dist.get('0',0)} NULL={dist.get('None',0)}")
    return known


# --------------------------------------------------------------------------- #
# invariants
# --------------------------------------------------------------------------- #
# * 2026-11-14 (a question from ui_lead):
#   "next time something breaks, is there anything that catches it without depending on me happening to look?"
#
# All four rounds of errors were found only by "the numbers did not add up afterwards" (354 -> 343, 233 vs 234, a missed prefix in 8 rows).
# That is result checking, not process prevention. The two invariants below turn "what got fixed today" into something every collection self-checks:
# if a criterion is quietly changed or the upstream mapping breaks, a WARN appears in the log immediately, without anyone looking.
#
# Note: this only checks, it never auto-fixes -- auto-fixing would also mask "we found a problem".
INVARIANTS = (
    (
        "human_from_owner_zero",
        "msg_kind='human' AND from_owner=0",
        "human 里出现了 from_owner=0：classify 没压住系统注入，或 from_owner 语义被改了",
    ),
    (
        "recent_orphan_session_key",
        "msg_at >= '2026-10-16' AND (session_key='' OR session_key IS NULL)",
        "10-16 之后的会话又没拿到 session_key：session_windows 映射可能断了",
    ),
)


def check_invariants(con_mysql, verbose: bool = True) -> list[tuple[str, int]]:
    """Return (name, count) for every invariant that is currently violated.

        * Violations are **always** printed (to stderr); ``verbose`` only controls the "all ok" line.
          Reason: this function exists to cry out when nobody is watching --
          if it stayed silent in quiet mode it would only work when run by hand, i.e. no protection at all.
    """
    bad: list[tuple[str, int]] = []
    for name, where, why in INVARIANTS:
        try:
            with con_mysql.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM agx_user_message WHERE {where}")
                n = int(cur.fetchone()[0] or 0)
        except Exception as exc:                                # noqa: BLE001
            print(f"  ! invariant {name}: 查询失败 ({exc})", file=sys.stderr)
            continue
        if n:
            bad.append((name, n))
            print(f"  ! INVARIANT VIOLATED {name}: {n} 行 —— {why}", file=sys.stderr)
    if verbose and not bad:
        print(f"  invariants: {len(INVARIANTS)}/{len(INVARIANTS)} ok")
    return bad


def _pass_one_base(cfg, host_id, base, dry_run, verbose, replies, archives,
                   trajectories, no_fp, fp, replay) -> int:
    """One full pass over a single OpenClaw profile root. Returns the error count."""
    tag = f"[{os.path.basename(os.path.dirname(base))}] " if verbose else ""
    errors = 0

    started = time.time()
    try:
        stats = collect_once(cfg, host_id, base, dry_run, verbose, replay=replay)
    except Exception as exc:  # noqa: BLE001 - surface everything, keep watch mode alive
        print(f"  ! {tag}collection failed: {exc}", file=sys.stderr)
        return 1

    elapsed = time.time() - started
    print(
        f"  {tag}agents={stats['agents']} touched_sessions={stats['sessions']} "
        f"owner_msgs={stats['kept']} inserted={stats['inserted']} "
        f"errors={stats['errors']} {elapsed:.2f}s"
    )
    errors += stats["errors"]
    if verbose:
        for agent_id, kept in sorted(stats["per_agent"], key=lambda x: -x[1]):
            print(f"    {kept:>6}  {agent_id}")

    # Replies run after the questions are in: a reply can only attach to a row
    # that already exists, and a question whose answer has not arrived yet simply
    # stays unfilled until the next pass.
    if replies:
        rstarted = time.time()
        try:
            rstats = collect_replies(cfg, host_id, base, dry_run, verbose)
            print(
                f"  {tag}replies: turns={rstats['turns']} with_reply={rstats['with_reply']} "
                f"updated={rstats['updated']} orphans={rstats['orphan']} "
                f"errors={rstats['errors']} {time.time() - rstarted:.2f}s"
            )
            errors += rstats["errors"]
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {tag}reply collection failed: {exc}", file=sys.stderr)
            errors += 1

    # Archives last: they add months the live stores no longer hold, and the rows
    # they produce are keyed like live rows, so overlap costs nothing.
    if archives:
        astarted = time.time()
        a_seen: dict[str, str] = {}
        a_counters: dict[str, int] = {}
        try:
            astats = collect_archives(cfg, host_id, base, dry_run, verbose,
                                      fp=None if no_fp else fp, seen=a_seen,
                                      counters=a_counters)
            if not dry_run and not no_fp:
                fp.update(a_seen)
            print(
                f"  {tag}archives: documents={astats['documents']} turns={astats['turns']} "
                f"inserted={astats['inserted']} replies_updated={astats['updated']} "
                f"skipped={a_counters.get('files_skipped', 0)} "
                f"errors={astats['errors']} {time.time() - astarted:.2f}s"
            )
            errors += astats["errors"]
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {tag}archive recovery failed: {exc}", file=sys.stderr)
            errors += 1

    if trajectories:
        tstarted = time.time()
        t_seen: dict[str, str] = {}
        t_counters: dict[str, int] = {}
        try:
            tstats = collect_trajectories(cfg, host_id, base, dry_run, verbose,
                                          fp=None if no_fp else fp, seen=t_seen,
                                          counters=t_counters)
            if not dry_run and not no_fp:
                fp.update(t_seen)
            print(
                f"  {tag}trajectories: snapshots={tstats['snapshots']} turns={tstats['turns']} "
                f"inserted={tstats['inserted']} replies_updated={tstats['updated']} "
                f"skipped={t_counters.get('files_skipped', 0)} "
                f"{time.time() - tstarted:.2f}s"
            )
            errors += tstats["errors"]
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {tag}trajectory recovery failed: {exc}", file=sys.stderr)
            errors += 1

    return errors


def run(cfg, host_id, bases, dry_run, watch, interval, verbose, replies=True, archives=True,
        trajectories=True, no_fp=False, replay=False) -> int:
    """Poll every OpenClaw profile root on this host, forever or once.

    A host can run several gateways (one profile each). Iterating ``bases`` here
    rather than spawning one collector per profile keeps a single fingerprint
    file, a single DB connection pool and — more importantly — a single log, so
    "did this host report?" stays a one-line question.
    """
    if isinstance(bases, str):
        bases = [bases]
    label = "DRY-RUN" if dry_run else "WRITE"
    print(f"[{label}] host={host_id} bases={len(bases)}")
    for b in bases:
        print(f"  root: {b}")
    # Signatures survive restarts so a fresh process does not re-parse 844 MB of
    # unchanged archives.  Never written on a dry run: marking rows as "seen"
    # without writing them would make the next real pass skip them for good.
    fp = {} if no_fp else load_fingerprints()
    while True:
        total_errors = 0
        for base in bases:
            total_errors += _pass_one_base(
                cfg, host_id, base, dry_run, verbose, replies, archives,
                trajectories, no_fp, fp, replay,
            )

        # Persist signatures only after every stage above has run without raising,
        # so a crash mid-pass retries those files next time instead of skipping them.
        if not dry_run and not no_fp:
            save_fingerprints(fp)

        if not watch:
            return 0 if total_errors == 0 else 1
        time.sleep(interval)


def main() -> int:
    ap = argparse.ArgumentParser(description="Collect owner messages from OpenClaw stores into MySQL.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run one pass and exit (default)")
    mode.add_argument("--watch", action="store_true", help="stay resident and poll")
    mode.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    mode.add_argument("--reclassify", action="store_true", help="re-derive msg_kind for stored rows")
    ap.add_argument("--backfill-session-keys", action="store_true",
                    help="fill empty session_key from the store's session map" + " (combine with --dry-run to preview)")
    ap.add_argument("--backfill-from-owner", action="store_true",
                    help="fill from_owner from __openclaw.senderIsOwner (live rows only)")
    ap.add_argument("--check-invariants", action="store_true",
                    help="verify the data invariants today's fixes established (exit 1 if violated)")
    ap.add_argument("--interval", type=float, default=2.0, help="poll interval seconds in --watch (default 2)")
    ap.add_argument("--config", default=None, help="agx_config.json path")
    ap.add_argument("--openclaw-base", default=None, help="OpenClaw agents dir")
    ap.add_argument("--host-id", default=None, help="override host_id from config")
    ap.add_argument("-v", "--verbose", action="store_true", help="per-agent breakdown")
    ap.add_argument("--no-replies", action="store_true",
                    help="collect questions only, skip the assistant replies")
    ap.add_argument("--no-archives", action="store_true",
                    help="skip the rotated transcripts / archive blobs (no history recovery)")
    ap.add_argument("--no-trajectories", action="store_true",
                    help="skip trajectory prompt snapshots")
    ap.add_argument("--no-fingerprints", action="store_true",
                    help="ignore stored file signatures and re-parse every archive "
                         "(use after wiping rows, or to force a full re-scan)")
    ap.add_argument("--replay", action="store_true",
                    help="ignore the stored seq cursor and re-scan every session from 0 "
                         "(back-fills history after a collection-rule change; "
                         "existing rows are skipped by msg_uid, cursors never go back)")
    args = ap.parse_args()

    cfg_path = find_config(args.config)
    cfg = load_mysql(cfg_path)
    host_id = args.host_id or cfg.get("host_id") or os.uname().nodename
    base = openclaw_base(args.openclaw_base)

    if args.reclassify:
        return reclassify(connect(cfg), dry_run=False)

    if args.backfill_session_keys:
        backfill_session_keys(cfg, base, dry_run=args.dry_run)
        return 0

    if args.backfill_from_owner:
        backfill_from_owner(cfg, host_id, base, dry_run=args.dry_run)
        return 0

    if args.check_invariants:
        con = connect(cfg)
        bad = check_invariants(con)
        con.close()
        return 1 if bad else 0

    return run(cfg, host_id, openclaw_bases(args.openclaw_base), args.dry_run, args.watch, args.interval, args.verbose,
               replies=not args.no_replies, archives=not args.no_archives,
               trajectories=not args.no_trajectories,
               no_fp=args.no_fingerprints, replay=args.replay)


if __name__ == "__main__":
    sys.exit(main())
