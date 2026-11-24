"""
AgentGeeX core functions — no FastMCP dependency.

Shared by mcp_server.py (MCP tools) and agx_watchdog.py (the watchdog).
Depends only on redis and the stdlib (json, os, uuid, datetime).

All database access goes through common.mysql.UsingMysql, driven by agx_config.json.
"""

import json
import time
import uuid
from datetime import datetime

import redis as redis_mod
from redis import Redis

from config_loader import config

# -----------------------------------------------------------
# environment configuration
# -----------------------------------------------------------

REDIS_HOST = config.redis.get("host", "127.0.0.1")
REDIS_PORT = int(config.redis.get("port", 6379))
REDIS_PASSWORD = config.redis.get("password", None)
REDIS_USERNAME = config.redis.get("username", "default")
REDIS_DB = int(config.redis.get("db", 0))

# Assistant file root: an assistant's record/doc outputs all land in [the owner's own workspace]
# (see docs/agent_docs, assistant file convention).
#
# * Design principle (Lao Feng, 2026-10-10): [never hard-code an absolute path prefix in code].
#   The platform evolves — where agentgeex is installed and what work_dir is are all [runtime variables],
#   so no fixed agents-directory prefix may be assumed. Hence the split in two parts:
#     base    : the runtime base directory (config.work_dir; decided by the deployment environment)
#     rel_tpl : the relative structure template (config.assistant_files_root_rel_template; contains {agent_id})
#   Resolution = normpath(base + "/" + rel_tpl) — the prefix always comes from runtime; the code holds no absolute prefix.
#
# * Original defect (2026-10-10): the old implementation used a [module-level constant] derived from this
#   file's location = the repo root, while the MCP process is [shared] (one process serves every Agent)
#   -> that constant always equalled the AgentGeeX kernel repo root, so every Agent's assistant files
#   were written into the AgentGeeX repo (out of scope / polluting git).
#   Measured incident: aifr_pm's assistant jev_research wrote into agentgeex's kernel repo.
#   Fix: (1) resolve dynamically per [caller agent_id] (in a shared process each lands in its own workspace)

#        (2) the base directory comes from the runtime config; the code keeps only the [relative structure].
AGX_AGENTS_DIR = "agx_agents"
AGX_HOST_SUBDIR = "host"


def _resolve_assistant_rel_template() -> str:
    """Return the assistant files' [relative-to-base template] (contains the {agent_id} placeholder, no slug).

    Priority:
      1. config.assistant_files_root_rel_template (supports {agent_id})
      2. fallback: agx_agents/{agent_id}/workspace/assistants
         (the host/ segment is appended by assistant_files_root_for() for host-type agents)

    * The directory name is no longer derived from agent_type — that was the root cause of
      "the assistant is in the wrong place", located on 2026-11-12.
    """
    # Relative structure template (path relative to base; platform convention: agx_agents/{agent_id}/workspace)
    try:
        _tpl = config.get("assistant_files_root_rel_template", "") if hasattr(config, "get") else ""
    except Exception:
        _tpl = ""
    if _tpl:
        return str(_tpl).strip("/")

    # 1) explicit config (new; supports {agent_id})
    # 2) fallback: fixed agx_agents
    # * fixed 2026-11-12: the old second tier was the platform convention {agent_type}_agents -> with agent_type=openclaw
    #   that yielded openclaw_agents/..., while the real directories on disk are always agx_agents (install.py's
    #   AGENTS_DIR is hard-coded to agx_agents for every platform; no longer split per platform).
    #   Consequence (measured 2026-11-12): Mac's config lacked assistant_files_root_rel_template
    #   -> fell through to this fallback -> agx_assistant_profile/drift created assistant skeleton
    #   directories under ~/.agx/openclaw_agents/ (an empty daily/notes/outputs set each for agx_maint
    #   and book_promo), while reading the real path found nothing -> "the assistant is in the wrong place".
    #   "Derive the directory from agent_type" is itself a drift source: the directory name is an install-time
    return f"{AGX_AGENTS_DIR}/{{agent_id}}/workspace/assistants"


def _resolve_assistant_base() -> str:
    """Return the assistant files' [base directory] (runtime; no {agent_id}).

    Priority:
      1. config.assistant_files_base
      2. config.work_dir (the AgentGeeX runtime root)
      3. fallback: empty string (resolution degrades to a relative path; the caller decides against what)
    """
    import os as _os
    try:
        _b = config.get("assistant_files_base", "") if hasattr(config, "get") else ""
    except Exception:
        _b = ""
    if not _b:
        try:
            _b = config.get("work_dir", "") if hasattr(config, "get") else ""
        except Exception:
            _b = ""
    return _os.path.expanduser(str(_b)).rstrip("/") if _b else ""


ASSISTANT_FILES_REL_TEMPLATE = _resolve_assistant_rel_template()
ASSISTANT_FILES_BASE = _resolve_assistant_base()


def assistant_files_root_for(agent_id: str) -> str:
    """* Resolve the assistant file base directory by [the owner agent_id] (no slug).

    Resolution = normpath(base + "/" + rel_template with agent_id substituted).
    base comes from the runtime config (no hard-coded absolute prefix); the structure comes from the relative template.
    When agent_id is empty the placeholder is left unsubstituted (a path containing {agent_id} is returned) to avoid landing in the wrong place.
    """
    import os as _os
    aid = (agent_id or "").strip()
    rel = ASSISTANT_FILES_REL_TEMPLATE.replace("{agent_id}", aid)
    #   constant and should not be inferred back from a mutable agent_type. Hence agx_agents is pinned.
    # * A host-type Agent's real workspace is agx_agents/host/{id}/workspace
    #   (same rule as install.py and mcp_server._agent_dir). If the template has no host/ segment,
    if aid.startswith("agx_host"):
        _parts = rel.split("/")
        if AGX_HOST_SUBDIR not in _parts:
            try:
                _parts.insert(_parts.index(aid), AGX_HOST_SUBDIR)
                rel = "/".join(_parts)
            except ValueError:
                pass
    if ASSISTANT_FILES_BASE:
        return _os.path.normpath(f"{ASSISTANT_FILES_BASE}/{rel}")
    #   it is appended here; otherwise the assistant directory would land on agx_agents/agx_host_x/..., which does not exist.
    return _os.path.normpath(rel) if not rel.startswith("/") else rel


def assistant_files_root_for_slug(agent_id: str, slug: str) -> str:
    """Resolve an assistant file root by (owner agent_id, slug) — including the slug."""
    base = assistant_files_root_for(agent_id).rstrip("/")
    s = (slug or "").strip()
    return f"{base}/{s}" if s else base


# no base: degrade to a relative path (the caller decides relative to what)
# legacy-name compatibility (only so unmigrated imports do not break; no longer used to resolve concrete assistant paths)
ASSISTANT_FILES_ROOT = ASSISTANT_FILES_REL_TEMPLATE

# -----------------------------------------------------------
# note: this is now a [relative structure] rather than an absolute path — the absolute prefix comes from the runtime base.
# -----------------------------------------------------------


def execute_sql(query, params=None, commit=False):
    """Execute a SQL query. commit=True returns lastrowid, otherwise fetchall."""
    from common.mysql.mysqlservice import executeSql

    return executeSql(query, params, commit)


def execute_sql_rowcount(query, params=None):
    """Execute UPDATE/DELETE and return the affected row count (used by atomic guards).

    * 2026-10-25: executeSql(commit=True) only returns lastrowid, so an UPDATE cannot get the
      affected row count. But "two watchdogs racing to activate" must rely on rowcount to know
      who won, hence this extra entry point.
      MySQL returns [rows actually changed] by default: 0 when the WHERE matches nothing (someone
      else already changed it).
    """
    from common.mysql.mysqlservice import UsingMysql

    with UsingMysql(commit=True, log_time=False) as um:
        um.cursor.execute(query, params or ())
        return um.cursor.rowcount


# -----------------------------------------------------------
# database access
# -----------------------------------------------------------

_GLOBAL_REDIS_POOL = redis_mod.ConnectionPool(
    host=config.redis.get("host", "127.0.0.1"),
    port=int(config.redis.get("port", 6379)),
    db=int(config.redis.get("db", 0)),
    username=config.redis.get("username", "default"),
    password=config.redis.get("password", ""),
    decode_responses=True,
    max_connections=3,  # core daemon workload; 15 spare slots is plenty
    socket_timeout=5,  # 5-second command timeout, so a distributed task cannot hang on a stuck network
    socket_connect_timeout=5,  # 5-second connect timeout, so a network blip reconnects quickly
    health_check_interval=10,
    socket_keepalive=True,  # enable system-level TCP keepalive
)


def get_redis_client(db: int = None) -> Redis:
    """Return the Redis client.

    * A single connection pool (multiple or dual pools are strictly forbidden). db is fixed in the
      _GLOBAL_REDIS_POOL kwargs; the db parameter is deprecated (kept for signature compatibility).
      To switch databases, change redis.db in the config file.
    """
    return redis_mod.Redis(connection_pool=_GLOBAL_REDIS_POOL)


# ── automatic connection-pool retry ──

_POOL_RETRY_MAX = 3
_POOL_RETRY_DELAYS = [2, 5, 10]  # exponential backoff


def _is_pool_error(e: Exception) -> bool:
    """Whether this is a recoverable connection-pool error (must not be surfaced to the upper Agent)."""
    msg = str(e).lower()
    return any(
        kw in msg
        for kw in (
            "pool",
            "too many connections",
            "busy",
            "can't connect",
            "connection refused",
            "connection reset",
            "max_connections",
            "connection pool",
            "cannot acquire",
        )
    )


def _retry_op(op_name: str, fn, max_retries: int = _POOL_RETRY_MAX):
    """Wrap a retryable operation: connection-pool errors are retried with exponential backoff, others are re-raised."""
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            if _is_pool_error(e) and attempt < max_retries - 1:
                delay = _POOL_RETRY_DELAYS[min(attempt, len(_POOL_RETRY_DELAYS) - 1)]
                time.sleep(delay)
                continue
            raise
    # unreachable


def _core_log(msg: str, level: str = "INFO") -> None:
    """Lightweight logger for this module (* log_setup is deliberately not imported, keeping agx_core framework-free).

    Why not log_setup: this file's header states "depends only on redis, stdlib", and the module is
    imported by both MCP and the watchdog — importing log_setup would initialize one logging setup
    each, producing duplicate handlers and mislocated log folders. So it writes to stderr only and
    lets the caller's logging chain take over.
    """
    try:
        import sys as _sys
        print(f"[{level}] [agx_core] {msg}", file=_sys.stderr, flush=True)
    except Exception:
        pass


def agx_generate_uuid(caller_agent_id: str = "", prefix: str = "agx", infix: str = "") -> str:
    """Generate a prefixed UUID that follows the AgentGeeX naming convention.

    Args:
        prefix: resource-type prefix (proj/task/run/sess/agt/host)
        infix:  optional middle segment such as task_labs, producing run_{task_lab}_{uuid}
    """
    clean_prefix = prefix.lower().strip().rstrip("_") + "_"
    unique_id = uuid.uuid4().hex[:12]
    if infix and infix.strip():
        clean_infix = infix.lower().strip().strip("_")
        return f"{clean_prefix}{clean_infix}_{unique_id}"
    return f"{clean_prefix}{unique_id}"


# -----------------------------------------------------------
# cache key prefix constants
# -----------------------------------------------------------

CACHE_PREFIX = {
    "project": "agx:entity:project:",
    "host": "agx:entity:host:",
    "agent": "agx:entity:agent:",
    "task": "agx:entity:task:",
    "execution": "agx:entity:execution:",
    "agent_group": "agx:entity:agent_group:",
    "agent_group_member": "agx:entity:agent_group_member:",
    "notice": "agx:entity:notice:",
}

IDX_PREFIX = {
    "project_all": "agx:idx:project:all",
    "host_all": "agx:idx:host:all",
    "agent_all": "agx:idx:agent:all",
    "task_all": "agx:idx:task:all",
    "execution_all": "agx:idx:execution:all",
    "agent_group_all": "agx:idx:agent_group:all",
    "agent_group_member_all": "agx:idx:agent_group_member:all",
    "notice_all": "agx:idx:notice:all",
    "agent_active": "agx:idx:agent:active",
    "task_active": "agx:idx:task:active",
}

EXEC_STATUS_IDX = "agx:idx:execution:status:"
# * 2026-10-28: every bucket of the status index. It must be the [full set] — list one bucket too
#   few on zrem and the member stays stuck in that bucket forever (measured: run_general_b17d16d81139
#   sat in both the succeeded and unknown buckets). Unknown states must be clearable too, so unknown/cancelled are included.
EXEC_STATUS_BUCKETS = (
    "running", "succeeded", "failed", "pending",
    "draft", "awaiting_confirm", "cancelled", "unknown",
)
EXEC_BY_PARENT = "agx:idx:execution:by_parent:"
EXEC_BY_TASK = "agx:idx:execution:by_task:"

# * Lao Feng, 2026-10-30: the receipt-pending-ack index (hot path for resend decisions)
#   Background: _resend_unacked_receipts used to full-scan the table every 30s
#   (EXPLAIN: type=ALL key=None rows=4025) — about 5ms at 5846 rows. That is not slow now,
#   but it degrades linearly: 100x the rows means 500ms, plus a filesort on the main table.
#   Speed is the secondary reason; the main one is "every decision field is already cached, yet the read path bypasses the cache".
#   So: the write side maintains a zset, the read side takes candidates with ZRANGEBYSCORE, and MySQL degrades to a reconciliation fallback.
#   Member = task_run_uuid; score = epoch seconds of COALESCE(reply_sent_at, finished_at).
RECEIPT_PENDING_IDX = "agx:idx:execution:receipt:pending"


def _receipt_pending_wanted(run_status, needs_reply, reply_read) -> bool:
    """Whether this execution should stay in the "receipt pending ack" index.

    The criteria must match watchdog:_resend_unacked_receipts' SQL exactly, otherwise the index
    and the source of truth each tell their own story (the same class of risk as the 2026-10-28
    "losing an index means going blind"):

        run_status IN ('succeeded','failed') AND needs_reply IN (1,2)
        AND COALESCE(reply_read,0) = 0
    """
    if str(run_status or "").strip() not in ("succeeded", "failed"):
        return False
    try:
        nr = int(needs_reply or 0)
    except Exception:
        nr = 0
    if nr not in (1, 2):
        return False
    try:
        rr = int(reply_read or 0)
    except Exception:
        rr = 0
    return rr != 1


def _receipt_pending_score(reply_sent_at, finished_at) -> float:
    """Index score: the moment the pending-ack window starts (epoch seconds). Falls back to now when missing."""
    import datetime as _dt
    import time as _time

    for v in (reply_sent_at, finished_at):
        if not v:
            continue
        try:
            if isinstance(v, _dt.datetime):
                return v.timestamp()
            return _time.mktime(_time.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S"))
        except Exception:
            continue
    return _time.time()


# -----------------------------------------------------------
# Redis cache sync
# -----------------------------------------------------------


def _build_extra_index(pipe: Redis, entity_type: str, entity_id: str, data: dict):
    """Build the extra index for a specific entity type."""
    if entity_type == "project":
        if data.get("network_id"):
            pipe.sadd(f"agx:idx:project:by_network:{data['network_id']}", entity_id)

    elif entity_type == "host":
        if data.get("network_id"):
            pipe.sadd(f"agx:idx:host:by_network:{data['network_id']}", entity_id)
        if data.get("presence_status") == "1":
            pipe.sadd("agx:idx:host:online", entity_id)
        else:
            pipe.srem("agx:idx:host:online", entity_id)

    elif entity_type == "agent":
        pipe.sadd(IDX_PREFIX["agent_active"], entity_id)
        if data.get("network_id"):
            pipe.sadd(f"agx:idx:agent:by_network:{data['network_id']}", entity_id)

    elif entity_type == "task":
        if data.get("active_status") == "1":
            pipe.sadd(IDX_PREFIX["task_active"], entity_id)
        else:
            pipe.srem(IDX_PREFIX["task_active"], entity_id)
        if data.get("agent_id"):
            pipe.sadd(f"agx:idx:task:by_agent:{data['agent_id']}", entity_id)

    elif entity_type == "execution":
        if data.get("task_id"):
            pipe.sadd(f"{EXEC_BY_TASK}{data['task_id']}", entity_id)
        if data.get("parent_run_uuid"):
            pipe.sadd(f"{EXEC_BY_PARENT}{data['parent_run_uuid']}", entity_id)
        # * fixed 2026-10-28, "losing an index means going blind":
        #   the old code did status = data.get("run_status", "unknown") — as soon as the caller performs a
        #   "partial field refresh" (writing only alert_note / reply_read / ack_state), run_status is absent
        #   from data, so the row is judged unknown:
        #   zrem from buckets like running, then zadd into status:unknown.
        #   But the watchdog finds work with zrange(agx:idx:execution:status:running) ->
        #   a running task got shifted out of view by its own cache refresh and is never woken or alerted again.
        #   Measured evidence: Redis status:unknown held 643 members while MySQL had 0 rows with
        #   run_status='unknown' (the database has no such state at all; it was purely a cache phantom).
        #
        #   Fix: touch the status index only when data really carries run_status; a partial field refresh
        #   always keeps existing membership (and keeps the score too — no more writing 0 when pkid is missing).
        status = (data.get("run_status") or "").strip()
        if status:
            score = data.get("pkid", 0) or 0
            for s in EXEC_STATUS_BUCKETS:
                pipe.zrem(f"{EXEC_STATUS_IDX}{s}", entity_id)
            pipe.zadd(f"{EXEC_STATUS_IDX}{status}", {entity_id: int(score)})

        # * Lao Feng, 2026-10-30: the receipt-pending index follows the same discipline as above —
        #   **touch the index only when all three decision fields are present**.
        #   Reason: a partial field refresh (agx_ack_receipt writes only reply_read/ack_state/needs_reply,
        #   agx_plan_cancel writes only run_status) has no full picture, so deciding membership from
        #   incomplete data inevitably moves the row to the wrong place; the 2026-10-28 "going blind" incident came from exactly this.
        #   When fields are missing keep existing membership; the watchdog's read side self-heals from the real hash contents and reconciles as a fallback.
        #   Note: acknowledgement is a deterministic "move out" event; agx_ack_receipt performs an explicit ZREM separately.
        if all(k in data for k in ("run_status", "needs_reply", "reply_read")):
            if _receipt_pending_wanted(
                data.get("run_status"), data.get("needs_reply"), data.get("reply_read")
            ):
                pipe.zadd(
                    RECEIPT_PENDING_IDX,
                    {entity_id: _receipt_pending_score(
                        data.get("reply_sent_at"), data.get("finished_at"))},
                )
            else:
                pipe.zrem(RECEIPT_PENDING_IDX, entity_id)

    elif entity_type == "agent_group":
        if data.get("network_id"):
            pipe.sadd(f"agx:idx:agent_group:by_network:{data['network_id']}", entity_id)

    elif entity_type == "agent_group_member":
        if data.get("network_id"):
            pipe.sadd(
                f"agx:idx:agent_group_member:by_network:{data['network_id']}",
                entity_id,
            )
        if data.get("group_id"):
            pipe.sadd(
                f"agx:idx:agent_group_member:by_group:{data['group_id']}",
                entity_id,
            )
        if data.get("agent_id"):
            pipe.sadd(
                f"agx:idx:agent_group_member:by_agent:{data['agent_id']}",
                entity_id,
            )

    elif entity_type == "notice":
        if data.get("agent_id"):
            pipe.sadd(f"agx:idx:notice:agent:{data['agent_id']}", entity_id)
        if data.get("project_id"):
            pipe.sadd(f"agx:idx:notice:project:{data['project_id']}", entity_id)
        if data.get("task_id"):
            pipe.sadd(f"agx:idx:notice:task:{data['task_id']}", entity_id)
        if data.get("run_uuid"):
            pipe.sadd(f"agx:idx:notice:run:{data['run_uuid']}", entity_id)


def _sync_entity(entity_type: str, entity_id: str, data: dict):
    """Write the entity into a Redis hash and maintain indexes. Degrades silently when Redis is unavailable."""
    try:
        r = get_redis_client()
        key = f"{CACHE_PREFIX[entity_type]}{entity_id}"
        clean = {k: v for k, v in data.items() if v is not None}
        _NO_CACHE_MD = {
            "agents_md",
            "heartbeat_md",
            "identity_md",
            "soul_md",
            "tools_md",
            "user_md",
        }
        if entity_type == "agent":
            clean = {k: v for k, v in clean.items() if k not in _NO_CACHE_MD}
        str_data = {k: str(v) if not isinstance(v, str) else v for k, v in clean.items()}
        pipe = r.pipeline()
        if str_data:
            pipe.hset(key, mapping=str_data)
        idx_all_key = IDX_PREFIX.get(f"{entity_type}_all")
        if idx_all_key:
            score = data.get("pkid", 0) or 0
            pipe.zadd(idx_all_key, {entity_id: int(score)})
        _build_extra_index(pipe, entity_type, entity_id, data)
        pipe.execute()
    except Exception:
        pass


def _publish_event(
    entity_type: str,
    event: str,
    entity_id: str,
    payload: dict = None,
):
    """Publish a Redis Pub/Sub event."""
    try:
        r = get_redis_client()
        message = {
            "type": entity_type,
            "event": event,
            "entity_id": entity_id,
            "ts": datetime.now().isoformat(),
        }
        if payload:
            message["payload"] = payload
        r.publish("agx:events", json.dumps(message, ensure_ascii=False))
    except Exception:
        pass


# -----------------------------------------------------------
# core: task dispatch
# -----------------------------------------------------------


def _resolve_send_host_id(caller_agent_id: str) -> str:
    """Derive send_host_id (the sender's host) from caller_agent_id."""
    if not caller_agent_id:
        return ""
    try:
        row = execute_sql(
            "SELECT host_id FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
            (caller_agent_id,),
        )
        return (row[0].get("host_id") or "") if row else ""
    except Exception:
        return ""


# -----------------------------------------------------------
# session registration: give every session_id a name (agx_session)
# -----------------------------------------------------------

# session-name suffixes for the conventional special task_labs / session segments
_SESSION_NAME_SUFFIX = {
    "main": "主会话",
    "general": "主会话",
    "universal": "通用会话",
    "watchdog_alert": "看门狗报警",
}


def _sanitize_name_field(value, limit: int = 255) -> str:
    """Normalize a session name/remark: collapse whitespace, truncate."""
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    # collapse runs of whitespace
    text = " ".join(text.split())
    return text[:limit]


def _is_safe_session_key(session_id) -> bool:
    """Validate the "character safety" of a session_key (cross-platform, independent of platform structure).

    Background (2026-10-05): an illegal session_key polluted the DB — a UI-truncated `agent:…main`
    (U+2026 ellipsis) was passed in explicitly as a real key -> stored with zero validation -> spread
    sideways across records.

    * Design principles (multi-platform, Lao Feng's call):
      - Police only "character safety", never "structural format".
      - "Illegal characters" is the greatest common divisor across four platforms: a Claude Code key
        must be a valid UUID (only 0-9a-f-), Codex cannot be specified, OC/DSH are lenient but have
        measurably produced garbage — so `…` / `%` / whitespace / control characters are all
        rejected, which is safe everywhere.
      - "Structure" (agent:xxx / uuid / mapping-table keys) differs per platform; a format regex
        would wrongly kill legitimate CC (UUID) / Codex keys -> so it is never done.

    Legal criteria: non-empty + all printable + no known dirty characters (…, %, whitespace,
    control characters, placeholders).
    """
    if session_id is None:
        return False
    sid = str(session_id)
    if not sid or not sid.strip():
        return False
    # 1) reject control characters (including \n \r \t and U+0000-U+001F, U+007F)
    for ch in sid:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            return False
    # 2) reject any ASCII whitespace (space/tab/newline, etc.)
    if any(c.isspace() for c in sid):
        return False
    # 3) reject the U+2026 ellipsis (the culprit of that incident) plus other common "UI truncation / encoding artifacts"
    #    … U+2026 / % (leftover URL encoding) / the literal \u2026
    for bad in ("\u2026", "%"):
        if bad in sid:
            return False
    # 4) reject zero-width / invisible characters (frequently copied in from UIs)
    for ch in sid:
        o = ord(ch)
        if o in (0x200B, 0x200C, 0x200D, 0xFEFF, 0x00A0):
            return False
    # 5) * 2026-10-19: reject [placeholders] — the same class of pollution as `…` (not a real value).
    #    Measured incident: a caller (an AI agent) copied the masked example `session_key="***"` from the tool docs
    #    -> all asterisks are printable, so the previous four rules let it through -> session_id='***' was written
    #    -> daemon/ws_client could not deliver -> the execution failed outright.
    #    Criteria: all asterisks / three consecutive asterisks / classic ellipsis spellings. Only shapes that are
    #    obviously not identifiers; no structural validation (which would violate the "do not police structure" principle).
    _stripped = sid.strip()
    if set(_stripped) == {"*"}:
        return False
    if "***" in _stripped:
        return False
    if _stripped in ("...", "…", "-", "null", "None", "undefined", "N/A", "n/a"):
        return False
    # 6) 2026-10-22: a blacklist of concrete fake values (raised by aifr_pm, decided by Lao Feng).
    #    Same category as rule 5 — only explicit "not an identifier" values, no structural regex,
    #    so the "police characters, not structure" principle still holds.
    #    Measured background: agents do pass placeholder examples from the docs through as real values,
    #    and `session_key="***"` already polluted a stored row on 2026-10-19.
    _low = _stripped.lower()
    if _low in ("todo", "fixme", "changeme", "replace_me", "replaceme", "your_key_here", "your_session_key", "your_session_key_here", "your_session_id", "session_key_here", "placeholder", "xxx", "xxx_xxx"):
        return False
    # 7) 2026-10-22: angle brackets / curly braces = the character signature of template placeholders.
    #    No platform's real session key contains < > { } (OC: agent:{id}:{x};
    #    CC: a UUID; Codex: cannot be specified), so this is a character-level rule rather than structural validation.
    #    Measured: `agent:<your_agent_id>:main` / `agent:{agent_id}:main`
    #    were all let through before this rule (4/4 True).
    for _ch in ("<", ">", "{", "}"):
        if _ch in _stripped:
            return False
    return True


# ══════════════════════════════════════════════════════════════
# idempotence sentinel for the injected block (fixed 2026-10-22 on Lao Feng's call)
#
# original defect: a "plain business string" was used as the sentinel —
#     if the old session-context marker is in instruction: return instruction
#   so any instruction that merely [mentions] those characters (e.g. "read it from the session-context block")
#   silently skipped the injection -> the agent received an instruction without that block and had to guess.
#   (Reproduced on 2026-10-22: a test instruction mentioning the term lost the block; agx_image found it itself.)
#
# Fix: an unforgeable HTML-comment sentinel. The sentinel is written together with the injected block,
#   and neither a human nor an LLM would naturally write `<!-- agx:session-ctx -->`.
#   The idempotence test changed from "is it mentioned in the body" to "did this function inject it" — semantically correct.
#
# Compatible: records injected by the old version (no sentinel) are recognized by [block start] (injection is always prepended), so nothing is missed or duplicated.
# ══════════════════════════════════════════════════════════════
_SESSION_CTX_SENTINEL = "<!-- agx:session-ctx -->"
_ASSISTANT_HINT_SENTINEL = "<!-- agx:assistant-hint -->"


def _already_injected(instruction: str, sentinel: str, legacy_head: str) -> bool:
    """Idempotence check: has this injection already happened.

    Two criteria (either one means already injected):
      (1) the sentinel string appears anywhere — new-style injection (since 2026-10-22);
      (2) the block starts with legacy_head — old-style injection (no sentinel); since injection is
          always prepended, only the block start is inspected.
         This way a mention of the term in the [middle] of the body is not misjudged (that was
         exactly the old implementation's bug).
    """
    if not instruction:
        return False
    if sentinel in instruction:
        return True
    return instruction.startswith(legacy_head)


def _inject_session_context(session_id: str, instruction: str, run_uuid: str = "") -> str:
    """Prepend a [session context] block to the instruction (a generic block injected for every task).

    Background (Lao Feng, 2026-10-09):
      At dispatch time "the sender's own session id" is not knowable (a caller cannot see which
      session it is in), so the old implementation guessed it afterwards with SQL -> receipts landed
      in the wrong place in multi-session scenarios (assistants / several tasks).
      The fix is explicitness: (1) the message structure carries self_session_id; (2) this generic
      block feeds the session id to the agent (read forcibly on every execution -> it does not rely
      on "memory" and resists forgetting; the same idea as the assistant identity loop).

    * Added 2026-10-13: injection of run_uuid (this execution's id):
      For the same reason, "the task_run_uuid of this execution" is fed to the agent explicitly — for
      tools that need to correlate with this execution (typically the task_run_uuid argument of
      agx_deploy_agent, which decides the deploy-callback target).
      Background: BUG-A2 recurred — the deploy step description never mentioned task_run_uuid, the host
      LLM could only "guess", the call was omitted -> empty callback target -> callback silently lost
      -> the execution stayed running forever. Root cause again "deterministic logic handed to an LLM";
      the same fix applies = feed the parameter explicitly, do not make the LLM guess (read forcibly
      on every execution).

    * Relation to _inject_assistant_hint:
      _inject_assistant_hint carries [assistant identity] (you are an assistant + where your files are
      + go read your profile); this function carries [session id + usage] (generic; every session has
      it). The assistant block's narration of the session id is de-duplicated (the id is not repeated),
      so the two functions each do their own job when stacked.

    * Injection order: this function first, _inject_assistant_hint second.
    * Idempotent: if the instruction already contains the session-context block, nothing is injected.

    Args:
        session_id: the session this execution runs in (= the sender's own session).
        instruction: the original instruction.
    Returns:
        the instruction with the block prepended (returned unchanged when session_id is empty).
    """
    if not session_id or not instruction:
        return instruction or ""
    if _already_injected(instruction, _SESSION_CTX_SENTINEL, "【会话上下文】"):
        return instruction
    _ctx_block = (
        _SESSION_CTX_SENTINEL + "\n"
        "【会话上下文】\n"
        f"你本次执行所在的会话编号是：{session_id}\n"
        "- 这是你「当前所在的会话」，也是本条任务消息的来源会话。\n"
        "- 若你需要给别人派活(agx_dispatch_task / agx_schedule_task)或续接(agx_continue_task)，\n"
        "  请把此编号作为 self_session_id 传入 —— 对方的回执才会回到这里。\n"
        "- 未传 self_session_id 时，回执默认回你的主会话。\n"
        + (f"- 你本次执行的执行编号(task_run_uuid)是：{run_uuid}\n"
           "  若你要调用的工具需要「本次执行的 task_run_uuid」作为关联参数\n"
           "  (如 agx_deploy_agent 的 task_run_uuid —— 它决定部署完成回调的投递目标)，\n"
           "  请原样传入此编号，不要留空、不要自行编造。\n" if run_uuid else "")
        + "--------------------------------------------------\n"
    )
    return _ctx_block + instruction


def _inject_assistant_hint(session_id: str, instruction: str) -> str:
    """If the target session is an [assistant session] (agx_session.is_assistant=1), prepend an
    [assistant identity hint] to the instruction (a pointer, not the full text).

    Background (2026-10-08):
      A main session (agent:{aid}:main) keeps its identity by having AGENTS.md loaded automatically on
      every execution. An assistant (case session) has no such loop — its identity can only rely on
      "memory", and memory is rotated/compacted away.
      Hence this function prepends a hint on [every dispatch] (only "who you are + session id + which
      method to call"), telling the agent to call agx_get_session(session_id) and read its profile
      (x_desc).
      -> the profile is re-read on every execution -> identity is always refreshed, never from memory.

    * Design: inject a "pointer" rather than the "full text" — no context cost, not compacted away;
      the long profile is loaded on demand.
    * Idempotent: if the instruction already contains the assistant identity hint (e.g. an already
      injected continuation), it is not injected again.

    Two call sites (identical behaviour, hence a shared function): _ao_dispatch_task (new) and
    _ao_continue_task (continuation).

    Args:
        session_id: the target session id (used to decide is_assistant).
        instruction: the original instruction.
    Returns:
        the instruction with the hint prepended (unchanged for a non-assistant session).
    """
    if not session_id or not instruction:
        return instruction or ""
    # idempotence guard: do not inject twice when the hint is already present
    if _already_injected(instruction, _ASSISTANT_HINT_SENTINEL, "【助理身份提示】"):
        return instruction
    try:
        _assist_row = execute_sql(
            "SELECT is_assistant, assistant_slug FROM agx_session WHERE session_id = %s",
            (session_id,),
        )
        _is_assistant = bool(_assist_row and _assist_row[0].get("is_assistant"))
        _slug = (_assist_row[0].get("assistant_slug") or "").strip() if _assist_row else ""
    except Exception:
        _is_assistant = False
        _slug = ""
    if not _is_assistant:
        return instruction
    # File root: an assistant's record/doc outputs all land here (see docs/agent_docs, assistant file convention)
    # * fixed 2026-10-10: resolved dynamically by [owner agent_id] (no more module constants);
    #   agent_id is parsed from the prefix of session_id's last segment: agent:{aid}:case_{slug}
    _owner_aid = ""
    if session_id and session_id.startswith("agent:"):
        _owner_aid = session_id.split(":", 2)[1] if len(session_id.split(":", 2)) >= 2 else ""
    _assist_root = assistant_files_root_for_slug(_owner_aid, _slug)
    _hint_file_block = ""
    if _assist_root:
        _hint_file_block = (
            f"你的标识(slug): {_slug}\n"
            f"【你的文件在哪】你的「记录/文档」类产出(日志/笔记/草稿/交付物)一律落到:\n"
            f"  {_assist_root}/\n"
            f"    - daily/    日志   - notes/  笔记/草稿   - outputs/  交付物\n"
            f"项目代码类产出仍落项目仓库(不要在助理文件夹里改代码)。\n"
        )
    _assist_hint = (
        _ASSISTANT_HINT_SENTINEL + "\n"
        "【助理身份提示】\n"
        "你是本会话的常驻助理(assistant)。\n"
        + _hint_file_block
        + "在执行本次任务【之前】，请先调用 agx_get_session("
        f"'{session_id}') 读取你的职责档案(x_desc 字段)。"
        "它说明了你【是谁、负责什么、与谁协作、产出什么、文件落哪】。\n"
        "读完档案后，结合本次指令的上下文判断你该做什么。\n"
        "—— 每次执行都要重读一遍，这是你身份认知的来源。\n"
        "--------------------------------------------------\n"
    )
    return _assist_hint + instruction


# ===========================================================
# assistant profile generator (pure functions, no side effects, unit-testable)
# ===========================================================

# fixed text: the owner identity line for an assistant (identical for every assistant)
_ASSISTANT_IDENTITY_LINE = (
    "  【助理身份】我不是独立 Agent，是本体伸出的手：对本体 100% 听命，不外借。"
)

# baseline work discipline (3 fixed items shared by every assistant; specific items are appended)
_ASSISTANT_DISCIPLINE_BASELINE = (
    "  - 我是助理，不是本体：不改架构级代码，重大变更须先与老冯同步",
    "  - 每次被唤醒，先读本档案确认身份，再结合本次指令判断该做什么",
    "  - 关键决策 / 待办变化，回写 execution.summary 留痕",
)


def build_assistant_profile(
    slug: str,
    assistant_name: str,
    responsibilities=None,
    collaborators=None,
    deliverables=None,
    extra_discipline=None,
    owner_agent_id: str = "",
    owner_agent_name: str = "",
    files_root: str = "",
    session_id: str = "",
) -> str:
    """Build the [assistant profile] x_desc body (template fill: fixed blocks + variable blocks).

    * Fixed items (filled automatically; the human does not supply them):
        - header / who I am (including the owner identity line) / * where my files are / baseline work discipline
    * Variable items (supplied by the human): responsibilities / collaborators / deliverables / discipline-specific items

    Args:
        slug: assistant identifier (e.g. ui_design)
        assistant_name: assistant name (e.g. "UI Design Assistant")
        responsibilities: responsibility list, list[str]
        collaborators: collaborators list, list[str]
        deliverables: deliverable list, list[str]
        extra_discipline: discipline-specific items, list[str] (appended after the baseline)
        owner_agent_id: owner agent_id (defaults to the caller)
        owner_agent_name: owner name (e.g. "AgentGeeX maintainer")
        files_root: absolute assistant file root (defaults to assistant_files_root_for_slug(owner_agent_id, slug),
            i.e. {work_dir}/agx_agents/{owner_agent_id}/workspace/assistants/{slug} under the owner's workspace)
        session_id: * added 2026-10-22. The assistant's [real] session id (e.g. agent:x:asst_foo / agent:x:case_foo).
            **The caller should pass the real value** (looked up from agx_session) rather than rely on
            this function's fallback — the fallback can only guess a prefix.
            When absent it falls back to agent:{owner_agent_id}:asst_{slug} per the current naming convention.

    Returns:
        the complete x_desc text (Markdown style)
    """
    slug = (slug or "").strip()
    assistant_name = (assistant_name or slug).strip()
    owner_agent_id = (owner_agent_id or "").strip()
    owner_agent_name = (owner_agent_name or "AgentGeeX 维护者").strip()
    # * fixed 2026-10-22 (aifr_pm reported P1): it used to hard-code the case_ prefix —
    #   the case type was retired (changed the same day), so any assistant with an asst_ prefix was written wrong.
    #   Now the real session_id passed by the caller wins; only when absent does it fall back to asst_ per the current convention.
    session_id = (session_id or "").strip() or (
        f"agent:{owner_agent_id}:asst_{slug}" if owner_agent_id else f"agent:?:asst_{slug}"
    )
    # * fixed 2026-10-10: by default resolved dynamically from [owner agent_id] (no more module constants)
    root = (files_root or assistant_files_root_for_slug(owner_agent_id, slug)).rstrip("/")

    def _bullets(items, default="(待补)"):
        items = [str(x).strip() for x in (items or []) if str(x).strip()]
        if not items:
            return [f"  - {default}"]
        return [f"  - {x}" for x in items]

    lines = []
    lines.append(f"【助理职责档案 · {assistant_name}】")
    lines.append("")

    # * who I am (fixed + variable)
    lines.append("■ 我是谁")
    lines.append(f"  我是 {owner_agent_id}({owner_agent_name}) 的分身助理之一，标识为「{assistant_name}」。")
    lines.append(f"  我是常驻这个会话({session_id})的助理，同一件事由我长期承接。")
    lines.append(_ASSISTANT_IDENTITY_LINE)
    lines.append("")

    # * what I am responsible for (variable)
    lines.append("■ 我负责什么")
    lines.extend(_bullets(responsibilities))
    lines.append("")

    # * who I work with (variable)
    lines.append("■ 我和谁协作")
    lines.extend(_bullets(collaborators))
    lines.append("")

    # * what I produce (variable)
    lines.append("■ 我产出什么")
    lines.extend(_bullets(deliverables))
    lines.append("")

    # * * where my files are (* fully fixed, filled by the program — the point Lao Feng called out)
    lines.append("■ ★ 我的文件在哪")
    lines.append(f"  标识(slug)：{slug}")
    lines.append(f"  文件根：{root}/")
    lines.append("    - daily/    我的日志")
    lines.append("    - notes/    我的笔记/草稿")
    lines.append("    - outputs/  我的交付物")
    lines.append("  规则：我的「记录/文档」类产出一律落上述文件夹；项目代码类产出仍落项目仓库。")
    lines.append("")

    # * work discipline (fixed baseline + appended specifics)
    lines.append("■ 工作纪律")
    lines.extend(_ASSISTANT_DISCIPLINE_BASELINE)
    if extra_discipline:
        lines.extend(_bullets(extra_discipline))
    return "\n".join(lines)


def derive_session_type(session_id: str, task_labs: str = "") -> str:
    """Derive the session type.

    Four-value enum (case was retired on 2026-10-22):
      - 'main'      main session (long-lived conversation channel): session_id ends with ':main'
      - 'assistant' assistant (a hand extended by the owner): last segment looks like asst_{slug}
                    * added 2026-10-21. It models a "person (alter ego)", not a "matter".
                    * fixed 2026-10-22: the old 'case' type was retired — measured: the whole DB held
                      only 2 case_ sessions (one general execution each, never reused), and
                      agx_execution_link (the case link mechanism) had 0 rows = never used.
                      Existing case_ sessions fall back to task/oneshot.
      - 'task'      task session (a stable session bound to a task family): not the main session, not
                    one-shot, and carries task_labs
      - 'oneshot'   one-shot temporary session (default fallback): last segment is run_/sess_, or undecidable

    The criteria match derive_session_name()'s 'one_off' detection (shape-based, no extra state).

    ** Fixed 2026-10-21 (aifr_pm's P1-5 "invisible assistant"):
      On 10-19 the assistant session prefix moved from case_ to asst_, but this function still only
      recognized case_ -> asst_writing_research fell into the task branch below (because it carries
      task_labs='universal'), giving session_type='task' + assistant_slug=NULL ->
      agx_assistant_list(WHERE session_type='case') could not find it -> the assistant was
      "created but gone".

      Historic fact: before the change, 14 of 16 case sessions had is_assistant=1; case had long been
      owned by assistants. So this change recognizes asst_ and also files case_-prefixed assistant
      sessions under assistant.

    Legacy note: this function is the type-derivation entry point for [new sessions]. Existing rows are
    corrected by ensure_session's type_fix backfill (only when the old value is empty/oneshot/task and
    the new derivation is more specific).
    """
    sid = (session_id or "").strip()
    if not sid:
        return "oneshot"

    parts = sid.split(":")

    # 1) main session
    if parts and parts[-1] == "main":
        return "main"

    # 2) one-shot session: the last segment (or any segment) starts with run_/sess_
    one_off = any(seg.startswith("run_") or seg.startswith("sess_") for seg in parts[1:])
    if one_off:
        return "oneshot"

    # 3) assistant (long-lived session · alter ego): last segment starts with asst_
    if parts and parts[-1].startswith("asst_"):
        return "assistant"

    # 4) * 2026-10-22: the case type was retired (Lao Feng's call). The old branch is gone —
    #    existing case_ sessions are no longer derived as case; they fall through to task (when task_labs is set)
    #    or the oneshot fallback. The semantics are carried by task_labs; there is no "matter" container any more.
    #    Basis: the whole DB holds only 2 case_ sessions (one general execution each, never reused);
    #    agx_execution_link (the case link mechanism) has 0 rows = never used.

    # 5) stable session with a task family -> task session
    if (task_labs or "").strip():
        return "task"

    # 6) cannot decide -> conservative fallback
    return "oneshot"


def derive_assistant_slug(session_id: str) -> str:
    """Derive the assistant slug.

    Source: when session_id's last segment looks like asst_{slug} or case_{slug}, take the slug as assistant_slug.
    Examples:
      agent:agentgeex_agent:asst_agx_image   -> 'agx_image'   * added 2026-10-21
      agent:agentgeex_agent:case_ui_design   -> 'ui_design'   (legacy compatibility)
      agent:xxx:main / anything else         -> '' (not an assistant)

    * Orthogonal to is_assistant:
      - is_assistant   : a 0/1 group flag (the program's decision entry point)
      - assistant_slug : a readable, stable, distinguishable name (used for log folders and similar)

    ** Fixed 2026-10-21 (aifr_pm P1-5):
      On 10-19 the assistant prefix moved from case_ to asst_, but this function still only recognized
      case_ -> asst_writing_research's assistant_slug was always NULL -> agx_my_sessions showed
      slug=null, contradicting is_assistant=1.
      Both prefixes are recognized now; asst_ wins (the new form).

    Note: the last segment must be a prefix followed by a non-empty slug; a double-prefix shape such as
    'case_case_xxx' yields 'case_xxx' (existing data is respected and not cleaned twice).
    """
    sid = (session_id or "").strip()
    if not sid:
        return ""
    tail = sid.split(":")[-1]
    for _pfx in ("asst_", "case_"):
        if tail.startswith(_pfx):
            return tail[len(_pfx):].strip()[:64]
    return ""


def derive_session_name(
    session_id: str,
    name_hint: str = "",
    agent_id: str = "",
    task_labs: str = "",
) -> str:
    """Derive the session name (three-tier lookup).

    (1) an explicitly passed name_hint
    (2) task_base.description (looked up by the caller and passed in as name_hint)
    (3) auto-derived: {agent_name}·{task_labs} / {agent_name} main session / session_id fallback
    """
    name = _sanitize_name_field(name_hint)
    if name:
        return name

    parts_key = (session_id or "").split(":")
    if not agent_id and len(parts_key) >= 2:
        agent_id = parts_key[1]

    # look up the agent's display name
    agent_name = ""
    if agent_id:
        try:
            rows = execute_sql(
                "SELECT agent_name FROM agx_agent_registry WHERE agent_id = %s",
                (agent_id,),
            )
            if rows:
                agent_name = (rows[0].get("agent_name") or "").strip()
        except Exception:
            agent_name = ""
    if not agent_name:
        agent_name = agent_id or "未命名 Agent"

    labs = (task_labs or "").strip()
    parts = (session_id or "").split(":")

    # one-shot session detection: agent:{aid}:{run_xxx} — the run_uuid fallback when no stable session_id is given
    # such a session is bound to one task instance, so task_labs must not become the session identity (otherwise every one is a same-named "main session")
    one_off = False
    if len(parts) == 3 and (parts[2].startswith("run_") or parts[2].startswith("sess_")):
        one_off = True
    elif (
        len(parts) >= 4
        and (parts[-1].startswith("run_") or parts[-1].startswith("sess_"))
        and parts[-2] not in _SESSION_NAME_SUFFIX
    ):
        one_off = True

    if not one_off and not labs:
        # infer the task family from the session_id shape: agent:{aid}:{labs} / agent:{aid}:{labs}:{uuid}
        if len(parts) >= 3:
            seg = parts[2]
            if not seg.startswith("run_") and not seg.startswith("sess_"):
                labs = seg

    if one_off:
        # one-shot session: agent name + a short marker
        if agent_id or agent_name:
            return _sanitize_name_field(f"{agent_name} 临时会话")
        return _sanitize_name_field(session_id) or "未命名会话"

    if labs in _SESSION_NAME_SUFFIX:
        return _sanitize_name_field(f"{agent_name} {_SESSION_NAME_SUFFIX[labs]}")
    if labs:
        return _sanitize_name_field(f"{agent_name}·{labs}")
    if agent_id:
        return _sanitize_name_field(f"{agent_name} 会话")
    return _sanitize_name_field(session_id) or "未命名会话"


# -----------------------------------------------------------
# execution instance short title: give every execution a short name (exec_name)
# -----------------------------------------------------------

# template first lines without distinguishing value, skipped when deriving automatically
_EXEC_NAME_SKIP_PREFIXES = ("【项目背景】", "【任务背景】", "【背景】", "【系统信息】")

# structured message type field -> friendly display label
_EXEC_NAME_TYPE_MAP = {
    "watchdog": "看门狗报警",
    "task_completion": "任务完成回执",
}


def derive_exec_name(
    exec_name: str = "",
    instruction: str = "",
    task_labs: str = "",
    task_desc: str = "",
) -> str:
    """Execution instance short title, three-tier lookup (read-only, idempotent, silently failing, never raising).

    (1) exec_name passed explicitly by the caller
    (2) extracted from the instruction:
          - JSON shape -> take the semantic field (exec_name/title/name/subject, else alert_type/reply_type)
          - text shape -> take the first distinctive title line (skip template lines such as the project background header)
    (3) fall back to task_base.description (the task-type level description)

    Design basis: Lao Feng's 2026-09-12 decision, option A — dispatch carries an explicit name and the
    core layer derives a fallback.
    Note: internal callers such as the watchdog pass a JSON string as instruction, so the JSON branch
    MUST be taken; never fall into "take the first line" (that would yield a blob of JSON).
    """
    try:
        # (1) explicit value wins
        if exec_name and str(exec_name).strip():
            return _sanitize_name_field(exec_name)

        s = (instruction or "").strip()

        # (2) JSON shape
        if s.startswith("{"):
            try:
                obj = json.loads(s)
            except Exception:
                obj = None
            if isinstance(obj, dict):
                for k in ("exec_name", "title", "name", "subject"):
                    v = obj.get(k)
                    if v and str(v).strip():
                        return _sanitize_name_field(str(v))
                for k in ("alert_type", "reply_type"):
                    v = obj.get(k)
                    if v and str(v).strip():
                        raw = str(v).strip()
                        return _sanitize_name_field(_EXEC_NAME_TYPE_MAP.get(raw, raw))
        else:
            # (2) text shape: prefer a markdown heading line (# / ##), else the first distinctive body line
            fallback_line = ""
            for raw_line in s.splitlines():
                stripped = raw_line.strip()
                if not stripped:
                    continue
                if stripped.startswith("#"):
                    heading = stripped.lstrip("#").strip()
                    if heading and not any(heading.startswith(p) for p in _EXEC_NAME_SKIP_PREFIXES):
                        return _sanitize_name_field(heading)
                    continue
                if not fallback_line:
                    line = stripped.lstrip("-*· •").strip()
                    if line and not any(line.startswith(p) for p in _EXEC_NAME_SKIP_PREFIXES):
                        fallback_line = line
            if fallback_line:
                return _sanitize_name_field(fallback_line)

        # (3) fall back to the task-type description
        return _sanitize_name_field(task_desc)
    except Exception:
        return _sanitize_name_field(task_desc)


def ensure_session(
    session_id: str,
    name_hint: str = "",
    agent_id: str = "",
    project_id: str = "",
    task_labs: str = "",
    run_uuid: str = "",
    creator_agent_id: str = "",
    network_id: str = "",
    x_desc: str = "",
) -> None:
    """Session registration (idempotent): INSERT a row and name it the first time a session_id is seen; otherwise only update counters.

    Design principle: **append only, never overwrite**.
      - new session -> INSERT (session_name = derive_session_name(...))
      - existing session -> UPDATE run_count+1 / last_run_uuid / last_active_at;
                 fill in a name only when the existing one is empty, never overwrite an existing name.

    session_id has the same name and meaning as agx_task_execution.session_id (globally unique);
    the two sides join directly on session_id, no extra mapping needed.

    This function never raises (a failed registration must not disturb the dispatch main flow);
    exceptions are only logged.
    """
    if not session_id:
        return
    try:
        session_id = str(session_id).strip()[:128]
        net = network_id or config.get("network_id", "") or ""
        proj = (project_id or "")[:64] or None
        labs = (task_labs or "")[:64] or None
        creator = (creator_agent_id or "")[:64] or None
        last_run = (run_uuid or "")[:64] or None

        existing = execute_sql(
            "SELECT pkid, session_name, session_type, assistant_slug FROM agx_session WHERE session_id = %s",
            (session_id,),
        )

        sess_type = derive_session_type(session_id, task_labs or "")
        slug = derive_assistant_slug(session_id)

        if existing:
            old_name = (existing[0].get("session_name") or "").strip()
            old_type = (existing[0].get("session_type") or "").strip()
            old_slug = (existing[0].get("assistant_slug") or "").strip()
            # Type backfill: correct only when the stored value is empty/a fallback value, or this derivation is more specific (never overwrite a manual setting).
            # * extended 2026-10-21 (aifr_pm P1-5): the old logic corrected only empty/oneshot,
            #   which pinned asst_ sessions to 'task' forever and case_ assistant sessions to 'case'.
            #   Now a one-way [case/task -> assistant] correction is allowed:
            #   assistant is a more specific meaning than case/task ("alter ego", not "matter"),
            #   and it only happens when this function derives assistant, so it can never overwrite in reverse.
            type_fix = ""
            if sess_type == "assistant" and old_type in ("", "oneshot", "task", "case"):
                type_fix = "assistant"
            elif (not old_type or old_type == "oneshot") and sess_type != "oneshot":
                type_fix = sess_type
            # slug backfill: fill only when the stored slug is empty and the new derivation is not (never overwrite an existing identifier)
            slug_fix = slug if (not old_slug and slug) else ""
            if old_name:
                if type_fix and slug_fix:
                    execute_sql(
                        "UPDATE agx_session SET run_count = run_count + 1, "
                        "last_run_uuid = %s, last_active_at = NOW(), session_type = %s, "
                        "assistant_slug = %s WHERE session_id = %s",
                        (last_run, type_fix, slug_fix, session_id),
                        commit=True,
                    )
                elif type_fix:
                    execute_sql(
                        "UPDATE agx_session SET run_count = run_count + 1, "
                        "last_run_uuid = %s, last_active_at = NOW(), session_type = %s WHERE session_id = %s",
                        (last_run, type_fix, session_id),
                        commit=True,
                    )
                elif slug_fix:
                    execute_sql(
                        "UPDATE agx_session SET run_count = run_count + 1, "
                        "last_run_uuid = %s, last_active_at = NOW(), assistant_slug = %s WHERE session_id = %s",
                        (last_run, slug_fix, session_id),
                        commit=True,
                    )
                else:
                    execute_sql(
                        "UPDATE agx_session SET run_count = run_count + 1, "
                        "last_run_uuid = %s, last_active_at = NOW() WHERE session_id = %s",
                        (last_run, session_id),
                        commit=True,
                    )
            else:
                # fill in a name for historically unnamed sessions (no other field is touched)
                execute_sql(
                    "UPDATE agx_session SET session_name = %s, run_count = run_count + 1, "
                    "last_run_uuid = %s, last_active_at = NOW(), session_type = %s, "
                    "assistant_slug = COALESCE(NULLIF(assistant_slug, ''), %s) WHERE session_id = %s",
                    (
                        derive_session_name(session_id, name_hint, agent_id, task_labs or ""),
                        last_run,
                        sess_type,
                        slug or None,
                        session_id,
                    ),
                    commit=True,
                )
            return

        session_name = derive_session_name(session_id, name_hint, agent_id, task_labs or "")
        execute_sql(
            "INSERT INTO agx_session "
            "(network_id, session_id, session_name, x_desc, agent_id, project_id, task_labs, "
            " session_type, assistant_slug, creator_agent_id, run_count, last_run_uuid, last_active_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, NOW())",
            (
                net,
                session_id,
                session_name,
                _sanitize_name_field(x_desc, 2000) or None,
                (agent_id or "")[:64],
                proj,
                labs,
                sess_type,
                slug or None,
                creator,
                last_run,
            ),
            commit=True,
        )
    except Exception as e:
        try:
            log  # noqa: F821  (ignored when agx_core has no log object)
        except Exception:
            pass
        # agx_core has no unified log object, so failures are silent (the main flow is unaffected)
        return


def _derive_session_key(agent_id: str, task_labs: str, dedicated_session) -> str:
    """The single source of truth for deriving a session key (established 2026-10-25).

    * Why this function exists: this ladder used to live only in the MCP facade agx_dispatch_task,
      while the inner _ao_dispatch_task honoured only dedicated_session. The consequence: every path
      that [bypasses the facade and calls the inner function] (watchdog dispatch/receipt,
      quick_cmd_send, etc.) put general tasks into the one-shot session agent:{aid}:run_xxx instead
      of the main session — the opposite of the design intent.

    Measured: all 100 general tasks in agx_task_base across the DB have dedicated_session=0;
      agx.sql:128 states explicitly that agx_agent_registry.default_session_id is the fallback target
      (all 93/93 agents have a value, shaped agent:{aid}:main), yet the inner layer never read it.

    Ladder (the order must not be changed):
      1) dedicated_session=1 and universal  -> agent:{aid}:universal:{new uuid}
         a new session each time, supporting concurrency (several self-dispatched tasks never pollute each other's context)
      2) dedicated_session=1 and anything else -> agent:{aid}:{task_labs}
         stable reuse; the context of same-family tasks accumulates continuously
      3) task_labs=general                  -> agent.default_session_id (the main session)
         * exactly the intent behind "general lands in the main session"
      4) everything else                    -> "" (the caller falls back to a one-shot session)

    An empty string means "no stable session ownership"; the caller keeps its existing run_uuid one-shot fallback.
    """
    labs = (task_labs or "").strip()
    if dedicated_session and labs:
        if labs == "universal":
            short_id = agx_generate_uuid(prefix="run", infix=labs)
            return f"agent:{agent_id}:{labs}:{short_id}"
        return f"agent:{agent_id}:{labs}"
    if labs == "general":
        try:
            row = execute_sql(
                "SELECT default_session_id FROM agx_agent_registry "
                "WHERE agent_id = %s AND active_status = 1",
                (agent_id,),
            )
            if row and row[0].get("default_session_id"):
                return row[0]["default_session_id"]
        except Exception as e:
            _core_log(f"_derive_session_key: 查 default_session_id 失败(回落一次性会话) | {agent_id} | {e}", "WARN")
    return ""


def _ao_dispatch_task(
    caller_agent_id: str,
    instruction: str,
    agent_id: str,
    task_id: str,
    network_id: str,
    project_id: str,
    host_id: str = "",
    sender_id: str = "",
    timeout_seconds: int = None,
    session_key: str = "",
    send_session_id: str = "",
    needs_reply: int = 0,
    is_reply_to: str = "",
    prev_task: str = "",
    next_task: str = "",
    plan_id: str = "",
    session_name: str = "",
    exec_name: str = "",
    quick_cmd_id: str = "",
    quick_cmd_params: str = "",
    client_request_id: str = "",
    needs_confirm: int = 0,
) -> dict:
    """[Internal] Create an execution record and send the protocol command message in one step.

    Key differences from agx_dispatch_task (MCP):
    - no MCP decorator, so internal components such as the watchdog may call it directly
    - the caller must pre-resolve every argument
    - instruction is already final; it is not passed through _build_exec_instruction again

    Args:
        quick_cmd_id: when this dispatch originates from a quick command (agx_quick_cmd_send), record
            the cmd_id; a normal dispatch leaves it empty ("") -> the column is not written. Backward compatible.
        quick_cmd_params: the parameters that took effect for this quick command (JSON, after defaults
            are filled in); a normal dispatch leaves it empty ("") -> the column is not written.
            Backward compatible.

    Returns:
        dict: {"run_uuid": "...", "msg_id": "...", "queue": "...",
               "queue_length": N, "timestamp": N}
    """
    timeout_seconds = timeout_seconds or int(config.task_timeout)

    # * 2026-10-19: entry defence for session_key — previously _ao_dispatch_task did zero validation on it.
    #   Measured incident: a caller (an AI agent) copied the placeholder `***` from the docs and passed it as a real value
    #   -> session_id='***' was stored -> daemon/ws_client could not deliver -> the execution failed outright,
    #   and the dirty value kept spreading through watchdog receipts.
    #   Handling: when illegal, [drop it and fall back to the default] instead of erroring — dispatch itself should succeed if possible,
    #   and landing on agent:{agent_id}:{run_uuid} (oneshot) beats losing the whole task.
    if session_key and not _is_safe_session_key(session_key):
        _core_log(f"dispatch: 非法 session_key={session_key!r} 已丢弃, 回落默认会话 | agent={agent_id}", "WARN")
        session_key = ""
    try:
        # ── look up task_labs (for run_uuid naming and session_key) ──
        task_labs = ""
        dedicated_session = 0
        task_desc = ""
        task_info = execute_sql(
            "SELECT task_labs, dedicated_session, description FROM agx_task_base WHERE task_id = %s",
            (task_id,),
        )
        if task_info:
            task_labs = (task_info[0].get("task_labs") or "").strip()
            dedicated_session = task_info[0].get("dedicated_session", 0)
            task_desc = (task_info[0].get("description") or "").strip()

        # ── execution instance short title (option A: explicit, or derived) ──
        exec_name = derive_exec_name(exec_name, instruction, task_labs, task_desc)

        # ── * idempotence pre-check (2026-10-19 P2) ──
        #   Motivation: a network retry / a front-end timeout resubmission dispatches the same intent twice, producing duplicate side effects
        #   (changing code twice / clearing data twice / posting a notice twice) — worse than "failing".
        #   When the caller passes client_request_id (reused for retries of one intent), the first result is returned directly.
        #   Unique key = (send_agent_id, client_request_id), deduplicated by [initiator] (not by the receiving agent_id).
        if client_request_id:
            _cid = str(client_request_id).strip()[:64]
            try:
                _hit = execute_sql(
                    "SELECT task_run_uuid, session_id, run_status FROM agx_task_execution "
                    "WHERE send_agent_id = %s AND client_request_id = %s LIMIT 1",
                    (caller_agent_id, _cid),
                    commit=False,
                )
            except Exception as e:
                _core_log(f"幂等查询失败(降级为正常派发) | {e}", "WARN")
                _hit = None
            if _hit:
                return {
                    "run_uuid": _hit[0].get("task_run_uuid", ""),
                    "session_key": _hit[0].get("session_id", ""),
                    "idempotent": True,
                    "client_request_id": _cid,
                    "msg_id": "",
                    "queue": "",
                }

        # ── create the execution record ──
        run_uuid = agx_generate_uuid(prefix="run", infix=task_labs)

        if not session_key:
            session_key = _derive_session_key(agent_id, task_labs, dedicated_session)
        session_id = session_key or f"agent:{agent_id}:{run_uuid}"

        # ── session-context injection (2026-10-09; a generic block injected for every task) ──
        #   Feed "the sender's own session id" to the agent (resists forgetting) and explain what self_session_id is for.
        #   2026-10-13: also feed "this execution's task_run_uuid" (for correlated tools such as the deploy callback).
        #   Must run before _inject_assistant_hint (the assistant block de-duplicates the session-id narration).
        instruction = _inject_session_context(session_id, instruction, run_uuid)

        # ── assistant identity injection (2026-10-08) ──
        #   When the target session is an [assistant session] (is_assistant=1), prepend the [identity hint].
        #   Same shape as _ao_continue_task (shared function _inject_assistant_hint),
        #   covering the path "the owner dispatches work into an assistant session".
        instruction = _inject_assistant_hint(session_id, instruction)

        # ── session registration (name and store it the first time the key is seen) ──
        ensure_session(
            session_id=session_id,
            name_hint=_session_name_hint(session_name, task_labs, task_desc, exec_name),
            agent_id=agent_id,
            project_id=project_id,
            task_labs=task_labs,
            run_uuid=run_uuid,
            creator_agent_id=caller_agent_id,
            network_id=network_id,
        )
        send_agent_id = caller_agent_id
        send_host_id = _resolve_send_host_id(caller_agent_id)

        # * Lao Feng, 2026-10-19: started_at is written lazily, "the first time the receiving Agent reads the execution".
        #   At insert time started_at=NULL and run_status='running' (queued time still counts as running; the watchdog scans as usual),
        #   and the real start moment is written by agx_get_execution when "caller == agent_id and started_at is empty".
        #   Reason: the old NOW() counted "queue wait + model load + context compaction" into the timeout, so
        #   after a daemon outage everything dispatched at once was judged timed out (measured: 83 timed-out executions that actually succeeded, timeout=600 vs 7434s average real runtime).
        sql = """
        INSERT INTO agx_task_execution
            (network_id, project_id, plan_id, task_id, agent_id, task_run_uuid, host_id,
             exec_instruction, send_agent_id, send_host_id, session_id, send_session_id, run_status,
             started_at, timeout_seconds, needs_reply, is_reply_to, needs_confirm,
             prev_task, next_task, exec_name, quick_cmd_id, quick_cmd_params, client_request_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'running', NULL, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s)
        """
        insert_params = (
            network_id,
            project_id,
            plan_id or None,
            task_id,
            agent_id,
            run_uuid,
            host_id,
            instruction,
            send_agent_id,
            send_host_id or None,
            session_id,
            send_session_id or None,
            timeout_seconds,
            needs_reply,
            is_reply_to or None,
            int(needs_confirm or 0),  # task-level attribute: 1 = a human must approve after success (awaiting_confirm)
            prev_task or None,
            next_task or None,
            exec_name or None,
            quick_cmd_id or None,
            quick_cmd_params or None,
            (str(client_request_id).strip()[:64] if client_request_id else None),
        )
        pkid = None
        try:
            pkid = _retry_op("dispatch_insert", lambda: execute_sql(sql, insert_params, commit=True))
        except Exception as _e:
            # * 2026-10-19 P2 concurrency fallback: a tiny race window exists between the SELECT duplicate check and the INSERT
            #   (two requests with the same cid both miss and both insert). The unique index uk_client_req stops the
            #   second one; here we catch it, re-read the first record and return the same result instead of raising to the caller.
            #   Criteria: MySQL duplicate key errno=1062 (PyMySQL puts it in args[0]).
            _errno = getattr(_e, "args", [None])[0] if getattr(_e, "args", None) else None
            if _errno == 1062 and client_request_id:
                _cid2 = str(client_request_id).strip()[:64]
                _hit2 = execute_sql(
                    "SELECT task_run_uuid, session_id FROM agx_task_execution "
                    "WHERE send_agent_id = %s AND client_request_id = %s LIMIT 1",
                    (caller_agent_id, _cid2),
                    commit=False,
                )
                if _hit2:
                    _core_log(f"幂等并发命中(1062) → 返回首次 {_hit2[0].get('task_run_uuid')}", "INFO")
                    return {
                        "run_uuid": _hit2[0].get("task_run_uuid", ""),
                        "session_key": _hit2[0].get("session_id", ""),
                        "idempotent": True,
                        "client_request_id": _cid2,
                        "msg_id": "",
                        "queue": "",
                        "concurrent": True,
                    }
            raise

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # * 2026-10-19: started_at is written lazily -> the dispatch stage caches it empty,
        #   and agx_get_execution fills it in on the first read (writing DB and Redis together).
        _sync_entity(
            "execution",
            run_uuid,
            {
                "pkid": pkid,
                "task_run_uuid": run_uuid,
                "network_id": network_id,
                "project_id": project_id,
                "plan_id": plan_id or "",
                "task_id": task_id,
                "agent_id": agent_id,
                "send_agent_id": send_agent_id,
                "send_host_id": send_host_id,
                "host_id": host_id,
                "run_status": "running",
                "session_id": session_id,
                "send_session_id": send_session_id or "",
                "exec_instruction": instruction,
                "exec_name": exec_name or "",
                "started_at": "",
                # * 2026-10-19: created_at is cached too, so the watchdog can compute queue duration without hitting the DB
                "created_at": now_str,
                "timeout_seconds": str(timeout_seconds),
                "needs_reply": str(needs_reply),
                "is_reply_to": is_reply_to or "",
                "needs_confirm": str(int(needs_confirm or 0)),
                "prev_task": prev_task or "",
                "next_task": next_task or "",
                "quick_cmd_id": quick_cmd_id or "",
                "quick_cmd_params": quick_cmd_params or "",
            },
        )

        _publish_event("execution", "started", run_uuid, {"task_id": task_id})

        # ── assemble and dispatch the command protocol message ──
        now_ts = int(datetime.now().timestamp())
        msg_id = agx_generate_uuid(prefix="msg")

        # ── resolve send_agent: the agent that actually sends the message ──
        # send_agent tracks the message source, decoupled from sender_id (the dispatcher)
        send_agent = caller_agent_id

        message = {
            "proto_ver": "1.0",
            "msg_id": msg_id,
            "timestamp": now_ts,
            "target_id": agent_id,
            "sender_id": sender_id,
            "send_agent": send_agent,
            "msg_type": "command",
            "payload": {
                "command": "execute_task",
                "task_run_uuid": run_uuid,
                "task_id": task_id,
                "project_id": project_id,
                "instruction": instruction,
                "session_key": session_key or "",
                # * 2026-10-22: self_session_id = the session id of [the receiver itself].
                #   The semantics are unified as "self always means the one currently speaking":
                #     · as an [argument] (filled by the caller when dispatching) -> the caller's own session (= S_A)
                #     · as a [message field] (what the receiver gets)        -> the receiver's own session (= S_B = session_id)
                #   Use: the platform cannot read its own session id, so the message feeds it to the receiver explicitly;
                #         when the receiver dispatches work it passes that along as self_session_id, which is how the receipt finds its way back.
                #   History: the first version on 2026-10-09 wrongly put send_session_id (the sender) here, so the
                #         message field contradicted the session-context block at the top of exec_instruction.
                #   Note: receipt routing does not read this field (it uses the DB column agx_task_execution.send_session_id),
                #         so this correction only changes the information fed to the agent, not the routing behaviour.
                "self_session_id": session_id or "",
                # * 2026-10-26 msg_type semantics:
                #   push is_reply_to down into the inner payload so the daemon can decide.
                #   Previously it was only stored (a DB column) and never entered the envelope -> the daemon could not tell
                #   "dispatch work" from "receipt", only three fields went down,
                #   and the receiver could only guess from JSON embedded in exec_instruction.
                "is_reply_to": is_reply_to or "",
                # * 2026-10-26 (second fix): actually compute msg_type before sending.
                #   Background: the previous version only pushed down is_reply_to without computing msg_type ->
                #   the daemon's struct_msg still had only 3 fields and the receiver could not see the type.
                #   Rule: is_reply_to non-empty -> receipt; otherwise -> dispatch.
                #   Note: urge is decided by the task_labs of the watchdog alert path and is not involved here.
                "msg_type": "receipt" if (is_reply_to or "").strip() else "dispatch",
            },
        }

        # host-scoped queue: each host has its own namespace
        queue_key = f"agx:queue:{host_id}:agent:{agent_id}"

        def _do_rpush():
            r = get_redis_client()
            return r.rpush(queue_key, json.dumps(message, ensure_ascii=False))

        _retry_op("dispatch_rpush", _do_rpush)
        r = get_redis_client()
        queue_len = r.llen(queue_key)

        _publish_event(
            "agent_message",
            "sent",
            msg_id,
            {
                "queue": queue_key,
                "msg_type": "command",
                "target_id": agent_id,
                "host_id": host_id,
                "send_agent": send_agent,
                "task_run_uuid": run_uuid,
            },
        )

        # ── receipt: record "the moment the acknowledgement was sent" + one receipt event (2026-10-28) ──
        #   Semantics: sent = enqueued successfully. Only rows with a non-empty is_reply_to take this path.
        #   Goal: turn "was it sent" into an observable fact (previously only reply_read existed, which only answered "was it read").
        receipt_event_id = ""
        if (is_reply_to or "").strip():
            try:
                execute_sql(
                    "UPDATE agx_task_execution SET reply_sent_at=NOW(), ack_state='sent' "
                    "WHERE task_run_uuid=%s AND reply_sent_at IS NULL",
                    (run_uuid,),
                    commit=True,
                )
            except Exception as _re:
                _core_log(f"回执发出时刻写入失败 | {run_uuid}: {_re}", "WARN")
            receipt_event_id = _ao_record_event(
                task_run_uuid=run_uuid,
                event_type="receipt",
                level=0,
                from_agent=send_agent_id,
                to_agent=agent_id,
                to_session=session_id,
                channel="queue",
                reason="回执已发出",
                delivery_state="sent",
            )

        return {
            "run_uuid": run_uuid,
            "event_id": receipt_event_id,
            "msg_id": msg_id,
            "queue": queue_key,
            "queue_length": queue_len,
            "host_id": host_id,
            # * 2026-10-17: return [the receiving session key that was actually stored] (= agx_task_execution.session_id).
            #   Motivation: when the kernel derives the session automatically (oneshot agent:{aid}:{run_uuid} / dedicated_session),
            #   the caller cannot know it -> the BFF once computed agent:{aid}:main itself, causing "display != reality".
            #   Once returned, the caller adopts it and stops guessing.
            "session_key": session_id,
            "timestamp": now_ts,
        }

    except Exception as e:
        return {"error": str(e)}


def _ao_schedule_task(
    caller_agent_id: str,
    instruction: str,
    agent_id: str,
    task_id: str,
    network_id: str,
    project_id: str,
    host_id: str = "",
    sender_id: str = "",
    timeout_seconds: int = None,
    session_key: str = "",
    send_session_id: str = "",
    needs_reply: int = 0,
    is_reply_to: str = "",
    parent_task_id: str = "",
    parent_run_uuid: str = "",
    prev_task: str = "",
    next_task: str = "",
    scheduled_at: str = "",
    plan_id: str = "",
    session_name: str = "",
    exec_name: str = "",
    needs_confirm: int = 0,
) -> dict:
    """[Internal] Create an execution record without dispatching it; status stays pending.

    Differences from _ao_dispatch_task:
    - writes the database + cache only, does not push to the Redis queue
    - run_status = 'pending', started_at = NULL
    - used while building a plan; the execution is later activated by _ao_activate_task
    - parent_task_id / parent_run_uuid record the hierarchy
    - prev_task / next_task record the position in a task chain
    - scheduled_at sets the expected start time (MySQL DATETIME, e.g. '2026-06-01 09:00:00'); the watchdog activates it then

    Returns:
        dict: {"run_uuid": "...", "status": "pending", "timestamp": N}
    """
    timeout_seconds = timeout_seconds or int(config.task_timeout)
    try:
        # ── look up task_labs (for run_uuid naming) ──
        task_labs = ""
        dedicated_session = 0
        task_desc = ""
        task_info = execute_sql(
            "SELECT task_labs, dedicated_session, description FROM agx_task_base WHERE task_id = %s",
            (task_id,),
        )
        if task_info:
            task_labs = (task_info[0].get("task_labs") or "").strip()
            dedicated_session = task_info[0].get("dedicated_session", 0)
            task_desc = (task_info[0].get("description") or "").strip()

        # ── execution instance short title (option A: explicit, or derived) ──
        exec_name = derive_exec_name(exec_name, instruction, task_labs, task_desc)

        run_uuid = agx_generate_uuid(prefix="run", infix=task_labs)

        if not session_key:
            session_key = _derive_session_key(agent_id, task_labs, dedicated_session)

        session_id = session_key if session_key else f"agent:{agent_id}:{run_uuid}"

        # ── session-context injection (2026-10-09 generic block; task_run_uuid added 2026-10-13) ──
        instruction = _inject_session_context(session_id, instruction, run_uuid)

        # ── assistant identity injection (2026-10-08; same shape as dispatch/continue) ──
        #   When the target session is an [assistant session] (is_assistant=1), prepend the [identity hint]
        #   (a pointer, not the full text). When a pre-scheduled plan step lands in an assistant session, the assistant learns its identity this way after activation.
        instruction = _inject_assistant_hint(session_id, instruction)

        # ── session registration (register pending tasks too, so activation does not repeat it) ──
        ensure_session(
            session_id=session_id,
            name_hint=_session_name_hint(session_name, task_labs, task_desc, exec_name),
            agent_id=agent_id,
            project_id=project_id,
            task_labs=task_labs,
            run_uuid=run_uuid,
            creator_agent_id=caller_agent_id,
            network_id=network_id,
        )
        send_agent_id = caller_agent_id
        send_host_id = _resolve_send_host_id(caller_agent_id)

        sql = """
        INSERT INTO agx_task_execution
            (network_id, project_id, plan_id, task_id, agent_id, task_run_uuid, host_id,
             exec_instruction, send_agent_id, send_host_id, session_id, send_session_id, run_status,
             started_at, timeout_seconds, needs_reply, is_reply_to, needs_confirm,
             parent_task_id, parent_run_uuid, scheduled_at, prev_task, next_task, exec_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending',
                NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        pkid = execute_sql(
            sql,
            (
                network_id,
                project_id,
                plan_id or None,
                task_id,
                agent_id,
                run_uuid,
                host_id,
                instruction,
                send_agent_id,
                send_host_id or None,
                session_id,
                send_session_id or None,
                timeout_seconds,
                needs_reply,
                is_reply_to or None,
                int(needs_confirm or 0),  # task-level attribute: 1 = a human must approve after success
                parent_task_id or None,
                parent_run_uuid or None,
                scheduled_at or None,
                prev_task or None,
                next_task or None,
                exec_name or None,
            ),
            commit=True,
        )

        _sync_entity(
            "execution",
            run_uuid,
            {
                "pkid": pkid,
                "task_run_uuid": run_uuid,
                "network_id": network_id,
                "project_id": project_id,
                "plan_id": plan_id or "",
                "task_id": task_id,
                "agent_id": agent_id,
                "send_agent_id": send_agent_id,
                "send_host_id": send_host_id,
                "host_id": host_id,
                "run_status": "pending",
                "session_id": session_id,
                "send_session_id": send_session_id or "",
                "exec_instruction": instruction,
                "exec_name": exec_name or "",
                "started_at": "",
                "timeout_seconds": str(timeout_seconds),
                "needs_reply": str(needs_reply),
                "is_reply_to": is_reply_to or "",
                "needs_confirm": str(int(needs_confirm or 0)),
                "parent_task_id": parent_task_id or "",
                "parent_run_uuid": parent_run_uuid or "",
                "scheduled_at": scheduled_at or "",
                "prev_task": prev_task or "",
                "next_task": next_task or "",
            },
        )

        now_ts = int(datetime.now().timestamp())

        return {
            "run_uuid": run_uuid,
            "status": "pending",
            "timestamp": now_ts,
        }

    except Exception as e:
        return {"error": str(e)}


def _ao_activate_task(
    caller_agent_id: str,
    task_run_uuid: str,
) -> dict:
    """[Internal] Activate a pending execution record and dispatch it to the Agent queue.

    Flow:
    1. verify run_status = 'pending', otherwise refuse
    2. set run_status -> 'running' (* started_at stays NULL; the receiver writes it lazily on first read)
    3. sync the Redis cache and rebuild the running index
    4. assemble the command message and push it to the Redis queue

    Returns:
        dict: {"run_uuid": "...", "msg_id": "...", "queue": "...", "queue_length": N}
    """
    try:
        # ── 1. verify the status ──
        exec_row = execute_sql(
            "SELECT run_status, agent_id, task_id, project_id, host_id, "
            "exec_instruction, session_id, send_agent_id, send_host_id, send_session_id, "
            "timeout_seconds, needs_reply, is_reply_to "
            "FROM agx_task_execution WHERE task_run_uuid = %s",
            (task_run_uuid,),
        )
        if not exec_row:
            return {"error": f"execution '{task_run_uuid}' not found"}
        exc = exec_row[0]
        if exc["run_status"] != "pending":
            return {"error": f"execution '{task_run_uuid}' status is '{exc['run_status']}', not 'pending'"}

        agent_id = exc["agent_id"]
        task_id = exc["task_id"]
        project_id = exc["project_id"]
        instruction = exc["exec_instruction"]
        session_id = exc["session_id"]

        # ── session-context injection (2026-10-09 generic block; idempotent; task_run_uuid added 2026-10-13) ──
        #   On this path exec_instruction was already injected at schedule time (run_uuid = its own), so idempotence skips it; task_run_uuid is the fallback.
        instruction = _inject_session_context(session_id, instruction, task_run_uuid)

        # ── assistant identity injection (2026-10-08; same shape as dispatch/continue/schedule) ──
        #   Idempotence guard: skip when the schedule stage already injected it; compatible with pending records stored before this change.
        instruction = _inject_assistant_hint(session_id, instruction)

        host_id = exc["host_id"] or ""
        # plan_deploy does not pin host_id (a plan is a logical blueprint); it is resolved dynamically on activation
        if not host_id:
            agent_info = execute_sql(
                "SELECT host_id FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
                (agent_id,),
            )
            if agent_info and agent_info[0].get("host_id"):
                host_id = agent_info[0]["host_id"]
            if not host_id:
                return {"error": f"cannot determine host_id for agent '{agent_id}' at activation time"}
        send_agent_id = exc["send_agent_id"] or ""
        send_host_id = exc.get("send_host_id") or ""
        timeout_seconds = exc["timeout_seconds"] or int(config.task_timeout)

        # ── 2. update the status (started_at stays NULL; the receiver writes it lazily on first read) ──
        #   * 2026-10-19: activation only means "enqueued, awaiting processing", not "work has started".
        #   Writing started_at here would count queue time into the timeout (the original defect).
        #
        #   * 2026-10-25 atomic guard (multi-host change, prevents duplicate dispatch):
        #   the old "SELECT to verify pending, then unconditional UPDATE" had a TOCTOU window —
        #   with several watchdogs, or a watchdog racing MCP, both sides can pass the SELECT check,
        #   so one pending row gets activated and dispatched twice -> the same task runs twice.
        #   Now it is a conditional UPDATE (CAS): only the caller that actually moves pending->running
        #   (affected_rows==1) may continue dispatching; everyone else is treated as "already taken".
        _affected = execute_sql_rowcount(
            "UPDATE agx_task_execution SET run_status='running' "
            "WHERE task_run_uuid = %s AND run_status = 'pending'",
            (task_run_uuid,),
        )
        if _affected != 1:
            return {
                "error": (
                    f"execution '{task_run_uuid}' was activated concurrently "
                    f"(affected_rows={_affected}), skip duplicate dispatch"
                )
            }

        # ── 3. sync the Redis cache (started_at left empty, consistent with the DB) ──
        _sync_entity(
            "execution",
            task_run_uuid,
            {
                "task_run_uuid": task_run_uuid,
                "agent_id": agent_id,
                "task_id": task_id,
                "project_id": project_id,
                "host_id": host_id,
                "send_agent_id": send_agent_id,
                "send_host_id": send_host_id,
                "run_status": "running",
                "session_id": session_id,
                "started_at": "",
                "timeout_seconds": str(timeout_seconds),
            },
        )
        _publish_event("execution", "started", task_run_uuid, {"task_id": task_id})

        # ── 4. push to the Redis queue ──
        now_ts = int(datetime.now().timestamp())
        msg_id = agx_generate_uuid(prefix="msg")

        # extract session_key from the execution (strip the agent: prefix)
        session_key = ""
        if session_id and session_id.startswith("agent:"):
            session_key = session_id

        # ── resolve send_agent ──
        send_agent = send_agent_id or caller_agent_id

        message = {
            "proto_ver": "1.0",
            "msg_id": msg_id,
            "timestamp": now_ts,
            "target_id": agent_id,
            "sender_id": caller_agent_id,
            "send_agent": send_agent,
            "msg_type": "command",
            "payload": {
                "command": "execute_task",
                "task_run_uuid": task_run_uuid,
                "task_id": task_id,
                "project_id": project_id,
                "instruction": instruction,
                "session_key": session_key,
                # * 2026-10-22: self_session_id = the session id of [the receiver itself] (= session_id).
                #   See the same field's comment in dispatch (self means the current session on both ends).
                "self_session_id": session_id or "",
            },
        }

        queue_key = f"agx:queue:{host_id}:agent:{agent_id}"
        r = get_redis_client()
        r.rpush(queue_key, json.dumps(message, ensure_ascii=False))
        queue_len = r.llen(queue_key)

        _publish_event(
            "agent_message",
            "sent",
            msg_id,
            {
                "queue": queue_key,
                "msg_type": "command",
                "target_id": agent_id,
                "send_agent": send_agent,
                "host_id": host_id,
                "task_run_uuid": task_run_uuid,
            },
        )

        return {
            "run_uuid": task_run_uuid,
            "msg_id": msg_id,
            "queue": queue_key,
            "host_id": host_id,
            "queue_length": queue_len,
            "timestamp": now_ts,
        }

    except Exception as e:
        return {"error": str(e)}


def _session_name_hint(session_name: str, task_labs: str, task_desc: str, exec_name: str = "") -> str:
    """Compute the "explicit hint" part of a session name (used by ensure_session / derive_session_name).

    Priority: (1) an explicit session_name > (2) the execution short title exec_name > (3) task_base.description;
    for generic task_labs (general/universal/watchdog_alert) the description is template copy, which
    derive_session_name derives better on its own, so it is not used as a name_hint here.

    * 2026-09-12: (2) exec_name was added — fixing "N sessions under one task_labs share a name"
      (e.g. all 176 book_revise_section sessions were named after the task template description).
    """
    if session_name:
        return _sanitize_name_field(session_name)
    if (task_labs or "").strip() in _SESSION_NAME_SUFFIX:
        return ""
    if exec_name and str(exec_name).strip():
        return _sanitize_name_field(exec_name)
    return _sanitize_name_field(task_desc)


def _ao_continue_task(
    caller_agent_id: str,
    original_run_uuid: str,
    instruction: str,
    sender_id: str = "",
    timeout_seconds: int = None,
    prev_task: str = "",
    next_task: str = "",
    session_name: str = "",
    exec_name: str = "",
    needs_reply: int = 1,
    self_session_id: str = "",
    needs_confirm: int = -1,
) -> dict:
    """[Internal] Continue the conversation on the session of an existing execution record.

    Use case: a task that one conversation cannot finish and needs several rounds.
    Difference from agx_dispatch_task: no new session is created; the existing one is reused via sessions.send.

    Flow:
    1. look up agent_id/session_id of the original execution by original_run_uuid
    2. generate a new run_uuid (parent_run_uuid = original_run_uuid)
    3. push to the agent queue (command=continue_task, carrying session_key)
    daemon -> ws_client then calls sessions.send instead of sessions.create

    Returns:
        dict: {"run_uuid": "...", "msg_id": "...", "session_key": "...", ...}
    """
    timeout_seconds = timeout_seconds or int(config.task_timeout)
    try:
        # ── 1. read the original execution record ──
        rows = execute_sql(
            "SELECT agent_id, task_id, project_id, network_id, host_id, session_id, exec_name, "
            "needs_confirm FROM agx_task_execution WHERE task_run_uuid = %s",
            (original_run_uuid,),
        )
        if not rows:
            return {"error": f"original execution '{original_run_uuid}' not found"}

        orig = rows[0]
        agent_id = orig["agent_id"]
        task_id = orig["task_id"]
        project_id = orig["project_id"]
        network_id = orig["network_id"]
        host_id = orig.get("host_id") or ""
        # * needs_confirm defaults to -1 = inherit the original task (continuing the same task must not drop its confirmation requirement)
        _orig_nc = int(orig.get("needs_confirm") or 0)
        nc = _orig_nc if needs_confirm < 0 else int(needs_confirm)

        # session_key = the original session_id (agent:{agent_id}:{original_run_uuid})
        session_key = orig["session_id"]
        if not session_key:
            # fallback: assemble it by hand
            session_key = f"agent:{agent_id}:{original_run_uuid}"

        # ── * 2026-10-09 FIX: normalize send_session_id (the sender's session) ──
        #   Aligned with dispatch/schedule: use the explicit value when given (after the character-safety check), else fall back to the main session.
        #   Previously this function body [never defined] send_session_id -> the column was absent from INSERT -> the DB row was NULL
        #   -> downstream daemon retries / watchdog receipt reads got nothing -> receipts landed in the wrong place.
        send_session_id = (
            self_session_id
            if (self_session_id and _is_safe_session_key(self_session_id))
            else f"agent:{caller_agent_id}:main"
        )

        # ── session-context injection (2026-10-09 generic block; task_run_uuid added 2026-10-13) ──
        #   The injection happens [before] the run_uuid is generated, so task_run_uuid is unknowable here -> the id is not injected
        #   (the continuation message's source of truth is still the dispatch-time one; an upstream that needs it must pass it explicitly).
        instruction = _inject_session_context(session_key, instruction)

        # ── assistant identity injection (2026-10-08, shared function) ──
        #   When the target session is an [assistant session] (is_assistant=1), prepend the [identity hint]
        #   (a pointer, not the full text), telling the agent to call agx_get_session and read its profile.
        #   Same shape as the owner: the main session relies on the platform loading AGENTS.md; the assistant relies on this loop.
        instruction = _inject_assistant_hint(session_key, instruction)

        # ── execution short title: when not passed explicitly, reuse the original execution's name and mark it as a continuation ──
        if not (exec_name or "").strip():
            _base_name = (orig.get("exec_name") or "").strip()
            exec_name = f"{_base_name}（续接）" if _base_name else ""

        # ── 2. create the new execution record ──
        # look up task_labs for run_uuid naming
        task_labs = ""
        tl_rows = execute_sql(
            "SELECT task_labs FROM agx_task_base WHERE task_id = %s",
            (task_id,),
        )
        if tl_rows:
            task_labs = (tl_rows[0].get("task_labs") or "").strip()
        run_uuid = agx_generate_uuid(prefix="run", infix=task_labs)

        # continuation count: the max across all records of this session, plus one
        count_rows = execute_sql(
            "SELECT COALESCE(MAX(continue_count), 0) AS cnt FROM agx_task_execution WHERE session_id = %s",
            (session_key,),
        )
        new_count = count_rows[0]["cnt"] + 1

        # ── session registration: a continuation reuses the same session_id, ensuring the existing session's counter increments ──
        ensure_session(
            session_id=session_key,
            name_hint=_session_name_hint(session_name, task_labs, "", exec_name),
            agent_id=agent_id,
            project_id=project_id,
            task_labs=task_labs,
            run_uuid=run_uuid,
            creator_agent_id=caller_agent_id,
            network_id=network_id,
        )

        sql = """
        INSERT INTO agx_task_execution
            (network_id, project_id, task_id, agent_id, task_run_uuid, host_id,
             exec_instruction, send_agent_id, send_host_id, session_id, send_session_id, parent_run_uuid,
             run_status, timeout_seconds, needs_reply, continue_count, needs_confirm, prev_task, next_task, exec_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'running', %s, %s, %s, %s, %s, %s, %s)
        """
        execute_sql(
            sql,
            (
                network_id,
                project_id,
                task_id,
                agent_id,
                run_uuid,
                host_id,
                instruction,
                caller_agent_id,
                _resolve_send_host_id(caller_agent_id) or None,
                session_key,  # reuse the original session_id
                # * 2026-10-09 FIX: fill in send_session_id (aligned with dispatch/schedule)
                #   downstream daemon retries / watchdog receipts read this DB column; a missing value makes receipts land in the wrong place
                send_session_id or None,
                original_run_uuid,  # parent_run_uuid
                timeout_seconds,
                needs_reply,
                new_count,
                nc,  # inherit the original task / explicit override
                prev_task or None,
                next_task or None,
                exec_name or None,
            ),
            commit=True,
        )

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _sync_entity(
            "execution",
            run_uuid,
            {
                "task_run_uuid": run_uuid,
                "network_id": network_id,
                "project_id": project_id,
                "task_id": task_id,
                "agent_id": agent_id,
                "send_agent_id": caller_agent_id,
                "send_host_id": _resolve_send_host_id(caller_agent_id),
                "send_session_id": send_session_id or "",
                "host_id": host_id,
                "run_status": "running",
                "session_id": session_key,
                "parent_run_uuid": original_run_uuid,
                "exec_instruction": instruction,
                "exec_name": exec_name or "",
                "started_at": "",
                "timeout_seconds": str(timeout_seconds),
                "continue_count": str(new_count),
                "needs_reply": str(needs_reply),
                "needs_confirm": str(nc),
                "prev_task": prev_task or "",
                "next_task": next_task or "",
            },
        )

        # ── 3. assemble and dispatch the command protocol message ──
        now_ts = int(datetime.now().timestamp())
        msg_id = agx_generate_uuid(prefix="msg")

        # ── resolve send_agent ──
        send_agent = caller_agent_id

        message = {
            "proto_ver": "1.0",
            "msg_id": msg_id,
            "timestamp": now_ts,
            "target_id": agent_id,
            "sender_id": sender_id,
            "send_agent": send_agent,
            "msg_type": "command",
            "payload": {
                "command": "continue_task",
                "task_run_uuid": run_uuid,
                "task_id": task_id,
                "project_id": project_id,
                "instruction": instruction,
                "session_key": session_key,
                # * 2026-10-22: self_session_id = the session id of [the receiver itself] (= session_key, i.e. the session the original ran in).
                #   See the same field's comment in dispatch (self means the current session on both ends).
                "self_session_id": session_key or "",
            },
        }

        queue_key = f"agx:queue:{host_id}:agent:{agent_id}"
        r = get_redis_client()
        r.rpush(queue_key, json.dumps(message, ensure_ascii=False))
        queue_len = r.llen(queue_key)

        return {
            "run_uuid": run_uuid,
            "msg_id": msg_id,
            "session_key": session_key,
            "host_id": host_id,
            "queue": queue_key,
            "queue_length": queue_len,
            "timestamp": now_ts,
        }

    except Exception as e:
        return {"error": str(e)}


def _resolve_host_manager(host_id: str) -> dict:
    """Look up the host_manager agent for a host_id.

    This is the core resolution function for multi-host communication — input host_id, output agent_id + agent_name.
    A dispatcher never hard-codes an agent_id; it only needs the target host_id.

    Lookup order:
    1. preferred: exact host_id match + agent_role='host_manager' + active_status=1
    2. fallback: agent_name = 'agx_host' (single-host compatibility mode)

    Args:
        host_id: the target host id (e.g. "robert_windows")

    Returns:
        dict: {"agent_id": "...", "agent_name": "...", "host_id": "..."}
              {"error": "..."} when nothing is found
    """
    rows = execute_sql(
        """SELECT agent_id, agent_name, host_id
           FROM agx_agent_registry
           WHERE host_id = %s AND agent_role = 'host_manager' AND active_status = 1
           ORDER BY CASE WHEN agent_name = 'agx_host' THEN 0 ELSE 1 END, pkid
           LIMIT 1""",
        (host_id,),
        commit=False,
    )
    if rows:
        return {
            "agent_id": rows[0]["agent_id"],
            "agent_name": rows[0]["agent_name"],
            "host_id": rows[0]["host_id"],
        }

    # fallback: single-host compatibility, look up agent_name = 'agx_host' (regardless of host_id)
    rows = execute_sql(
        """SELECT agent_id, agent_name, host_id
           FROM agx_agent_registry
           WHERE agent_name = 'agx_host' AND agent_role = 'host_manager' AND active_status = 1
           ORDER BY pkid
           LIMIT 1""",
        commit=False,
    )
    if rows:
        return {
            "agent_id": rows[0]["agent_id"],
            "agent_name": rows[0]["agent_name"],
            "host_id": rows[0]["host_id"],
        }

    return {"error": f"no host_manager found for host_id '{host_id}'"}


# -----------------------------------------------------------
# task event stream / notice delivery (2026-10-28)
# -----------------------------------------------------------
# design basis: docs/agent_docs, the watchdog alert/wake/acknowledgement remediation plan
#
# role: notifications such as wakes/alerts no longer each create an execution (that spawns
#       "alerts that spawn alerts" and polluted the task table with 408 rows); instead:
#         (1) write alert_* / wake_count fields on [the watched task row] (state)
#         (2) write one row into agx_task_execution_event (a trail that can be sorted into a timeline)
#         (3) deliver the body into the target session using a send_text envelope (a channel; cross-host, cacheable offline)
# -----------------------------------------------------------

EVENT_TABLE = "agx_task_execution_event"


def _ao_record_event(
    task_run_uuid: str,
    event_type: str,
    level: int = 0,
    from_agent: str = "",
    to_agent: str = "",
    to_session: str = "",
    channel: str = "",
    reason: str = "",
    payload: dict | None = None,
    delivery_state: str = "pending",
) -> str:
    """Write one task event (wake/alert/receipt/acknowledgement...). Returns event_id.

    Idempotence is not this function's job — whether to send is decided by the caller (the watchdog)
    from wake_count/alert_count.
    """
    event_id = "evt_" + uuid.uuid4().hex[:12]
    try:
        execute_sql(
            "INSERT INTO " + EVENT_TABLE + " "
            "(event_id, task_run_uuid, event_type, level, from_agent, to_agent, "
            " to_session, channel, reason, payload, delivery_state) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                event_id,
                task_run_uuid,
                event_type,
                int(level),
                from_agent or None,
                to_agent or None,
                to_session or None,
                channel or None,
                (reason or "")[:255] or None,
                json.dumps(payload, ensure_ascii=False) if payload else None,
                delivery_state,
            ),
            commit=True,
        )
    except Exception as e:
        _core_log(f"记录任务事件失败 | {event_type} {task_run_uuid}: {e}", "WARN")
    return event_id


def _ao_mark_event_delivery(event_id: str, delivery_state: str, ack: bool = False) -> bool:
    """Write back an event's delivery/acknowledgement result (called by daemon / ws_client / the receiver)."""
    if not event_id:
        return False
    try:
        if ack:
            execute_sql(
                "UPDATE " + EVENT_TABLE + " SET ack_state='read', ack_at=NOW() "
                "WHERE event_id=%s AND ack_state<>'read'",
                (event_id,),
                commit=True,
            )
        else:
            execute_sql(
                "UPDATE " + EVENT_TABLE + " SET delivery_state=%s, delivered_at=NOW() "
                "WHERE event_id=%s",
                (delivery_state, event_id),
                commit=True,
            )
        return True
    except Exception as e:
        _core_log(f"回填事件状态失败 | {event_id}: {e}", "WARN")
        return False


def _ao_send_notice(
    target_agent_id: str,
    session_key: str,
    text: str,
    host_id: str = "",
    event_id: str = "",
    delivery_key: str = "",
    msg_type: str = "command",
    send_agent: str = "agx_core",
) -> dict:
    """Send a plain-text notice to the session of a given agent (no execution is created).

    * For [system processes] (agx_watchdog) only, and **not exposed as an MCP method**.
      Lao Feng, 2026-10-28: notices are sent by the watchdog and do not count as "Agents chatting with
      each other", so they are not bound by agx_send_text's "may only send to yourself" guard.
      An ordinary Agent still has no such capability (the MCP layer has no entry point at all).

    Args:
        target_agent_id: the receiving agent (usually the send_agent_id of the watched task)
        session_key: the target session (e.g. agent:xxx:main)
        text: the body
        host_id: the target host (looked up in agx_agent_registry when empty)
        event_id: the related event id (written into the envelope so the receiver can acknowledge)
        delivery_key: delivery-confirmation marker (optional). When passed it is written into
            payload.task_run_uuid, and daemon/ws_client produce agx:ws:result:<key> from it, after which
            the daemon writes delivery_state back onto the event. **Must start with run_**
            (the daemon result scan only recognizes that prefix).
            Omitted = pure fire-and-forget (the current alert behaviour, fully unchanged).
        msg_type: envelope type. Defaults to "command" (alerts/wakes keep using it).
            * 2026-10-29: a receipt MUST pass "receipt" — in the protocol msg_type is one of
              dispatch/receipt/urge, and a receipt is not a command.
        send_agent: the envelope sender. Defaults to "agx_core" (sent on behalf of the system).
            * 2026-10-29: a receipt MUST pass [the completing agent's agent_id]. A receipt is what the
              completing side hands to the initiating side, not something agx_core sends — if it says
              agx_core, the receiver cannot tell who finished it (only guess from the body).

    Returns:
        {"ok": bool, "queue": str, "msg_id": str, "error": str|""}
    """
    if not target_agent_id or not session_key:
        return {"ok": False, "error": "target_agent_id/session_key required"}
    try:
        if not host_id:
            rows = execute_sql(
                "SELECT host_id FROM agx_agent_registry WHERE agent_id=%s AND active_status=1 LIMIT 1",
                (target_agent_id,),
                commit=False,
            )
            host_id = (rows[0].get("host_id") or "") if rows else ""
        if not host_id:
            return {"ok": False, "error": f"cannot resolve host_id for '{target_agent_id}'"}

        msg_id = "msg_" + uuid.uuid4().hex[:12]
        message = {
            "proto_ver": "1.0",
            "msg_id": msg_id,
            "timestamp": int(time.time()),
            "target_id": target_agent_id,
            "sender_id": send_agent or "agx_core",
            "send_agent": send_agent or "agx_core",
            "msg_type": msg_type or "command",
            "payload": {
                "command": "send_text",
                "instruction": text,
                "session_key": session_key,
                "notice_event_id": event_id or "",
                # * 2026-10-29: delivery-confirmation marker (receipt path only).
                #   The daemon's send_text branch passes it through unchanged as the ws queue's run_uuid,
                #   ws_client writes agx:ws:result:<delivery_key>, and the daemon then writes the event back.
                #   Alerts do not pass it -> empty string -> the ws_client result key is empty (old behaviour unchanged).
                "task_run_uuid": delivery_key or "",
            },
        }
        queue_key = f"agx:queue:{host_id}:agent:{target_agent_id}"
        r = get_redis_client()
        r.rpush(queue_key, json.dumps(message, ensure_ascii=False))
        _core_log(f"通知已入队 | {event_id or msg_id} → {queue_key} | {session_key}", "INFO")
        return {"ok": True, "queue": queue_key, "msg_id": msg_id, "error": ""}
    except Exception as e:
        _core_log(f"通知入队失败 | {event_id or ''} {session_key}: {e}", "WARN")
        return {"ok": False, "error": str(e)}
