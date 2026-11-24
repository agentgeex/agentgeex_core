#!/usr/bin/env python3
"""
agx_watchdog.py -- AgentGeeX task watchdog

Duties:
  - Plan scheduling: scan pending executions and activate them automatically when the conditions hold
    - prev_task finished, or there is no preceding task
    - all child tasks are terminal (not running/pending)
    - scheduled_at is due (when set)
  - Timeout monitoring: periodically scan every running execution
  - Timeout / session gone -> first collect diag_info by agent_type (including live data) -> send watchdog_alert to the original send_agent_id
  - Alert deduplication: after an alert on one execution a grace period starts (GRACE_PERIOD, default 1800 seconds = 20 minutes)
    during which no repeat alert is sent. When it expires, if the task is still anomalous another alert is sent, alert_count accumulates correctly,
    up to MAX_RETRIES times (default 3); at the limit, escalate to a human and stop tracking.
  - Design principle: use the state.grace_until field to control deduplication, with alert_count persisted in Redis.
    The key has no TTL -- it is deleted only when the execution ends normally or escalates to a human.
  - System-level failure (>= N executions timing out at once) -> notify a human (alert escalation)

Design principles:
  - The watchdog does not reason. It does not read logs and does not judge real-death versus false-death.
  - The watchdog does not do fallback marking. It never calls agx_finish_task itself.
  - It only "detects an anomaly -> alerts". Analysis and decisions belong to agents that have an LLM.
  - Alert deduplication. Do not bombard an agent; at most one alert, then escalate to a human.

How to start:
  Started as a child process (fork) by agx_daemon.py
  It can also run standalone: python agx_watchdog.py
"""

import json
import os
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime

import redis as redis_mod

# Make sure PROJECT_ROOT is on sys.path (config_loader / common etc. live at the project root)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from config_loader import config

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# * 2026-11-16: message envelopes go through agx_proto only (single construction point; stop hand-writing them)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agx_proto import build_struct, dumps_struct, msg_type_for  # noqa: E402
from agx_mcp.agx_core import (
    _ao_activate_task,
    _is_safe_session_key,
    _ao_record_event,
    _ao_send_notice,
    _ao_mark_event_delivery,
    # * 2026-10-30: the pending-acknowledgement index. Its decision rule may have only one source --
    #   if the write side (MCP) and the read side (the watchdog) each copy the same if-conditions, they will inevitably disagree.
    RECEIPT_PENDING_IDX,
    _receipt_pending_wanted,
    _receipt_pending_score,
)

# -- Config ------------------------------------------------------

_GLOBAL_REDIS_POOL = redis_mod.ConnectionPool(
    host=config.redis.get("host", "127.0.0.1"),
    port=int(config.redis.get("port", 6379)),
    db=int(config.redis.get("db", 0)),
    username=config.redis.get("username", "default"),
    password=config.redis.get("password", ""),
    decode_responses=True,
    max_connections=5,  # The daemon core business uses 5 slots by default, which leaves plenty of headroom
    socket_timeout=5,  # 5s command timeout, preventing a distributed task from hanging on the network
    socket_connect_timeout=5,  # 5s connect timeout, so a network blip triggers reconnect quickly
    health_check_interval=10,
    socket_keepalive=True,  # Enable OS-level TCP keepalive
)


POLL_INTERVAL = int(config.daemon.get("watchdog_poll_interval", 30))
SYSTEM_CRISIS_THRESHOLD = int(config.daemon.get("watchdog_crisis_threshold", 20))
FALLBACK_TIMEOUT = int(config.task_timeout)  # Read task_timeout from the config file agx_config.json

# -- Alert deduplication ------------------------------------------
# Grace period after each alert in seconds. Default 1800 = 20 minutes, and it must match the .get() fallback below --
# a comment here once said "default 30 minutes" while the code said 1800, so anyone changing the config would compute it wrong (corrected 2026-10-30).
GRACE_PERIOD = int(config.daemon.get("watchdog_grace_period", 1800))
MAX_RETRIES = int(config.daemon.get("watchdog_max_retries", 3))  # Maximum number of alerts; after that, escalate to a human

# -- System crisis ------------------------------------------
CRISIS_GRACE = int(config.daemon.get("watchdog_crisis_grace", 600))  # System crisis re-report interval in seconds, default 10 minutes
CRISIS_MAX_RETRIES = int(config.daemon.get("watchdog_crisis_max_retries", 3))  # Maximum number of system crisis notifications

# -- Test mode ------------------------------------------
TEST_MODE = str(config.daemon.get("watchdog_test_mode", "")).lower() in ("1", "true", "yes")
TEST_TTL = int(config.get("test_data_ttl", 604800))  # Redis TTL for test data (default 7 days)
TEST_TASK_LIST = "agx:test:task"  # Key prefix for test-mode task records
TEST_ALERT_LIST = "agx:test:alert"  # Key prefix for test-mode alert records
WS_TIMEOUT = int(config.daemon.get("watchdog_ws_timeout", 5))  # General WebSocket timeout in seconds
WS_HIST_TIMEOUT = int(config.daemon.get("watchdog_ws_hist_timeout", 10))  # WebSocket history query timeout in seconds

# Alert state Redis key prefix
ALERT_STATE_PREFIX = "agx:watchdog:alert"  # Alert state written by the watchdog
CRISIS_STATE_PREFIX = "agx:watchdog:crisis"  # System crisis state key

# -- Transport-phase close-out (plan B, Feng 2026-10-27) ------------------
#   After the split, `started_at IS NULL` means "sent, the agent has not read the instruction" = the transport phase,
#   and the watchdog does not judge a business timeout on it and does not alert (that belongs to the business phase).
#   But doing nothing at all would leave the task hanging at running forever (if the message is lost nobody knows) -- hence two openings:
#     (1) a clear delivery failure (delivery=error/timeout) reaching the threshold -> resend once proactively
#     (2) waiting too long without starting -> report once (still not judged dead; left to a human / the PM)
#   Note: delivery=pending and delivered are never resent -- the former may be normal queueing,
#         and for the latter the message is already in the session (the agent is busy), so resending only adds load.
SEND_RESEND_AFTER = int(config.daemon.get("send_resend_after", 600))   # 10 minutes
SEND_RESEND_MAX = int(config.daemon.get("send_resend_max", 2))         # At most 2 resends
SEND_REPORT_AFTER = int(config.daemon.get("send_report_after", 3600))  # Report once every 60 minutes

# Plan-scheduling host-isolation log throttle: no spam while the count is unchanged (previously one line every 2s, 40k lines a day)
_ISO_LOG = {"sig": None, "ts": 0.0}

# -- Wake mechanism ------------------------------------------
WAKE_ENABLED = str(config.daemon.get("watchdog_wake_enabled", "1")).lower() in ("1", "true", "yes")
WAKE_RATIO = float(config.daemon.get("watchdog_wake_ratio", "0.9"))  # The percentage of the timeout at which a wake fires
# * Feng, 2026-10-28: waking is now tiered (70% / 80% / 90%), each tier fires only once (see _try_wake).
#   The old behaviour fired once at WAKE_RATIO (50%) and afterwards relied on the Redis key expiring to re-fire passively;
#   measured: the same run was woken 3 times at exactly 240s intervals (the TTL of WAKE_GRACE x 2), so the count>1 suppression never worked.
WAKE_TIERS = [
    float(x) for x in str(config.daemon.get("watchdog_wake_tiers", "0.7,0.8,0.9")).split(",")
    if str(x).strip()
]
# Deduplication TTL for plan completion notifications in seconds.
# 0 = deduplicate forever (no expiry). A terminal plan's "completed" is a final fact,
# so one notification should suppress it permanently -- otherwise once the TTL passes the same plan is treated as "newly completed" and notified again
# (BUG-B1: plan_daily_log_3ccf1d4ebd0e woke the creator's main session at 21:17 on several consecutive days).
PLAN_NOTIFIED_TTL = int(config.daemon.get("watchdog_plan_notified_ttl", 0))

WAKE_GRACE = int(config.daemon.get("watchdog_wake_grace", 600))  # Grace period after a wake in seconds
# Watchdog wake message: read from daemon.watchdog_wake_message in the config file; takes effect after the watchdog is restarted
WAKE_MESSAGE = str(
    config.daemon.get(
        "watchdog_wake_message",
        "系统提醒：如果任务仍在执行中，请继续完成。如果已完成，请调用 agx_finish_task 更新状态。\n"
        '另外，如果你收到一条包含 "task_run_uuid" 字段的 JSON 消息，'
        "这表示有一个超时任务需要你检查。请用该 task_run_uuid 查询 agx_get_execution，"
        "确认任务是否已完成，若已完成请调用 agx_finish_task 标记状态。",
    )
)
WAKE_STATE_PREFIX = "agx:watchdog:wake"  # Wake state key prefix
_WAKE_FAIL_TS: dict = {}  # Wake send-failure backoff table {run:tier -> ts} (in memory, cleared on restart)
SESSION_DONE_WAKE_COOLDOWN = int(config.daemon.get("watchdog_session_done_wake_cooldown", 60))  # Session-done wake cooldown in seconds

WORK_DIR = os.path.expanduser(config.get("work_dir", "~/.agx"))

# -- Local identity and multi-host watchdog (2026-10-25 distributed rework) ---------
# Background: one dog per machine. A dog only scans tasks of *agents under its own host_id*,
#   preventing several dogs from scanning the same task -> duplicate alerts + duplicate activation + false crisis detection.
# Host keepalive is a network-wide public duty: every dog checks every heartbeat, and an agx:lock: takeover
#   guarantees only one dog alerts per lost host (preventing an alert storm).
HOST_ID = config.get("host_id", "") or os.environ.get("AGX_DAEMON_HOST_ID", "")
NETWORK_ID = config.get("network_id", "") or os.environ.get("AGX_DAEMON_NETWORK_ID", "")


# -- Full local host set (2026-11-04 multi-instance) -------------------------
# One machine can run several OpenClaw instances, each with its own host_id. They are the *same physical machine*,
# so one dog should manage them all -- otherwise the second instance's timeouts/wakes/receipts/plan scheduling are handled by nobody.
# The set comes from the same source as the daemon: config.host_id plus config.gateways[].host_id.
# With no gateways configured it is {HOST_ID}, behaving byte for byte as before the rework.
def _local_host_ids() -> set:
    ids = set()
    h = (HOST_ID or "").strip()
    if h:
        ids.add(h)
    for _g in config.get("gateways") or []:
        if isinstance(_g, dict):
            _gh = str(_g.get("host_id") or "").strip()
            if _gh:
                ids.add(_gh)
    return ids


HOST_IDS = _local_host_ids()
# Quote list used for SQL generation, e.g. "'test_srv_a','test_srv_a2'"
HOST_IDS_SQL = ",".join(f"'{_h}'" for _h in sorted(HOST_IDS)) or "''"
# For log display, e.g. "test_srv_a,test_srv_a2"
HOST_IDS_LABEL = ",".join(sorted(HOST_IDS)) or "?"

# Host keepalive parameters
HOST_HEARTBEAT_TIMEOUT = int(config.daemon.get("watchdog_host_heartbeat_timeout", 90))  # No heartbeat for longer than this many seconds counts as lost
HOST_ALERT_COOLDOWN = int(config.daemon.get("watchdog_host_alert_cooldown", 600))  # Alert cooldown for the same lost host in seconds
HOST_ORPHAN_ESCALATE = int(config.daemon.get("watchdog_host_orphan_escalate", 3600))  # How long after going missing to escalate to a human in seconds


# -- Logging ------------------------------------------------------


from log_setup import setup_logging, update_file_log_level

log = setup_logging("WATCHDOG", log_dir=os.path.join(PROJECT_ROOT, "logs"))

# Log level (config driven, default INFO)
_wd_log_level = config.daemon.get("watchdog_log_level", "INFO")
update_file_log_level("WATCHDOG", _wd_log_level)


# -- Database / Redis --------------------------------------------


def _get_redis():
    import redis as redis_mod

    # return redis_mod.Redis(
    #     host=config.redis.get("host", "127.0.0.1"),
    #     port=int(config.redis.get("port", 6379)),
    #     db=int(config.redis.get("db", 0)),
    #     username=config.redis.get("username", "default"),
    #     password=config.redis.get("password", "") or None,
    #     decode_responses=True,
    #     # --- keepalive defence tuned for the Windows environment ---
    #     socket_timeout=15,  # relax the underlying socket read/write timeout to 15s
    #     socket_keepalive=True,  # enable OS-level TCP probes
    #     health_check_interval=10,  # [keepalive core] force a PING every 10 seconds
    # )

    return redis_mod.Redis(connection_pool=_GLOBAL_REDIS_POOL)


def _exec_mysql(sql: str):
    """Execute write SQL, accessed uniformly through updateBySql.

    Returns the affected row count (int): updateBySql has returned cursor.rowcount since 2026-10-26,
    for _claim_reply to use in its CAS claim check. Old callers do not rely on the return value and are unaffected.
"""
    from common.mysql.mysqlservice import updateBySql

    return updateBySql(sql)


def _get_mysql_rows(sql: str) -> list:
    """Execute read SQL, accessed uniformly through getRowsByFullSQl."""
    from common.mysql.mysqlservice import getRowsByFullSQl

    return getRowsByFullSQl(sql)


# -- Helpers ------------------------------------------------------


def _escape(value: str) -> str:
    """MySQL string escaping."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _gen_msg_id() -> str:
    return "msg_" + uuid.uuid4().hex[:12]


# -- Alert state management (Redis) -----------------------------


def get_alert_state(run_uuid: str) -> dict | None:
    """
    Read an execution's alert state from Redis.

    Returns:
        dict: the alert state, containing status/alert_count/first_alert_at/last_alert_at/reason
        None: never alerted
"""
    try:
        r = _get_redis()
        raw = r.get(f"{ALERT_STATE_PREFIX}:{run_uuid}")
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return None


def save_alert_state(run_uuid: str, state: dict):
    """
    Persist the alert state into Redis.

    Deduplication is no longer controlled by key TTL but by the `grace_until` field inside the state.
    alert_count accumulates correctly (it is not reset by key expiry),
    and at MAX_RETRIES it escalates to a human and deletes the key.
"""
    try:
        r = _get_redis()
        r.set(f"{ALERT_STATE_PREFIX}:{run_uuid}", json.dumps(state, ensure_ascii=False))
    except Exception as e:
        log(f"保存报警状态失败: {e}", "WARN")


def delete_alert_state(run_uuid: str):
    """Delete the alert state. Called after escalation or after the execution is resolved."""
    try:
        r = _get_redis()
        r.delete(f"{ALERT_STATE_PREFIX}:{run_uuid}")
    except Exception:
        pass


# -- System crisis state management ---------------------------------


def _get_crisis_state() -> dict:
    """Get the system crisis deduplication state."""
    try:
        r = _get_redis()
        raw = r.get(CRISIS_STATE_PREFIX)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return {}


def _save_crisis_state(state: dict):
    """Save the system crisis deduplication state."""
    try:
        r = _get_redis()
        r.set(CRISIS_STATE_PREFIX, json.dumps(state, ensure_ascii=False))
    except Exception as e:
        log(f"保存危机状态失败: {e}", "WARN")


def _delete_crisis_state():
    """Clear the system crisis state (called once the crisis is over)."""
    try:
        r = _get_redis()
        r.delete(CRISIS_STATE_PREFIX)
    except Exception:
        pass


# -- Wake state management ---------------------------------------


def _get_wake_state(run_uuid: str) -> dict | None:
    """Get the wake record."""
    try:
        r = _get_redis()
        data = r.hgetall(f"{WAKE_STATE_PREFIX}:{run_uuid}")
        if data:
            data["sent_at"] = int(data.get("sent_at", 0))
            data["count"] = int(data.get("count", 0))
        return data if data else None
    except Exception:
        return None


def _heartbeat_file_path() -> str:
    """Heartbeat file path (same convention as _WATCHDOG_HEARTBEAT_FILE on the daemon side)."""
    import tempfile
    return config.daemon.get("watchdog_heartbeat_file") or os.path.join(
        tempfile.gettempdir(), "agx_watchdog_heartbeat"
    )


def _touch_heartbeat():
    """Refresh the heartbeat file mtime -- meaning this round is still alive and the main loop is advancing."""
    #
    # Why it must be called at every sub-step boundary (2026-10-22):
    #   if the heartbeat were only written at the top of the main loop, any sub-step taking too long in a round would make the daemon judge a false death and hard-kill it.
    #   Measured worst case: _query_session_status does 4 ws.recv calls,
    #   connect/auth/list each at WS_TIMEOUT (120s) plus history at WS_HIST_TIMEOUT (300s) = 660s;
    #   in one round _handle_anomaly calls it once per anomaly, so 3 anomalies already exceed 1800s.
    #
    # Why it cannot be changed to a separate thread writing periodically (original plan 3, rejected):
    #   the heartbeat's only use is to let the daemon judge from its mtime whether the main loop has hung.
    #   A separate thread would keep writing while the main loop is hung -> the heartbeat is always fresh -> false-death detection completely fails.
    #   The write must happen at a point where the main loop really advanced, otherwise it means nothing.
    try:
        with open(_heartbeat_file_path(), "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def _save_wake_state(run_uuid: str, state: dict):
    """Save the wake record, TTL = WAKE_GRACE x 2 (grace plus a buffer)."""
    try:
        r = _get_redis()
        key = f"{WAKE_STATE_PREFIX}:{run_uuid}"
        r.hset(key, mapping=state)
        r.expire(key, WAKE_GRACE * 2)
    except Exception as e:
        log(f"保存唤醒状态失败: {e}", "WARN")


def _clear_wake_state(run_uuid: str):
    """Clean up the wake state. Called when the task completes or a timeout is judged."""
    try:
        r = _get_redis()
        r.delete(f"{WAKE_STATE_PREFIX}:{run_uuid}")
    except Exception:
        pass


def escalate_to_human(run_uuid: str, reason: str, state: dict):
    """
    Alert escalation: notify the human assistant.

    One unified entry point covering two scenarios:
      - individual escalation: run_uuid is a concrete execution ID and state carries alert_count
      - system crisis: run_uuid="SYSTEM_CRISIS" and state carries crisis_count

    The current implementation just prints a log. It can be replaced later with message push (Feishu / DingTalk / email etc.).
"""
    is_crisis = run_uuid == "SYSTEM_CRISIS"
    count = state.get("alert_count", state.get("crisis_count", 0))

    # TODO: replace with a real human notification channel
    if is_crisis:
        log(
            f" [系统危机 第{count}次] {reason}\n   首次检测: {state.get('first_alert_at', '?')}\n   上次通知: {state.get('last_alert_at', '?')}\n    请立即人工介入检查基础设施。",
            "CRITICAL",
        )
    else:
        log(
            f" [告警升级] execution={run_uuid} 已报警 {count} 次未解决\n"
            f"   原因: {reason}\n"
            f"   首次报警: {state.get('first_alert_at', '?')}\n"
            f"   上次报警: {state.get('last_alert_at', '?')}\n"
            f"    请人工介入检查该 execution 的状态。",
            "CRITICAL",
        )


# -- Timeouts ------------------------------------------------------


def get_timeout_for_execution(exc: dict) -> int:
    """Get the timeout from the execution data. Set by agx_dispatch_task at dispatch time."""
    raw = exc.get("timeout_seconds", "")
    if raw and str(raw).isdigit():
        return int(raw)
    return FALLBACK_TIMEOUT


def get_elapsed_seconds(started_at: str) -> int:
    """Seconds elapsed since started_at. Returns 0 when it is empty (the caller is responsible for the backstop)."""
    if not started_at:
        return 0
    try:
        dt = datetime.strptime(str(started_at), "%Y-%m-%d %H:%M:%S")
        return int((datetime.now() - dt).total_seconds())
    except Exception:
        return 0


def get_queue_wait_seconds(exc: dict) -> int:
    """Return "queued but not yet counted" seconds (the case where started_at is empty).

    * 2026-10-19, together with the lazy started_at write:
      started_at is now written only when the receiving agent first reads the execution,
      so during "delivered but the agent has not started reading" started_at is empty and
      get_elapsed_seconds() always returns 0 -> the watchdog would never judge a timeout,
      and a dead task would hang at running forever.

      This function uses created_at as the queueing start point, so the watchdog can still alert
      after the "queued + not counted" timeout.

    Returning 0 means it cannot be judged (no created_at), and the caller should skip it.
"""
    created_at = exc.get("created_at", "")
    if created_at:
        try:
            dt = datetime.strptime(str(created_at), "%Y-%m-%d %H:%M:%S")
            return max(0, int((datetime.now() - dt).total_seconds()))
        except Exception:
            pass

    # Cache missing created_at (old data / a partial sync path) -> go back to MySQL once
    run_uuid = exc.get("_id", "")
    if not run_uuid:
        return 0
    try:
        rows = _get_mysql_rows(
            f"SELECT created_at FROM agx_task_execution WHERE task_run_uuid='{_escape(run_uuid)}' LIMIT 1"
        )
        if rows and rows[0].get("created_at"):
            ca = rows[0]["created_at"]
            dt = ca if isinstance(ca, datetime) else datetime.strptime(str(ca)[:19], "%Y-%m-%d %H:%M:%S")
            exc["created_at"] = dt.strftime("%Y-%m-%d %H:%M:%S")
            return max(0, int((datetime.now() - dt).total_seconds()))
    except Exception as e:
        log(f" get_queue_wait_seconds({run_uuid}) 回源失败: {e}", "WARN")
    return 0


# -- tmux detection (auxiliary; WebSocket is the primary diagnostic channel) ----------------


def is_tmux_session_alive(session_name: str) -> bool:
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
            timeout=WS_TIMEOUT,
        )
        return result.returncode == 0
    except Exception:
        return False


# -- Scanning ------------------------------------------------------

# * 2026-10-29: _get_agent_general_task / _get_agent_watchdog_task were deleted.
#   They were artefacts of the era when "an alert = create a watchdog_alert execution": an alert first had to look up the target's
#   task_id before it could dispatch. Now that receipts/wakes/alerts go through event plus plain messages, those two functions have zero call sites.
#   The reason to delete them is not "the code looked bad" but: keeping them leaves a one-line-reachable path
#   back to the old design -- the next person asking "why do alerts not create tasks any more" could put the disease right back.


def _get_agent_type(agent_id: str) -> str:
    """Query the agent_type field, used to decide the runtime mode (openclaw / claude_code etc.)."""
    try:
        rows = _get_mysql_rows(f"SELECT agent_type FROM agx_agent_registry WHERE agent_id='{_escape(agent_id)}'")
        if rows:
            return rows[0].get("agent_type", "") or "openclaw"
    except Exception:
        pass
    return "openclaw"


def _get_my_agent_ids() -> set:
    """Get the set of all active agent_ids *under this machine's host_id* (with a Redis cache).

    * 2026-10-25 multi-host rework: this is the divider of "one dog per machine".
      The watchdog is responsible only for the tasks of *agents on its own machine* (timeout/wake/receipt),
      never across hosts. Otherwise several dogs would scan the same task -> duplicate alerts + duplicate activation
      + false crisis thresholds (each one's detected count stacking up).

    Cached for 60 seconds, the same window as the daemon's agx:worker_queues:{host_id},
    so a "newly created agent" is discovered by this machine's dog within 60 seconds.
"""
    if not HOST_IDS:
        return set()
    cache_key = f"agx:watchdog:my_agents:{HOST_ID}"
    try:
        r = _get_redis()
        cached = r.get(cache_key)
        if cached:
            return set(json.loads(cached))
    except Exception:
        pass

    try:
        rows = _get_mysql_rows(
            f"SELECT agent_id FROM agx_agent_registry "
            f"WHERE host_id IN ({HOST_IDS_SQL}) AND active_status=1"
        )
        ids = {row.get("agent_id", "") for row in rows if row.get("agent_id")}
    except Exception as e:
        log(f" 获取本机 agent 列表失败: {e}", "WARN")
        return set()

    try:
        r = _get_redis()
        r.set(cache_key, json.dumps(sorted(ids)), ex=60)
    except Exception:
        pass
    return ids


def get_running_executions() -> list:
    """Get the running executions of *agents under this machine's host* from Redis.

    * 2026-10-25 multi-host rework (critical): the original implementation scanned the whole table for running,
      which under the "one dog per machine" topology made several dogs handle the same task:
        - duplicate alerts (the alert_state read-judge-write is not atomic, so every dog reads None)
        - duplicate activation (the UPDATE in _ao_activate_task has no pending condition)
        - false crisis detection (check_system_crisis counts this round's total detected)
      It now returns only executions of this machine's agents, removing the overlapping view at the root.
"""
    try:
        r = _get_redis()
        exec_ids = list(r.zrange("agx:idx:execution:status:running", 0, -1))
    except Exception as e:
        log(f" 获取 running executions 失败 (Redis): {e}", "ERROR")
        return []

    my_agents = _get_my_agent_ids()
    if not my_agents:
        # Cannot get this machine's agent list -> return empty conservatively; better to skip this round than to manage someone else's tasks
        log(f" 本机 agent 名单为空 (host_id={HOST_IDS_LABEL or '?'})，本轮跳过任务扫描", "WARN")
        return []

    results = []
    skipped_other_host = 0
    for exc_id in exec_ids:
        try:
            data = r.hgetall(f"agx:entity:execution:{exc_id}")
            if not data or data.get("run_status") != "running":
                continue
            if data.get("agent_id", "") not in my_agents:
                skipped_other_host += 1
                continue
            data["_id"] = exc_id
            results.append(data)
        except Exception:
            continue
    if skipped_other_host:
        log(
            f" 任务扫描 host 隔离 | 本机={HOST_IDS_LABEL} | 本机agent数={len(my_agents)} | "
            f"归属本机running={len(results)} | 跳过他机={skipped_other_host}",
            "DEBUG",
        )
    return results


def _rebuild_alert_state_from_db(run_uuid: str) -> dict | None:
    """When the Redis alert state is lost (restart / memory eviction / key deleted), rebuild it from the main row.

    * 2026-10-28 addition: deduplication used to rely *entirely on Redis* -- if state was None it simply alerted again.
      So one Redis restart re-alerted the same task (one of the hotbeds of the historic "alerts begetting alerts").
      But alert_level / alert_count / alerted_at are already written on agx_task_execution,
      a second truth more durable than Redis. This uses it to fill the gap, covering the Redis-loss case.

    Returns:
        dict  -> the reused state (also written back to Redis, so later rounds take the fast path)
        None  -> nothing in the DB either, so an alert really is due
"""
    try:
        rows = _get_mysql_rows(
            "SELECT alert_level, alert_count, alerted_at FROM agx_task_execution "
            "WHERE task_run_uuid='%s'" % _escape(run_uuid)
        )
    except Exception as e:
        log(f" 报警状态兜底查询失败 | {run_uuid}: {e}", "WARN")
        return None
    if not rows:
        return None
    row = rows[0]
    try:
        level = int(row.get("alert_level") or 0)
        count = int(row.get("alert_count") or 0)
    except Exception:
        return None
    if level not in (2, 3, 9) or count <= 0:
        return None

    now_ts = time.time()
    if level == 9:                       # Already closed out (the task finished or was closed itself)
        grace_until = now_ts + 86400 * 365
        status = "escalated"
    elif level == 3:                     # Already escalated to a human
        grace_until = now_ts + 86400 * 365
        status = "escalated"
    else:
        base = 0.0
        alerted_at = row.get("alerted_at")
        if isinstance(alerted_at, datetime):
            base = alerted_at.timestamp()
        elif alerted_at:
            try:
                base = datetime.strptime(str(alerted_at)[:19], "%Y-%m-%d %H:%M:%S").timestamp()
            except Exception:
                base = now_ts
        grace_until = base + GRACE_PERIOD
        status = "sent"

    state = {
        "status": status,
        "alert_count": count,
        "first_alert_at": str(row.get("alerted_at") or ""),
        "last_alert_at": str(row.get("alerted_at") or ""),
        "grace_until": grace_until,
        "reason": "rebuilt_from_db",
    }
    try:
        save_alert_state(run_uuid, state)
        log(f" 报警状态已从 DB 重建 | {run_uuid} | level={level} count={count}", "INFO")
    except Exception:
        pass
    return state


# -- Diagnostic snapshot --------------------------------------------------


def _query_session_status(agent_id: str, run_uuid: str, agent_type: str, session_id: str = "") -> dict:
    """Pick a channel by agent_type and query the state of the session *corresponding to the current run*.

    * Selector (2026-10-16 skeleton): switched "query the session state" from a hard-wired Gateway to per-platform dispatch.
        - openclaw / ""  -> WS (Gateway sessions.describe)
        - dsh            -> reserved branch (the DSH bridge has no session-state query interface yet; to be added)
        - claude_code / others -> non-WS platforms, return empty for now (falling back to the original tmux logic)
"""
    at = (agent_type or "").lower()
    if at in ("openclaw", ""):
        return _query_session_via_ws(agent_id, run_uuid)
    elif at == "dsh":
        # * Reserved: the DSH bridge currently has no session-state query interface.
        log(f" [dsh] 会话状态查询未实现 | {run_uuid}（待 DSH 桥补齐）", "DEBUG")
        return {"status": "unknown", "platform": "dsh", "note": "query_not_implemented"}
    else:
        # claude_code etc.: no WS query channel, so return empty and let the caller fall back to tmux
        return {"status": "unknown", "platform": at}


# -- Multi-instance: pick the matching Gateway by host ------------------------------
# * 2026-11-04 multi-instance (118 runs two openclaw instances on one machine):
#   all three direct ws connections used to be hard-wired to config.gateway (singular) -> only the main instance was connected.
#   For tasks of the second instance (test_srv_a2, 19789): wakes went to 18789, diagnostics found nothing, plan notifications could not be delivered,
#   and all of it failed silently (no error). Here the gateway is chosen by the agent's owning host.
_AGENT_HOST_CACHE: dict = {}


def _host_of_agent(agent_id: str) -> str:
    """The host_id an agent belongs to (cached in-process; returns an empty string when unknown)."""
    if not agent_id:
        return ""
    if agent_id in _AGENT_HOST_CACHE:
        return _AGENT_HOST_CACHE[agent_id]
    host = ""
    try:
        rows = _get_mysql_rows(
            "SELECT host_id FROM agx_agent_registry WHERE agent_id='%s'"
            % _escape(agent_id)
        )
        if rows:
            host = (rows[0].get("host_id") or "").strip()
    except Exception as e:
        log(f" 查 agent 归属 host 失败 | agent_id={agent_id} | {e}", "WARN")
    _AGENT_HOST_CACHE[agent_id] = host
    return host


def _host_of_session(session_id: str) -> str:
    """Session key agent:{agent_id}:xxx -> that agent's host_id."""
    if not session_id or not session_id.startswith("agent:"):
        return ""
    parts = session_id.split(":")
    return _host_of_agent(parts[1]) if len(parts) >= 2 else ""


def _gateway_for(host_id: str):
    """Get (ws_url, token) by host; on a miss fall back to the singular gateway (backward compatible)."""
    if host_id:
        for g in (config.get("gateways") or []):
            if (g.get("host_id") or "").strip() != host_id:
                continue
            url = (g.get("ws_url") or "").strip()
            token = (g.get("token") or "").strip()
            if url and token:
                return url, token
            log(f" gateways 条目不可用(缺 ws_url/token) | host_id={host_id}", "WARN")
    # <- the singular gateway is a deprecated fallback: it must be accessed safely with .get() (_Config has no __getattr__,
    #   so attribute access on a missing key raises AttributeError). With only gateways in the config this code is never reached.
    _gw_single = config.get("gateway") or {}
    return (
        _gw_single.get("ws_url") or "ws://127.0.0.1:18789",
        _gw_single.get("token") or "",
    )


def _query_session_via_ws(agent_id: str, run_uuid: str) -> dict:
    """Query the session state over WebSocket and fetch the session messages, writing them into the diagnostic snapshot."""
    import websockets.sync.client as ws_sync

    ws_url, ws_token = _gateway_for(_host_of_agent(agent_id))

    try:
        ws = ws_sync.connect(ws_url, proxy=None, open_timeout=WS_TIMEOUT)
        ws.recv(timeout=WS_TIMEOUT)
        ws.send(
            json.dumps({
                "type": "req",
                "id": "wd_auth",
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 4,
                    "client": {"id": "gateway-client", "version": "0.1", "platform": "python", "mode": "backend"},
                    "role": "operator",
                    "scopes": ["operator.read", "operator.write", "operator.admin"],
                    "caps": [],
                    "commands": [],
                    "permissions": {},
                    "auth": {"token": ws_token},
                    "locale": "zh-CN",
                    "userAgent": "agx-watchdog",
                },
            })
        )
        auth_resp = json.loads(ws.recv(timeout=WS_TIMEOUT))
        if not auth_resp.get("ok"):
            ws.close()
            return {"error": "auth failed"}

        # 1. Query the session list
        ws.send(
            json.dumps({
                "type": "req",
                "id": "wd_list",
                "method": "sessions.list",
                "params": {},
            })
        )
        list_resp = json.loads(ws.recv(timeout=WS_TIMEOUT))

        result = {}
        target_key = f"agent:{agent_id}:{run_uuid}"
        if list_resp.get("ok"):
            sessions = list_resp.get("payload", {}).get("sessions", [])
            for s in sessions:
                if s.get("key") == target_key:
                    result["session_key"] = s.get("key", "")
                    result["status"] = s.get("status", "unknown")
                    result["age_seconds"] = int(s.get("age", 0) / 1000)
                    break
            if "status" not in result:
                result["status"] = "not_found"
                result["total_sessions"] = len(sessions)

        # 2. Fetch the session message history
        if result.get("status") not in ("not_found", "unknown"):
            try:
                ws.send(
                    json.dumps({
                        "type": "req",
                        "id": "wd_hist",
                        "method": "chat.history",
                        "params": {"sessionKey": target_key, "limit": 20},
                    })
                )
                hist_resp = json.loads(ws.recv(timeout=WS_HIST_TIMEOUT))
                if hist_resp.get("ok"):
                    messages = hist_resp.get("payload", {}).get("messages", [])
                    result["message_count"] = len(messages)
                    # Keep only the last 5 messages, and keep only the text of each (no thinking blocks -- far too large)
                    last_msgs = messages[-5:]
                    compact = []
                    for m in last_msgs:
                        role = m.get("role", "?")
                        content = m.get("content", [])
                        text_parts = []
                        for c in content:
                            if c.get("type") == "text":
                                text_parts.append(c.get("text", ""))
                        compact.append({"role": role, "text": "\n".join(text_parts)[:500]})
                    result["last_messages"] = compact
                else:
                    result["history_error"] = hist_resp.get("error", {}).get("message", "unknown")
            except Exception as e:
                result["history_error"] = str(e)

        ws.close()
        return result
    except Exception as e:
        return {"error": str(e)}


def _capture_diag_info(run_uuid: str, agent_id: str, agent_type: str, reason: str, elapsed: int, timeout: int):
    """Capture a diagnostic snapshot at the moment an exception occurs and write it into the diag_info field.

    Collect live data per platform according to agent_type, and write it into template_file for the agent to read.
"""
    log_path = os.path.join(WORK_DIR, "logs", f"{run_uuid}.out")
    log_tail = ""
    try:
        with open(log_path) as f:
            lines = f.readlines()
            log_tail = "".join(lines[-30:]).strip()
    except Exception:
        log_tail = "(log not found)"

    # -- Collect live data by agent_type --
    #
    # * 2026-10-24 fix: template_file changed to an *absolute path*.
    #   It used to be a *relative* path under docs/ (the platform supplement filename as of 2026-10-24), but:
    #     - the watchdog runs in WORK_DIR (~/.agx), while an agent runs in its own workspace
    #     - when an agent uses the read tool, relative paths resolve against its workspace, not WORK_DIR
    #     - result: the path resolved to ~/.agx/agx_agents/{id}/workspace/docs/...
    #       only system agents have that docs/ symlink, every other agent simply could not read it -> a silent degradation
    #   With an absolute path any agent can open it directly regardless of its workspace layout.
    at = (agent_type or "").lower()
    platform = "unknown"
    live = {}
    template_file = ""

    if at in ("openclaw", ""):
        # OpenClaw: query the session over the Gateway WebSocket
        platform = "openclaw"
        live = {"gateway_session": _query_session_via_ws(agent_id, run_uuid)}
        _tf = os.path.join(WORK_DIR, "docs", "internal", "watchdog-openclaw.md")
        template_file = _tf if os.path.exists(_tf) else ""
    elif at == "claude_code":
        # Claude Code: tmux session detection
        platform = "claude_code"
        live = {"tmux_session": {"alive": is_tmux_session_alive(run_uuid), "session_name": run_uuid}}
        # No Claude Code watchdog supplement exists -- there never has been one, in this repo or in the
        # development repo. Pointing at a file that can never exist only looks like a template and then
        # degrades silently; the alert falls back to the inline text built from `live` above.
        template_file = ""
    else:
        # Others: generic tmux detection
        platform = "tmux"
        live = {"tmux_session": {"alive": is_tmux_session_alive(run_uuid), "session_name": run_uuid}}

    diag = {
        "platform": platform,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reason": reason,
        "elapsed_seconds": elapsed,
        "timeout_seconds": timeout,
        "log_tail": log_tail,
        "template_file": template_file,
        "live": live,
    }

    try:
        _exec_mysql(f"UPDATE agx_task_execution SET diag_info='{_escape(json.dumps(diag, ensure_ascii=False))}' WHERE task_run_uuid='{_escape(run_uuid)}'")
        log(f" 诊断快照已写入 | {run_uuid} | {reason}")
    except Exception as e:
        log(f"写入 diag_info 失败: {e}", "WARN")


# -- System-level alert escalation --------------------------------------------


def check_system_crisis(detected: list) -> bool:
    """
    Detect a system-level failure.

    If the number of timed-out executions in one scan round is >= CRISIS_THRESHOLD,
    it may be an infrastructure problem such as a network outage or an expired token, so escalate_to_human directly.

    Deduplication: no repeat report within CRISIS_GRACE (default 10 minutes), at most CRISIS_MAX_RETRIES (default 3) times.

    Args:
        detected: list of (exc_dict, reason) tuples from _detect_anomaly

    Returns:
        True  -> the crisis is handled or within grace; the caller should skip individual alerts
        False -> below the threshold; the caller should handle individual alerts normally
"""
    count = len(detected)
    if count < SYSTEM_CRISIS_THRESHOLD:
        return False

    state = _get_crisis_state()
    now_ts = time.time()

    # Already escalated -> silent
    if state.get("status") == "escalated":
        return True

    # Within grace -> silent
    if state and now_ts < state.get("grace_until", 0):
        return True

    # Determine which crisis alert this round is
    if state:
        crisis_count = state.get("count", 0) + 1
    else:
        crisis_count = 1
        state = {"first_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

    # Reached the maximum retries -> after escalation, stop tracking
    if crisis_count > CRISIS_MAX_RETRIES:
        state["status"] = "escalated"
        state["count"] = crisis_count
        _save_crisis_state(state)
        log(f"系统危机通知已达上限 ({CRISIS_MAX_RETRIES} 次)，不再追踪", "CRITICAL")
        return True

    # Collect sample run_uuids
    run_ids = [e[0].get("_id", "?") for e in detected[:5]]
    reason = f"{count} 个 execution 同时超时（阈值={SYSTEM_CRISIS_THRESHOLD}）\n可能原因：断网、API token 过期、Redis/MySQL 不可用、Agent 大面积失能\n首批 run_uuid: {', '.join(run_ids)}"

    escalate_to_human(
        run_uuid="SYSTEM_CRISIS",
        reason=reason,
        state={
            "crisis_count": crisis_count,
            "first_alert_at": state.get("first_at", "?"),
            "last_alert_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
    )

    state["count"] = crisis_count
    state["grace_until"] = now_ts + CRISIS_GRACE
    state["last_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save_crisis_state(state)

    log(f"系统危机已通知 | timeout_count={count} | crisis_count={crisis_count}/{CRISIS_MAX_RETRIES}", "CRITICAL")
    return True


# -- Sending the wake message ----------------------------------------------


def _send_wake_to_session(agent_type: str, session_id: str, run_uuid: str,
                          wake_type: str = "timeout wakeup", agent_id: str = "") -> bool:
    """Pick a wake channel by agent_type and send a wake message to the agent session.

    * Selector (2026-10-16 skeleton): different platforms use different channels.
        - openclaw / ""        -> WS (Gateway sessions.send)
        - claude_code          -> tmux (send-keys)
        - dsh                  -> reserved branch (the DSH bridge has no wake/interject capability yet; wire it up once provided)
        - others               -> WARN, explicitly unsupported (no longer silent)
"""
    at = (agent_type or "").lower()
    if at in ("openclaw", ""):
        return _send_wake_via_ws(session_id, run_uuid, wake_type)
    elif at == "claude_code":
        return _send_wake_via_tmux(run_uuid, wake_type)
    elif at == "dsh":
        # * Reserved: the DSH bridge (agx_dsh_client.py) currently only has "consume the queue and run tasks",
        #   with no interface to inject into / wake a running session yet. Wire it up here once the DSH side provides one.
        #   For now warn explicitly, to avoid a silent failure.
        log(f" [dsh] 唤醒通道未实现，暂无法唤醒 {run_uuid}（待 DSH 桥补齐注入能力）", "WARN")
        return False
    else:
        log(f" 不支持的 agent_type={agent_type}，无法唤醒 {run_uuid}", "WARN")
        return False


def _send_wake_via_ws(session_id: str, run_uuid: str, wake_type: str = "timeout wakeup") -> bool:
    """Send a wake through Gateway WebSocket sessions.send (the openclaw platform)."""
    import websockets.sync.client as ws_sync

    ws_url, ws_token = _gateway_for(_host_of_session(session_id))

    try:
        ws = ws_sync.connect(ws_url, proxy=None, open_timeout=WS_TIMEOUT)
        ws.recv(timeout=WS_TIMEOUT)

        ws.send(
            json.dumps({
                "type": "req",
                "id": "wd_wake_auth",
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 4,
                    "client": {"id": "gateway-client", "version": "0.1", "platform": "python", "mode": "backend"},
                    "role": "operator",
                    "scopes": ["operator.read", "operator.write", "operator.admin"],
                    "caps": [],
                    "commands": [],
                    "permissions": {},
                    "auth": {"token": ws_token},
                },
            })
        )
        auth_resp_raw = ws.recv(timeout=WS_TIMEOUT)
        auth_resp = json.loads(auth_resp_raw)
        if not auth_resp.get("ok"):
            ws.close()
            log(f"唤醒 WS 认证失败 {run_uuid} | Gateway 响应: {auth_resp_raw[:300]}", "WARN")
            return False

        wake_req_id = f"wd_wake_{run_uuid[:12]}"
        ws.send(
            json.dumps({
                "type": "req",
                "id": wake_req_id,
                "method": "sessions.send",
                "params": {
                    "key": session_id,
                    "message": f"{WAKE_MESSAGE}-[{run_uuid}]-[{wake_type}]-[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]",
                },
            })
        )
        # Loop over messages until the response with the matching id arrives (skipping events such as health)
        resp = None
        for _ in range(20):
            try:
                resp_raw = ws.recv(timeout=WS_TIMEOUT)
                msg = json.loads(resp_raw)
                if msg.get("id") == wake_req_id:
                    resp = msg
                    break
                # A non-target message (e.g. a health event), skip it
            except Exception:
                break
        ws.close()

        if resp is None:
            log(f"[WS] 唤醒响应超时 {run_uuid}: 未收到 {wake_req_id}", "WARN")
            return False
        if resp.get("ok"):
            log(f" [WS] 已唤醒 {run_uuid} → {session_id}", "INFO")
            return True
        else:
            resp_err = json.dumps(resp, ensure_ascii=False)[:300]
            log(f"[WS] 唤醒发送失败 {run_uuid}: session={session_id} | Gateway 响应: {resp_err}", "WARN")
            return False
    except Exception as e:
        log(f"[WS] 唤醒异常 {run_uuid}: {e}", "WARN")
        return False


def _send_wake_via_tmux(run_uuid: str, wake_type: str = "timeout wakeup") -> bool:
    """Send a wake through tmux send-keys (claude_code etc.)."""
    import subprocess

    wake_text = WAKE_MESSAGE.replace("\n", " ").replace("'", "'\\''")
    wake_text = f"{wake_text}-[{run_uuid}]-[{wake_type}]-[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"
    try:
        # Check whether the session exists first
        result = subprocess.run(
            ["tmux", "has-session", "-t", run_uuid],
            capture_output=True,
            timeout=5,
        )
        if result.returncode != 0:
            log(f"[tmux] 唤醒失败 {run_uuid}: session 不存在", "WARN")
            return False

        # Send the wake message into the tmux session
        subprocess.run(
            ["tmux", "send-keys", "-t", run_uuid, wake_text, "Enter"],
            capture_output=True,
            timeout=5,
        )
        log(f" [tmux] 已唤醒 {run_uuid}", "INFO")
        return True
    except Exception as e:
        log(f"[tmux] 唤醒异常 {run_uuid}: {e}", "WARN")
        return False


# -- Per-execution check -----------------------------------------


def _check_session_done_wake(run_uuid: str, agent_id: str, session_id: str, agent_type: str) -> bool:
    """Check whether the session is already done and the task allows an immediate wake.

    Flow:
    1. Look up task_id on agx_task_execution
    2. Look up the session_done_wake field on agx_task_base
    3. If session_done_wake=0 -> do not query the session, return False
    4. If session_done_wake=1 -> query the session state through sessions.describe
    5. If the session state is done -> send the wake message, return True
    6. If the session is still running / cannot be found -> return False and fall back to the original timeout logic

    Returns:
        True  -> a wake was triggered (the session is done and the wake message was sent)
        False -> no wake needed (session_done_wake=0 / the session has not ended / the query failed)
"""
    # 1. Look up task_id + started_at (* 2026-10-19 added the started_at criterion)
    try:
        rows = _get_mysql_rows(
            f"SELECT task_id, started_at FROM agx_task_execution WHERE task_run_uuid='{run_uuid}'"
        )
        if not rows:
            log(f" session done 检查 | {run_uuid} | 未找到 execution 记录", "DEBUG")
            return False
        task_id = rows[0]["task_id"]
        # * An empty started_at means the receiving agent has not read it yet and has not started at all,
        #   and the session may just happen to be done (left over from a previous round), so it must not be woken as "answered but forgot to close out".
        #   Waking here would send a "continue" message to an agent that has not even read the task: pure noise, and it may cause duplicate execution.
        if not rows[0].get("started_at"):
            log(f" session done 跳过(未起算) | {run_uuid} | started_at 为空", "DEBUG")
            return False
    except Exception as e:
        log(f" session done 检查 | {run_uuid} | 查询 execution 失败: {e}", "DEBUG")
        return False

    # 2. Check session_done_wake
    try:
        rows = _get_mysql_rows(f"SELECT session_done_wake FROM agx_task_base WHERE task_id='{task_id}'")
        if not rows:
            log(f" session done 检查 | {run_uuid} | 未找到 task_base 记录", "DEBUG")
            return False
        sdw = int(rows[0].get("session_done_wake", 1))
    except Exception as e:
        log(f" session done 检查 | {run_uuid} | 查询 task_base 失败: {e}", "DEBUG")
        return False

    if sdw != 1:
        log(f" session done 跳过(编排型task) | {run_uuid} | task_id={task_id}", "DEBUG")
        return False

    # 3. Check the session state (by platform, per agent_type)
    session_status = None
    try:
        live = _query_session_status(agent_id, run_uuid, agent_type, session_id)
        session_status = live.get("status", "") if isinstance(live, dict) else ""
        log(f" session done 检查 | {run_uuid} | 返回: {session_status}", "DEBUG")
    except Exception as e:
        log(f" session done 检查 | {run_uuid} | ws查询异常: {e}", "DEBUG")
        pass

    if session_status != "done":
        log(f" session done 未触发 | {run_uuid} | status={session_status}", "DEBUG")
        return False

    # 4. Dedup check: use an independent cooldown so the same round is not woken twice
    now_ts = int(datetime.now().timestamp())
    wake_state = _get_wake_state(run_uuid)
    if wake_state:
        sent_at = int(wake_state.get("sent_at", 0))
        if now_ts - sent_at < SESSION_DONE_WAKE_COOLDOWN:
            log(f" session done 唤醒冷却中 | {run_uuid} | 距离上次 {now_ts - sent_at}s")
            return False

    # 5. The session is already done -> wake immediately
    log(f" session done 唤醒 | {run_uuid} | session={session_status}")
    # * Claim the slot before sending (same as _try_wake; prevents duplicate wakes if the process is killed midway)
    _save_wake_state(run_uuid, {"sent_at": str(now_ts), "count": "1"})
    msg_sent = _send_wake_to_session(agent_type, session_id, run_uuid, "session done", agent_id)
    if not msg_sent:
        _clear_wake_state(run_uuid)
        log(f" session done 唤醒发送失败, 已撤销占位 | {run_uuid}", "WARN")
    return msg_sent


def _try_wake(exc: dict, effective_timeout: int, elapsed: int, run_uuid: str, session_id: str, agent_type: str, agent_id: str = "") -> str | None:
    """Wake a stuck agent session by tier (70% / 80% / 90%, once per tier).

    * 2026-10-28 rework (Feng decision): waking is no longer "fire once at 50% then re-fire passively on Redis TTL expiry"
      but "multiple proactive tiers with the state persisted in the DB".

    The old implementation had two defects:
      (1) the state lived in a Redis hash with expire=WAKE_GRACE x 2 (240s) -> once the key expired it re-sent,
          and the same run was measured to be woken 3 times (08:58:03 / 09:02:03 / 09:06:04),
          with the `count>1` suppression failing as the key disappeared;
      (2) waking was not persisted to the DB -> "how many times did I nudge it" could never be answered.
    The new implementation: the idempotency predicate = `agx_task_execution.wake_count` (DB),
            and every wake writes one `agx_task_execution_event` row (event_type=wake).

    Returns:
        None  -> no timeout handling needed
        str   -> the timeout reason after the wake tiers are exhausted (normally the elapsed>timeout branch fires first)
"""
    if not WAKE_ENABLED or not WAKE_TIERS:
        return None
    if effective_timeout <= 0:
        return None

    passed = sum(1 for t in WAKE_TIERS if elapsed >= int(effective_timeout * t))
    if passed <= 0:
        return None

    wake_count = int(str(exc.get("wake_count") or 0) or 0)
    if wake_count == 0:
        # * 2026-10-28: the same class of disease as on the alert side -- if the idempotency predicate only reads the Redis cache,
        #   a Redis restart/eviction causes a re-wake (the same tier fires again). When the cache is 0, trust the DB.
        try:
            _rows = _get_mysql_rows(
                "SELECT wake_count FROM agx_task_execution WHERE task_run_uuid='%s'"
                % _escape(run_uuid)
            )
            if _rows:
                wake_count = max(wake_count, int(_rows[0].get("wake_count") or 0))
        except Exception:
            pass
    if wake_count >= passed:
        return None                      # Every tier already reached has been woken
    if wake_count >= len(WAKE_TIERS):
        return None                      # Tiers exhausted -> hand over to the timeout alert

    # * One scan sends only one wake, but it aligns directly with "the highest tier already reached":
    #   the watchdog scan interval is load-dependent (17s gaps have been measured), so firing tier by tier as wake_count+1
    #   would skip tiers (at a 40s timeout the 80%/90% tiers are only 4s apart, a single jump skips one).
    #   Hence tier_no = passed (the tier already reached), and after a success wake_count is set directly to passed.
    tier_no = passed
    tier = WAKE_TIERS[tier_no - 1]
    remain = max(0, int(effective_timeout - elapsed))
    reason = (
        f"已运行 {elapsed}s / 预计 {effective_timeout}s"
        f"（第{tier_no}档 {int(tier * 100)}% 唤醒，剩余 {remain}s）"
    )

    # Send-failure backoff: do not retry the same tier within 60s, avoiding "one failed event every 2s" spam
    _fk = f"{run_uuid}:{tier_no}"
    _now = time.time()
    if _now - _WAKE_FAIL_TS.get(_fk, 0) < 60:
        return None

    ok = _send_wake_to_session(
        agent_type, session_id, run_uuid,
        wake_type=f"tier{tier_no}_{int(tier * 100)}", agent_id=agent_id,
    )
    if not ok:
        _WAKE_FAIL_TS[_fk] = _now
        if len(_WAKE_FAIL_TS) > 2000:
            _WAKE_FAIL_TS.clear()
        # * 2026-10-28: a send failure must leave a trace. The old implementation only wrote an event on success ->
        #   "was it sent at all" could not be queried (the historical ws_delivery_status.failed was always 0).
        #   On failure write delivery_state='failed', symmetric with _ao_mark_event_delivery on the alert side.
        try:
            _ao_record_event(
                task_run_uuid=run_uuid,
                event_type="wake",
                level=1,
                from_agent="agx_core",
                to_agent=agent_id,
                to_session=session_id,
                channel="ws" if (agent_type or "").lower() == "openclaw" else "tmux",
                reason=reason,
                payload={
                    "tier": tier, "tier_no": tier_no, "elapsed": elapsed,
                    "timeout": effective_timeout, "delivery_failed": True,
                },
                delivery_state="failed",
            )
        except Exception:
            pass
        log(f" 唤醒发送失败(第{tier_no}档) {run_uuid} → {session_id} | 60s 内不重试", "WARN")
    event_id = ""
    if ok:
        event_id = _ao_record_event(
            task_run_uuid=run_uuid,
            event_type="wake",
            level=1,
            from_agent="agx_core",
            to_agent=agent_id,
            to_session=session_id,
            channel="ws" if (agent_type or "").lower() == "openclaw" else "tmux",
            reason=reason,
            payload={
                "tier": tier, "tier_no": tier_no, "elapsed": elapsed,
                "timeout": effective_timeout, "wake_count": tier_no,
            },
            delivery_state="delivered",
        )
    if ok:
        try:
            _exec_mysql(
                "UPDATE agx_task_execution SET wake_count=%d, alerted_at=NOW(), "
                "alert_level=IF(alert_level<1,1,alert_level), "
                "alert_reason='%s', alert_target='%s' "
                "WHERE task_run_uuid='%s' AND run_status='running'"
                % (tier_no, _escape(reason[:255]), _escape(agent_id), _escape(run_uuid))
            )
            _refresh_execution_cache(run_uuid)
        except Exception as e:
            log(f" 唤醒计数写库失败 | {run_uuid}: {e}", "WARN")
        log(f" [WS] 已唤醒(第{tier_no}档/{int(tier * 100)}%) {run_uuid} → {session_id} | event={event_id}")
    else:
        log(f" 唤醒发送失败（不计数，60s 后重试该档）| {run_uuid} | tier={tier_no}", "WARN")
    return None


def _send_age_seconds(r, run_uuid: str, exc: dict) -> int | None:
    """"How long since it was really sent" -- check Redis first, then fall back to the DB dispatched_at.

    * Feng, 2026-10-27 (B+C): the time condition for a resend must be based on the send instant.
      It used to use created_at (the plan persist time), more than ten minutes earlier than the real send,
      so "sent 42 seconds ago" was judged as "waited 804 seconds".

    The Redis key is written by the daemon at the moment it rpushes into the WS queue (TTL 24h);
    the DB column dispatched_at is the trace of the same instant, as a fallback when the Redis key is missing.
    If neither is available -> return None and the caller *must not resend* (better not to send than to execute twice).
"""
    if not run_uuid:
        return None
    try:
        v = r.get(f"agx:send:at:{run_uuid}")
        if v:
            return max(0, int(time.time() - int(v)))
    except Exception:
        pass
    d = str(exc.get("dispatched_at") or "").strip()
    if d and d not in ("None", "null"):
        try:
            dt = datetime.strptime(d, "%Y-%m-%d %H:%M:%S")
            return max(0, int((datetime.now() - dt).total_seconds()))
        except Exception:
            pass
    return None


def _agent_has_marker(r, agent_id: str, run_uuid: str) -> bool:
    """Whether ws_client has already put this run into the agent context (it writes the mark once it gets the runId).

    * 2026-10-27 12:08 incident (4 review tasks were resent and the agent ran them twice at once):
      the root cause was this function missing. `delivery=error` only says "no final received",
      **it does not mean "the message was not delivered"**. On a WS disconnect the agent is often already executing,
      and resending then = two runIds for the same task in one session (the logs show
      "expected runId=A but got B" coexisting).

      The criterion is now the activity mark: ws_client writes the hash
      `agx:active:run:{agent_id}` (field=run_uuid) after sessions.send returns a runId. A mark
      = the message really did reach the agent context = **never resend**.

    Returns True when the Redis read fails (conservative: rather send one time too few than execute twice).
"""
    if not run_uuid:
        return False
    try:
        return bool(r.hexists(f"agx:active:run:{agent_id}", run_uuid))
    except Exception:
        return True


def _resend_task(r, run_uuid: str, fallback_agent: str, fallback_timeout: int,
                 attempt: int = 1) -> bool:
    """Push an "unstarted" task into the ws delivery queue once more.

    * 2026-10-27 plan B companion: called only when delivery=error/timeout (a clear delivery failure).
      The message body is byte-for-byte identical to the daemon's first delivery / retry (see the ws_task construction in agx_daemon.py),
      otherwise a missing field on the ws_client side would send mode down the wrong branch.

    Double-check against the DB (do not trust the cache): it must still be "running + empty started_at" before resending,
    avoiding a case where the agent has actually started and only the cache is stale -> duplicate execution.
"""
    try:
        rows = _get_mysql_rows(
            "SELECT run_status, started_at, agent_id, host_id, session_id, "
            "send_agent_id, timeout_seconds, is_reply_to FROM agx_task_execution "
            f"WHERE task_run_uuid='{_escape(run_uuid)}' LIMIT 1"
        )
    except Exception as e:
        log(f"[send] 重发前回源失败 | {run_uuid} | {e}", "WARN")
        return False
    if not rows:
        return False
    row = rows[0]
    if str(row.get("run_status") or "") != "running" or row.get("started_at"):
        log(f"[send] 重发取消（已开工或已终态）| {run_uuid}", "INFO")
        return False

    owner_host = (row.get("host_id") or "").strip() or HOST_ID
    agent_id = row.get("agent_id") or fallback_agent
    sess = row.get("session_id") or ""
    send_agent = row.get("send_agent_id") or ""
    try:
        timeout = int(row.get("timeout_seconds") or fallback_timeout or FALLBACK_TIMEOUT)
    except Exception:
        timeout = FALLBACK_TIMEOUT

    # * 2026-11-16 fix: the resend envelope used to be hand-written with 3 fields, missing msg_type / resend_attempt
    #   (the same defect as the daemon's retry delivery). It now goes through agx_proto.
    ws_task = {
        "run_uuid": run_uuid,
        "agent_id": agent_id,
        "instruction": dumps_struct(
            run_uuid,
            send_agent,
            sess,
            msg_type=msg_type_for(row.get("is_reply_to") or ""),
            resend_attempt=attempt,
        ),
        "timeout_seconds": timeout,
        "target_host_id": owner_host,
    }
    if sess:
        ws_task["session_key"] = sess
        ws_task["mode"] = "default"
    try:
        r.rpush(f"agx:ws:task:{owner_host}", json.dumps(ws_task, ensure_ascii=False))
    except Exception as e:
        log(f"[send] 重发入队失败 | {run_uuid} | {e}", "WARN")
        return False
    return True


def _handle_unstarted(exc: dict, run_uuid: str, agent_id: str,
                      session_id: str, timeout: int) -> str | None:
    """Close-out for the transport phase (empty started_at): three tiers -- resend / report / stay silent.

    * 2026-10-27 plan B (Feng decision): started_at is the only criterion for "the agent itself declared it started".
      Empty = sent but not delivered (or delivered but not read), which is the transport phase -- the watchdog does not judge a business timeout.

    Why this opening still exists: complete silence would leave "the message was lost" unknown and the task at running forever.
    So it handles by delivery tier (delivery is the transport axis, orthogonal to the business axis):

      delivery=error/timeout  -> the daemon knows for certain delivery failed -> resend proactively at the threshold (at most 2 times)
      delivery=pending        -> may just be normal queueing -> do not resend; report once after waiting long enough
      delivery=delivered      -> the message is already in the session (the agent is busy / the context is long) -> do not resend; report once after waiting long enough

    Under no circumstances is *run_status* written -- business success/failure is decided only by the agent's agx_finish_task.

    Returning None = normal (no alert); returning str = the reason to report (goes through watchdog_alert but is not judged dead).
"""
    queue_wait = get_queue_wait_seconds(exc)
    delivery = str(exc.get("ws_delivery_status") or "").strip()
    try:
        r = _get_redis()
    except Exception:
        return None

    # -- (1) Clear delivery failure -> proactive resend (interval and count both controlled) --
    #   * 2026-10-27 measured correction: the first version limited only the count, not the interval, and the watchdog scans every 2s,
    #     so it burned the quota of 2 within 2 seconds (09:48:29 / 09:48:31 back to back).
    #     Now a SETNX gate (TTL = SEND_RESEND_AFTER) provides the interval: only the claimant may resend,
    #     and after one resend nobody can claim again for 10 minutes -- natural throttling, no extra last_ts field needed.
    if delivery in ("error", "timeout"):
        # ** 2026-10-27 12:08 incident gates (mandatory, otherwise duplicate execution) **
        #   Gate 1, delivery evidence: delivery=error only means "ws_client got no final",
        #         not "the message was not delivered". When the WS disconnects the agent is often already running.
        #         Measured: disconnect at 12:08:02 -> 4 reviews judged error -> resent at 12:08:18,
        #         while the activity mark of all 4 was already written (HEXISTS=1): the agent ran for nothing.
        if _agent_has_marker(r, agent_id, run_uuid):
            return None
        #   Gate 2, time condition (Feng, 2026-10-27: resend only when both conditions hold)
        #         first "how long since it was sent" -- based on the real send instant (Redis/DB);
        #         then "how long since it was persisted" -- based on created_at.
        #         Previously only the latter existed, and created_at is the plan persist time -> satisfied the moment it was dispatched.
        send_age = _send_age_seconds(r, run_uuid, exc)
        if send_age is None or send_age < SEND_RESEND_AFTER:
            return None
        if queue_wait < SEND_RESEND_AFTER:
            return None
        rk = f"agx:send:resend:{run_uuid}"
        try:
            sent = int(r.get(rk) or 0)
        except Exception:
            sent = 0
        if sent < SEND_RESEND_MAX:
            try:
                gate = r.set(f"agx:send:resend_gate:{run_uuid}", "1",
                             nx=True, ex=SEND_RESEND_AFTER)
            except Exception:
                gate = False
            if not gate:
                return None  # Less than SEND_RESEND_AFTER since the last resend, do nothing this round
            if _resend_task(r, run_uuid, agent_id, timeout, attempt=sent + 1):
                try:
                    r.incr(rk)
                    r.expire(rk, 86400)
                except Exception:
                    pass
                log(
                    f"[send] 投递失败重发 | {run_uuid} | delivery={delivery} "
                    f"已等 {queue_wait}s 第 {sent + 1}/{SEND_RESEND_MAX} 次"
                    f"（下次最早 {SEND_RESEND_AFTER}s 后）",
                    "INFO",
                )
            else:
                # The double check failed (already started / already terminal) -> withdraw the gate so later rounds can decide normally
                try:
                    r.delete(f"agx:send:resend_gate:{run_uuid}")
                except Exception:
                    pass
            return None
        # Resend quota exhausted -> fall through to (2) below and report once

    # -- (2) Waited too long without starting -> report once (not judged dead) --
    if queue_wait >= SEND_REPORT_AFTER:
        rk = f"agx:send:reported:{run_uuid}"
        try:
            first = r.set(rk, "1", nx=True, ex=86400)
        except Exception:
            first = False
        if first:
            return (
                f"已发送 {queue_wait}s 仍未开工（delivery={delivery or '?'}，"
                f"未判失败，请查投递链/会话是否卡死）"
            )
        return None

    # -- (3) Everything else: silent, leaving one trace every 30 minutes --
    try:
        if r.set(f"agx:send_wait:{run_uuid}", "1", nx=True, ex=1800):
            log(
                f"[send] 已发送未开工 | {run_uuid} | 已等 {queue_wait}s "
                f"| delivery={delivery or '?'}",
                "INFO",
            )
    except Exception:
        pass
    return None


def _handle_receipt_execution(exc: dict, run_uuid: str, agent_id: str,
                              session_id: str, timeout: int,
                              elapsed: int = 0) -> str | None:
    """Dispatch for receipt-type executions (only historical leftover rows; new receipts no longer create executions).

    * Feng, 2026-10-29: a receipt is only *a message*, not a task.
      New code no longer creates an execution for a receipt (_reply_to_sender now goes through event plus send_text).
      The old logic for leftover rows did `return None` inside _detect_anomaly without even logging.

    ** Red line (Feng corrected 2026-10-29): the watchdog **does not write run_status and does not write finished_at**.
       A task's success/failure and end time can only be decided by the agent's agx_finish_task (same criterion as _handle_unstarted).
       The watchdog only "detects + alerts" and hands the disposition back to the initiator.

    Dispatch rules:
      (1) Acknowledged (ack_state='read' or reply_read=1)
         - not timed out -> wait normally for the agent to close out, do nothing
         - timed out -> **return the reason to go through _handle_anomaly -> alert the initiator directly**
           (acknowledged = the message was delivered and read; but the task was not closed out = the agent failed to wrap up)
      (2) Not acknowledged -> the transport/reading phase is incomplete -> reuse _handle_unstarted (resend / report / silent)

    Never enters the wake logic: a wake means "urge the task to keep working", which is meaningless for a notification.
"""
    ack = str(exc.get("ack_state") or "").strip().lower()
    try:
        rr = int(exc.get("reply_read") or 0)
    except Exception:
        rr = 0

    # The cache may lack these two columns (old data / an incremental sync path) -> go back to the source once
    if not ack and not rr:
        try:
            rows = _get_mysql_rows(
                "SELECT ack_state, reply_read FROM agx_task_execution "
                f"WHERE task_run_uuid='{_escape(run_uuid)}' LIMIT 1"
            )
            if rows:
                ack = str(rows[0].get("ack_state") or "").strip().lower()
                rr = int(rows[0].get("reply_read") or 0)
                exc["ack_state"] = ack
                exc["reply_read"] = rr
        except Exception:
            pass

    if ack == "read" or rr == 1:
        # Acknowledged = the message was delivered and read. The transport axis ends here.
        # Leave one trace only (no spam every 2s), and never write run_status / finished_at.
        try:
            _r = _get_redis()
            if _r.set(f"agx:receipt:acked:{run_uuid}", "1", nx=True, ex=86400):
                log(
                    f"[receipt] 已签收 | {run_uuid} | agent={agent_id} | "
                    f"已过 {elapsed}s / 限制 {timeout}s",
                    "INFO",
                )
        except Exception:
            pass

        # * Acknowledged but still not closed out past the timeout -> the agent finished (or errored) without calling agx_finish_task.
        #   The watchdog does not close it out for the agent, but a human must know -- alert the initiator directly.
        if elapsed > timeout:
            return (
                f"回执已签收但未结案（已过 {elapsed}s，限制 {timeout}s）"
                f" —— 需执行方 agent={agent_id} 调 agx_finish_task 结案"
            )
        return None

    return _handle_unstarted(exc, run_uuid, agent_id, session_id, timeout)


def _detect_anomaly(exc: dict) -> str | None:
    """
    Pure detection: decide whether an execution is anomalous. It does not read/write alert_state and does not dispatch alerts.
    The wake mechanism is still triggered here (a wake is an "attempted recovery", not an "alert").

    Returning None means normal; returning str means the anomaly reason.
"""
    run_uuid = exc.get("_id", "")
    agent_id = exc.get("agent_id", "")
    session_id = exc.get("session_id", "")
    started_at = exc.get("started_at", "")
    elapsed = get_elapsed_seconds(started_at)
    timeout = get_timeout_for_execution(exc)
    agent_type = _get_agent_type(agent_id)
    tmux_alive = is_tmux_session_alive(run_uuid)

    # * 2026-10-19 plan B (Feng decision): a receipt is not a task and takes no part in timeout/wake decisions.
    #   Receipt semantics = a notification saying "this has been handled"; its lifecycle ends at *successful delivery* (the daemon closes it out
    #   as succeeded); there is no such thing as "execution timeout" or "needs waking to continue".
    #   Without this guard a receipt would be falsely reported as timed out whenever it queued a little long, and trigger pointless wakes.
    #   Note: the cache may lack the is_reply_to field (old data / incremental sync path); when missing, go back to the DB once.
    _rt = exc.get("is_reply_to")
    if _rt is None:
        try:
            _rows = _get_mysql_rows(
                f"SELECT is_reply_to FROM agx_task_execution WHERE task_run_uuid='{_escape(run_uuid)}' LIMIT 1"
            )
            _rt = (_rows[0].get("is_reply_to") or "") if _rows else ""
            exc["is_reply_to"] = _rt  # Write back, avoiding repeated source round trips
        except Exception:
            _rt = ""
    if str(_rt or "").strip():
        # * Feng, 2026-10-29: "a receipt is only a message", not a task.
        #   The old code returned None directly here -> accidentally exempting four things: the timeout judgement / delivery-failure close-out /
        #   alerts / logging (899f evidence: started 13:31, acknowledged 13:34, the model errored,
        #   then 1h46m of zero activity, wake_count=0 / alert_count=0, and running hung forever).
        #   The new code exempts only the *wake* (a wake = urging a task to keep working, meaningless for a notification),
        #   but still does the close-out: acknowledged and timed out -> alert the initiator (without closing it out for them);
        #   not acknowledged -> reuse the transport-phase _handle_unstarted (resend / report / silent).
        return _handle_receipt_execution(exc, run_uuid, agent_id, session_id, timeout, elapsed)

    # * 2026-10-19 backstop for the lazy started_at write:
    #   an empty started_at means the agent has not started reading yet (queueing / just delivered, not yet counted).
    #   elapsed is then always 0 -> a timeout would never be judged -> a dead task hangs at running forever.
    #   So the timeout is judged from "queue wait measured from created_at", with the threshold doubled:
    #   the queueing phase should not be squeezed by the strict execution-timeout gate, but it needs a ceiling to stop endless accumulation.
    queue_wait = 0
    if not started_at:
        # * 2026-10-27 plan B (Feng decision): an empty started_at means "sent, the agent has not read the instruction yet".
        #   That is the *transport phase*, not the business phase -- alerts/timeouts/wakes do not apply at all; hand it back to the sending side.
        #   Basis: started_at is lazily written by agx_get_execution when "caller==agent_id and started_at is empty
        #   and run_status=='running'" = the agent itself declaring "I started".
        #   Old behaviour: queueing longer than 2x timeout reported a "queue timeout" -> mistaking "not delivered" for "ran too long",
        #   the same source as the callback misjudgement in the 2026-10-26 22:58 incident.
        #   The three handling tiers are in _handle_unstarted: clear delivery failure -> resend / waited too long -> report once /
        #   otherwise silent (one trace every 30 minutes). Under no circumstances is run_status written.
        return _handle_unstarted(exc, run_uuid, agent_id, session_id, timeout)

    # Ignore anything that started less than 30 seconds ago
    if elapsed < 30:
        return None

    # Determine the anomaly reason (including the wake check)
    # Note: do not clean up wake_state during detection, to avoid a wake loop
    # wake_state cleanup is owned uniformly by agx_finish_task (when the execution completes)
    if agent_type == "openclaw":
        if elapsed > timeout:
            return f"超时（已运行 {elapsed}s，限制 {timeout}s）"
        elif WAKE_ENABLED and not TEST_MODE:
            # Check session_done_wake first: the session is done and the task allows it -> wake immediately
            if _check_session_done_wake(run_uuid, agent_id, session_id, agent_type):
                return None  # Already woken, return normal this round (confirm next round)
            # * 2026-10-28: the old gate (return None whenever the Redis wake_state exists) was removed.
            #   Wake idempotency is now decided by the DB wake_count (see _try_wake),
            #   otherwise a Redis key expiry re-fires, or conversely blocks all later tiers forever.
            # No session-done wake triggered -> fall back to the original WAKE_RATIO path
            reason = _try_wake(exc, timeout, elapsed, run_uuid, session_id, agent_type, agent_id)
            if reason is None:
                return None
            return reason
        else:
            return None
    elif not tmux_alive:
        return f"session 消失（已运行 {elapsed}s）"
    elif elapsed > timeout:
        return f"超时（已运行 {elapsed}s，限制 {timeout}s）"
    elif WAKE_ENABLED and not TEST_MODE:
        reason = _try_wake(exc, timeout, elapsed, run_uuid, session_id, agent_type, agent_id)
        if reason is None:
            return None
        return reason
    else:
        return None


def _notify_owner(exc: dict, reason: str) -> bool:
    """Timeout / not started -> notify the original task's *initiator* (creating no execution).

    * 2026-10-28 rework (Feng decision):
      It used to dispatch a `watchdog_alert` execution as the alert -- so the "alert"
      timed out itself and generated more alerts (measured 12 self-loops, one spawning 4),
      and it polluted agx_task_execution with 408 rows in total.
      Now it does exactly three things:
        (1) write the main-row fields (alert_level / alert_count / alerted_at / alert_reason / alert_target)
        (2) write one event (event_type=alert, sortable into a timeline)
        (3) deliver the body into the initiator's session using the send_text envelope (cross-host, cacheable while offline)

    The recipient stays = the original task's `send_agent_id` (Feng confirmed: at this point the system has judged "cannot handle it",
    so disposition returns to the initiator; the executor side is the wake mechanism's job and must not be disturbed twice).

    Returns:
        True  -> the notification was queued
        False -> queueing failed (a failed event was already written, leaving it for the next round)
"""
    run_uuid = exc.get("_id", "")
    send_agent_id = (exc.get("send_agent_id") or "").strip()
    if not send_agent_id:
        log(f" 告警跳过（原任务无 send_agent_id）| {run_uuid}", "WARN")
        return False

    agent_id = exc.get("agent_id", "")
    session_id = exc.get("session_id", "") or ""
    elapsed = get_elapsed_seconds(exc.get("started_at", ""))
    timeout = get_timeout_for_execution(exc)
    wake_count = int(str(exc.get("wake_count") or 0) or 0)
    # * 2026-10-28: the count comes from the DB (the alert_count in the Redis hash may be missing/expired,
    #   and once it reads 0 the MAX_RETRIES escalation can never be reached).
    _db_count = 0
    try:
        _rows = _get_mysql_rows(
            "SELECT alert_count FROM agx_task_execution WHERE task_run_uuid='%s'"
            % _escape(run_uuid)
        )
        if _rows:
            _db_count = int(_rows[0].get("alert_count") or 0)
    except Exception:
        _db_count = int(str(exc.get("alert_count") or 0) or 0)
    alert_count = _db_count + 1
    to_session = f"agent:{send_agent_id}:main"

    text = (
        "【AgentGeeX 告警 L2】任务判定超时，请你处置\n"
        "\n"
        f"任务: {run_uuid}\n"
        f"执行方: {agent_id}\n"
        f"执行会话: {session_id}\n"
        f"已运行: {elapsed}s / 预计 {timeout}s（已唤醒 {wake_count} 次）\n"
        f"原因: {reason}\n"
        "\n"
        "请做三件事:\n"
        "1) 写总结: 该任务未完成造成的影响（结果缺失 / 下游阻塞 / 是否需要重投）\n"
        "2) 判定: 重投 or 终止；结论写入 alert_note\n"
        f"   agx_update_execution(task_run_uuid=\"{run_uuid}\", updates={{\"alert_note\": \"...\"}})\n"
    )

    event_id = _ao_record_event(
        task_run_uuid=run_uuid,
        event_type="alert",
        level=2,
        from_agent="agx_core",
        to_agent=send_agent_id,
        to_session=to_session,
        channel="queue",
        reason=reason,
        payload={
            "alert_count": alert_count,
            "elapsed_seconds": elapsed,
            "timeout_seconds": timeout,
            "wake_count": wake_count,
            "target_agent_id": agent_id,
            "target_session": session_id,
        },
        delivery_state="pending",
    )

    text += f"3) 签收: agx_ack_notice(caller_agent_id=\"{send_agent_id}\", event_id=\"{event_id}\")\n"
    text += "\n（系统判定「无计可施」才发此告警；若任务随后自行完成，会自动记一条 revived 事件。）"

    res = _ao_send_notice(
        send_agent_id,
        to_session,
        text,
        event_id=event_id,
        # * 2026-10-29: delivery_key is mandatory, otherwise the run_uuid of a send_text message is empty,
        #   the ws result key becomes agx:ws:result: (empty suffix) -> the daemon cannot scan it -> _ao_send_notice
        #   reports ok=False -> the alert state enters the 60s backoff branch (GRACE_PERIOD 1800s never applies)
        #   -> measured: two reports 63 seconds apart at 16:09:44 / 16:10:47.
        #   With run_notice_<evt suffix> the daemon can write back event.delivery_state=delivered,
        #   ok=True -> grace works normally at 1800s.
        delivery_key=("run_notice_" + event_id[len("evt_"):]) if event_id.startswith("evt_") else "",
    )
    ok = bool(res.get("ok"))
    _ao_mark_event_delivery(event_id, "sent" if ok else "failed")

    try:
        _exec_mysql(
            "UPDATE agx_task_execution SET alert_level=2, alert_count=alert_count+1, "
            "alerted_at=NOW(), alert_reason='%s', alert_target='%s' "
            "WHERE task_run_uuid='%s' AND run_status='running'"
            % (_escape(reason[:255]), _escape(send_agent_id), _escape(run_uuid))
        )
        _refresh_execution_cache(run_uuid)
    except Exception as e:
        log(f" 告警字段写库失败 | {run_uuid}: {e}", "WARN")

    # -- Alert state machine (Redis side) --
    # * 2026-10-28 fix: the state machine used to be saved as a side effect of the now-deprecated dispatch_watchdog_alert,
    #   and after notifications moved to _notify_owner this step was missed -> get_alert_state was always None
    #   -> the same alert repeated every 2s (measured: 5 alerts in 5 seconds for one task). Restored here.
    try:
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        existing = get_alert_state(run_uuid) or {}
        first_at = existing.get("first_alert_at") or now_iso
        if alert_count > MAX_RETRIES:
            escalate_to_human(run_uuid, reason, existing or {})
            save_alert_state(run_uuid, {
                "status": "escalated",
                "alert_count": alert_count,
                "first_alert_at": first_at,
                "last_alert_at": now_iso,
                "grace_until": time.time() + 86400 * 365,
                "reason": reason,
            })
            try:
                _exec_mysql(
                    "UPDATE agx_task_execution SET alert_level=3 "
                    "WHERE task_run_uuid='%s' AND run_status='running'"
                    % _escape(run_uuid)
                )
            except Exception:
                pass
            log(f" 报警已升级人类 | {run_uuid} | #{alert_count} | {reason}", "WARN")
        elif ok:
            save_alert_state(run_uuid, {
                "status": "sent",
                "alert_count": alert_count,
                "first_alert_at": first_at,
                "last_alert_at": now_iso,
                "grace_until": time.time() + GRACE_PERIOD,
                "reason": reason,
            })
        else:
            # * Delivery failure -> do not consume grace, but back off 60s (avoid retrying every 2s and spamming).
            #   The old code recorded 'sent' + GRACE_PERIOD regardless of success, i.e. gave up after one failure.
            save_alert_state(run_uuid, {
                "status": "failed",
                "alert_count": alert_count,
                "first_alert_at": first_at,
                "last_alert_at": now_iso,
                "grace_until": time.time() + 60,
                "reason": reason,
            })
            log(f" 告警投递失败，60s 后重试 | {run_uuid} | {reason}", "WARN")
    except Exception as e:
        log(f" 告警状态保存失败 | {run_uuid}: {e}", "WARN")

    if ok:
        log(
            f" 已告警(第{alert_count}次) | {run_uuid} → {send_agent_id} | "
            f"event={event_id} | {reason}"
        )
    else:
        log(
            f" 告警入队失败 | {run_uuid} → {send_agent_id} | "
            f"event={event_id} | {res.get('error', '')}",
            "WARN",
        )
    return ok


def _handle_anomaly(exc: dict, reason: str) -> str | None:
    """
    State machine plus dispatch: handle anomalies found by _detect_anomaly.
    alert_state dedup control plus _notify_owner / escalate_to_human.

    Returning None means no action this round (within grace or already escalated).
    Returning str means an alert or escalation was triggered.
"""
    run_uuid = exc.get("_id", "")
    agent_id = exc.get("agent_id", "")
    send_agent_id = exc.get("send_agent_id", "")
    agent_type = _get_agent_type(agent_id)
    elapsed = get_elapsed_seconds(exc.get("started_at", ""))
    timeout = get_timeout_for_execution(exc)

    # -- State machine: driven by grace_until --
    state = get_alert_state(run_uuid)
    if state is None:
        # * Redis-loss fallback: rebuild from the DB alert_level/alert_count/alerted_at,
        #   so a Redis restart does not re-alert the same task.
        state = _rebuild_alert_state_from_db(run_uuid)

    if state is None:
        _capture_diag_info(run_uuid, agent_id, agent_type, reason, elapsed, timeout)
        if send_agent_id:
            _notify_owner(exc, reason)
        return reason

    if state.get("status") == "escalated":
        return None

    now_ts = time.time()
    if now_ts < state.get("grace_until", 0):
        return None

    _capture_diag_info(run_uuid, agent_id, agent_type, reason, elapsed, timeout)
    if send_agent_id:
        _notify_owner(exc, reason)
    return reason


# -- Receipt scanning --------------------------------------------------


def _get_completed_need_reply() -> list:
    """Get (from MySQL) the executions *under this machine's agents* that are finished (succeeded/failed) and have needs_reply=1.

    * 2026-10-26 multi-host host isolation (DH-067): the original implementation scanned the whole table, and under
      the "one dog per machine" topology three dogs fought over the same task -> duplicate receipts (measured up to 3 per task, earliest 2026-09-11).
      Evidence: run_general_9898f22c1393 (host=test_srv_a) was answered by both the test_host_mac and test_srv_b
      dogs, while test_srv_a's own dog did not send at all.

    * Why filter by agent_id rather than host_id:
      agx_task_execution.host_id records "which machine the task ran on", while the agent's
      agx_agent_registry.host_id records "which machine the agent belongs to" -- the two may disagree.
      Evidence: xplane_toliss_dev is registered on test_host_mac but its tasks ran on test_srv_b
      (three rows on 2026-10-25, host_id=test_srv_b / reg_host=test_host_mac).
      get_running_executions decides by agent ownership, so this must use the same criterion, otherwise the two views fight.
"""
    my_agents = _get_my_agent_ids()
    if not my_agents:
        # Cannot get this machine's agent list -> return empty conservatively; better to send nothing this round than to send receipts on someone else's behalf
        log(f" 回执扫描跳过：本机 agent 名单为空 (host_id={HOST_IDS_LABEL or '?'})", "WARN")
        return []
    # agent_id comes from agx_agent_registry (the naming rule allows only [a-z0-9_]), already validated, so it can be inlined directly
    agent_list = ",".join(f"'{_escape(a)}'" for a in sorted(my_agents))
    return _get_mysql_rows(
        "SELECT task_run_uuid, session_id, send_session_id, send_agent_id, agent_id, "
        "task_id, project_id, network_id, host_id, summary, run_status, exec_instruction "
        "FROM agx_task_execution "
        "WHERE (run_status = 'succeeded' OR run_status = 'failed') AND needs_reply = 1 "
        f"AND agent_id IN ({agent_list})"
    )


def _mark_receipt_sent(run_uuid: str, ack_state: str = "sent"):
    """The receipt has been sent -> write the *send instant* onto the task row.

    * Feng, 2026-10-29: all three time axes of a receipt live on *the task*; none is written to the event table.
        reply_sent_at      the time I sent it (every resend overwrites with the latest, the first is not kept)
        reply_delivered_at the delivery time (written by the daemon)
        reply_read/_at     the acknowledgement time (written by agx_ack_receipt)
      The event table only keeps history: how many times sent, when each time, whether delivery succeeded. **It is never a predicate**.

    * Deliberately *not* setting needs_reply=3 here: the closing point is *acknowledgement*, not *sending*.
      Previously it was set to 3 the moment it was sent = "already replied", so not acknowledging counted as done and neither resend nor alert could see it.

    Args:
        run_uuid: the original task (not the receipt itself -- a receipt is not an execution).
        ack_state: sent / delivered; once acknowledged it automatically stays read.
"""
    _exec_mysql(
        "UPDATE agx_task_execution SET reply_sent_at=NOW(), "
        f"ack_state=IF(reply_read=1,'read','{_escape(ack_state)}') "
        f"WHERE task_run_uuid='{_escape(run_uuid)}'"
    )
    _refresh_execution_cache(run_uuid)


def _reply_to_sender(exc: dict):
    """Send the task-completion receipt to the original sender. Swap the sender and receiver, using the general channel."""
    original_run_uuid = exc["task_run_uuid"]
    send_agent_id = exc["send_agent_id"]
    completed_agent_id = exc["agent_id"]
    send_session_id = exc.get("send_session_id") or ""

    # * 2026-10-19: dirty-value backstop -- send_session_id may have inherited a placeholder from upstream (e.g. "***")
    #   or contain illegal characters (a UI-truncated "...", whitespace, etc.). Using it directly as session_key would make the receipt delivery fail.
    #   Fails the character safety check -> treat it as empty and fall through to "look up default_session_id" below.
    if send_session_id and not _is_safe_session_key(send_session_id):
        log(f"回执: 脏 send_session_id={send_session_id!r} 已丢弃，回落 default_session_id | {original_run_uuid}", "WARN")
        send_session_id = ""

    if not send_agent_id:
        log(f"回执跳过: {original_run_uuid} send_agent_id 为空", "WARN")
        _set_needs_reply(original_run_uuid, 3)  # Skip, and mark as replied
        return

    # -- Atomic claim, preventing duplicate scanning (CAS: only the dog that claims proceeds) --
    if not _claim_reply(original_run_uuid):
        return

    # Determine the target reply session
    if not send_session_id:
        rows = _get_mysql_rows(f"SELECT default_session_id FROM agx_agent_registry WHERE agent_id='{_escape(send_agent_id)}' AND active_status=1")
        if rows:
            send_session_id = rows[0].get("default_session_id", "")

    if not send_session_id:
        log(f"回执跳过: send_agent_id={send_agent_id} 无会话通道", "WARN")
        _set_needs_reply(original_run_uuid, 3)
        return

    summary = exc.get("summary", "") or ""
    status = exc.get("run_status", "succeeded")

    # * Feng, 2026-10-29: a receipt is a message, not an execution.
    #   The old implementation went through _ao_dispatch_task -> creating a run_* row in agx_task_execution:
    #   it did no work yet occupied the running index, could only be exempted from timeout/wake via is_reply_to,
    #   and once delivery failed it was pinned at running forever (evidence run_general_899f0a87dea7 hung 2h+).
    #   The new implementation follows the proven alert approach: write one receipt event plus a plain-message delivery.
    #   So target_task_id is no longer needed (no execution means no general task template).
    # * Feng, 2026-10-29: a receipt is a message *isomorphic to dispatch*, neither a new kind of message nor an execution.
    #   The four envelope fields follow the protocol: task_run_uuid / send_agent / self_session_id / msg_type.
    #   The receiver reads the summary itself by task_run_uuid (a receipt does *not* carry the conclusion text -- the protocol says it does not,
    #   and carrying it would make reading optional); from msg_type=receipt it knows "this one needs an acknowledgement".
    #   The thing acknowledged is the *task*, not this record -- acknowledging the record means nothing.
    # * 2026-11-16: go through agx_proto uniformly (fields consistent with other paths)
    receipt_struct = build_struct(
        original_run_uuid,
        completed_agent_id,
        send_session_id,
        msg_type="receipt",
        resend_attempt=0,
    )
    receipt_text = json.dumps(receipt_struct, ensure_ascii=False)
    status = exc.get("run_status", "succeeded")

    if TEST_MODE:
        today = datetime.now().strftime("%Y-%m-%d")
        r_test = _get_redis()
        r_test.lpush(
            f"{TEST_ALERT_LIST}:{today}",
            json.dumps(
                {
                    "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "type": "receipt",
                    "from_agent": completed_agent_id,
                    "to_agent": send_agent_id,
                    "original_run_uuid": original_run_uuid,
                    "status": status,
                },
                ensure_ascii=False,
            ),
        )
        r_test.expire(f"{TEST_ALERT_LIST}:{today}", TEST_TTL)
        log(
            f" [TEST] 回执 | from={completed_agent_id} → {send_agent_id} | original={original_run_uuid} | status={status}",
            "INFO",
        )
        _mark_receipt_sent(original_run_uuid)
        return

    # -- History: one event per send (only for later queries "how many times / when / delivered or not"; never a predicate) --
    event_id = _ao_record_event(
        task_run_uuid=original_run_uuid,
        event_type="receipt",
        level=0,
        from_agent=completed_agent_id,
        to_agent=send_agent_id,
        to_session=send_session_id,
        channel="queue",
        reason=f"任务回执 status={status}",
        payload={
            "reply_type": "task_completion",
            "original_run_uuid": original_run_uuid,
            "original_task_id": exc.get("task_id", ""),
            "completed_by": completed_agent_id,
            "status": status,
        },
        delivery_state="pending",
    )

    # Delivery confirmation marker: the run_ prefix is mandatory (the daemon result scan pattern is agx:ws:result:run_*)
    delivery_key = "run_receipt_" + event_id[len("evt_"):]

    res = _ao_send_notice(
        send_agent_id,
        send_session_id,
        receipt_text,
        event_id=event_id,
        delivery_key=delivery_key,
        msg_type="receipt",
        send_agent=completed_agent_id,
    )

    if not res.get("ok"):
        log(f"回执发送失败 {original_run_uuid}: {res.get('error')}", "ERROR")
        _ao_mark_event_delivery(event_id, "failed")
        # * Do not call _set_needs_reply(3): a failed send even less deserves to count as "replied".
        #   needs_reply stays at 2 (replying), and the resend scan picks it up by "not acknowledged".
        return

    # -- State lands on *the task*: write the send instant; needs_reply stays 2 until acknowledged --
    _mark_receipt_sent(original_run_uuid)
    _ao_mark_event_delivery(event_id, "sent")
    log(f"回执已发送(消息) | {original_run_uuid} → {send_agent_id} | {status} | {event_id}")


def _refresh_execution_cache(run_uuid: str):
    """Read the whole DB row back and refresh the execution cache wholesale (avoid making a partial entity via hset).

    Why the whole row must be read back (fixed defect 4 on 2026-10-22):
      the problem with a single-field hset is not "writing too little" but "hset creating a partial entity when none exists".
      Redis hset is upsert semantics -- when the key does not exist it creates a hash with only those fields.
      Measured: 11 execution entities had only 1~6 fields (normal is 29), all caused by the direct
      hset write path in watchdog/daemon. Such partial entities make later read paths receive data with "missing fields",
      and the cache never heals itself (Redis does not expire).

    Why not use MCP's _sync_entity:
      watchdog/daemon are separate processes, and importing mcp_server would pull up the whole MCP server.
      So an equivalent "write the whole row into the hash" is implemented here, with the field list taken from the DB row (new columns included automatically).
"""
    try:
        rows = _get_mysql_rows(
            "SELECT * FROM agx_task_execution WHERE task_run_uuid='" + _escape(run_uuid) + "'"
        )
        if not rows:
            return
        row = rows[0]
        clean = {}
        for k, v in row.items():
            if v is None:
                continue
            clean[str(k)] = str(v) if not isinstance(v, str) else v
        if not clean:
            return
        r = _get_redis()
        pipe = r.pipeline()
        pipe.hset("agx:entity:execution:" + run_uuid, mapping=clean)
        # * Feng, 2026-10-30: cache and index must be written in one go.
        #   "Updated the data but not the index" is exactly the disease this round fixes -- written separately it will always be missed.
        #   Note it uses row (raw DB values, including datetime/int), not clean (all stringified).
        if _receipt_pending_wanted(
            row.get("run_status"), row.get("needs_reply"), row.get("reply_read")
        ):
            pipe.zadd(
                RECEIPT_PENDING_IDX,
                {run_uuid: _receipt_pending_score(
                    row.get("reply_sent_at"), row.get("finished_at"))},
            )
        else:
            pipe.zrem(RECEIPT_PENDING_IDX, run_uuid)
        pipe.execute()
    except Exception as e:
        log(f"刷新 execution 缓存失败 | {run_uuid}: {e}", "WARN")


def _claim_reply(run_uuid: str) -> bool:
    """Atomically claim the receipt right (CAS): only the watchdog that turns needs_reply from 1 to 2 may send the receipt.

    * 2026-10-26 DH-067: the original implementation was an unconditional UPDATE, and although the comment said "claim first",
      in practice everyone succeeded -- when three dogs scanned the same task at once, each thought it had claimed it and each sent a receipt.

    Approach: put needs_reply=1 in the WHERE clause and rely on InnoDB row locks for serialisation.
      Under concurrency only the first gets affected_rows=1; the rest get 0.
      Simpler and more reliable than a Redis distributed lock: no extra component, and the lock is released automatically with the row's lifetime.
"""
    n = _exec_mysql(
        f"UPDATE agx_task_execution SET needs_reply=2 "
        f"WHERE task_run_uuid='{_escape(run_uuid)}' AND needs_reply=1"
    )
    if n != 1:
        log(f"回执抢占失败（其他看门狗已接管）| {run_uuid} | affected={n}", "DEBUG")
        return False
    _refresh_execution_cache(run_uuid)
    return True


def _set_needs_reply(run_uuid: str, value: int):
    """Set an execution's needs_reply state (0-not needed 1-pending reply 2-replying 3-replied)."""
    _exec_mysql(f"UPDATE agx_task_execution SET needs_reply={value} WHERE task_run_uuid='{_escape(run_uuid)}'")
    # 2026-10-22: single-field hset -> read the whole row back and write all fields (do not build a partial entity)
    _refresh_execution_cache(run_uuid)


# * 2026-10-29: _update_run_status was deleted (zero call sites).
#   It was the generic exit of the era when "the watchdog may judge a task dead": one UPDATE could set any run_status
#   to any value. The red line Feng corrected twice today lands exactly here -- a task's success/failure and end time are decided
#   only by the executing agent's agx_finish_task; the watchdog has only the right to "detect + alert".
#   The function already had no callers; keeping it would be leaving a tool for "bypassing the red line" within reach.
#   Note: the DH-066 rule it used to carry, "a status change must sync the index", still holds and is now carried by
#   _claim_legacy_migration (CAS + _sync_execution_status_index).


def _sync_execution_status_index(run_uuid: str, new_status: str):
    """Sync the Redis status index: remove the old status, add the new one."""
    try:
        r = _get_redis()
        # Remove the old index entry
        for s in ("running", "succeeded", "failed", "pending", "cancelled", "draft", "awaiting_confirm"):
            r.zrem(f"agx:idx:execution:status:{s}", run_uuid)
        # Add the new index entry
        score = int(time.time())
        r.zadd(f"agx:idx:execution:status:{new_status}", {run_uuid: score})
        # Update the by_plan index
        raw = r.hgetall(f"agx:entity:execution:{run_uuid}")
        if raw and raw.get("plan_id"):
            _PLAN_SCORE = {
                "draft": -1,
                "pending": 0,
                "running": 1,
                "succeeded": 2,
                "failed": 3,
                "cancelled": 4,
                "awaiting_confirm": 0,
            }
            r.zadd(f"agx:idx:execution:by_plan:{raw['plan_id']}", {run_uuid: _PLAN_SCORE.get(new_status, 0)})
    except Exception:
        pass


def _scan_chain_executions(
    r,
    chain_pending,
    pending_data,
    plan_starts,
    all_children,
    child_status,
    activatable,
):
    """Chain-mode dedicated scan.

    Algorithm: traverse the chain starting from plan.start_task_run_uuid.
    - running/pending/draft: activate children when it has any -> stop; otherwise activate itself
    - succeeded: advance to next_task -> continue
    - failed/cancelled: stop

    Pure in-memory work, no extra Redis queries.
"""
    if not chain_pending:
        return

    # -- session-done wake preprocessing --
    running_ids = set(r.zrange("agx:idx:execution:status:running", 0, -1))
    if WAKE_ENABLED and not TEST_MODE:
        log(f"[chain] session_done_wake 预处理 | running={len(running_ids)} chain_plans={len(chain_pending)}", "INFO")
        _todo = set()
        for _pid, _entries in chain_pending.items():
            for _uid, _data in _entries:
                if _uid in running_ids:
                    _todo.add(_uid)
            _start = plan_starts.get(_pid, "")
            if _start:
                _curr = _start
                while _curr and len(_todo) <= 50:
                    for _c in all_children.get(_curr, set()):
                        if _c in running_ids:
                            _todo.add(_c)
                    _cd = pending_data.get(_curr, {})
                    _nxt = _cd.get("next_task", "") if _cd else ""
                    if not _nxt:
                        _nxt = r.hget(f"agx:entity:execution:{_curr}", "next_task") or ""
                    if _nxt and _nxt not in _todo:
                        _curr = _nxt
                    else:
                        break
        for _uid in _todo:
            _ag = r.hget(f"agx:entity:execution:{_uid}", "agent_id") or ""
            _ss = r.hget(f"agx:entity:execution:{_uid}", "session_id") or ""
            log(f"[chain] session_done_wake 预处理 | {_uid} | agent={_ag} session={_ss}", "INFO")
            if _ag and _ss:
                _trig = _check_session_done_wake(_uid, _ag, _ss, "openclaw")
                log(f"[chain] session_done_wake 预处理 | {_uid} | triggered={_trig}", "INFO")

    def _activate_children(children_set, label):
        """Activate the children that are in draft/pending."""
        if not children_set:
            return None
        activated = []
        unknown = [c for c in children_set if c not in pending_data]
        if unknown:
            pipe = r.pipeline()
            for c in unknown:
                pipe.hget(f"agx:entity:execution:{c}", "run_status")
            for c, st in zip(unknown, pipe.execute()):
                if st in ("draft", "pending"):
                    activatable.append(c)
                    activated.append(c)
        for c in children_set:
            exec_raw_c = pending_data.get(c)
            if exec_raw_c and exec_raw_c.get("run_status") in ("draft", "pending"):
                activatable.append(c)
                activated.append(c)
        if activated:
            log(f"[chain] plan={plan_id} current={current} {label} | 激活 children={activated}", "INFO")
        return activated

    def _check_children_all_succeeded(children_set):
        """Check whether all children succeeded."""
        if not children_set:
            return False
        all_good = True
        unknown = [c for c in children_set if c not in pending_data]
        if unknown:
            pipe = r.pipeline()
            for c in unknown:
                pipe.hget(f"agx:entity:execution:{c}", "run_status")
            results = pipe.execute()
            for c, st in zip(unknown, results):
                log(f"[chain] child={c} status={st}", "INFO")
                if st != "succeeded":
                    all_good = False
        for c in children_set:
            exec_raw_c = pending_data.get(c)
            child_st = exec_raw_c.get("run_status", "") if exec_raw_c else ""
            if child_st and child_st in ("draft", "pending", "running"):
                log(f"[chain] child={c} status={child_st} (from pending_data)", "INFO")
                all_good = False
        return all_good

    # running_ids is already defined during preprocessing

    log(f"[chain] 开始扫描 chain plans: count={len(chain_pending)}", "INFO")

    for plan_id, _entries in chain_pending.items():
        start = plan_starts.get(plan_id, "")
        log(f"[chain] plan={plan_id} start={start}", "INFO")
        if not start:
            log(f"[chain] plan={plan_id} 无 start_task_run_uuid → 跳过", "WARN")
            continue

        current = start
        step = 0
        while current:
            step += 1
            cur_status = None
            cur_next = ""
            cur_children = set()

            exec_raw = pending_data.get(current)
            if exec_raw:
                cur_status = exec_raw.get("run_status", "")
                cur_next = exec_raw.get("next_task", "")
                cur_children = all_children.get(current, set())
                log(f"[chain] step={step} current={current} status={cur_status} next={cur_next[:40]} children={list(cur_children)} from=pending_data", "INFO")
            elif current in running_ids:
                cur_status = "running"
                pipe = r.pipeline()
                pipe.hget(f"agx:entity:execution:{current}", "next_task")
                pipe.smembers(f"agx:idx:execution:by_parent:{current}")
                cur_next_raw, cur_children_raw = pipe.execute()
                cur_next = cur_next_raw or ""
                cur_children = cur_children_raw or set()
                log(f"[chain] step={step} current={current} status=running next={cur_next[:40]} children={list(cur_children)} from=redis", "INFO")
            else:
                cur_next = r.hget(f"agx:entity:execution:{current}", "next_task") or ""
                cur_status = r.hget(f"agx:entity:execution:{current}", "run_status") or "unknown"
                log(f"[chain] step={step} current={current} status={cur_status} next={cur_next[:40]} from=redis(hget)", "INFO")
                if cur_status != "succeeded":
                    log(f"[chain] step={step} current={current} status={cur_status} != succeeded → 断链", "WARN")
                    break

            if cur_status in ("running", "draft", "pending"):
                log(f"[chain] step={step} current={current} status={cur_status} → 检查 children", "INFO")
                # chain mode also runs the session-done wake check on running tasks
                if cur_status == "running" and WAKE_ENABLED and not TEST_MODE:
                    agg_id = r.hget(f"agx:entity:execution:{current}", "agent_id") or ""
                    sess_id = r.hget(f"agx:entity:execution:{current}", "session_id") or ""
                    if agg_id and sess_id:
                        _check_session_done_wake(current, agg_id, sess_id, "openclaw")
                activated_kids = _activate_children(cur_children, cur_status)
                if activated_kids:
                    log(f"[chain] step={step} children 已激活 → 停止", "INFO")
                    break
                # Has children: check whether all of them succeeded
                if cur_children:
                    all_done = _check_children_all_succeeded(cur_children)
                    if all_done:
                        log(f"[chain] step={step} children 全部 succeeded → 激活当前", "INFO")
                        activatable.append(current)
                    else:
                        log(f"[chain] step={step} children 未全部完成 → 继续等待", "INFO")
                    break
                log(f"[chain] step={step} 无 children → 激活当前", "INFO")
                activatable.append(current)
                log(f"[chain] plan={plan_id} current={current} {cur_status} → 激活", "INFO")
                break

            if cur_status == "succeeded":
                log(f"[chain] step={step} current={current} succeeded", "INFO")
                if cur_next:
                    log(f"[chain] step={step} → 推进 next={cur_next[:40]}", "INFO")
                    current = cur_next
                    continue
                log(f"[chain] step={step} 无 next_task → 链结束", "INFO")
                break

            if cur_status == "awaiting_confirm":
                # * 2026-11-06: the task has been delivered and is waiting for a human to release it -> the cascade must stop here.
                #   This is not an anomaly (deliberately no WARN, otherwise one log line every round).
                log(f"[chain] plan={plan_id} current={current} awaiting_confirm → 等人类放行", "INFO")
                break

            if cur_status in ("failed", "cancelled"):
                log(f"[chain] plan={plan_id} current={current} {cur_status} → 链终止", "WARN")
                break

            # unknown -> skip
            log(f"[chain] plan={plan_id} current={current} unknown status={cur_status} → 跳过", "WARN")
            break


def _scan_pending_executions():
    """Scan every pending execution and activate the tasks whose scheduling conditions are met.

    Executability (all must hold to activate):
    1. prev_task is empty or prev_task already succeeded
    2. all child tasks are terminal (not in running / pending)
    3. scheduled_at is due (when set)

    Pure Redis work, no SQL dependency.
    Uses pipeline batch queries, supporting 5000+ pending tasks in sub-second time.
"""
    try:
        r = _get_redis()
        pending_ids = list(r.zrange("agx:idx:execution:status:pending", 0, -1))
    except Exception as e:
        log(f"获取 pending 列表失败 (Redis): {e}", "ERROR")
        return

    if not pending_ids:
        return

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_count = len(pending_ids)

    # -- Phase 1: batch-fetch metadata of all pending tasks --
    pending_data = {}
    BATCH = 2000
    for start in range(0, total_count, BATCH):
        chunk = pending_ids[start : start + BATCH]
        pipe = r.pipeline()
        for uid in chunk:
            pipe.hgetall(f"agx:entity:execution:{uid}")
        for uid, raw in zip(chunk, pipe.execute(), strict=False):
            if raw and raw.get("run_status") == "pending":
                pending_data[uid] = raw

    if not pending_data:
        return

    # -- Phase 1.5: group by plan_id plus batch-query each plan's activate_mode --
    plan_ids = {d.get("plan_id", "") for d in pending_data.values() if d.get("plan_id")}
    plan_modes = {}  # plan_id → "watchdog" | "chain"
    plan_starts = {}  # plan_id → start_task_run_uuid
    if plan_ids:
        pipe = r.pipeline()
        for pid in plan_ids:
            pipe.hget(f"agx:entity:plan:{pid}", "activate_mode")
            pipe.hget(f"agx:entity:plan:{pid}", "start_task_run_uuid")
        results = pipe.execute()
        for i, pid in enumerate(plan_ids):
            plan_modes[pid] = results[i * 2] or "watchdog"
            plan_starts[pid] = results[i * 2 + 1] or ""

    # -- Phase 1.6: plan ownership isolation (Feng, 2026-10-27) -----------------
    #   A plan belongs to the dog of the machine that created it (agx_plan.host_id).
    #   Previously pending used a global zrange, so three dogs scanned the same plan -> the same step was claimed three times:
    #   the losers spammed "activation failed: status is running", and if the winner failed to deliver, the task was stuck with nobody taking over.
    #   Non-plan tasks (no plan_id) are filtered by "the executing agent's ownership", the same criterion as the timeout scan.
    plan_hosts = {}  # plan_id → host_id
    if plan_ids:
        pipe = r.pipeline()
        for pid in plan_ids:
            pipe.hget(f"agx:entity:plan:{pid}", "host_id")
        for pid, h in zip(plan_ids, pipe.execute(), strict=False):
            plan_hosts[pid] = (h or "").strip()

    my_agents = _get_my_agent_ids()
    if not my_agents:
        log(f" 计划调度跳过：本机 agent 名单为空 (host_id={HOST_IDS_LABEL or '?'})", "WARN")
        return

    _owned, _skip_plan, _skip_agent = {}, 0, 0
    for _uid, _d in pending_data.items():
        _pid = (_d.get("plan_id") or "").strip()
        if _pid:
            _ph = plan_hosts.get(_pid, "")
            if _ph:
                if _ph not in HOST_IDS:
                    _skip_plan += 1
                    continue
            elif (_d.get("agent_id") or "") not in my_agents:
                # Historical plans with no ownership backfilled -> conservatively fall back to the agent ownership
                _skip_agent += 1
                continue
        elif (_d.get("agent_id") or "") not in my_agents:
            _skip_agent += 1
            continue
        _owned[_uid] = _d

    if _skip_plan or _skip_agent:
        _sig = (_skip_plan, _skip_agent, len(_owned))
        _ts = time.time()
        if _sig != _ISO_LOG["sig"] or _ts - _ISO_LOG["ts"] > 60:
            _ISO_LOG["sig"], _ISO_LOG["ts"] = _sig, _ts
            log(
                f" 计划调度主机隔离 | 本机={HOST_IDS_LABEL} | 归属本机={len(_owned)} | "
                f"跳过他机计划={_skip_plan} 他机agent={_skip_agent}",
                "INFO",
            )
    pending_data = _owned
    if not pending_data:
        return

    # -- Phase 2: batch-fetch prev_task state (run_status + needs_confirm) --
    prev_ids = {d["prev_task"] for d in pending_data.values() if d.get("prev_task", "")}
    prev_status = {}
    prev_needs_confirm = {}  # prev_task → needs_confirm
    if prev_ids:
        pipe = r.pipeline()
        for pid in prev_ids:
            pipe.hget(f"agx:entity:execution:{pid}", "run_status")
            pipe.hget(f"agx:entity:execution:{pid}", "needs_confirm")
        results = pipe.execute()
        for i, pid in enumerate(prev_ids):
            prev_status[pid] = results[i * 2]
            prev_needs_confirm[pid] = results[i * 2 + 1]

    # -- Phase 3: batch-fetch all child-task sets plus child-task states --
    # 3a: smembers to get the child task ID set
    all_children = {}  # uid → set of child_ids
    child_pipe = r.pipeline()
    for uid in pending_data:
        child_pipe.smembers(f"agx:idx:execution:by_parent:{uid}")
    child_sets = child_pipe.execute()

    # 3b: collect all child task IDs -> batch-fetch run_status
    all_child_ids = set()
    for uid, cset in zip(pending_data, child_sets, strict=False):
        if cset:
            all_children[uid] = cset
            all_child_ids.update(cset)

    child_status = {}  # child_id → run_status
    if all_child_ids:
        pipe = r.pipeline()
        for cid in all_child_ids:
            pipe.hget(f"agx:entity:execution:{cid}", "run_status")
        child_status = dict(zip(all_child_ids, pipe.execute(), strict=False))

    # -- Phase 4: in-memory decision --
    activatable = []
    chain_pending = {}  # plan_id -> [(uid, data), ...]  kept for the Phase 5 chain scan
    for uid, data in pending_data.items():
        try:
            # chain mode skips the watchdog decision and is handled by the Phase 5 chain scan
            plan_id = data.get("plan_id", "")
            if plan_modes.get(plan_id) == "chain":
                chain_pending.setdefault(plan_id, []).append((uid, data))
                continue

            # Condition 1: prev_task already succeeded and needs no human confirmation
            prev_task = data.get("prev_task", "")
            if prev_task:
                if prev_status.get(prev_task) != "succeeded":
                    continue
                # The preceding task has needs_confirm=1 -> cannot activate (wait for agx_finish_task to write awaiting_confirm / for a human to release it)
                if prev_needs_confirm.get(prev_task) == "1":
                    continue

            # Condition 2: no child task is in running/pending
            children = all_children.get(uid, set())
            if children and any(child_status.get(c) in ("running", "pending") for c in children):
                continue

            # Condition 3: scheduled_at
            scheduled_at = data.get("scheduled_at", "")
            if scheduled_at and scheduled_at > now_str:
                continue

            activatable.append(uid)
        except Exception as e:
            log(f"判定 pending 执行 {uid} 异常: {e}", "WARN")

    # -- Phase 4.5: Chain-mode scan --
    log(f"[chain] Phase 4 完成 | chain_pending plans={len(chain_pending)} ex={sum(len(v) for v in chain_pending.values())} | plan_modes keys={list(plan_modes.keys())[:5]}...", "INFO")
    # Algorithm: walk the chain from start_task_run_uuid; on running activate children, on succeeded advance to next_task
    _scan_chain_executions(r, chain_pending, pending_data, plan_starts, all_children, child_status, activatable)

    # -- Phase 5: batch activation --

    if not activatable:
        return

    log(f" 计划调度 | pending={len(pending_ids)} | 可激活={len(activatable)}")

    for run_uuid in activatable:
        if TEST_MODE:
            today = datetime.now().strftime("%Y-%m-%d")
            r_test = _get_redis()
            r_test.lpush(
                f"{TEST_TASK_LIST}:{today}",
                json.dumps(
                    {
                        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "type": "activate",
                        "run_uuid": run_uuid,
                    },
                    ensure_ascii=False,
                ),
            )
            r_test.expire(f"{TEST_TASK_LIST}:{today}", TEST_TTL)
            log(f" [TEST] 激活 {run_uuid} | Redis={TEST_TASK_LIST}:{today}", "INFO")
            continue
        try:
            result = _ao_activate_task("agx_watchdog", run_uuid)
            if isinstance(result, dict) and "error" in result:
                log(f"激活失败 {run_uuid}: {result['error']}", "ERROR")
            else:
                log(f" 已激活 {run_uuid}")
        except Exception as e:
            log(f"激活异常 {run_uuid}: {e}", "ERROR")


# -- Sub-plan completion monitoring -----------------------------------------


def _is_plan_completed(plan_id: str) -> tuple:
    """
    Decide whether the given plan is entirely terminal.

    Decision logic (short-circuit):
    1. plan_status is cancelled -> return (False, None) (cancelled, do not notify)
    2. plan_status is completed / failed -> return (True, plan_status)
    3. query every step in agx:idx:execution:by_plan:{plan_id}; if all are terminal -> return (True, "completed")

    Returns:
        (is_terminal, status) -- status is used to distinguish the notification message type
"""
    try:
        r = _get_redis()

        # plan_status terminal-state decision: cancelled/archived notify nothing, completed/failed notify
        # * FIX (2026-10-13): the original code short-circuited only cancelled and missed archived -- an archived plan
        # fell into the "judge alive by step" branch below, where its steps are all archived too -> judged (True, "completed")
        # -> a bogus plan_completed notification (evidence: a wave of 5 at 16:36, all archived plans).
        # archived = an archived terminal state, in the same "must not notify again" class as cancelled.
        plan_status = r.hget(f"agx:entity:plan:{plan_id}", "plan_status")
        if plan_status in ("cancelled", "archived"):
            log(f"[子plan监控] plan={plan_id} plan_status={plan_status} 已终止(不通知)，跳过", "DEBUG")
            return False, None
        if plan_status in ("completed", "failed"):
            log(f"[子plan监控] plan={plan_id} plan_status={plan_status} 已是终态", "DEBUG")
            return True, plan_status

        step_ids = r.zrange(f"agx:idx:execution:by_plan:{plan_id}", 0, -1)
        if not step_ids:
            log(f"[子plan监控] plan={plan_id} 无 step 记录，视为已完成", "DEBUG")
            # A 2-tuple must be returned: the caller unpacks it as (is_terminal, final_status).
            # The old code did a bare `return True` -> TypeError: cannot unpack non-iterable bool object
            # (BUG-B2: draft / 0-step orphan plans hit this branch, raising once every 2s, 160k+ in total).
            return True, "completed"

        # Batch-fetch the run_status of every step
        pipe = r.pipeline()
        for sid in step_ids:
            pipe.hget(f"agx:entity:execution:{sid}", "run_status")
        statuses = pipe.execute()

        # * 2026-11-06 added awaiting_confirm (same criterion as _check_plan_completion):
        #   the executor has delivered and a human has not released it = not done. The old criterion recognised only running/pending/draft ->
        #   a step awaiting release was treated as finished -> the plan was declared complete early and a plan_completed notification was sent
        #   before anyone had looked at the result. This is an accounting error caused by "one member missing from the state set",
        #   the same class as the archived one on 2026-10-13 (exhaustive predicates must be kept in sync with the full state set).
        unfinished = [
            sid
            for sid, st in zip(step_ids, statuses, strict=False)
            if st in ("running", "pending", "draft", "awaiting_confirm") or st is None
        ]
        if unfinished:
            log(
                f"[子plan监控] plan={plan_id} | 未完成 step={len(unfinished)}/{len(step_ids)} | 示例: {unfinished[:3]}",
                "DEBUG",
            )
            return False, None

        log(f"[子plan监控] plan={plan_id} 全部终止 | 共 {len(step_ids)} step", "INFO")
        # Derive the plan terminal state from the step states: any failed -> failed, otherwise completed
        any_failed = any(st == "failed" for st in statuses)
        final_status = "failed" if any_failed else "completed"
        return True, final_status
    except Exception as e:
        log(f"[子plan监控] 判定 plan={plan_id} 异常: {e}", "ERROR")
        return False, None


def _send_plan_completed(session_id: str, message: str, log_label: str) -> str:
    """
    Send a plan_completed notification through Gateway WebSocket sessions.send.

    Whoever activates checks; the watchdog only sends a notification and closes out nothing on anyone's behalf.

    * 2026-11-11: the return value changed from bool to a three-state string:
      "ok"        -- delivered
      "permanent" -- permanent failure (the session does not exist); retrying ten thousand times will not succeed
      "retry"     -- temporary failure (unreachable / auth / timeout); retryable inside the window
      Old callers used a bool as a condition; a three-state string is equally truthy or falsy, but new code should distinguish explicitly,
      because a "permanent" failure must be persisted, otherwise every scan round retries it (measured 23 times per round x several rounds).
"""
    import websockets.sync.client as ws_sync

    ws_url, ws_token = _gateway_for(_host_of_session(session_id))

    try:
        ws = ws_sync.connect(ws_url, proxy=None, open_timeout=WS_TIMEOUT)
        ws.recv(timeout=WS_TIMEOUT)

        ws.send(
            json.dumps({
                "type": "req",
                "id": "wd_plan_cmp_auth",
                "method": "connect",
                "params": {
                    "minProtocol": 3,
                    "maxProtocol": 4,
                    "client": {"id": "gateway-client", "version": "0.1", "platform": "python", "mode": "backend"},
                    "role": "operator",
                    "scopes": ["operator.read", "operator.write", "operator.admin"],
                    "caps": [],
                    "commands": [],
                    "permissions": {},
                    "auth": {"token": ws_token},
                },
            })
        )
        auth_resp_raw = ws.recv(timeout=WS_TIMEOUT)
        auth_resp = json.loads(auth_resp_raw)
        if not auth_resp.get("ok"):
            ws.close()
            log(f"[计划监控] WS 认证失败 {log_label}: {auth_resp_raw[:300]}", "WARN")
            return "retry"

        req_id = f"wd_plan_cmp_{uuid.uuid4().hex[:8]}"
        ws.send(
            json.dumps({
                "type": "req",
                "id": req_id,
                "method": "sessions.send",
                "params": {
                    "key": session_id,
                    "message": message,
                },
            })
        )
        resp = None
        for _ in range(20):
            try:
                resp_raw = ws.recv(timeout=WS_TIMEOUT)
                msg = json.loads(resp_raw)
                if msg.get("id") == req_id:
                    resp = msg
                    break
            except Exception:
                break
        ws.close()

        if resp is None:
            log(f"[计划监控] WS 通知超时 {log_label}: 未收到 {req_id}", "WARN")
            return "retry"
        if resp.get("ok"):
            log(f"[计划监控] 已通知激活方 {log_label} → {session_id}", "INFO")
            return "ok"
        else:
            _err = (resp.get("error") or {})
            _err_msg = str(_err.get("message") or "").lower()
            _err_code = str(_err.get("code") or "")
            # Permanent failure: the target session simply does not exist. Retrying solves nothing and only blows up the logs and the
            # channel (measured 2026-11-11 09:34: a batch of wincublabs_agent plans retried 23 times per round,
            # gave up after 193s cumulative, and then the next round did it again).
            if "session not found" in _err_msg or _err_code in (
                "SESSION_NOT_FOUND",
                "SESSION_UNKNOWN",
            ):
                log(
                    f"[计划监控] WS 通知永久失败(session 不存在) {log_label}: {session_id}",
                    "WARN",
                )
                return "permanent"
            log(f"[计划监控] WS 通知失败 {log_label}: {json.dumps(resp, ensure_ascii=False)[:300]}", "WARN")
            return "retry"
    except Exception as e:
        log(f"[计划监控] WS 通知异常 {log_label}: {e}", "WARN")
        return "retry"


# -- Plan completion notification: failure retry budget (prevents an infinite retry storm, 2026-10-23; changed to a time budget 2026-10-29) --
# * Feng, 2026-10-29: changed from "maximum failure count" to "time budget".
#   The problem with a count limit: the retry cadence = one main-loop scan (measured about 2s), so 3 tries is about 6s and 6 tries about 12s;
#   the moment the gateway is unreachable for longer than that window, the plan_completed notification is lost forever
#   (notify_key stays claimed and is never retried) -> the activator never closes out -> a false timeout alert arrives hours later.
#   And "how many times" cannot express "how long a gateway restart takes" -- a restart typically takes 20~60s.
#   A time budget writes the intent directly in seconds: retry on every scan inside the window, give up only past it.
PLAN_NOTIFY_RETRY_WINDOW = int(config.daemon.get("watchdog_plan_notify_retry_window", 180))
# Compatible with the old config key: if an old deployment explicitly set watchdog_plan_notify_max_attempts, convert it at about 2s/try into a window,
# avoiding a behaviour jump after the upgrade. (New deployments should use watchdog_plan_notify_retry_window directly.)
_LEGACY_MAX_ATTEMPTS = int(config.daemon.get("watchdog_plan_notify_max_attempts", 0))
if _LEGACY_MAX_ATTEMPTS > 0 and "watchdog_plan_notify_retry_window" not in config.daemon:
    PLAN_NOTIFY_RETRY_WINDOW = _LEGACY_MAX_ATTEMPTS * 2


def _set_plan_notified(r, notify_key: str) -> None:
    """Write the "already notified" marker (idempotent)."""
    if PLAN_NOTIFIED_TTL > 0:
        r.setex(notify_key, PLAN_NOTIFIED_TTL, "1")
    else:
        r.set(notify_key, "1")


def _claim_plan_notify(r, notify_key: str) -> bool:
    """Atomically claim "the notification right for this plan" -- only the watchdog that claims it may send the notification.

    * 2026-10-26 DH-068: the original implementation did `get(notify_key)` for emptiness -> send -> `setex`, with a window between the steps.
      In a multi-host topology:
        - three dogs all got empty -> all three sent the notification -> the same plan notified 3 times
        - and the key was written only on success, so a dog that failed did not write it -> it could reclaim next round
      With SET NX (atomic), claim first then send; a failed claim skips directly.

    * Why not filter by host: a plan is a network-wide entity with no host ownership concept.
      And one machine may also run several watchdog processes (during a restart overlap),
      so SET NX works equally for "several processes on one machine" and is a more fundamental defence than host filtering.

    Returns True  = claimed, may proceed to send the notification
    Returns False = claimed/notified elsewhere, skip this round
"""
    try:
        if PLAN_NOTIFIED_TTL > 0:
            got = r.set(notify_key, "1", nx=True, ex=PLAN_NOTIFIED_TTL)
        else:
            got = r.set(notify_key, "1", nx=True)
        return bool(got)
    except Exception as e:
        log(f"[计划监控] 抢占通知权失败 {notify_key}: {e}", "WARN")
        # Conservative no-send when the claim itself fails, avoiding a regression to the pre-fix duplicate behaviour
        return False


def _release_plan_notify(r, notify_key: str) -> None:
    """Release the claimed notification right (called when sending failed and the retry limit is not reached, so the next round may retry)."""
    try:
        r.delete(notify_key)
    except Exception as e:
        log(f"[计划监控] 释放通知权失败 {notify_key}: {e}", "WARN")


def _record_plan_notify_failure(r, notify_key: str, plan_id: str) -> None:
    """Sending failed: release the notification right inside the budget window so the next round retries; past the window, give up and keep it claimed.

    Background (the 2026-10-23 incident): originally notify_key was written only on a successful send.
    When the activator session no longer existed (session not found) -> the send always failed -> the key was never written ->
    retried every scan cycle, thousands of useless WS calls an hour, blowing up the logs and the channel.

    * 2026-10-26 DH-068: after switching to the SET NX claim model the semantics changed --
      notify_key is already written when claiming, so a failed send *releases* it (_release_plan_notify) for a retry next round,
      and at the limit it stays *claimed* (no more retries). Hence this function no longer writes notify_key itself.

    * 2026-10-29: the stop condition changed from "failure count" to "time budget" (PLAN_NOTIFY_RETRY_WINDOW).
      The hazard of counting: the retry interval drifts with main-loop load, so "how many" is not "how many seconds",
      and it cannot answer "can it still be delivered during a gateway restart".

    State keys (auxiliary only; they take no part in the claim semantics):
      agx:notified_start:{plan_id}  first failure timestamp (the window start, self-cleaning via expiry)
      agx:notified_fail:{plan_id}   failure count (used only in logs)
"""
    first_key = f"agx:notified_start:{plan_id}"
    fail_key = f"agx:notified_fail:{plan_id}"
    try:
        now = time.time()
        r.set(first_key, str(now), nx=True, ex=PLAN_NOTIFY_RETRY_WINDOW + 60)
        first_raw = r.get(first_key)
        first = float(first_raw) if first_raw else now
        n = r.incr(fail_key)
        r.expire(fail_key, 7 * 86400)
    except Exception as e:
        # Redis error -> give up conservatively (notify_key was written when claiming and stays claimed)
        log(f"[计划监控] 失败计数写入异常，放弃重试 | plan={plan_id}: {e}", "ERROR")
        return

    elapsed = now - first
    if elapsed >= PLAN_NOTIFY_RETRY_WINDOW:
        try:
            r.delete(first_key)
            r.delete(fail_key)
        except Exception:
            pass
        log(
            f"[计划监控] 通知失败 {n} 次 / 累计 {elapsed:.0f}s（预算 {PLAN_NOTIFY_RETRY_WINDOW}s），放弃重试 | plan={plan_id}",
            "WARN",
        )
        # * 2026-11-11: giving up on retries = this notification is already lost (the message will not be made up by itself later).
        #   Previously only the claim stayed in Redis while MySQL kept NULL -> the next cache rebuild treated it
        #   as "never notified" and ran the whole 180s retry cycle again. Persisting it pins down the fact "already abandoned",
        #   otherwise the same plan burns another round on every rebuild.
        _mark_plan_notified(
            plan_id, reason=f"gave up after {n} retries / {elapsed:.0f}s budget"
        )
    else:
        # Budget not exhausted -> release the notification right so the next round may retry
        _release_plan_notify(r, notify_key)


def _mark_plan_notified(plan_id: str, reason: str = "") -> bool:
    """Persist "already notified" into MySQL -- the deduplication source of truth (root fix, 2026-11-11).

    Why it must be persisted: idempotency used to rely entirely on the Redis key `agx:notified:plan_completed:{plan_id}`.
    It has already caused three full resends because "the key was lost":
      - 2026-10-22 cache_rebuild cleared all ao:*   -> 146 rows
      - 2026-10-25 install.py scan_iter cleared all  -> resend
      - 2026-11-11 the kernel rename ao:->agx: left the key in the old namespace -> 182 plans resent 123 rows
    Redis is lossy (rebuild/restart/eviction/rename); a MySQL row is not.

    Idempotent: `AND (notified_at IS NULL OR notified_at < COALESCE(completed_at, notified_at))`
    -- a rerun does not overwrite the first notification time (so "when was this plan notified" is always queryable),
    but a *new completion* of the same plan can write again: after completed_at is refreshed the old notified_at is earlier,
    the condition holds -> writes NOW(). If it still wrote `notified_at IS NULL`, a second completion could never be written
    while the skip predicate has already let go -> the state would stick at "not notified". This round could be saved by zrem after success,
    but one index rebuild (which has happened three times) sends it again -- worse than a missed send. The two places must change in pairs.
    (The earlier note "re-sends every 30s" is inaccurate: the ok path zrems it out of the scan source, so it does not send every round.)

    * 2026-11-11 third fix (Feng chose case B): terminal rows with `completed_at` NULL are normalised to "already notified".
    COALESCE(completed_at, notified_at) -> when NULL it degrades to `notified_at < notified_at` = FALSE
    -> no update. Strictly paired with the skip predicate's `notified_at >= notified_at` = TRUE.
    Direction: the predicate flips from "send only if it can be proved *new*" to "send only if it can be proved to be a *stale mark*";
    when it cannot be proved, do not send (better a missed send than a resend -- the same stance as the backfill guard).

    reason: the cause of a permanent failure (e.g. session not found). agx_plan currently has no INSTR/x_desc
    column, so the reason goes into the watchdog log plus a Redis diagnostic key (expires, carries no correctness);
    to make it queryable in the DB a notify_note column would be needed (listed as a pending item; the schema was not changed unilaterally).
"""
    try:
        _exec_mysql(
            "UPDATE agx_plan SET notified_at = NOW() "
            f"WHERE plan_id = '{_escape(plan_id)}' "
            "AND (notified_at IS NULL OR notified_at < COALESCE(completed_at, notified_at))"
        )
    except Exception as e:
        # A failed write must not roll back the send flow: the Redis key is still there and the next round decides again
        log(f"[计划监控] 写 notified_at 失败 | plan={plan_id}: {e}", "ERROR")
        return False
    if reason:
        try:
            _get_redis().setex(f"agx:notified_reason:{plan_id}", 7 * 86400, reason[:400])
        except Exception:
            pass
    return True


def _handle_plan_notify_outcome(r, plan_id: str, notify_key: str, outcome: str) -> None:
    """Persist one send result into the DB / Redis and decide whether to try again next round.

      ok        -> mark notified_at in the DB + clear the failure count + remove it from the monitoring index
      permanent -> same as above, plus record the reason (the session does not exist, retrying is pointless)
      retry     -> release the notification right inside the budget window, retry next round
"""
    if outcome == "ok":
        _mark_plan_notified(plan_id)
        _clear_plan_notify_markers(r, plan_id)
        return
    if outcome == "permanent":
        _mark_plan_notified(plan_id, reason="permanent: session not found at notify time")
        _clear_plan_notify_markers(r, plan_id)
        log(
            f"[计划监控] 永久失败已落库(notified_at)，不再重试 | plan={plan_id}",
            "WARN",
        )
        return
    _record_plan_notify_failure(r, notify_key, plan_id)


# -- Plan completion notification: backfill guard (2026-11-11) --------------------------
# The new logic uses agx_plan.notified_at as the single source of truth. If some database added the column but never ran the backfill script,
# existing completed plans are all NULL -> the new code treats them as "never notified" -> an immediate repeat of the storm.
# Better to send nothing than to resend 182 rows: if old plans (completed more than 3 days ago) exist while notified_at
# has not been written a single time, that is hard proof "the backfill never ran"; in that case suspend scanning and raise an error.
_PLAN_GUARD = {"ok": False, "checked_at": 0.0, "announced": False}
_PLAN_GUARD_RECHECK = 300  # Once suspended, probe again every 5 minutes (ops may run the backfill while the process is alive)


def _plan_notify_guard_ok() -> bool:
    """Returns False = the backfill has not run, scanning is forbidden (otherwise it is a resend storm)."""
    now = time.time()
    if _PLAN_GUARD["ok"]:
        return True
    if _PLAN_GUARD["checked_at"] and now - _PLAN_GUARD["checked_at"] < _PLAN_GUARD_RECHECK:
        return False
    _PLAN_GUARD["checked_at"] = now
    try:
        rows = _get_mysql_rows(
            "SELECT COUNT(*) AS total, "
            "SUM(notified_at IS NOT NULL) AS done, "
            "SUM(completed_at < NOW() - INTERVAL 3 DAY) AS old "
            "FROM agx_plan WHERE plan_status IN ('completed','failed')"
        )
    except Exception as e:
        # If the query fails, let it through: a single DB hiccup must not stop the whole plan monitor
        log(f"[计划监控] 回填守卫查询失败，放行本轮: {e}", "WARN")
        _PLAN_GUARD["ok"] = True
        return True
    row = rows[0] if rows else {}
    total = int(row.get("total") or 0)
    done = int(row.get("done") or 0)
    old = int(row.get("old") or 0)
    if old > 0 and done == 0:
        if not _PLAN_GUARD["announced"]:
            log(
                f"[计划监控] ！！已挂起：{old} 个完成超过 3 天的 plan 的 notified_at 全为 NULL → "
                "回填脚本没跑。现不扫描（否则会全量重发）。修法："
                ".venv/bin/python scripts/backfill_plan_notified_at.py --apply",
                "ERROR",
            )
            _PLAN_GUARD["announced"] = True
        return False
    _PLAN_GUARD["ok"] = True
    log(f"[计划监控] 回填守卫通过: completed/failed={total} 已标记={done} 超3天={old}", "INFO")
    return True


def _clear_plan_notify_markers(r, plan_id: str) -> None:
    """After a successful notification, clear the failure count / window start and remove the plan from the monitoring index.

    * 2026-11-11 added the zrem (root fix; see the same-day note in _scan_plan_completions):
      idempotency used to rely only on the single Redis key agx:notified:*. Once the key is lost (cache_rebuild rebuilding indexes,
      a redis restart, eviction, manual cleanup) the scan treats long-finished plans as newly completed and resends them.
      After a successful notification it is removed from agx:idx:plan:all -- a terminal plan should not stay in the monitoring set anyway,
      and the index is the scan source, so removing it writes "already notified" somewhere that cannot be lost.
      If the send failed this function is not called and the plan stays in the index to be retried as usual (retry semantics unchanged).
"""
    for k in (f"agx:notified_start:{plan_id}", f"agx:notified_fail:{plan_id}"):
        try:
            r.delete(k)
        except Exception:
            pass
    try:
        r.zrem("agx:idx:plan:all", plan_id)
    except Exception as e:
        log(f"[计划监控] 通知成功后移出索引失败 plan={plan_id}: {e}", "WARN")


def _scan_plan_completions():
    """
    Scan every plan and check whether it is entirely terminal.

    Traverse agx:idx:plan:all -> for each plan query agx:idx:execution:by_plan:{plan_id}
    -> all steps terminal -> find the execution whose activate_plan_id = plan_id
    -> send a plan_completed notification to that execution's session.

    An independent monitoring dimension, unrelated to timeouts/alerts/receipts.
    The watchdog closes out nothing on anyone's behalf; it only notifies (whoever activates is responsible to the end).
"""
    if not _plan_notify_guard_ok():
        return
    try:
        r = _get_redis()
        plan_ids = r.zrange("agx:idx:plan:all", 0, -1)
    except Exception as e:
        log(f"[计划监控] 获取 plan 列表失败: {e}", "ERROR")
        return

    # Monitor only the plans that "need a notification".
    # * FIX (2026-10-13): remove "never notifies / must not notify" plans at the source and zrem the index in step,
    #   rooting out the hazard of "terminal plans lingering in agx:idx:plan:all, propped up by notify_key":
    #     - draft     : not yet activated, never completes (the BUG-B2 source) -> drop + zrem
    #     - archived  : an archived terminal state, in the same "must not notify" class as cancelled -> drop + zrem
    #                   (evidence: a wave of 5 bogus sends at 16:36, all archived, held off only by notify_key)
    #   cancelled is kept (short-circuited by _is_plan_completed, so the existing behaviour surface does not change);
    #   completed/failed are kept (they need notifications, protected by notify_key).
    live_plan_ids = []
    stale_ids = []
    for _pid in plan_ids:
        try:
            _ps = r.hget(f"agx:entity:plan:{_pid}", "plan_status")
            _ph = (r.hget(f"agx:entity:plan:{_pid}", "host_id") or "").strip()
        except Exception:
            _ps, _ph = None, ""
        # * 2026-10-27: plan ownership isolation -- plans that are not this machine's are not monitored (an empty owner follows the old behaviour, allowed)
        if _ph and _ph not in HOST_IDS:
            continue
        if _ps in ("draft", "archived"):
            stale_ids.append(_pid)
            continue
        live_plan_ids.append(_pid)
    plan_ids = live_plan_ids

    # Remove stale plans (draft/archived) from the monitoring index, preventing pointless repeated scans and duplicate notifications
    if stale_ids:
        try:
            r.zrem("agx:idx:plan:all", *stale_ids)
            log(f"[计划监控] 剔除陈旧 plan {len(stale_ids)} 个(draft/archived) 出索引: {stale_ids[:5]}", "INFO")
        except Exception as _e:
            log(f"[计划监控] zrem 陈旧 plan 失败: {_e}", "WARN")

    if not plan_ids:
        return

    log(f"[计划监控] 开始扫描 | plan 总数={len(plan_ids)}", "DEBUG")

    for plan_id in plan_ids:
        try:
            # ** 2026-11-11 root fix: use MySQL notified_at to decide "does it still need a notification".
            #    Placed first -- it is more reliable than a Redis key and cheaper than _is_plan_completed.
            #    Incident history (three of the same kind): 10-22 cache_rebuild cleared ao:* -> 146 rows; 10-25
            #    install.py scan_iter cleared everything -> resend; 11-11 the ao:->agx: rename left the dedup key in the old
            #    namespace -> 182 completed plans resent 123 rows.
            #    The common factor is "the dedup state lived only in Redis": change the key name or clear the cache once and it is all gone.
            #
            # * 2026-11-11 second fix: the predicate semantics changed from "was ever notified" to "notified for *this completion*".
            #    A plan can go completed -> run again -> completed again (completed_at refreshed),
            #    and the old predicate `notified_at IS NOT NULL` would silently swallow the second completion as "already notified".
            #    Evidence plan_multi_3chapters_a12e1762: completed_at=11-11 10:10:12,
            #    notified_at=11-04 14:53:38 (from the round seven days earlier) -> zero notifications for this completion.
            #    So only "the notification time is not earlier than this completion time" is accepted.
            #
            # * 2026-11-11 third fix (Feng chose case B): terminal rows with completed_at IS NULL are normalised to "already notified".
            #    The SQL three-valued-logic trap: `notified_at >= NULL` = NULL (not FALSE),
            #    and in Python `if <value fetched from a NULL row>` also happens to be falsy, so "cannot prove" was treated as
            #    "proved new" -> it would send. Changed to COALESCE(completed_at, notified_at):
            #    when completed_at is NULL it degrades to `notified_at >= notified_at` = TRUE -> skip.
            #    The semantics are not a third state; rather the direction flips from "send only if it can be proved new" to "send only if it can be proved stale".
            #    The data side is untouched (plan_da628308fbf3 keeps completed_at=NULL and the code tolerates it).
            plan_info = _get_mysql_rows(
                "SELECT task_run_uuid, created_by, project_id, completed_at, notified_at, "
                "(notified_at IS NOT NULL AND notified_at >= "
                "COALESCE(completed_at, notified_at)) AS already_notified "
                f"FROM agx_plan WHERE plan_id='{_escape(plan_id)}'"
            )
            if plan_info and plan_info[0].get("already_notified"):
                log(
                    f"[计划监控] 本次完成已通知(notified_at={plan_info[0]['notified_at']} "
                    f">= completed_at={plan_info[0]['completed_at']})，跳过 | plan={plan_id}",
                    "DEBUG",
                )
                # Already notified = this matter is closed; a terminal plan should not keep occupying the monitoring index
                try:
                    r.zrem("agx:idx:plan:all", plan_id)
                except Exception:
                    pass
                continue

            # * 2026-10-26 DH-068: atomically claim the notification right (SET NX); if the claim fails, skip.
            #   The Redis lock is still kept -- but only as "mutual exclusion between dogs" plus a "fast path", no longer as the dedup basis.
            notify_key = f"agx:notified:plan_completed:{plan_id}"

            # Check whether the plan already terminated and get its terminal type (pure read, done first to avoid a pointless claim)
            is_terminal, final_status = _is_plan_completed(plan_id)
            if not is_terminal:
                continue

            plan_task_run_uuid = plan_info[0]["task_run_uuid"] if plan_info else ""
            plan_created_by = plan_info[0]["created_by"] if plan_info else "unknown"

            # * 2026-11-11 removed the stopgap "the activator execution is already closed -> skip".
            #   It was added during the 09:39 emergency (at that time only Redis was visible, so execution state was the only proxy),
            #   but it used the wrong signal: execution closed != notification sent. Measured: of 182 completed plans,
            #   167 had their activator closed long ago (161 succeeded / 6 failed), so this rule would swallow their
            #   legitimate notifications too, leaving no trace. Now that the DB has notified_at as the real predicate,
            #   it has turned from a "stopgap" into a "new source of false negatives", so it is deleted.

            # Claim only when terminal: avoids wasting the key on an unfinished plan
            if not _claim_plan_notify(r, notify_key):
                log(f"[计划监控] plan={plan_id} 通知权已被其他看门狗抢到，跳过", "DEBUG")
                continue

            log(f"[计划监控] plan 全部终止 | plan={plan_id} | status={final_status} | 已抢到通知权", "INFO")

            # Choose the notification message by terminal type
            if final_status == "failed":
                msg_type = "plan_failed"
            else:
                msg_type = "plan_completed"

            # Path 1: find the activator through task_run_uuid (written by agx_activate_plan)
            if plan_task_run_uuid:
                activator_rows = _get_mysql_rows(f"SELECT task_run_uuid, agent_id, session_id FROM agx_task_execution WHERE task_run_uuid='{_escape(plan_task_run_uuid)}'")
                if activator_rows:
                    row = activator_rows[0]
                    run_uuid = row["task_run_uuid"]
                    session_id = row["session_id"]
                    if not session_id:
                        log(
                            f"[计划监控] session_id 为空 | activator={run_uuid} | plan={plan_id}",
                            "WARN",
                        )
                        # Undeliverable != not yet delivered. Pin it in the DB, otherwise the next cache rebuild treats it as newly completed and retries.
                        _handle_plan_notify_outcome(r, plan_id, notify_key, "permanent")
                        continue

                    message = json.dumps(
                        {"type": msg_type, "plan_id": plan_id},
                        ensure_ascii=False,
                    )

                    log(
                        f"[计划监控] 通知激活方 | activator={run_uuid} | plan={plan_id} | session={session_id}",
                        "INFO",
                    )

                    if not TEST_MODE:
                        _handle_plan_notify_outcome(
                            r, plan_id, notify_key,
                            _send_plan_completed(session_id, message, f"{run_uuid}|{plan_id}"),
                        )
                    else:
                        log(f"[计划监控] [TEST] plan 已完成 | activator={run_uuid} | plan={plan_id}", "INFO")
                        _release_plan_notify(r, notify_key)
                else:
                    log(
                        f"[计划监控] task_run_uuid 指向的 execution 不存在 | plan={plan_id} | task_run_uuid={plan_task_run_uuid}",
                        "WARN",
                    )
                    # Same as above: a missing target is a permanent fact; persist it so the next index rebuild does not come back for it.
                    _handle_plan_notify_outcome(r, plan_id, notify_key, "permanent")
            else:
                # Path 2: no task_run_uuid -> send to the creator's default main session
                session_id = f"agent:{plan_created_by}:main"
                message = json.dumps(
                    {"type": msg_type, "plan_id": plan_id},
                    ensure_ascii=False,
                )

                log(
                    f"[计划监控] 通知创建者(默认通道) | plan={plan_id} | creator={plan_created_by} | session={session_id}",
                    "INFO",
                )

                if not TEST_MODE:
                    _handle_plan_notify_outcome(
                        r, plan_id, notify_key,
                        _send_plan_completed(session_id, message, f"{plan_id}|{plan_created_by}"),
                    )
                else:
                    log(f"[计划监控] [TEST] plan 已完成 | plan={plan_id} | session={session_id}", "INFO")
                    _release_plan_notify(r, notify_key)

        except Exception as e:
            log(f"[计划监控] 处理异常 | plan={plan_id}: {e}", "ERROR")


def _scan_replies():
    """Scan every finished execution that needs a reply and send the receipt."""
    executions = _get_completed_need_reply()
    if not executions:
        return
    log(f"回执扫描 | 待回复={len(executions)}")
    for exc in executions:
        try:
            _reply_to_sender(exc)
        except Exception as e:
            # * 2026-10-30: if it cannot be sent, roll the claim back (2->1), otherwise needs_reply sticks at 2,
            #   reply_sent_at stays empty, and it is neither resent nor noticed -- measured on the 188 node at 00:03:32.
            run_uuid = exc.get("task_run_uuid", "")
            log(f"回执处理异常 {run_uuid or '?'}: {e}", "ERROR")
            if run_uuid:
                try:
                    aff = _exec_mysql(
                        "UPDATE agx_task_execution SET needs_reply = 1 "
                        f"WHERE task_run_uuid = '{_escape(run_uuid)}' "
                        "AND needs_reply = 2 AND reply_sent_at IS NULL"
                    )
                    if aff:
                        _refresh_execution_cache(run_uuid)
                        log(f" 回执抢占已释放 (needs_reply 2→1) | {run_uuid}", "WARN")
                except Exception as e2:
                    log(f" 回执抢占释放失败 | {run_uuid}: {e2}", "WARN")


# -- Receipt redelivery ------------------------------------------------
# * Feng, 2026-10-29: a receipt is a message (carried by the event table), and the core of a message is "being read".
#   A failed delivery must be redelivered; it cannot sit at running forever the way an execution does.
# * Feng, 2026-11-02: default 600 (the old hard-coded 300 was too short for agents doing long work --
#   the receiver has to read the whole execution and independently re-check before acknowledging, and 5 minutes was often not enough, becoming a false nudge).
#   Now read from config.daemon.receipt_resend_after, tunable on the deployment side without a code change.
RECEIPT_RESEND_AFTER = int(config.daemon.get("receipt_resend_after", 600))
RECEIPT_RESEND_MAX = int(config.daemon.get("receipt_resend_max", 2))  # Resend count; 3 in total including the first (Feng, 2026-10-29: stop after three failures)
RECEIPT_RESEND_INTERVAL = 300   # Base interval between two redeliveries (seconds; the n-th interval = n x this value)
RECEIPT_RESEND_WINDOW = 86400   # Only look at receipts within 24h (anything older is not worth redelivering)
# * 2026-10-30: index reconciliation period (in ticks). POLL_INTERVAL=30s -> every 10 ticks is about 5 minutes
#   of a full table scan as a backstop, adding what the write side missed and removing what should not be there.
#   Why this step: one missing index entry = the task that should be nudged is never nudged, and nothing reports an error.
#   A silent missed nudge is far worse than a slow scan, so paying 5ms every 5 minutes is the right trade.
RECEIPT_IDX_RECONCILE_TICKS = 10
_RECEIPT_IDX_TICKS = {"n": 0}
# * 2026-10-30: the index "already built" marker.
#   Pitfall: when a zset has no members Redis does not create the key at all (exists=0) --
#   so if "key does not exist" were used to mean "never built", an empty index would be judged unbuilt and rebuilt every round,
#   turning into an endless spam loop plus a full table scan per round. Use a separate marker meaning "built, just currently empty".
#   The marker lives and dies with the index: agx_cache_clear clears both, and the semantics stay correct.
RECEIPT_IDX_BUILT_KEY = "agx:meta:execution:receipt_idx:built"


def _receipt_candidates_from_mysql(my_agents, limit: int = 20) -> list:
    """Backstop: when the index is unavailable (a Redis hiccup), query MySQL directly with the original criterion.

    Exactly the same filter conditions as the index read path; the only difference is the data source.
"""
    agent_list = ",".join(f"'{_escape(a)}'" for a in sorted(my_agents))
    try:
        return _get_mysql_rows(
            "SELECT task_run_uuid, send_agent_id, send_session_id, agent_id, task_id, "
            "run_status, COALESCE(reply_read,0) AS reply_read, ack_state, reply_sent_at, finished_at "
            "FROM agx_task_execution "
            "WHERE run_status IN ('succeeded','failed') AND needs_reply IN (1,2) "
            "AND COALESCE(reply_read,0) = 0 "
            f"AND agent_id IN ({agent_list}) "
            f"AND COALESCE(reply_sent_at, finished_at) < DATE_SUB(NOW(), INTERVAL {RECEIPT_RESEND_AFTER} SECOND) "
            f"AND COALESCE(reply_sent_at, finished_at) > DATE_SUB(NOW(), INTERVAL {RECEIPT_RESEND_WINDOW} SECOND) "
            f"ORDER BY COALESCE(reply_sent_at, finished_at) LIMIT {int(limit)}"
        )
    except Exception as e:
        log(f" 回执重发扫描失败(MySQL 兜底): {e}", "WARN")
        return []


def _reconcile_receipt_index():
    """Index reconciliation: MySQL is the truth, the Redis zset is a derived view.

    Add what is missing, remove what should not be there. Without this step any missed maintenance on the write side becomes a "silent missed nudge" --
    a task that should be nudged is not, with not even a peep in the log. Paying for one full table scan periodically is the better trade.
"""
    try:
        rows = _get_mysql_rows(
            "SELECT task_run_uuid, run_status, needs_reply, reply_read, reply_sent_at, finished_at "
            "FROM agx_task_execution "
            "WHERE run_status IN ('succeeded','failed') AND needs_reply IN (1,2) "
            "AND COALESCE(reply_read,0) = 0 "
            f"AND COALESCE(reply_sent_at, finished_at) > DATE_SUB(NOW(), INTERVAL {RECEIPT_RESEND_WINDOW} SECOND)"
        )
        want = {
            r["task_run_uuid"]: _receipt_pending_score(r.get("reply_sent_at"), r.get("finished_at"))
            for r in rows
            if r.get("task_run_uuid")
        }
        r = _get_redis()
        have = set(r.zrange(RECEIPT_PENDING_IDX, 0, -1))
        pipe = r.pipeline()
        added = removed = 0
        for rid, sc in want.items():
            if rid not in have:
                pipe.zadd(RECEIPT_PENDING_IDX, {rid: sc})
                added += 1
        for rid in have - set(want):
            pipe.zrem(RECEIPT_PENDING_IDX, rid)
            removed += 1
        pipe.execute()
        if added or removed:
            log(
                f" 回执索引对账 | 补 {added} / 清 {removed} | 应为 {len(want)} 条",
                "WARN",
            )
        # Write the marker regardless of whether there was any difference: an empty index is not an unbuilt index
        r.set(RECEIPT_IDX_BUILT_KEY, int(time.time()))
    except Exception as e:
        log(f" 回执索引对账失败: {e}", "WARN")


def _receipt_pending_candidates(my_agents, limit: int = 20) -> list:
    """Take the "receipt awaiting acknowledgement" candidates from the Redis index (oldest first).

    * Feng, 2026-10-30: the hot path reads Redis, with MySQL only for backstop and reconciliation.
      The index score = COALESCE(reply_sent_at, finished_at), so ZRANGEBYSCORE naturally orders by
      "who has waited longest", consistent with the ORDER BY semantics of the original SQL.

    Three lines of defence (missing any one turns into a silent missed nudge or a false nudge):
      (1) the index does not exist (first deployment / cleared by agx_cache_clear) -> reconcile and rebuild immediately, then read;
      (2) re-check each entry against the real hash content, and ZREM on the spot when it does not match (the index heals itself when dirty);
      (3) the hash is missing -> go back to MySQL for the whole row and repair both the cache and the index.
"""
    now = time.time()
    amin = now - RECEIPT_RESEND_WINDOW
    amax = now - RECEIPT_RESEND_AFTER
    num = max(limit * 3, 60)

    # (1) Periodic reconciliation (the first tick runs it too = cold-start index build)
    _RECEIPT_IDX_TICKS["n"] += 1
    if _RECEIPT_IDX_TICKS["n"] % RECEIPT_IDX_RECONCILE_TICKS == 1:
        _reconcile_receipt_index()

    try:
        r = _get_redis()
        ids = r.zrangebyscore(RECEIPT_PENDING_IDX, amin, amax, start=0, num=num)
        # Decide by the "already built" marker, not by whether the key exists -- Redis has no key at all for an empty index
        if not ids and not r.exists(RECEIPT_IDX_BUILT_KEY):
            log(" 回执待签索引未建（首次部署或被缓存清理）→ 立即从 MySQL 重建", "WARN")
            _reconcile_receipt_index()
            ids = r.zrangebyscore(RECEIPT_PENDING_IDX, amin, amax, start=0, num=num)
    except Exception as e:
        log(f" 回执索引不可读，回源 MySQL: {e}", "WARN")
        return _receipt_candidates_from_mysql(my_agents, limit)

    if not ids:
        return []

    out = []
    for rid in ids:
        try:
            h = r.hgetall(f"agx:entity:execution:{rid}")
        except Exception:
            h = {}
        if not h:
            # (3) Cache missing -> read the whole row back and fix both the cache and the index on the way
            rows = _get_mysql_rows(
                f"SELECT * FROM agx_task_execution WHERE task_run_uuid='{_escape(rid)}'"
            )
            if not rows:
                r.zrem(RECEIPT_PENDING_IDX, rid)
                continue
            _refresh_execution_cache(rid)
            h = {k: str(v) for k, v in rows[0].items() if v is not None}
        # (2) Second safety net: re-check by real content (the index may be dirty)
        if not _receipt_pending_wanted(
            h.get("run_status"), h.get("needs_reply"), h.get("reply_read")
        ):
            r.zrem(RECEIPT_PENDING_IDX, rid)
            continue
        # Handle only agents under this machine (the agent_id IN (...) condition of the original SQL)
        if (h.get("agent_id") or "") not in my_agents:
            continue
        out.append(h)
        if len(out) >= limit:
            break
    return out


def _first_receipt_sent_at(run_uuid: str) -> str:
    """The creation time of the *earliest* receipt event for this task -- original_sent_at in the resend envelope.

    Must not take exc["reply_sent_at"]: it is overwritten to the latest by _mark_receipt_sent on every resend,
    so it would always be "the time of the previous resend", not the first send.
"""
    try:
        rows = _get_mysql_rows(
            "SELECT created_at FROM agx_task_execution_event "
            f"WHERE task_run_uuid='{_escape(run_uuid)}' AND event_type='receipt' "
            "ORDER BY created_at ASC, event_id ASC LIMIT 1"
        )
        if not rows:
            return ""
        v = rows[0].get("created_at")
        return v.strftime("%Y-%m-%d %H:%M:%S") if hasattr(v, "strftime") else str(v or "")
    except Exception:
        return ""


def _resend_unacked_receipts():
    """Receipt resend: for a task that has finished but whose initiator *has not acknowledged*, send once more (limit: RECEIPT_RESEND_MAX).

    * Feng, 2026-10-29: the resend criterion is *whether it was acknowledged*, not *whether it was delivered*.
        A successful WS delivery != it was read -- out of credits, offline, unable to reason, WS still accepts it and it still cannot acknowledge.
        The old implementation only retried delivery_state IN (pending,sent,failed); once delivered it never looked again,
        i.e. "delivered = done", so an unacknowledged message was never chased. That is one of the roots of "the system keeps sending, the agent stays clueless".
        Now it looks only at reply_read, not delivery_state.

      On every send: add one event row + overwrite the task's reply_sent_at with the latest time (the first is not kept).
      At the limit with still no acknowledgement -> stop sending, ack_state='unacked' (for a human).
      **Never change run_status**: "the other side finished but did not read" and "the other side never did it" are two different things.
        The former is looked up via reply_read, the latter via failed/alerts; the search criteria stay separate.

    Three dogs may scan the same row at once: a Redis set(nx) lock means only one can send.
"""
    my_agents = _get_my_agent_ids()
    if not my_agents:
        return
    # * 2026-10-30: candidates come from the Redis index (the hot path), with MySQL demoted to backstop plus reconciliation.
    #   Original criterion note (semantics preserved): reply_sent_at is not required to be non-empty -- otherwise rows where the claim succeeded but the send
    #   errored (needs_reply=2, reply_sent_at=NULL) could never be found; evidence on the 188 node,
    #   run_general_eb5e3fe9ea78. The index side does the equivalent: a missing score falls back to the current time.
    rows = _receipt_pending_candidates(my_agents, limit=20)
    if not rows:
        return

    r = _get_redis()
    for exc in rows:
        run_uuid = (exc.get("task_run_uuid") or "").strip()
        to_agent = (exc.get("send_agent_id") or "").strip()
        to_session = (exc.get("send_session_id") or "").strip()
        done_by = (exc.get("agent_id") or "").strip()
        if not run_uuid or not to_agent or not to_session:
            continue

        ckey = f"agx:receipt:resend:{run_uuid}"
        try:
            n = int(r.get(ckey) or 0)
        except Exception:
            n = 0

        if n >= RECEIPT_RESEND_MAX:
            # Sent the maximum and still unacknowledged -> stop sending, mark for a human (only ack_state is touched, never run_status)
            try:
                aff = _exec_mysql(
                    "UPDATE agx_task_execution SET ack_state='unacked' "
                    f"WHERE task_run_uuid='{_escape(run_uuid)}' "
                    "AND COALESCE(reply_read,0)=0 AND (ack_state IS NULL OR ack_state<>'unacked')"
                )
                if aff:
                    _refresh_execution_cache(run_uuid)
                    log(
                        f" 回执未签收已发满 {n} 次,停发待人工 | {run_uuid} → {to_agent}",
                        "WARN",
                    )
            except Exception:
                pass
            continue

        akey = f"agx:receipt:resend:at:{run_uuid}"
        try:
            last_at = float(r.get(akey) or 0)
        except Exception:
            last_at = 0
        if last_at and (time.time() - last_at) < RECEIPT_RESEND_INTERVAL * (n + 1):
            continue

        try:
            if not r.set(f"agx:receipt:resend:lock:{run_uuid}", "1", nx=True, ex=60):
                continue
        except Exception:
            continue

        try:
            # * 2026-11-16: go through agx_proto uniformly (a resend must be recognisable at a glance)
            receipt_struct = build_struct(
                run_uuid,
                done_by,
                to_session,
                msg_type="receipt",
                resend_attempt=n + 1,
                original_sent_at=_first_receipt_sent_at(run_uuid),
            )
            event_id = _ao_record_event(
                task_run_uuid=run_uuid,
                event_type="receipt",
                level=0,
                from_agent=done_by,
                to_agent=to_agent,
                to_session=to_session,
                channel="queue",
                reason=f"任务回执重发 status={exc.get('run_status')}",
                payload={
                    "reply_type": "task_completion_resend",
                    "original_run_uuid": run_uuid,
                    "original_task_id": exc.get("task_id", ""),
                    "completed_by": done_by,
                    "status": exc.get("run_status", ""),
                    "attempt": n + 1,
                },
                delivery_state="pending",
            )
            delivery_key = "run_receipt_" + event_id[len("evt_"):]
            res = _ao_send_notice(
                to_agent,
                to_session,
                json.dumps(receipt_struct, ensure_ascii=False),
                event_id=event_id,
                delivery_key=delivery_key,
                msg_type="receipt",
                send_agent=done_by,
            )
            if res.get("ok"):
                r.incr(ckey)
                r.expire(ckey, 86400)
                r.set(akey, time.time(), ex=86400)
                _mark_receipt_sent(run_uuid)
                _ao_mark_event_delivery(event_id, "sent")
                log(f" 回执重发 ({n + 1}/{RECEIPT_RESEND_MAX}) | {run_uuid} → {to_agent}")
            else:
                _ao_mark_event_delivery(event_id, "failed")
                log(f" 回执重发入队失败 | {run_uuid}: {res.get('error')}", "WARN")
        except Exception as e:
            log(f" 回执重发异常 | {run_uuid}: {e}", "WARN")


# -- Human confirmation scanning ----------------------------------------------


def _get_succeeded_need_confirm() -> list:
    """Get (from MySQL) the executions *under this machine's agents* that are succeeded and have needs_confirm=1 (fallback migration).

    * 2026-11-06 responsibility change: the state transition is now done directly by agx_finish_task
      (on success with needs_confirm=1 it lands awaiting_confirm). This function is only a *backstop*: it scans leftover
      succeeded+needs_confirm=1 rows (from older versions / concurrent-write residue) and migrates them once. Normally it finds no rows at all.

    * 2026-10-26 DH-069: the original implementation scanned the whole table, and all three dogs migrated the same task to
      pending_confirm (idempotent, harmless), but each printed its own "needs human confirmation" -> the same task was printed three times
      across three machines' consoles, so a human could not tell how many items really awaited confirmation.
      Now filtered by agent ownership, the same criterion as _get_completed_need_reply.
"""
    my_agents = _get_my_agent_ids()
    if not my_agents:
        log(f" 人类确认扫描跳过：本机 agent 名单为空 (host_id={HOST_IDS_LABEL or '?'})", "WARN")
        return []
    agent_list = ",".join(f"'{_escape(a)}'" for a in sorted(my_agents))
    return _get_mysql_rows(
        "SELECT task_run_uuid, agent_id, project_id, host_id, exec_instruction, summary "
        "FROM agx_task_execution "
        "WHERE run_status = 'succeeded' AND needs_confirm = 1 "
        f"AND agent_id IN ({agent_list})"
    )


def _claim_legacy_migration(run_uuid: str) -> bool:
    """Atomically claim "the pending-confirmation fallback migration right" (leftover rows only: succeeded+needs_confirm=1 -> awaiting_confirm).

    * 2026-10-26 DH-069: even with host filtering, one machine may run several watchdog processes
      (restart overlap, started twice by hand). Only CAS guarantees "one notification printed per task".
      It also prevents an overwrite where dog A already migrated and dog B migrates again from a stale read.
"""
    n = _exec_mysql(
        f"UPDATE agx_task_execution SET run_status='awaiting_confirm' "
        f"WHERE task_run_uuid='{_escape(run_uuid)}' AND run_status='succeeded' AND needs_confirm=1"
    )
    if n != 1:
        log(f"待确认迁移未抢到（已处理）| {run_uuid} | affected={n}", "DEBUG")
        return False
    _refresh_execution_cache(run_uuid)
    _sync_execution_status_index(run_uuid, "awaiting_confirm")
    return True


def _migrate_legacy_confirms():
    """Backstop: leftover succeeded+needs_confirm=1 -> awaiting_confirm.

    * 2026-11-06 responsibility change: the state transition is now done directly by agx_finish_task.
      This function only serves "old-version writes / concurrency residue"; normal runs find no rows at all.
"""
    executions = _get_succeeded_need_confirm()
    if not executions:
        return
    log(f"人类确认兜底迁移 | 存量={len(executions)}")
    for exc in executions:
        _uid = exc["task_run_uuid"]
        if _claim_legacy_migration(_uid):
            log(f"legacy confirm migrated: {_uid} agent={exc.get('agent_id', '')}")


def _get_awaiting_confirms() -> list:
    """Get (from MySQL) the awaiting_confirm tasks *under this machine's agents* (delivered, waiting for a human to release)."""
    my_agents = _get_my_agent_ids()
    if not my_agents:
        log(f" 人类放行扫描跳过：本机 agent 名单为空 (host_id={HOST_IDS_LABEL or '?'})", "WARN")
        return []
    agent_list = ",".join(f"'{_escape(a)}'" for a in sorted(my_agents))
    return _get_mysql_rows(
        "SELECT task_run_uuid, agent_id, project_id, host_id, exec_instruction, summary "
        "FROM agx_task_execution "
        "WHERE run_status = 'awaiting_confirm' "
        f"AND agent_id IN ({agent_list})"
    )


def _claim_confirm_notice(run_uuid: str) -> bool:
    """Redis SET NX to claim "the notification right": one notification per task.

    * Why not use MySQL for deduplication: a notification is not a state, and a duplicate only spams;
      while Redis SET NX is atomic and naturally blocks "two watchdogs scanning during a restart overlap at the same time".
      TTL 7 days; the cost of losing it on a Redis restart is one duplicate notification, never a wrong state.
"""
    try:
        r = _get_redis()
        return bool(r.set(f"agx:confirm_notified:{run_uuid}", "1", nx=True, ex=7 * 86400))
    except Exception as e:
        log(f"confirm notice claim failed（不阻断报信）: {e}", "WARN")
        return True


def _scan_awaiting_confirms():
    """The task has been delivered and awaits a human -> notify (**no state migration, no nudging the executor**).

    * Feng, 2026-11-06: the state is set to awaiting_confirm by agx_finish_task;
      the watchdog only "tells the human to look". The old design (scan succeeded then migrate) is abolished,
      keeping only _migrate_legacy_confirms as a leftover backstop.
"""
    _migrate_legacy_confirms()
    executions = _get_awaiting_confirms()
    if not executions:
        return
    log(f"人类放行扫描 | 待确认={len(executions)}")
    for exc in executions:
        run_uuid = exc["task_run_uuid"]
        if not _claim_confirm_notice(run_uuid):
            continue
        instruction = exc.get("exec_instruction", "") or ""
        summary = exc.get("summary", "") or ""
        agent_id = exc.get("agent_id", "") or ""
        project_id = exc.get("project_id", "") or ""

        print("\n" + "=" * 60)
        print("🔔 任务已交付，需要人类放行")
        print(f"   run_uuid   : {run_uuid}")
        print(f"   agent      : {agent_id}")
        print(f"   project    : {project_id}")
        print(f"   指令       : {instruction[:200]}")
        print(f"   结果摘要   : {summary[:200]}")
        print(f"   放行: agx_confirm_task(task_run_uuid='{run_uuid}', action='approve')")
        print(f"   驳回: agx_confirm_task(task_run_uuid='{run_uuid}', action='reject')")
        print("=" * 60 + "\n")

        try:
            _ao_record_event(
                task_run_uuid=run_uuid,
                event_type="confirm_request",
                level=2,
                from_agent="agx_watchdog",
                to_agent=agent_id,
                to_session="",
                channel="",
                reason="任务已交付，等待人类放行",
                payload={"summary": summary[:500]},
                delivery_state="pending",
            )
        except Exception as e:
            log(f"confirm_request event write failed: {e}", "WARN")

        log(f"awaiting_confirm 报信: {run_uuid} agent={agent_id}")


# -- Main loop ----------------------------------------------------



            # -- Host keepalive (2026-10-25 multi-host rework) -------------------

def _scan_host_presence():
    """Scan host liveness network-wide; on loss alert agx_core (with a distributed lock to prevent a storm).

    * Design points (agreed with Feng 2026-10-25):
      Host keepalive is a *public duty* -- every dog watches the whole network, because
      when the core machine dies only a dog on another host can discover it (nobody can report their own death).
      But that inevitably means "several dogs discover the same lost host at once".

    * Storm prevention (critical):
      Use the Redis distributed lock agx:lock:host_alert:{host_id} (SET NX EX) as the
      claim. Only the dog that claims it sends the alert; the rest stay silent.
      agx:lock: is inside the protected whitelist in both install.py and MCP, so it is never cleared.

    * What happens to the lost host's tasks (Feng chose a):
      Nothing. A machine going missing != the task is dead; it may just be a network blip.
      Letting it come back and close out itself is safer than a false kill.
      Only after HOST_ORPHAN_ESCALATE (default 1 hour) is it escalated to a human.
"""
    if not HOST_ID:
        return

    try:
        rows = _get_mysql_rows(
            "SELECT host_id, host_alias, presence_status, "
            "UNIX_TIMESTAMP(last_heartbeat) AS hb_ts "
            "FROM agx_host_inventory WHERE active_status=1"
        )
    except Exception as e:
        log(f"[主机保活] 查主机列表失败: {e}", "WARN")
        return

    if not rows:
        return

    now_ts = time.time()
    offline = []
    for row in rows:
        hid = (row.get("host_id") or "").strip()
        if not hid:
            continue
        hb_ts = row.get("hb_ts")
        # last_heartbeat is NULL -> that host never reported a heartbeat (old data / not upgraded)
        #   Do not judge it lost, avoiding a network-wide false alarm the moment the upgrade lands.
        if hb_ts in (None, "", 0, "0"):
            continue
        try:
            idle = now_ts - float(hb_ts)
        except (TypeError, ValueError):
            continue
        if idle > HOST_HEARTBEAT_TIMEOUT:
            offline.append((hid, row.get("host_alias") or hid, int(idle)))

    if not offline:
        return

    try:
        r = _get_redis()
    except Exception as e:
        log(f"[主机保活] 连 Redis 失败: {e}", "WARN")
        return

    for hid, alias, idle in offline:
        lock_key = f"agx:lock:host_alert:{hid}"
        try:
            got_lock = r.set(lock_key, HOST_ID, nx=True, ex=HOST_ALERT_COOLDOWN)
        except Exception as e:
            log(f"[主机保活] 抢锁失败 {hid}: {e}", "WARN")
            continue

        if not got_lock:
            # Another dog is already handling it, silent
            log(f"[主机保活] {hid} 失联 {idle}s 但已被其它狗接管，静默", "DEBUG")
            continue

        # Claimed the lock -> this dog owns the alert
        escalate = idle > HOST_ORPHAN_ESCALATE
        # Use % formatting rather than nested f-strings: nested f-strings need 3.12+,
        #   while 118/188 run 3.14/3.12 and this machine runs 3.13 -- go through the compatible form uniformly.
        _over = ("；已超 %ds，需人工介入" % HOST_ORPHAN_ESCALATE) if escalate else ""
        reason = (
            f"主机失联（{idle}s 无心跳，阈值 {HOST_HEARTBEAT_TIMEOUT}s）"
            f"{_over}"
        )
        log(
            f"[主机保活] 发现失联 | host={hid} ({alias}) | idle={idle}s | "
            f"发现者={HOST_ID} | 已抢锁 | escalate={escalate}",
            "CRITICAL" if escalate else "WARN",
        )

        _dispatch_host_alert(hid, alias, idle, reason, escalate)

        if escalate:
            try:
                escalate_to_human(
                    run_uuid=f"HOST_OFFLINE:{hid}",
                    reason=reason,
                    state={
                        "alert_count": 1,
                        "first_alert_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "last_alert_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
            except Exception as e:
                log(f"[主机保活] 升级人类失败: {e}", "WARN")


def _dispatch_host_alert(host_id: str, alias: str, idle: int, reason: str, escalate: bool):
    """Host-loss alert -> **write the event only, deliver no message** (and create no execution).

    * Feng, 2026-10-29: receipts/wakes/alerts uniformly create no execution; they all land in
      agx_task_execution_event plus send_text. The old implementation went through _ao_dispatch_task ->
      every lost host spawned an extra running execution out of thin air (hanging forever if nobody closed it out),
      and it wrote no event at all (so "which machine was lost when" was unqueryable afterwards).

    * Feng, 2026-11-09: **stop delivering messages too; persist only.**
      Reason: host loss is an infrastructure event and needs no LLM involvement -- delivering one to
      agx_core every 10 minutes made core run a pointless round. Measured: all 2917 alerts were
      delivered to core, and this topic alone burned those tokens.
      Query the table when needed: agx_task_execution_event WHERE task_run_uuid='HOST_OFFLINE:<host_id>'.
      Irreversible point of the change: this function no longer calls _ao_send_notice, so the
      "delivery failed -> release the Redis lock -> retry next round" branch no longer exists either.
"""
    # Aggregate key: one timeline per host (occupies no agx_task_execution row)
    run_uuid = f"HOST_OFFLINE:{host_id}"
    payload = {
        "alert_type": "host_offline",
        "target_host_id": host_id,
        "target_host_alias": alias,
        "offline_seconds": idle,
        "reason": reason,
        "detected_by": HOST_ID,
        "escalate": bool(escalate),
    }

    if TEST_MODE:
        try:
            r = _get_redis()
            today = datetime.now().strftime("%Y-%m-%d")
            r.lpush(
                f"{TEST_ALERT_LIST}:{today}",
                json.dumps(
                    {
                        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "type": "host_alert",
                        "host_id": host_id,
                        "detected_by": HOST_ID,
                        "instruction": json.dumps(payload, ensure_ascii=False)[:200],
                    },
                    ensure_ascii=False,
                ),
            )
            r.expire(f"{TEST_ALERT_LIST}:{today}", TEST_TTL)
        except Exception as e:
            log(f"[主机保活] [TEST] 记录失败: {e}", "WARN")
        return

    event_id = _ao_record_event(
        task_run_uuid=run_uuid,
        event_type="alert",
        level=3 if escalate else 2,
        from_agent="agx_core",
        to_agent="agx_core",
        to_session="agent:agx_core:main",
        channel="event",
        reason=reason,
        payload=payload,
        delivery_state="logged",
    )

    # Persist only, do not deliver; query the event timeline by HOST_OFFLINE:<host_id> when needed.
    log(
        f"[主机保活] 告警已落库(不投递) | host={host_id} ({alias}) | "
        f"idle={idle}s | escalate={escalate} | {event_id}",
        "CRITICAL" if escalate else "WARN",
    )

def run_watchdog():
    log(
        f"看门狗启动 | poll={POLL_INTERVAL}s | crisis_threshold={SYSTEM_CRISIS_THRESHOLD} | crisis_grace={CRISIS_GRACE}s | crisis_max_retries={CRISIS_MAX_RETRIES} | fallback_timeout={FALLBACK_TIMEOUT}s | test_mode={TEST_MODE}"
    )

    parent_pid = os.getppid()
    log(f"父进程 PID={parent_pid}")

    # Heartbeat file path (passed in by the daemon, cross-platform compatible)
    import tempfile

    # The heartbeat path is provided uniformly by _heartbeat_file_path() (same convention as the daemon)

    while True:
        try:
            # -- Write the heartbeat file (the daemon uses mtime to detect a hung watchdog) --
            _touch_heartbeat()

            executions = get_running_executions()

            _touch_heartbeat()

            # Phase 1: detect all anomalies (pure detection, no dispatch)
            detected = []
            for exc in executions:
                reason = _detect_anomaly(exc)
                if reason:
                    detected.append((exc, reason))

            log(f"扫描完成 | running={len(executions)} | 异常={len(detected)}")

            # Phase 2: decision and dispatch
            #   - crisis threshold reached -> escalate_to_human, skipping individual alerts
            #   - below the threshold -> _handle_anomaly one by one (state machine + individual alert)
            _touch_heartbeat()

            # Phase 2: decision and dispatch
            #   * Refresh the heartbeat after each anomaly: a single _handle_anomaly may go through
            #     _check_session_done_wake -> _query_session_status (660s worst case),
            #     and 3 anomalies already exceed the 1800s heartbeat threshold.
            if detected and not check_system_crisis(detected):
                for exc, reason in detected:
                    _handle_anomaly(exc, reason)
                    _touch_heartbeat()

            # -- Plan completion monitoring: scan all plans -> all terminal -> notify the activator when there is an activate_plan_id --
            _scan_plan_completions()
            _touch_heartbeat()

            # -- Plan scheduling: scan pending executions and activate ready tasks --
            _scan_pending_executions()
            _touch_heartbeat()

            # -- Receipt scanning: completed + needs_reply=1 -> reply automatically --
            _scan_replies()
            _touch_heartbeat()

            # -- Receipt redelivery: still no delivery confirmation after sending -> resend (a receipt is a message and must arrive) --
            _resend_unacked_receipts()
            _touch_heartbeat()

            # -- Human confirmation: awaiting_confirm -> notify (the state transition is already done by agx_finish_task) --
            _scan_awaiting_confirms()
            _touch_heartbeat()

            # -- Host keepalive: network-wide heartbeat sweep, alert on loss (distributed lock prevents a storm) --
            _scan_host_presence()
            _touch_heartbeat()

# 2026-05-25 11:42:14 monitoring mode changed
            # time.sleep(POLL_INTERVAL)
            # --- Polling plus the Redis wake signal ---
            # Check every second: parent process alive plus the Redis wake key (written by agx_finish_task)
            for _ in range(POLL_INTERVAL):
                time.sleep(1)
                # When a task completes, MCP writes agx:watchdog:wake; detected -> wake the scan immediately
                try:
                    r = _get_redis()
                    if r.get("agx:watchdog:wake"):
                        r.delete("agx:watchdog:wake")
                        break
                except Exception:
                    pass
                if os.getppid() == 1:
                    log("检测到父进程已死（变为孤儿进程），看门狗立即自杀退出", "WARN")
                    return

            # # Parent process already gone -> end itself (prevents becoming an orphan process)
            # if os.getppid() == 1:
            #     log("the parent process no longer exists (adopted by init); the watchdog exits on its own")
            #     break

        except SystemExit:
            log("看门狗退出")
            break
        except KeyboardInterrupt:
            log("看门狗收到 Ctrl+C")
            break
        except Exception as e:
            log(f"主循环异常: {e}", "ERROR")
            traceback.print_exc()
            time.sleep(POLL_INTERVAL * 2)


def main():
    log("看门狗进程启动")
    run_watchdog()


if __name__ == "__main__":
    main()
