#!/usr/bin/env python3
"""
agx_daemon.py - AgentGeeX host daemon

What it does:
  1. Main loop - polls the Redis message queues of this machine's worker agents and, on finding
     a message, dispatches by agent_type:
       - openclaw -> calls sessions.create through the WS child process (agx_ws_client.py)
       - other types -> starts a subagent through tmux (compatibility mode)
  2. Watchdog - forks the child process agx_watchdog.py, which independently monitors every
     running execution, marks a timeout as failed and notifies the initiator.
  3. WS child process - forks the child process agx_ws_client.py, which keeps a long-lived
     connection to the Gateway WebSocket and handles session creation and result delivery.

How to start: start_daemon.sh launches it through tmux
How to stop: send SIGTERM to the tmux session, or send a shutdown command through Redis

Config sources:
  1. agx_config.json (project root)
  2. the defaults in code (fallback)
"""

import atexit
import json
import os
import platform
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime

import redis as redis_mod

# AgentGeeX project root (parent directory of the script directory)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Make sure PROJECT_ROOT is on sys.path (config_loader / common etc. live at the project root)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# * 2026-11-16: message envelopes go through agx_proto only (single construction point; stop hand-writing them)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from agx_proto import dumps_struct, msg_type_for  # noqa: E402

from config_loader import (
    AGENT_TYPE_DSH,
    config,
    gateway_entries,
    has_dsh_entries,
)

# -- Config ------------------------------------------------------
POLL_INTERVAL = int(config.daemon.get("poll_interval", 2))
MAX_WS_BACKLOG = int(config.ws.get("max_backlog", 20))

# -- Blocking queue wait (2026-10-11 P0-1) ------------------------------
# Old implementation: LPOP polling per queue plus sleep(POLL_INTERVAL) -> about 1s of idle latency on average.
# Now BLPOP blocking wait: return at once when a message arrives, otherwise block for at most BLOCK_TIMEOUT seconds.
# Why BLOCK_TIMEOUT is short rather than long: the worker queue list from get_worker_queues
# is cached for 60s and workers may be added -- a short timeout lets the new queue join the blocking watch set in time.
#
# * Hard constraint: BLOCK_TIMEOUT must be strictly smaller than the Redis pool socket_timeout (currently 5s),
#   otherwise BLPOP blocks long enough to hit the socket read timeout first -> every round raises
#   "Timeout reading from socket" (we once set 5 and got bitten). The clamp below pins it to socket_timeout-1.
_REDIS_SOCKET_TIMEOUT = 5  # Keep in sync with the socket_timeout of _GLOBAL_REDIS_POOL
BLOCK_TIMEOUT = min(int(config.daemon.get("queue_block_timeout", 3)), _REDIS_SOCKET_TIMEOUT - 1)

TEST_MODE = str(config.daemon.get("watchdog_test_mode", "")).lower() in ("1", "true", "yes")
TEST_TTL = int(config.get("test_data_ttl", 604800))

# -- Host heartbeat (2026-10-25 multi-host rework) -----------------
# The daemon writes agx_host_inventory.last_heartbeat every this many seconds.
# The watchdog uses HOST_HEARTBEAT_TIMEOUT (default 90s = 3 missed beats) to decide a host is lost.
# Set <=0 to disable the heartbeat (debug only; with it off the watchdog will false-alarm on a stale last_heartbeat).
HEARTBEAT_INTERVAL = int(config.daemon.get("heartbeat_interval", 30))

# -- Delivery retry config --
MAX_DELIVERY_RETRIES = 5  # Max retries for a failed WS delivery


_GLOBAL_REDIS_POOL = redis_mod.ConnectionPool(
    host=config.redis.get("host", "127.0.0.1"),
    port=int(config.redis.get("port", 6379)),
    db=int(config.redis.get("db", 0)),
    username=config.redis.get("username", "default"),
    password=config.redis.get("password", ""),
    decode_responses=True,
    max_connections=5,  # The daemon core business uses 15 slots by default, which leaves plenty of headroom
    socket_timeout=5,  # 5s command timeout, preventing a distributed task from hanging on the network
    socket_connect_timeout=5,  # 5s connect timeout, so a network blip triggers reconnect quickly
    health_check_interval=10,
    socket_keepalive=True,  # Enable OS-level TCP keepalive
)


def _is_transient_delivery_error(error_text: str) -> bool:
    """Decide whether a failed WS delivery is a recoverable transient error.

    Retryable cases:
      - the Gateway does not know the agent yet (just deployed, not hot-reloaded)
      - sessions.send returns an empty response (a Gateway internal routing problem)
      - the WS connection dropped (temporary network blip)
      - connection refused / connection reset

    Non-retryable cases:
      - the agent returned a business-logic error
      - timeout (handled by the watchdog)
"""
    if not error_text:
        return True  # An empty result is usually transient too (e.g. sessions.send returning {})
    lower = error_text.lower()
    transient_keywords = (
        "sessions.send",
        "unknown agent",
        "invalid_request",
        "connection lost",
        "connection closed",
        "connection refused",
        "connection reset",
        "cannot acquire",
    )
    return any(kw in lower for kw in transient_keywords)


def _is_retryable_result(result: dict) -> bool:
    """Decide whether one WS delivery result is retryable.

    * 2026-11-12 (R5, Feng picked plan A): prefer the field, use the keyword table only as a fallback.
      Root cause: the table above only recognises English, while ws_client writes Chinese
      ("WS disconnected, no final received") -- not one English keyword matches -> judged a permanent
      error, no retry -> the task hangs until the watchdog timeout, while the connection recovers a
      few seconds later (the most unfair kind of failure).
      Fix: ws_client puts retryable / reason_code in the result and this reads the field directly.
      The keyword fallback stays: the DSH bridge and older clients do not carry the field yet, and
      retry capability must not be lost just because of different upgrade timings.
      Note: "field exists" must be tested with `in` -- retryable=False is an explicit "do not retry"
      and must not be overridden the other way by the keyword table.
"""
    if not isinstance(result, dict):
        return True
    if "retryable" in result:
        return bool(result.get("retryable"))
    return _is_transient_delivery_error(str(result.get("result") or ""))


# AgentGeeX working directory (working directory of the openclaw agent subagent)
WORK_DIR = os.path.expanduser(config.get("work_dir", "~/.agx"))


def _test_log(redis_client, list_key: str, data: dict):
    """Test mode: write a per-day Redis list with an automatic TTL."""
    entry = json.dumps({"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **data}, ensure_ascii=False)
    redis_client.lpush(list_key, entry)
    redis_client.expire(list_key, TEST_TTL)


def query_mysql(sql: str) -> list:
    """Execute a MySQL query, accessed uniformly through getRowsByFullSQl."""
    from common.mysql.mysqlservice import getRowsByFullSQl

    return getRowsByFullSQl(sql)


def _record_dispatch(r, run_uuid: str) -> None:
    """Record the *instant it was really sent to the agent* -- Redis for real-time decisions, DB for after-the-fact audit.

    * Feng, 2026-10-27 (plan B+C):
      The resend decision must be based on "the real send instant". It previously used created_at --
      that is the plan persist time (all 47 steps in the same second, 13 minutes earlier than the real
      send), i.e. measuring length against a time that does not exist, so the 600s gate was satisfied
      the moment it was dispatched.

      Redis `agx:send:at:{run}` is the read path (the watchdog reads it live, TTL 24h);
      DB `dispatched_at` is the audit trail (so "what time was it actually sent" can be answered later).
      Both are written, each with its own purpose; neither can be dropped.
"""
    if not run_uuid:
        return
    safe_uuid = "".join(ch for ch in str(run_uuid) if ch.isalnum() or ch in "_-")
    if not safe_uuid:
        return
    # (1) Redis (read path)
    try:
        r.set(f"agx:send:at:{safe_uuid}", int(time.time()), ex=86400)
    except Exception as e:
        log(f" 发送时刻写 Redis 失败 | {safe_uuid}: {e}", "WARN")
    # (2) DB (for the audit trail, queryable)
    try:
        query_mysql(
            f"UPDATE agx_task_execution "
            f"SET dispatched_at=NOW(), created_at=COALESCE(created_at, NOW()) "
            f"WHERE task_run_uuid='{safe_uuid}'"
        )
    except Exception as e:
        log(f" 发送时刻写 DB 失败 | {safe_uuid}: {e}", "WARN")


def build_redis_client():
    """Build the Redis client (configuration injected through the AGX_REDIS_* env vars)."""

    # return redis_mod.Redis(
    #     host=config.redis.get("host", "127.0.0.1"),
    #     port=int(config.redis.get("port", 6379)),
    #     db=int(config.redis.get("db", 0)),
    #     username=config.redis.get("username", "default"),
    #     password=config.redis.get("password", ""),
    #     decode_responses=True,
    #     socket_keepalive=True,
    #     health_check_interval=30,
    #     # --- keepalive defence tuned for Windows ---
    #     socket_timeout=15,  # relax the underlying socket read/write timeout to 15s
    # )

    return redis_mod.Redis(connection_pool=_GLOBAL_REDIS_POOL)


# -- host_id detection ----------------------------------------------


def get_host_id() -> str:
    """Detect this machine's host_id, read from agx_config.json."""
    hid = config.get("host_id", "")
    if not hid:
        hid = os.environ.get("AGX_DAEMON_HOST_ID", "")  # Legacy env var compatibility
    return hid


def get_network_id() -> str:
    """Get this machine's network_id, read from agx_config.json."""
    nid = config.get("network_id", "")
    if not nid:
        nid = os.environ.get("AGX_DAEMON_NETWORK_ID", "")  # Legacy env var compatibility
    return nid


def get_daemon_hosts() -> list:
    """Host list this process maintains (one machine can run several OpenClaw instances).

    Why this is needed: one host_id corresponds to one OpenClaw instance (different --profile,
    different port, different gateway token). Two instances on one machine are two logical hosts,
    each with its own agx:queue:{host}:agent:{id} queue. If the daemon only recognised one host_id,
    the other instance's tasks would never be picked up -- measured: they sit at running/queued with
    an empty started_at.

    Config source is the same as this process's ws_client: the config.gateways array (one item each).
    When unset, fall back to [this machine's host_id] -- behaviour then matches the pre-rework version
    byte for byte.

    Order-preserving dedup; this machine's host_id always comes first (single-host equivalence
    depends on this).
"""
    local = get_host_id()
    hosts = []
    if local:
        hosts.append(local)

    # * 2026-11-17: switched to config_loader.gateway_entries() for one normalised source.
    #   Previously ws_client / state_relay / daemon each parsed gateways on their own,
    #   so the same config meant different things in different components (measured drift).
    #
    #   * The daemon must accept both kinds of entry: openclaw and dsh each have their own entry queue
    #     agx:queue:{host}:agent:{id}, and an unconsumed one just sits at queued with nobody taking it.
    #     A dsh entry has no ws_url, but it does have an agent queue, so it must be maintained too.
    for e in gateway_entries():
        if e["skip"]:
            log(f" get_daemon_hosts 跳过 host={e['host_id'] or '-'}: {e['skip']}", "WARN")
            continue
        hid = e["host_id"]
        if not hid or hid in hosts:
            continue
        hosts.append(hid)

    return hosts


def ws_queue_for(host_id: str) -> str:
    """The WS task queue key for that host (same as agx_ws_client.py)."""
    return f"agx:ws:task:{host_id}"


def dsh_queue_for(host_id: str) -> str:
    """The DSH task queue key for that host (same as agx_dsh_client.py)."""
    return f"agx:dsh:task:{host_id}"


# -- Worker queue lookup (with Redis cache) --------------------------


def get_worker_queues(host_id: str, r) -> list:
    """
    Get the message queue keys of every worker on this machine.

    Prefer the worker queue list from the Redis cache; on a cache miss go back to MySQL.
    Cache key: agx:worker_queues:{host_id}, TTL 60 seconds.
    When the cache expires, or an MCP tool changes a worker, it automatically refreshes from MySQL.
"""
    cache_key = f"agx:worker_queues:{host_id}"

    # 1. Try to read from the Redis cache
    try:
        cached = r.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    # 2. Cache miss, go back to MySQL
    rows = query_mysql(
        f"SELECT agent_id FROM agx_agent_registry WHERE host_id = '{host_id}' AND agent_role IN ('worker', 'host_manager', 'project_manager', 'workshop_manager', 'agent_manager') AND active_status = 1"
    )
    # host-scoped queue: a separate namespace per host
    queues = [f"agx:queue:{host_id}:agent:{r['agent_id']}" for r in rows]

    # 3. Write the Redis cache with a 60s TTL (so a new worker is discovered within 60s of creation)
    try:
        # r.setex(cache_key, 60, json.dumps(queues))
        r.set(cache_key, json.dumps(queues), ex=60)
    except Exception:
        pass

    return queues


# -- Concurrency control ----------------------------------------------


def get_running_task_count(r=None, host_id: str = "") -> int:
    """Count the tasks currently *running* on this machine (cross-platform, 2026-10-11 P2 rework).

    History: early on there was no gateway WebSocket interface, so tasks could only be run by starting
    a process through tmux -> back then "tmux session count = running task count" was accurate.
    Now (agent_type=openclaw) tasks go through the ws_client WS channel and no longer start tmux
    -> the old `tmux list-sessions | grep run_` always counted 0 and the slot check was meaningless.

    New source of truth: active_tasks of the ws_client child process (= the real concurrent run count,
    same source as the concurrency limiter), written by the main loop into the Redis key
    `agx:ws:running:{host_id}` every round (TTL 30s). This function prefers it.

    Fallback chain:
      1. Redis `agx:ws:running:{host_id}` -- the new source of truth (openclaw/WS mode)
      2. tmux session count -- legacy environments / non-openclaw platforms (when tmux really runs tasks)
      3. 0 -- none available (degrades to no limit)

    Note: a Redis value > 0 is taken directly (even if tmux also has a value, WS wins, because on the
    openclaw platform every task lives on the WS side). When the Redis value is 0 or missing, fall back
    to tmux (so non-WS modes are not miscounted as 0).
"""
    # (1) New source of truth: the active count reported by ws_client (visible across processes)
    if r is not None:
        try:
            # When host_id is empty, fall back to this machine -- single-host behaviour is identical to before the parameter existed.
            # In multi-instance mode the caller must pass a concrete host, otherwise it gets this machine's number.
            v = r.get(f"agx:ws:running:{host_id or get_host_id()}")
            if v is not None:
                return int(v)
        except Exception:
            pass

    # 2 fallback: tmux session count (legacy environments / non-openclaw platforms)
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            return 0
        names = result.stdout.strip().split("\n")
        return sum(1 for n in names if n and n.startswith("run_"))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0


def startup_dead_letter_cleanup(host_id: str, r):
    """
    Step 5: dead-letter cleanup at startup.

    Handle leftovers from before the daemon restarted:
      1. Handle leftover agx:ws:result:run_* result keys in Redis (written by the WS client but not yet
         persisted by the daemon)

    Do not mark running as failed directly any more -- orphan tasks are detected and alerted by the
    watchdog, and agx_core decides whether the task is really dead from the diagnostic information
    before calling agx_finish_task.
"""
    # -- 1. Handle leftover WS results --
    try:
        result_keys = r.keys("agx:ws:result:run_*")
        if result_keys:
            log(f" 发现 {len(result_keys)} 个残留 WS 结果,开始清理...")
            cleaned = check_ws_results(host_id, r)
            log(f" 清理残留 WS 结果: {cleaned} 个")
    except Exception as e:
        log(f"清理残留 WS 结果失败: {e}", "WARN")


def cleanup_orphan_sessions():
    """
    Clean up orphan tmux sessions when the daemon starts.

    Orphan definition:
      - the tmux session name has the run_* prefix
      - that task_run_uuid does not exist in MySQL, or its run_status IN ('failed', 'succeeded')

    Cleanup method: tmux kill-session.

    This solves the problem of old sessions occupying slots after the daemon restarts.
"""
    if not _tmux_available():
        return

    # 1. List all run_* sessions
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            return
        names = [n.strip() for n in result.stdout.strip().split("\n") if n.strip().startswith("run_")]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return

    if not names:
        log(" 无孤儿 session(无可清理)")
        return

    # 2. Batch-query MySQL
    uuids = "','".join(names)
    try:
        rows = query_mysql(
            f"SELECT task_run_uuid, run_status FROM agx_task_execution WHERE task_run_uuid IN ('{uuids}')"
        )
        db_map = {r["task_run_uuid"]: r["run_status"] for r in rows}
    except Exception as e:
        log(f"清理孤儿 session:查询 MySQL 失败,跳过 | {e}", "WARN")
        return

    # 3. Decide and clean up
    orphans = []
    for name in names:
        status = db_map.get(name)
        if status is None:
            # No MySQL record -> orphan
            orphans.append(name)
        elif status in ("failed", "succeeded"):
            # Already closed out but the session is still there -> orphan
            orphans.append(name)
        # running/processing -> keep

    if orphans:
        log(f" 清理孤儿 session ({len(orphans)} 个): {', '.join(orphans)}")
        for name in orphans:
            try:
                subprocess.run(
                    ["tmux", "kill-session", "-t", name],
                    capture_output=True,
                    timeout=5,
                )
                log(f"   已清理: {name}")
            except Exception as e:
                log(f"   清理失败: {name} | {e}", "WARN")
    else:
        log(f" 无孤儿 session({len(names)} 个 run_* 均有效)")


def get_task_slots_limit(r, host_id: str) -> int:
    """
    Read this machine's task_slots_limit (maximum concurrency).

    Read strategy: local in-memory cache (5s) -> Redis cache (60s) -> MySQL source of truth.
    The layered cache reduces MySQL pressure while still allowing hot updates (a change in the DB or
    Redis takes effect within 60s).

    Returning -1 means "no limit".

    * 2026-10-11 P2 correction: tmux availability is no longer a precondition.
      The old `if not _tmux_available(): return -1` mistook "does tmux exist" for "is limiting needed"
      -- but slots is a configuration value in the host table, unrelated to tmux.
      In a real environment (tmux with no sessions) that guard made the slot check permanently
      ineffective -> meaningless.
      Now it returns the configured value in any environment; only an explicit 0/negative value counts
      as no limit.
"""
    # L1: local in-memory cache (5s, kept separately per host)
    #   * Multi-instance rework: it used to be a single dict {host_id, ts, value} -- with two hosts
    #     querying in turn, each evicted the other's cache entry, i.e. L1 was useless. Now host -> {ts, value}.
    now = time.time()
    _c = _slots_cache.get(host_id)
    if _c and now - _c.get("ts", 0) < 5:
        return _c["value"]

    # L2: Redis cache (60s)
    cache_key = f"agx:host_slots:{host_id}"
    try:
        v = r.get(cache_key)
        if v:
            val = int(v)
            _slots_cache[host_id] = {"ts": now, "value": val}
            return val
    except Exception:
        pass

    # L3: MySQL source of truth
    try:
        rows = query_mysql(f"SELECT task_slots_limit FROM agx_host_inventory WHERE host_id='{host_id}'")
        val = int(rows[0].get("task_slots_limit", 4) or 4) if rows else 4
    except Exception as e:
        log(f"读取 task_slots_limit 失败: {e}", "WARN")
        val = 4  # fallback

    # Write back to the Redis cache
    try:
        # r.setex(cache_key, 60, str(val))
        r.set(cache_key, str(val), ex=60)
    except Exception:
        pass

    # Write into the local cache
    _slots_cache[host_id] = {"ts": now, "value": val}

    return val


_IS_WINDOWS = sys.platform == "win32"


def _tmux_available() -> bool:
    """Check whether the tmux command is available (skip directly on Windows)."""
    if _IS_WINDOWS:
        return False
    now = time.time()
    if now - _tmux_ok_cache.get("ts", 0) < 60:
        return _tmux_ok_cache.get("ok", True)
    try:
        result = subprocess.run(["tmux", "-V"], capture_output=True, timeout=2)
        ok = result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        ok = False
    _tmux_ok_cache["ts"] = now
    _tmux_ok_cache["ok"] = ok
    return ok


def sync_active_task_count(host_id: str, r=None):
    """
    Periodically sync active_task_count and the host heartbeat to MySQL.

    * 2026-10-11 P2 correction: the old `if not _tmux_available(): return` skipped everything
      -> in a real environment (tmux with no sessions) active_task_count never updated and the panel
      showed fake data.
      Now it uses the unified get_running_task_count(r) (preferring the real run count reported by ws_client).

    * 2026-10-25 multi-host rework: the same UPDATE also writes the heartbeat.
      The heartbeat is the only evidence that "this machine is still alive"; the watchdog uses it for
      network-wide liveness. Merged into one UPDATE instead of another DB round trip, cutting pointless cost.
"""
    count = get_running_task_count(r, host_id)
    try:
        query_mysql(
            f"UPDATE agx_host_inventory "
            f"SET active_task_count={count}, presence_status=1, last_heartbeat=NOW() "
            f"WHERE host_id='{host_id}'"
        )
    except Exception as e:
        log(f"同步 active_task_count/heartbeat 失败: {e}", "WARN")


def write_heartbeat(host_id: str):
    """Write the heartbeat on its own (time-driven, not dependent on the polling counter).

    * Why the heartbeat inside sync_active_task_count is not enough:
      the daemon main loop blocks in BLPOP (BLOCK_TIMEOUT defaults to 3s),
      and sync_active_task_count hangs off loop_count % 10 --
      counter-driven timing is uncontrollable when "busy = fast loop / idle = slow block":
      too frequent when busy, possibly overdue when idle, hence a separate time-driven entry point.
"""
    try:
        query_mysql(
            f"UPDATE agx_host_inventory SET presence_status=1, last_heartbeat=NOW() "
            f"WHERE host_id='{host_id}'"
        )
    except Exception as e:
        log(f"写心跳失败: {e}", "WARN")


# Local in-memory cache
_slots_cache: dict = {}  # host_id -> {"ts": float, "value": int} (multi-instance: one entry per host)
_tmux_ok_cache: dict = {}  # {ts, ok}
_tmux_warned: dict = {}  # {done} - alert only once


# -- Logging ------------------------------------------------------


import contextlib

from log_setup import setup_logging

log = setup_logging("DAEMON", log_dir=os.path.join(PROJECT_ROOT, "logs"))


# -- Watchdog child process management --------------------------

_watchdog_process: subprocess.Popen | None = None

# Watchdog heartbeat file path (must match agx_watchdog.py)
_WATCHDOG_HEARTBEAT_FILE = config.daemon.get("watchdog_heartbeat_file") or os.path.join(
    WORK_DIR, "logs", "agx_watchdog_heartbeat"
)
# Heartbeat timeout in seconds; no heartbeat for longer than this means it hung
_WATCHDOG_HEARTBEAT_TIMEOUT = int(config.daemon.get("watchdog_heartbeat_timeout", 1800))
# Startup grace period in seconds; the heartbeat timeout does not fire right after start
_WATCHDOG_STARTUP_GRACE = int(config.daemon.get("watchdog_startup_grace", 60))


def start_watchdog():
    """
    Start the watchdog child process (using subprocess instead of multiprocessing).

    Why subprocess.Popen rather than multiprocessing.Process:
    macOS uses the spawn start_method by default, and the spawn stage can hang -- the child process is
    already created but stuck in module import, never entering main().
    subprocess is a fully independent OS process that shares no Python interpreter state, removing the
    spawn-hang problem at the root.

    The watchdog runs independently and monitors every running execution:
      - timeout detection (configurable per task type)
      - tmux liveness detection
      - failure marking + notification
"""
    global _watchdog_process

    script_dir = os.path.dirname(os.path.abspath(__file__))
    watchdog_script = os.path.join(script_dir, "agx_watchdog.py")

    if not os.path.exists(watchdog_script):
        log(f"看门狗脚本不存在,跳过: {watchdog_script}", "WARN")
        return

    # Clean up the old heartbeat file
    try:
        os.remove(_WATCHDOG_HEARTBEAT_FILE)
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # Pass env vars to the watchdog child process (heartbeat file path)
    child_env = os.environ.copy()
    child_env["AGX_WATCHDOG_HEARTBEAT_FILE"] = _WATCHDOG_HEARTBEAT_FILE

    _watchdog_process = subprocess.Popen(
        [sys.executable, watchdog_script],
        env=child_env,
        cwd=PROJECT_ROOT,
        stdout=sys.stdout,  # Watchdog logs go into the same log file
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,  # Separate process group, so the whole group can be killed together
    )

    log(f"看门狗已启动 | pid={_watchdog_process.pid}")


def stop_watchdog():
    """Stop the watchdog child process."""
    global _watchdog_process

    if _watchdog_process is None:
        return

    # Already exited, nothing to stop
    if _watchdog_process.poll() is not None:
        _watchdog_process = None
        return

    log("正在停止看门狗...", "WARN")
    try:
        _watchdog_process.terminate()
        _watchdog_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        log("看门狗未响应 SIGTERM,强制 kill", "WARN")
        _watchdog_process.kill()
        _watchdog_process.wait(timeout=3)

    log("看门狗已停止")
    _watchdog_process = None


def check_watchdog():
    """
    Check whether the watchdog is still alive and whether its heartbeat is healthy.

    Double detection:
    1. process liveness (poll) -- fires when the watchdog crashes
    2. heartbeat timeout (heartbeat file mtime) -- fires when the watchdog hangs / the spawn hung

    Either condition firing = forced restart.
"""
    global _watchdog_process

    if _watchdog_process is None:
        start_watchdog()
        return

    # -- Check 1: is the process alive --
    rc = _watchdog_process.poll()
    if rc is not None:
        log(f"看门狗意外退出 | exit_code={rc},正在重启...", "WARN")
        _watchdog_process = None
        start_watchdog()
        return

    # -- Check 2: has the heartbeat expired --
    try:
        mtime = os.path.getmtime(_WATCHDOG_HEARTBEAT_FILE)
        age = time.time() - mtime
        if age > _WATCHDOG_HEARTBEAT_TIMEOUT:
            log(f"看门狗心跳超时 ({age:.0f}s),判定为假死,强制重启...", "WARN")
            stop_watchdog()
            with contextlib.suppress(Exception):
                os.remove(_WATCHDOG_HEARTBEAT_FILE)
            start_watchdog()
    except FileNotFoundError:
        # Heartbeat file missing -- it may have just started and not written yet (no alert during the grace period)
        pass
    except Exception as e:
        log(f"检查心跳文件异常: {e}", "WARN")


# -- WS client child process management ---------------------------

_ws_process: subprocess.Popen | None = None

# DSH bridge launcher (agx_dsh_supervisor.py, config-driven since 2026-11-17)
#
# * Why the daemon no longer starts the bridge itself:
#   the daemon used to spawn an agx_dsh_client.py **unconditionally**, taking host_id from the top-level config.host_id.
#   So a host without DSH also started one (idling), and on a host that has DSH the host_id was wrong
#   (top-level test_host_srv vs the real test_host_srv) -- two bridges, one doing the work.
#   Now the daemon knows only "one launcher handle"; how many bridges start is decided by the number of
#   agent_type=dsh entries in config.gateways (same shape as state_relay).
_dsh_supervisor_process: subprocess.Popen | None = None
_dsh_supervisor_logfh = None
# Quiet period after exit=0: this machine has no dsh entry (a normal case), so it must not be treated as a crash and relaunched forever
_dsh_supervisor_next_check = 0.0

# WS result polling interval in seconds
def refresh_execution_cache(run_uuid: str):
    """Read the whole DB row back and refresh the execution cache wholesale (avoid making a partial entity via hset).

    Why (fixed defect 4 on 2026-10-22, same source as watchdog._refresh_execution_cache):
      Redis hset is upsert semantics -- when the key does not exist it creates a hash with only those fields.
      Measured: 11 execution entities had only 1~6 fields (normal is 29), all caused by the direct hset
      write path in watchdog/daemon, and the cache never expires and never heals itself.
      So it was changed to "read the row back -> write all fields", with the field list taken from the DB row.

    Why not use MCP's _sync_entity: the daemon is a separate process, and importing mcp_server would
    pull up the whole MCP server.
"""
    try:
        rows = query_mysql(
            "SELECT * FROM agx_task_execution WHERE task_run_uuid='" + str(run_uuid).replace("'", "") + "'"
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
        r = build_redis_client()
        r.hset("agx:entity:execution:" + str(run_uuid), mapping=clean)
    except Exception as e:
        log(f"刷新 execution 缓存失败 | {run_uuid}: {e}", "WARN")


# ════════════════════════════════════════════════════════════════════════════
# * 2026-10-25: the *single implementation* of execution status changes (fixes DH-065 + DH-066)
# ════════════════════════════════════════════════════════════════════════════
#   Root cause: a status change needs three things -- (1) update DB, (2) refresh the hash, (3) sync the status index,
#   but three processes (MCP / daemon / watchdog) each did their own thing and only the first one or two:
#    - MCP   _sync_entity          -> all three (mcp_server.py:641-643)
#    - daemon                      -> only the index in the _is_receipt branch (:1031-1033); the failure branch did nothing
#      -> DH-065: status=failed falls into else, _status_sql lacks run_status -> stuck at running forever
#    - watchdog _update_run_status -> only DB + hash, never calls _sync_execution_status_index
#      -> DH-066: agx:idx:execution:status:running keeps accumulating finished tasks
#         (measured 4/4 wrong: 2 cancelled + 2 failed all left in the running index for hours)
#   Consequence: the watchdog uses zrange(running) as its only scan source (watchdog.py:529) ->
#         finished tasks keep being processed as running -> spurious alerts / duplicate receipts / false timeouts.
#   This function = the three-in-one, reused by daemon and watchdog; the MCP side keeps its own _sync_entity.

_EXEC_ALL_STATUSES = ("running", "succeeded", "failed", "pending", "cancelled", "draft", "awaiting_confirm")


def apply_execution_status(run_uuid: str, new_status: str):
    """Change an execution's status to new_status -- DB + hash + index all together.

    Difference from MCP _sync_entity: MCP "syncs one entity" (reads entity data and handles every
    entity_type branch); this function performs only the execution status action, for independent
    processes to call. Index semantics match MCP (zrem every old status -> zadd the new one, by_plan synced).
"""
    if not run_uuid or not new_status:
        return
    safe_uuid = str(run_uuid).replace("'", "")
    safe_status = str(new_status).replace("'", "")
    query_mysql(f"UPDATE agx_task_execution SET run_status='{safe_status}' WHERE task_run_uuid='{safe_uuid}'")
    refresh_execution_cache(run_uuid)
    _sync_exec_status_index(run_uuid, safe_status)


def _sync_exec_status_index(run_uuid: str, new_status: str):
    """Sync the status index only (the DB was already changed by the caller) -- reused by the close-out/failure branches.

    * 2026-10-25 DH-066: this function fills in the index sync the daemon side had always been missing.
      The old code hard-coded zadd(succeeded) only in the _is_receipt branch; ordinary tasks (including
      failures) were never synced. It now syncs by the real terminal state read back from the DB, no longer
      inferred from memory.
"""
    if not run_uuid or not new_status:
        return
    safe_uuid = str(run_uuid).replace("'", "")
    safe_status = str(new_status).replace("'", "")
    try:
        r = build_redis_client()
        pipe = r.pipeline()
        for s in _EXEC_ALL_STATUSES:
            pipe.zrem(f"agx:idx:execution:status:{s}", run_uuid)
        pipe.zadd(f"agx:idx:execution:status:{safe_status}", {run_uuid: int(time.time())})
        # by_plan index (score encodes the status, same convention as MCP / watchdog)
        raw = r.hgetall(f"agx:entity:execution:{run_uuid}") or {}
        plan_id = raw.get("plan_id") or ""
        if plan_id:
            _PLAN_SCORE = {"draft": -1, "pending": 0, "running": 1, "succeeded": 2, "failed": 3, "cancelled": 4}
            pipe.zadd(f"agx:idx:execution:by_plan:{plan_id}", {run_uuid: _PLAN_SCORE.get(safe_status, 0)})
        pipe.execute()
    except Exception as e:
        log(f"同步 execution 状态索引失败 | {run_uuid} -> {new_status}: {e}", "WARN")



WS_RESULT_POLL_INTERVAL = int(config.daemon.get("ws_result_poll_interval", 5))


def start_ws_client():
    """
    Start the WS client child process (agx_ws_client.py).

    That child process:
      - connects to the OpenClaw Gateway WebSocket
      - takes tasks from the Redis agx:ws:task:{host_id} queue
      - creates a session through sessions.create
      - writes the result into agx:ws:result:{run_uuid}
"""
    global _ws_process

    # * 2026-11-04 multi-instance: gateways is the config source, the singular gateway is only a backward-compatible fallback.
    #   Root cause: the old check only recognised the singular gateway -- with only the gateways array present,
    #   ws_client did not start at all, both instances lost their connections, and only one WARN was left (silent stall).
    gws = config.get("gateways") or []
    # * The singular gateway is a deprecated fallback and must be accessed with .get() (a _Config has no __getattr__,
    #   so attribute access on a missing key raises AttributeError and deleting the node crashes).
    _gw_single = config.get("gateway") or {}
    gs_url = _gw_single.get("ws_url", "")
    gs_token = _gw_single.get("token", "")

    if not gws and (not gs_url or not gs_token):
        log(" gateways 与 gateway 均未配置,跳过 WS 客户端启动", "WARN")
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    ws_script = os.path.join(script_dir, "agx_ws_client.py")

    if not os.path.exists(ws_script):
        log(f"WS 客户端脚本不存在: {ws_script}", "WARN")
        return

    _ws_process = subprocess.Popen(
        [sys.executable, ws_script],
        stdout=sys.stdout,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        cwd=PROJECT_ROOT,
        start_new_session=True,
        env={**os.environ},  # Inherit env vars plus AGX_CONFIG_PATH pointing at the config file
    )

    log(f"WS 客户端已启动 | pid={_ws_process.pid}")


def stop_ws_client():
    """Stop the WS client child process."""
    global _ws_process

    if _ws_process is None:
        return

    if _ws_process.poll() is not None:
        _ws_process = None
        return

    log("正在停止 WS 客户端...", "WARN")
    try:
        _ws_process.terminate()
        _ws_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        log("WS 客户端未响应 SIGTERM,强制 kill", "WARN")
        _ws_process.kill()
        _ws_process.wait(timeout=3)

    log("WS 客户端已停止")
    _ws_process = None


def check_ws_client():
    """Check whether the WS client is alive and restart it if it died."""
    global _ws_process

    if _ws_process is None:
        start_ws_client()
        return

    rc = _ws_process.poll()
    if rc is not None:
        log(f"WS 客户端意外退出 | exit_code={rc},正在重启...", "WARN")
        _ws_process = None
        start_ws_client()


# -- DSH bridge launcher management (agx_dsh_supervisor.py, exactly the same shape as state_relay) --
#
# * Feng, 2026-11-17: "a host starts only one daemon and only one DSH.
#   On the openclaw side it is configuration-driven: as many as are configured in the file. DSH should be the same."
#
#   Before the rework: the daemon spawned one agx_dsh_client.py unconditionally, host_id from top-level config.host_id
#     - test_host_mac (no DSH): started one anyway, idling against 127.0.0.1:8791
#     - 188: the one it started had host_id=test_host_srv and consumed agx:dsh:task:test_host_srv,
#       while tasks went to test_host_srv -> never received; the real work was done by a hand-made systemd service
#     Result: two bridge processes, one working, one sitting on an empty queue.
#
#   Now: the daemon knows only this one handle; how many start is decided by agent_type=dsh entries in gateways.
#   Same shape as start_state_relay -- the main loop maintains only the "single handle, three-piece" pattern, no added complexity.


def start_dsh_supervisor():
    """Start the DSH bridge launcher (agx_dsh_supervisor.py).

    That child process:
      - reads config.gateways and starts one agx_dsh_client.py per entry with agent_type=dsh
      - passes AGX_DAEMON_HOST_ID=<that entry's host_id> plus entry-level dsh overrides (host/port/...)
      - supervises them (relaunch with backoff on exit)
      - collects the child processes when it receives SIGTERM

    When this machine has no dsh entry the supervisor prints one explanatory line and exits normally
    (exit=0), and the daemon goes into its quiet period without relaunching forever. The decision lives
    in one place only, avoiding two copies of the rule disagreeing.
"""
    global _dsh_supervisor_process, _dsh_supervisor_logfh

    # Belt and braces: a direct call also honours "no entries, no launcher"
    if not has_dsh_entries():
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))
    sup_script = os.path.join(script_dir, "agx_dsh_supervisor.py")

    if not os.path.exists(sup_script):
        log(f"DSH 桥启动器不存在,跳过: {sup_script}", "WARN")
        return

    log_dir = os.path.join(WORK_DIR, "runtime_watch")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "dsh_supervisor.log")
    _dsh_supervisor_logfh = open(log_path, "a", encoding="utf-8")

    _dsh_supervisor_process = subprocess.Popen(
        [sys.executable, sup_script],
        stdout=_dsh_supervisor_logfh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        cwd=PROJECT_ROOT,
        start_new_session=False,  # Same group as the daemon: systemd stop collects it too
        env={**os.environ},
    )

    log(f"DSH 桥启动器已启动 | pid={_dsh_supervisor_process.pid}")


def stop_dsh_supervisor():
    """Stop the DSH bridge launcher (it collects the N bridges first)."""
    global _dsh_supervisor_process, _dsh_supervisor_logfh

    if _dsh_supervisor_process is None:
        return

    if _dsh_supervisor_process.poll() is not None:
        _dsh_supervisor_process = None
        return

    log("正在停止 DSH 桥启动器...", "WARN")
    try:
        _dsh_supervisor_process.terminate()
        _dsh_supervisor_process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        _dsh_supervisor_process.kill()
        _dsh_supervisor_process.wait(timeout=5)

    if _dsh_supervisor_logfh is not None:
        try:
            _dsh_supervisor_logfh.close()
        except OSError:
            pass
        _dsh_supervisor_logfh = None

    log("DSH 桥启动器已停止")
    _dsh_supervisor_process = None


def check_dsh_supervisor():
    """Check whether the DSH bridge launcher is alive and restart it if it died."""
    global _dsh_supervisor_process, _dsh_supervisor_next_check

    # * 2026-11-17: no agent_type=dsh entry in the config -> this machine needs no launcher.
    #   In the early rework it was "start unconditionally; the supervisor finds no entry, exits 0, and is relaunched after 60s",
    #   measured to waste 1440 "spawn -> exit at once" cycles a day on a machine without DSH.
    #   The decision still lives only in config_loader.has_dsh_entries() (which goes through gateway_entries); there is no second copy of the rule.
    if _dsh_supervisor_process is None and not has_dsh_entries():
        return

    if _dsh_supervisor_process is None:
        if time.time() < _dsh_supervisor_next_check:
            return
        start_dsh_supervisor()
        return

    rc = _dsh_supervisor_process.poll()
    if rc is not None:
        if rc == 0:
            # Normal self-exit: this machine has no agent_type=dsh entry, or another instance holds the lock.
            # This is a **normal state** and must not log-and-relaunch every 2 seconds.
            log("DSH 桥启动器已退出(exit=0，本机无 dsh 条目或有实例在跑)，60s 后再检查")
            _dsh_supervisor_next_check = time.time() + 60
        else:
            log(f"DSH 桥启动器意外退出 | exit_code={rc},正在重启...", "WARN")
        _dsh_supervisor_process = None
        if rc != 0:
            start_dsh_supervisor()


# -- Session state collector launcher management (agx_state_relay.py, same three-piece shape as ws_client) --
#
# * 2026-11-05: "start one collector per gateway" was collapsed into a single supervisor.
#   The daemon knows only this one child process -- how many gateways are configured is decided by the supervisor reading the config,
#   so the daemon does not have to change along with it and will not start one too few because of config drift (the silent-stall trap).
#
#   Why not start N of them inside the daemon:
#     the main loop maintains a single handle with three pieces (poll -> restart). Packing N in means bookkeeping,
#     relaunching and shutting down each one separately, loading all the complexity into the hottest loop.

_state_relay_process: subprocess.Popen | None = None
_state_relay_logfh = None
# Quiet period after exit=0: the lock is held by someone else / this machine has no gateways config,
# such exits must not be treated as "crashes" and relaunched forever (otherwise one log line every 20s and the process count jumps)
_state_relay_next_check = 0.0


def start_state_relay():
    """Start the session state collector launcher (agx_state_relay.py).

    That child process:
      - reads config.gateways and starts one gateway_state_relay.py per entry
      - supervises them (relaunch with backoff on exit)
      - collects the child processes when it receives SIGTERM

    With no gateways configured the supervisor itself WARNs and exits; the daemon does not pre-check
    here -- the decision lives in one place only, avoiding two copies of the rule disagreeing.
"""
    global _state_relay_process, _state_relay_logfh

    script_dir = os.path.dirname(os.path.abspath(__file__))
    sup_script = os.path.join(script_dir, "agx_state_relay.py")

    if not os.path.exists(sup_script):
        log(f"采集器启动器不存在,跳过: {sup_script}", "WARN")
        return

    log_dir = os.path.join(WORK_DIR, "runtime_watch")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "state_relay_supervisor.log")
    _state_relay_logfh = open(log_path, "a", encoding="utf-8")

    _state_relay_process = subprocess.Popen(
        [sys.executable, sup_script],
        stdout=_state_relay_logfh,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        cwd=PROJECT_ROOT,
        start_new_session=False,  # Same group as the daemon: systemd stop collects it too
        env={**os.environ},  # Inherit env vars plus AGX_CONFIG_PATH pointing at the config file
    )

    log(f"会话状态采集器启动器已启动 | pid={_state_relay_process.pid}")


def stop_state_relay():
    """Stop the session state collector launcher (it collects the N collectors first)."""
    global _state_relay_process, _state_relay_logfh

    if _state_relay_process is None:
        return

    if _state_relay_process.poll() is not None:
        _state_relay_process = None
        return

    log("正在停止会话状态采集器...", "WARN")
    try:
        _state_relay_process.terminate()
        _state_relay_process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        log("采集器启动器未响应 SIGTERM,强制 kill", "WARN")
        _state_relay_process.kill()
        _state_relay_process.wait(timeout=5)

    log("会话状态采集器已停止")
    _state_relay_process = None
    if _state_relay_logfh is not None:
        try:
            _state_relay_logfh.close()
        except OSError:
            pass
        _state_relay_logfh = None


def check_state_relay():
    """Check whether the collector launcher is alive and restart it if it died."""
    global _state_relay_process, _state_relay_next_check

    if _state_relay_process is None:
        if time.time() < _state_relay_next_check:
            return
        start_state_relay()
        return

    rc = _state_relay_process.poll()
    if rc is not None:
        if rc == 0:
            # Normal self-exit: no gateways config, or another instance holds the lock -- do not relaunch, just wait a while
            log("会话状态采集器启动器已退出(exit=0，无 gateways 配置或已有实例在跑)，60s 后再检查")
            _state_relay_next_check = time.time() + 60
        else:
            log(f"会话状态采集器启动器意外退出 | exit_code={rc},正在重启...", "WARN")
        _state_relay_process = None
        if rc != 0:
            start_state_relay()


def get_agent_type(agent_id: str, r) -> str:
    """
    Look up an agent's agent_type.

    Read the Redis cache agx:entity:agent:{agent_id} -> agent_type first; on a miss go back to MySQL and
    write the cache. Returns "openclaw" by default (compatible with existing data).
"""
    try:
        agent_key = f"agx:entity:agent:{agent_id}"
        data = r.hgetall(agent_key)
        if data and "agent_type" in data:
            return data["agent_type"]
    except Exception:
        pass

    # Cache miss -> go back to MySQL
    try:
        rows = query_mysql(f"SELECT agent_type FROM agx_agent_registry WHERE agent_id='{agent_id}' AND active_status=1")
        if rows and rows[0].get("agent_type"):
            return rows[0]["agent_type"]
    except Exception:
        pass

    return "openclaw"  # Default


def check_ws_results(host_id: str, r) -> int:
    """
    Handle a task result completed by the WS client.

    Only writes ws_delivery_status (the delivery state); never touches run_status/result_data:
      - business success or failure is set by the agent itself through agx_finish_task
      - timeouts are decided by the watchdog plus send_agent inference

    Returns the number of results handled.
"""

    try:
        keys = r.keys("agx:ws:result:run_*")
    except Exception:
        return 0

    if not keys:
        return 0

    processed = 0
    for key in keys:
        run_uuid = ""
        delivery = ""
        try:
            raw = r.get(key)
            if not raw:
                r.delete(key)
                continue

            result = json.loads(raw)
            run_uuid = result.get("run_uuid", "")
            if not run_uuid:
                log(f" 删除前 key_exists={r.exists(key)} , key={key}")
                delete_result = r.delete(key)
                log(f" 删除结果={delete_result}")
                continue

            # ** 2026-10-25 DH-067 fix: verify the host a result belongs to (prevent execution on the wrong host)
            #   Root cause: `agx:ws:result:run_*` is a global key (no host isolation), so every daemon scans the whole set.
            #   Measured incident: a task record with host_id=test_host_srv failed on 188, and the test_host_mac daemon
            #   picked up that failure result -> triggered a retry delivery -> pushed with **its own** host_id
            #   agx:ws:task:test_host_mac -> executed locally and judged succeeded, and the scheduling record was completely distorted.
            #   Fix: the result carries its own host_id (written by ws_client / dsh_client) plus a DB lookup of
            #   execution.host_id. On mismatch, this is not ours to handle -- skip it (leave it for the owning host).
            #   Backward compatible: for an old result with no host_id field, fall back to the DB check (allow it when the DB has no record either).
            _res_host = (result.get("host_id") or "").strip()
            _owner_host = ""
            try:
                _hr = query_mysql(
                    f"SELECT host_id FROM agx_task_execution WHERE task_run_uuid='{run_uuid}'"
                )
                if _hr:
                    _owner_host = (_hr[0].get("host_id") or "").strip()
            except Exception:
                _owner_host = ""
            _src_host = _res_host or _owner_host
            if _src_host and host_id and _src_host != host_id:
                log(
                    f" 跳过非本机结果 | {run_uuid} | 结果主机={_src_host} "
                    f"本机={host_id} | 留给归属机处理"
                )
                continue
            if _res_host and _owner_host and _res_host != _owner_host:
                # The producing host differs from the scheduling record -- a wrong-host execution already happened; alert but still persist
                log(
                    f" ⚠️ 错主机执行痕迹 | {run_uuid} | 结果主机={_res_host} "
                    f"调度记录={_owner_host} | 见 DH-067",
                    "WARN",
                )

            # * Feng, 2026-10-29: a receipt is a message and no longer creates an execution.
            #   This section handles delivery confirmation for *event-type notifications* (receipts and alerts share the shape):
            #   the watchdog delivers with delivery_key = run_receipt_<evt suffix> or
            #   run_notice_<evt suffix> (the run_ prefix is mandatory, otherwise it falls outside the
            #   agx:ws:result:run_* scan range and delivery confirmation is never received).
            #   Nothing here touches agx_task_execution -- only the event delivery_state is written back.
            _evt_prefix = ""
            if run_uuid.startswith("run_receipt_"):
                _evt_prefix = "run_receipt_"
            elif run_uuid.startswith("run_notice_"):
                _evt_prefix = "run_notice_"
            if _evt_prefix:
                _evt = "evt_" + run_uuid[len(_evt_prefix):]
                _st = (result.get("status") or "").strip()
                _label = "回执" if _evt_prefix == "run_receipt_" else "告警"
                try:
                    if _st == "succeeded":
                        query_mysql(
                            "UPDATE agx_task_execution_event "
                            "SET delivery_state='delivered', delivered_at=NOW() "
                            f"WHERE event_id='{_evt}' AND delivery_state<>'delivered'"
                        )
                        log(f" {_label}送达 | {_evt} | delivered")
                        # * Feng, 2026-10-30: all three acknowledgement axes live on the *task* row,
                        #   the event table only keeps the history. The delivery instant is the second axis -- without it,
                        #   "not delivered" and "delivered but unread" cannot be told apart on the task row.
                        #   ack_state only moves forward: a read is never overwritten by delivered.
                        try:
                            query_mysql(
                                "UPDATE agx_task_execution e "
                                "JOIN agx_task_execution_event v ON v.task_run_uuid=e.task_run_uuid "
                                "SET e.reply_delivered_at=COALESCE(v.delivered_at, NOW()), "
                                "e.ack_state=CASE WHEN e.ack_state='read' THEN e.ack_state "
                                "ELSE 'delivered' END "
                                f"WHERE v.event_id='{_evt}'"
                            )
                            # * Feng, 2026-10-30: updates must go into the cache -- cache and database
                            #   must never disagree as "in the DB but not in the cache". Measured: when only the DB was written,
                            #   the cached reply_delivered_at stayed None (measured 5/40 missing),
                            #   and any path deciding on that field read an empty value.
                            _tn = query_mysql(
                                "SELECT task_run_uuid FROM agx_task_execution_event "
                                f"WHERE event_id='{_evt}'"
                            )
                            if _tn and _tn[0].get("task_run_uuid"):
                                refresh_execution_cache(_tn[0]["task_run_uuid"])
                        except Exception as _e2:
                            log(f" {_label}送达时刻回写失败 | {_evt}: {_e2}", "WARN")
                    else:
                        query_mysql(
                            "UPDATE agx_task_execution_event "
                            f"SET delivery_state='failed' WHERE event_id='{_evt}'"
                        )
                        log(f" {_label}送达 | {_evt} | failed | {str(result.get('result'))[:120]}")
                except Exception as _e:
                    log(f" {_label}送达回写失败 | {_evt}: {_e}", "WARN")
                r.delete(key)
                processed += 1
                continue

            # Decide the delivery state
            ws_status = result.get("status", "")
            result_text = result.get("result", "")
            if ws_status == "succeeded":
                delivery = "delivered"
            elif "超时" in (result_text or ""):
                delivery = "timeout"
            elif _is_retryable_result(result):
                # transient error: try to retry, mark error only after the limit
                _self_closed = False
                retry_key = f"agx:ws:retry:{run_uuid}"
                try:
                    retry_count = int(r.get(retry_key) or 0)
                except Exception:
                    retry_count = 0

                if retry_count < MAX_DELIVERY_RETRIES:
                    try:
                        row = query_mysql(
                            "SELECT agent_id, timeout_seconds, session_id, send_session_id, "
                            "send_agent_id, host_id, run_status, is_reply_to FROM agx_task_execution "
                            f"WHERE task_run_uuid='{run_uuid}'"
                        )
                        _rs = (row[0].get("run_status") or "") if row else ""
                        if row and _rs and _rs != "running":
                            # * 2026-11-12 DH-070: the task already closed itself out (while disconnected the agent
                            #   finished and called agx_finish_task itself, writing to the DB) -> redelivering means
                            #   duplicate execution. Measured: drill-1/drill-3 were closed out by the original run itself.
                            log(
                                f" 跳过重投：任务已自行收口 | {run_uuid} | run_status={_rs}",
                                "WARN",
                            )
                            _self_closed = True
                            delivery = "self_closed"
                        elif row:
                            agent_id = row[0].get("agent_id", "")
                            timeout = row[0].get("timeout_seconds") or int(config.task_timeout)
                            sess = row[0].get("session_id", "")
                            # * 2026-10-22: retry delivery aligns byte for byte with the first delivery message structure.
                            #   send_agent = the original sending agent (from execution.send_agent_id)
                            #   self_session_id = the **receiver's own** session (from execution.session_id)
                            #     ^ the 2026-10-09 first version wrongly took send_session_id (the original sender's session),
                            #       contradicting the session-context block at the top of exec_instruction; fixed.
                            send_agent = row[0].get("send_agent_id", "") or ""
                            self_sid = sess or ""
                            # ** 2026-10-25 DH-067 fix: a retry must go to the **host the task belongs to**.
                            #   The old code used ws_queue = f"agx:ws:task:{host_id}" -- and host_id was
                            #   the current consumer's own, unrelated to where the task should actually run.
                            #   Measured: the task failed on 188, the test_host_mac daemon picked up the retry,
                            #   pushed agx:ws:task:test_host_mac -> executed locally and judged succeeded.
                            _target_host = (row[0].get("host_id") or "").strip() or host_id
                            if _target_host != host_id:
                                log(
                                    f" 重试归属非本机，不推本地队列 | {run_uuid} "
                                    f"| 任务主机={_target_host} 本机={host_id}"
                                )
                                continue
                            ws_queue = ws_queue_for(_target_host)
                            ws_task = {
                                "run_uuid": run_uuid,
                                "agent_id": agent_id,
                                # * 2026-11-16 fix (Feng's evidence run_general_6e0a76907c02):
                                #   retry delivery used to hand-write a 3-field envelope, missing msg_type / resend_attempt
                                #   -- the 2026-10-26 field addition only changed the first send and missed this spot. Now everything goes through agx_proto.
                                "instruction": dumps_struct(
                                    run_uuid,
                                    send_agent,
                                    self_sid,
                                    msg_type=msg_type_for(row[0].get("is_reply_to") or ""),
                                    resend_attempt=retry_count + 1,
                                ),
                                "timeout_seconds": int(timeout),
                                # * 2026-10-25 DH-067: as above, retry delivery also carries the owning host
                                "target_host_id": _target_host,
                            }
                            if sess:
                                ws_task["session_key"] = sess
                                ws_task["mode"] = "default"
                            r.incr(retry_key)
                            r.expire(retry_key, 3600)
                            r.rpush(ws_queue, json.dumps(ws_task, ensure_ascii=False))
                            r.delete(key)
                            processed += 1
                            log(f" 重试投递 ({retry_count + 1}/{MAX_DELIVERY_RETRIES}) | {run_uuid} → {ws_queue}"
                                f" | code={result.get('reason_code') or '-'}")
                            continue  # Skip the MySQL update and wait for the next result
                    except Exception as e:
                        log(f"重试入队失败 | {run_uuid}: {e}", "WARN")
                # Over the limit or not retryable: mark delivery_error (distinct from a business error)
                # * 2026-10-19: the length must be <= 20 (the column is varchar(20)).
                #   The old text f"error_delivery (retried {retry_count}x)" is at least 23 characters
                #   -> guaranteed MySQL 1406 Data too long -> the whole UPDATE fails
                #   -> the delivery state stays pending forever (and every retry round reports it again).
                # * 2026-11-12 DH-070: if "task closed itself out" was decided above, keep that more accurate
                #   state (self_closed) instead of letting the retry count overwrite it with error_retried_Nx --
                #   that would record "the task succeeded, we chose not to deliver" as "delivery failed after 4 retries".
                if not _self_closed:
                    delivery = f"error_retried_{retry_count}x"
            else:
                delivery = "error"
                # * R5: a non-retryable result must still make some noise (the old implementation silently wrote a field and stopped,
                #   leaving the task hanging with nobody aware). This only adds one log line and changes no state semantics.
                log(
                    f" 送达判为不可重试 → error（不重试） | {run_uuid} | "
                    f"code={result.get('reason_code') or '-'} | {str(result_text)[:140]}",
                    "WARN" if result.get("retryable") is False else "INFO",
                )

            # * 2026-10-19: final backstop -- whatever upstream computed, truncate beyond 20 characters
            #   and log an alert. Better to lose a detail field than to let the whole UPDATE fail
            #   (which would magnify a small "state not written" problem into "the task stays pending forever").
            if len(delivery) > 20:
                log(f"ws_delivery_status 超长已截断 | {delivery!r} ({len(delivery)}字符) | {run_uuid}", "WARN")
                delivery = delivery[:20]

            # Only update the delivery state
            safe_delivery = delivery.replace("'", "\\'")

            # Collect the token fields (update the ones present, skip the rest)
            token_sets = []
            for field, token_key in [
                ("input_tokens", "input_tokens"),
                ("output_tokens", "output_tokens"),
                ("total_tokens", "total_tokens"),
                ("context_tokens", "context_tokens"),
            ]:
                val = result.get(token_key)
                if val is not None:
                    token_sets.append(f"{field}={int(val)}")

            try:
                # (1) Backstop persist (2026-10-16): the WS exit only wrote ws_delivery_status and dropped the output text.
                #     The output text is already in the "result" field of the result key (both ws_client and the DSH bridge write it),
                #     so here we fill it in only when result_data is empty -- parameterised SQL whose semantics are "fill only when empty":
                #     whatever the agent wrote via agx_finish_task takes precedence and is never overwritten; run_status untouched; forward-only.
                if delivery == "delivered" and isinstance(result_text, str) and result_text:
                    from common.mysql.mysqlservice import executeSql

                    executeSql(
                        "UPDATE agx_task_execution SET result_data=%s "
                        "WHERE task_run_uuid=%s AND (result_data IS NULL OR result_data='')",
                        (result_text, run_uuid),
                        commit=True,
                    )

                # (2) Delivery state update plus automatic close-out of receipt-type tasks
                #   * 2026-10-19 plan B (Feng decision):
                #     a receipt (is_reply_to non-empty) means "this has been handled" notification,
                #     not a work unit -> a successful delivery counts as done, no more waiting for the agent to call agx_finish_task.
                #     Reason: measured, receipts account for 31% of the execution quota, get stuck at running / trigger false timeouts,
                #     and make later accounting wrong. Only receipts change; ordinary task behaviour is untouched.
                _started_fix = "started_at=COALESCE(started_at, NOW())"
                _reply_row = None
                _cur_status = ""
                # * 2026-11-05: the second predicate for receipts. See the _is_receipt_row comment below.
                _reply_sent = False
                try:
                    _rr = query_mysql(
                        f"SELECT is_reply_to, run_status, reply_sent_at FROM agx_task_execution WHERE task_run_uuid='{run_uuid}'"
                    )
                    if _rr:
                        _reply_row = (_rr[0].get("is_reply_to") or "").strip()
                        _cur_status = (_rr[0].get("run_status") or "").strip()
                        _reply_sent = bool(_rr[0].get("reply_sent_at"))
                except Exception:
                    _reply_row = None

                # Receipt, delivered successfully, still running -> close out automatically.
                # Anything a human set to cancelled or already closed out is never overwritten (so manual intervention is not clobbered by the system).
                _is_receipt = bool(_reply_row) and delivery == "delivered" and _cur_status == "running"
                # * 2026-10-28: the second of the three acknowledgement axes -- the "delivery confirmed at" instant.
                #   Difference from _is_receipt: _is_receipt also requires run_status==running (used for auto close-out),
                #   whereas this mark only asks "is this a receipt row", so an already-terminal receipt can still get its delivery instant filled in.
                # * 2026-11-05 fix: the second predicate adds reply_sent_at.
                #   Root cause: since 2026-10-29 a receipt no longer creates a new execution; the
                #   receipt event lands on the **original execution row** and is distinguished by msg_type=receipt.
                #   So is_reply_to is always empty for a receipt -- while _is_receipt_row still only looked at it,
                #   and therefore the **middle axis** of "sent -> delivered -> read", reply_delivered_at,
                #   could never be written. Measured on three receipts: reply_sent_at set, reply_read_at
                #   set, reply_delivered_at all NULL -- the chain was broken in the middle.
                #   The predicate is reply_sent_at rather than a join on the event table: it is the direct mark that
                #   "this row sent a receipt", one query suffices, no need to scan another table for timing statistics.
                _is_receipt_row = bool(_reply_row) or _reply_sent
                # * 2026-10-25 DH-065 fix: an ordinary task that is not delivered -> must reach a terminal state.
                #   Before the fix: only the delivered branch wrote run_status; error/timeout/pending branches
                #   had _status_sql with no run_status at all -> a business-failed task was stuck at running forever
                #   (measured on 188: status=failed written into the result key, the key consumed, DB still running).
                #   After the fix: ordinary task (not a receipt) + delivery unsuccessful + still running -> mark failed.
                #   Only running is touched: anything already succeeded/failed/cancelled is left alone, manual intervention is not overwritten.
                #   (warning) The timeout-branch-already-succeeded case is untouched: those successes were written by the
                #   agent itself via agx_finish_task; _cur_status is no longer running, so they are naturally unaffected.
                _is_failed_delivery = (
                    not _is_receipt and delivery != "delivered" and _cur_status == "running"
                )
                if _is_receipt:
                    # Receipt: delivery is completion. finished_at and started_at take the same instant (a notification has no execution duration)
                    _status_sql = (
                        "run_status='succeeded', "
                        "started_at=COALESCE(started_at, NOW()), "
                        "finished_at=NOW()"
                    )
                elif _is_failed_delivery:
                    # * 2026-10-27 plan B (Feng decision): on a delivery/spawn exception, write **neither run_status nor started_at**.
                    #   started_at is the axis meaning "the agent itself declared it started" (lazily written by agx_get_execution:
                    #   caller==agent_id, started_at empty, run_status=='running').
                    #   The moment the daemon fills in started_at here, it disguises "not delivered" as "already started",
                    #   and plan B's predicate (started_at IS NULL = not started yet) breaks that same day.
                    #   So this branch leaves only a delivery trace and touches neither business axis.
                    _status_sql = ""
                else:
                    # * 2026-10-19 correction: an ordinary task **must not blindly overwrite finished_at**.
                    #   The agent already wrote the real completion instant in agx_finish_task; WS delivery is only a "delivery confirmation",
                    #   and writing NOW() here again pushes the completion time later (measured: 11:10:49 became 11:11:03,
                    #   adding 14 phantom seconds and corrupting duration and statistics).
                    #   -> COALESCE: keep an existing value, fill in a backstop only when the agent did not write one.
                    # * Feng, 2026-10-27: the daemon does not write started_at.
                    #   started_at can only be declared by the agent itself (lazily written by agx_get_execution) --
                    #   filling NOW() here amounts to disguising "delivered at" as "started at",
                    #   measured at 12:08:31 to write started_at of 4 reviews in the same second as their completion,
                    #   making duration always 0. Better to leave NULL (honest) than to write a fake value.
                    _status_sql = "finished_at=COALESCE(finished_at, NOW())"

                # * 2026-10-27: the SET clause is assembled on demand -- _status_sql may be empty
                #   (the delivery-failure branch writes neither run_status nor started_at, so there is no column to assemble)
                _set_parts = [f"ws_delivery_status='{safe_delivery}'"]
                _set_parts.extend(token_sets)
                if _status_sql:
                    _set_parts.append(_status_sql)
                # * 2026-10-28: receipt delivery confirmation -- write the second timestamp (sent -> delivered -> read)
                if _is_receipt_row and delivery == "delivered":
                    _set_parts.append("reply_delivered_at=COALESCE(reply_delivered_at, NOW())")
                    _set_parts.append("ack_state=IF(reply_read=1, 'read', 'delivered')")
                query_mysql(
                    f"UPDATE agx_task_execution SET {', '.join(_set_parts)} WHERE task_run_uuid='{run_uuid}'"
                )
                # Sync the delivery state of the receipt event (silently no-op when the event table has no matching row)
                if _is_receipt_row and delivery == "delivered":
                    try:
                        query_mysql(
                            "UPDATE agx_task_execution_event SET delivery_state='delivered', "
                            f"delivered_at=NOW() WHERE task_run_uuid='{run_uuid}' "
                            "AND event_type='receipt' AND delivery_state<>'delivered'"
                        )
                        # Note: refresh_execution_cache is called uniformly by the "sync Redis cache + status index" block below
                        # (both receipts and ordinary tasks go through it), so no extra DB read here.
                    except Exception as _ee:
                        log(f" 回执事件投递状态回填失败 | {run_uuid}: {_ee}", "WARN")

                # Sync the Redis cache and the status index (needed by both receipt close-out and ordinary tasks)
                #   * 2026-10-19 correction: previously only receipts were synced, so after the daemon changed the DB for an ordinary task
                #   the cache still held the old value (measured DB finished_at=11:11:03 vs cache=11:10:49, 14s apart).
                #   Now the actual persisted value is always read back from the DB, never inferred from memory.
                #   * 2026-10-25 DH-066 fix: index sync is now driven by the *real DB terminal state* rather than only the
                #   _is_receipt branch -- the old code only did zrem/zadd for receipts, while ordinary tasks (including failures)
                #   were never synced -> the running index kept accumulating finished tasks (measured 4/4 wrong).
                try:
                    _back = query_mysql(
                        "SELECT run_status, started_at, finished_at, ws_delivery_status "
                        f"FROM agx_task_execution WHERE task_run_uuid='{run_uuid}'"
                    )
                    if _back:
                        # 2026-10-22: 4-field hset -> read the whole row back and write all fields (do not build a partial entity)
                        refresh_execution_cache(run_uuid)
                        _db_status = (_back[0].get("run_status") or "").strip()
                        # Sync the index from the DB (no memory inference, avoiding branch differences such as COALESCE)
                        if _db_status and _db_status != "running":
                            _sync_exec_status_index(run_uuid, _db_status)
                    if _is_receipt:
                        log(f" 回执自动收口 | {run_uuid} | 投递即完成（is_reply_to={_reply_row[:20]}）")
                    elif _is_failed_delivery:
                        # 2026-10-27: no longer records a terminal state; wording now matches actual behaviour (so logs do not mislead diagnosis)
                        log(f" 投递失败 | {run_uuid} | delivery={delivery} → 保持 running（不判业务失败）")
                except Exception as _e:
                    log(f" 缓存/索引同步失败 | {run_uuid}: {_e}", "WARN")
            except Exception as e:
                log(f"更新送达状态/兜底落库失败 | {run_uuid}: {e}", "WARN")
                # The two SQL statements did not both succeed -> keep the key for the next retry (do not delete, so output is not lost)
                continue

            r.delete(key)
            processed += 1
            log(f" WS 送达状态 | {run_uuid} | {delivery} | {key}")

        except json.JSONDecodeError:
            r.delete(key)
            processed += 1
            log(f" WS 结果清理 (无效JSON) | {key}")
        except Exception as e:
            log(f"处理结果 {key} 异常: {e}", "WARN")
            with contextlib.suppress(Exception):
                r.delete(key)

    return processed


# -- Signal handling ----------------------------------------------


def shutdown_handler(signum, frame):
    """Signal handling: graceful exit, making sure child processes are cleaned up."""
    sig_names = {signal.SIGTERM: "SIGTERM", signal.SIGINT: "SIGINT"}
    if hasattr(signal, "SIGHUP"):
        sig_names[signal.SIGHUP] = "SIGHUP"
    sig_name = sig_names.get(signum, str(signum))
    log(f"收到信号 {sig_name},准备优雅退出...", "WARN")
    stop_ws_client()
    stop_dsh_supervisor()
    stop_state_relay()
    stop_watchdog()
    raise SystemExit(0)


def _atexit_cleanup():
    """atexit backstop cleanup: do the best to kill child processes even if the process exits abnormally."""
    stop_ws_client()
    stop_dsh_supervisor()
    stop_state_relay()
    stop_watchdog()


# -- Main loop ----------------------------------------------------


def run_daemon(host_id: str, with_watchdog: bool = True):
    """
    Daemon main loop: poll this machine's worker Redis message queues and activate workers.
"""
    r = build_redis_client()
    log(
        f"Redis config | host={config.redis.get('host')} user={config.redis.get('username')} pwd_len={len(config.redis.get('password', ''))}"
    )

    hostname_short = platform.node().split(".")[0]
    log(f"守护进程启动 | host_id={host_id} | hostname={hostname_short} | poll_interval={POLL_INTERVAL}s")

    # -- Host list this process maintains (2026-11-04 multi-instance rework) --
    #   One machine can run several OpenClaw instances, each with its own host_id and its own set of queues.
    #   With no config.gateways, hosts=[this machine], behaving byte for byte as before the rework.
    hosts = get_daemon_hosts() or [host_id]
    if len(hosts) > 1:
        log(f" 多实例模式：本进程维护 {len(hosts)} 个 host 队列 | hosts={','.join(hosts)}")

    # -- Host heartbeat (2026-10-25 multi-host rework) --
    #   Time-driven, not loop_count driven: the main loop blocks in BLPOP,
    #   so the gap between counts is uneven when busy versus idle and the heartbeat would be irregular.
    #   Write once at startup so a newly deployed host does not wait one period before "coming online".
    _last_heartbeat_ts = 0.0
    try:
        for _h in hosts:
            write_heartbeat(_h)
        _last_heartbeat_ts = time.time()
        log("主机心跳已写入（启动首次）")
    except Exception as e:
        log(f"启动写心跳失败: {e}", "WARN")

    # -- Step 5: dead-letter cleanup at startup --
    for _h in hosts:
        startup_dead_letter_cleanup(_h, r)
    cleanup_orphan_sessions()
    loop_count = 0
    while True:
        try:
            # -- Merge the worker queues of all hosts, remembering which host each queue belongs to --
            #   After taking a message, q2host reverse-maps the host, used to pick the ws queue bucket and target_host_id.
            queues = []
            q2host = {}
            for _h in hosts:
                for _q in get_worker_queues(_h, r):
                    if _q not in q2host:
                        queues.append(_q)
                        q2host[_q] = _h
            if not queues:
                time.sleep(POLL_INTERVAL * 5)
                continue

            # -- P0-1: blocking wait (BLPOP across multiple queues) ------------------
            # Blocks here when there is no message, returns at once when there is one. It returns (queue_key, raw) or None (timeout).
            # The message it took goes straight to the per-queue consumer below (handle it first, then scan the other queues).
            _blpop_hit = None
            try:
                _blpop_hit = r.blpop(queues, timeout=BLOCK_TIMEOUT)
            except Exception as _bp_err:
                log(f"BLPOP 异常: {_bp_err}", "WARN")
            if _blpop_hit is None:
                # Blocking timeout: no new message, still finish this round's periodic maintenance and then keep waiting
                pass

            def _requeue_prefetched():
                """Put back at the head of its queue (LPUSH) the message BLPOP already took but has not processed.

                * Key point: BLPOP "takes" rather than "peeks" -- if a later `continue` due to a full slot
                or back-pressure skips the consumer body, this message would be lost, so it must be LPUSHed
                back onto the queue head before continuing.
"""
                if _blpop_hit is not None:
                    try:
                        r.lpush(_blpop_hit[0], _blpop_hit[1])
                        log("BLPOP 预览消息已回队 | 跳过本轮消费", "WARN")
                    except Exception as _rq_err:
                        log(f"回队失败(消息可能丢失): {_rq_err}", "ERROR")

            # -- Concurrency control: check slots (counted separately per host) --
            #   * Multi-instance rework: it used to be one set of max_slots/running/available for a single host.
            #     Two instances each have their own task_slots_limit and running count, and combining them makes them encroach on each other:
            #     when test_srv_a is full, test_srv_a2 also stalls (and vice versa). Changed to a host_avail dict.
            host_avail = {}
            for _h in hosts:
                _ms = get_task_slots_limit(r, _h)
                if _ms > 0:
                    _run = get_running_task_count(r, _h)
                    host_avail[_h] = max(_ms - _run, 0)
                    if len(hosts) == 1:
                        log(f" 槽位状态 | running={_run}/{_ms} | available={host_avail[_h]}")
                else:
                    host_avail[_h] = 9999  # tmux unavailable, no limit

            # Which host the message BLPOP already took belongs to -- all later routing decisions use it
            _hit_host = q2host.get(_blpop_hit[0], host_id) if _blpop_hit is not None else host_id

            # The host this message belongs to is full -> put it back and retry next round (other hosts are handled by the inner loop)
            if _blpop_hit is not None and host_avail.get(_hit_host, 0) <= 0:
                if len(hosts) > 1:
                    log(f" 槽位已满 | host={_hit_host} | available=0")
                else:
                    log(f" 槽位已满 | running={get_running_task_count(r, _hit_host)}/{get_task_slots_limit(r, _hit_host)}")
                _requeue_prefetched()
                time.sleep(5)
                continue

            # -- WS queue back-pressure: when full, skip this round and leave the message in the agent queue --
            #   With several hosts each host has its own ws queue; take the maximum to decide (pause as soon as any bucket is full).
            ws_queue_len = max(r.llen(ws_queue_for(_h)) for _h in hosts) if hosts else 0
            if ws_queue_len >= MAX_WS_BACKLOG:
                if loop_count % 10 == 0:
                    log(f" WS 队列反压 | 积压={ws_queue_len}/{MAX_WS_BACKLOG} | 暂停消费 agent 队列", "WARN")
                _requeue_prefetched()
                time.sleep(POLL_INTERVAL * 2)
                continue

            processed = 0
            # How many this host already handled this round (slots are counted per host)
            host_processed = {}
            # The one message BLPOP already took is booked first, so it is not taken twice (no second lpop on the same queue)
            _prefetched = {}
            if _blpop_hit is not None:
                _prefetched[_blpop_hit[0]] = _blpop_hit[1]
            for queue_key in queues:
                # * Multi-instance rework: queue -> host mapping. A full slot skips only *that host's* queues,
                #   no more break -- otherwise one full instance would starve the other.
                msg_host = q2host.get(queue_key, host_id)
                if host_processed.get(msg_host, 0) >= host_avail.get(msg_host, 0):
                    continue

                try:
                    if queue_key in _prefetched:
                        raw = _prefetched.pop(queue_key)
                    else:
                        raw = r.lpop(queue_key)
                    if raw is None:
                        continue

                    log(f"收到消息 | queue={queue_key} | msg={raw}")

                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError:
                        log(f"消息解析失败 | queue={queue_key}", "ERROR")
                        continue

                    inner = payload.get("payload", {})
                    command = inner.get("command", "") or payload.get("command", "")
                    # Queue format: agx:queue:{host_id}:agent:{agent_id}; extract the trailing agent_id
                    agent_id = queue_key.rsplit(":agent:", 1)[-1]

                    # Trim the message: the agent only needs task_run_uuid + send_agent + self_session_id,
                    # do not expose other protocol details.
                    # * 2026-10-22 semantics unified: self_session_id is the **receiver's own** session id
                    #   (same source as the session-context block at the top of exec_instruction).
                    #   Here it is only passed through verbatim; receipt routing reads DB agx_task_execution.send_session_id,
                    #   not this field -- zero business reads of it across the repo.
                    task_run_uuid = inner.get("task_run_uuid", "")
                    send_agent = payload.get("send_agent", "")
                    self_session_id = inner.get("self_session_id", "") or ""
                    # * 2026-10-26 (second fix): msg_type must be delivered to the receiver.
                    #   The previous version only pushed is_reply_to into inner, but struct_msg still assembled only 3 fields,
                    #   so the receiver's message had no msg_type at all -- effectively no change.
                    #   Priority:
                    #     (1) inner.msg_type  (agx_core already computed it: dispatch/receipt)
                    #     (2) inner.is_reply_to non-empty -> receipt (compatible with older agx_core)
                    #     (3) default dispatch
                    _mt = (inner.get("msg_type") or "").strip()
                    if not _mt:
                        _mt = msg_type_for(inner.get("is_reply_to") or "")
                    struct_msg = dumps_struct(
                        task_run_uuid,
                        send_agent,
                        self_session_id,
                        msg_type=_mt,
                        resend_attempt=0,
                    )

                    # -- Direct text send (a 2026-10-05 side path; does not affect execute_task/continue_task) --
                    #    Semantics: deliver the instruction text straight into the given session, creating no execution and bypassing struct_msg.
                    if command == "send_text":
                        session_key = inner.get("session_key", "")
                        if not session_key:
                            log(" send_text 缺少 session_key | 丢弃", "WARN")
                            processed += 1
                            host_processed[msg_host] = host_processed.get(msg_host, 0) + 1
                            continue
                        send_text_task = {
                            "run_uuid": task_run_uuid,
                            "agent_id": agent_id,
                            "instruction": inner.get("instruction", ""),
                            "timeout_seconds": 30,
                            "session_key": session_key,
                            "mode": "send_text",
                        }
                        # * 2026-11-17 fix: send_text used to push into the WS queue unconditionally.
                        #   A DSH host has no ws_client consumer -> receipts/alerts were silently dropped
                        #   (evidence run_general_464df3d84dc0: the PM dispatched to a worker successfully and
                        #    the worker closed out, but the receipt ack_state stayed at delivered and
                        #    reply_read=0 -- because the receipt went into a queue nobody reads).
                        _st_type = get_agent_type(agent_id, r)
                        if _st_type == "dsh":
                            r.rpush(dsh_queue_for(msg_host), json.dumps(send_text_task, ensure_ascii=False))
                            log(f" 直接发文本已推送 DSH 队列 | {dsh_queue_for(msg_host)} | session={session_key}")
                        else:
                            r.rpush(ws_queue_for(msg_host), json.dumps(send_text_task, ensure_ascii=False))
                            log(f" 直接发文本已推送 WS 队列 | {ws_queue_for(msg_host)} | session={session_key}")
                        processed += 1
                        host_processed[msg_host] = host_processed.get(msg_host, 0) + 1
                        continue

                    if command in ("execute_task", "continue_task"):
                        instruction = inner.get("instruction", "")

                        # * 2026-11-04 upstream gate: a message without task_run_uuid must not be dispatched.
                        #   Evidence (leftover Redis key agx:ws:result:, ttl=-1, host=test_host_srv,
                        #   session=agent:book_efficient_agent:main): the old code went straight to
                        #   inner.get("task_run_uuid", "") -> an empty string travelled all the way to ws_client,
                        #   the result was written into an empty-suffix key; the daemon scans agx:ws:result:run_*,
                        #   an empty suffix is never scanned -> that task got neither a close-out nor an alert, silently lost.
                        #   send_text creates no execution and is not subject to this gate (that branch precedes this block).
                        if not str(task_run_uuid or "").strip():
                            log(
                                f" ❌ 丢弃无 task_run_uuid 的任务消息 | agent={agent_id} | "
                                f"cmd={command} | queue={queue_key} | 原文={raw[:300]}",
                                "ERROR",
                            )
                            processed += 1
                            host_processed[msg_host] = host_processed.get(msg_host, 0) + 1
                            continue
                        log(f"激活 Worker | agent={agent_id} | task={task_run_uuid}")
                        log(f"  → 指令: {instruction[:100]}")

                        # -- Test mode: record to Redis only, do not actually dispatch --
                        if TEST_MODE:
                            today = datetime.now().strftime("%Y-%m-%d")
                            _test_log(
                                r,
                                f"agx:test:task:{today}",
                                {
                                    "command": command,
                                    "agent_id": agent_id,
                                    "run_uuid": task_run_uuid,
                                    "instruction": instruction[:300],
                                },
                            )
                            log(f" [TEST] 任务已记录 Redis | agx:test:task:{today} | {task_run_uuid}")
                            processed += 1
                            host_processed[msg_host] = host_processed.get(msg_host, 0) + 1
                            continue

                        # -- Choose the execution path by agent_type --
                        agent_type = get_agent_type(agent_id, r)

                        if agent_type == "openclaw":
                            # WS path: push into the WS task queue, handled by agx_ws_client.py
                            ws_task = {
                                "run_uuid": task_run_uuid,
                                "agent_id": agent_id,
                                "instruction": struct_msg,
                                "timeout_seconds": inner.get("timeout_seconds") or int(config.task_timeout),
                                # * 2026-10-25 DH-067: tell ws_client which host this task belongs to,
                                #   so it self-checks before actually executing (the last gate against wrong-host execution).
                                "target_host_id": msg_host,
                            }

                            # Continue-dialogue: carry session_key, telling ws_client to use sessions.send rather than sessions.create
                            if command == "continue_task":
                                session_key = inner.get("session_key", "")
                                if not session_key:
                                    log(f" continue_task 缺少 session_key | {task_run_uuid}", "WARN")
                                    continue
                                ws_task["session_key"] = session_key
                                ws_task["mode"] = "continue"
                                log(
                                    f" 续接任务已推送 WS 队列 | {ws_queue_for(msg_host)} | {task_run_uuid} | session={session_key}"
                                )
                            elif command == "execute_task":
                                session_key = inner.get("session_key", "")
                                if session_key:
                                    ws_task["session_key"] = session_key
                                    ws_task["mode"] = "default"
                                    log(f" 默认对话任务 | {ws_queue_for(msg_host)} | {task_run_uuid} | session={session_key}")
                                else:
                                    log(f" 任务已推送 WS 队列 | {ws_queue_for(msg_host)} | {task_run_uuid}")

                            r.rpush(ws_queue_for(msg_host), json.dumps(ws_task, ensure_ascii=False))
                            _record_dispatch(r, task_run_uuid)
                            processed += 1
                            host_processed[msg_host] = host_processed.get(msg_host, 0) + 1

                        elif agent_type == "dsh":
                            # -- DSH path: push into the DSH queue, handled by agx_dsh_client.py --
                            #    It shares the agx:ws:result result contract with the openclaw branch, but the *payload differs*:
                            #
                            #    openclaw receives struct_msg (a 4-field envelope: task_run_uuid/send_agent/
                            #    self_session_id/msg_type) and ws_client reads exec_instruction from the DB itself
                            #    and then assembles the prompt -- what it needs is a "notification".
                            #
                            #    The DSH bridge has no such step: it hands the instruction to the model as the prompt verbatim.
                            #    So instruction must be passed here (= the merged final execution instruction, containing the session context
                            #    + this instruction + the task description). If struct_msg were passed, the model would only see four
                            #    protocol fields and answer "there is no task content" -- and that is a *false success*:
                            #    it really did answer, it just answered "nothing to do".
                            #    (evidence 2026-11-16 run_general_33cbbb15a9a1)
                            dsh_task = {
                                "run_uuid": task_run_uuid,
                                "agent_id": agent_id,
                                "instruction": instruction,
                                "timeout_seconds": inner.get("timeout_seconds") or int(config.task_timeout),
                            }
                            session_key = inner.get("session_key", "")
                            if session_key:
                                dsh_task["session_key"] = session_key
                                dsh_task["mode"] = "continue" if command == "continue_task" else "default"
                            r.rpush(dsh_queue_for(msg_host), json.dumps(dsh_task, ensure_ascii=False))
                            _record_dispatch(r, task_run_uuid)
                            processed += 1
                            host_processed[msg_host] = host_processed.get(msg_host, 0) + 1
                            log(
                                f" 任务已推送 DSH 队列 | {dsh_queue_for(msg_host)} | {task_run_uuid} "
                                f"| mode={dsh_task.get('mode', 'create')}"
                            )

                        else:
                            # tmux compatibility path (agent_type != openclaw)
                            _mt2 = (inner.get("msg_type") or "").strip()
                            if not _mt2:
                                _mt2 = msg_type_for(inner.get("is_reply_to") or "")
                            struct_msg = dumps_struct(
                                task_run_uuid,
                                send_agent,
                                self_session_id,
                                msg_type=_mt2,
                                resend_attempt=0,
                            )
                            log_dir = os.path.join(WORK_DIR, "logs")
                            os.makedirs(log_dir, exist_ok=True)
                            log_file = os.path.join(log_dir, f"{task_run_uuid}.out")

                            safe_msg = struct_msg.replace("'", "'\\''")

                            # -- Build the launch command per agent_type --
                            if agent_type == "claude_code":
                                shell_cmd = f"cd {WORK_DIR} && claude --print --permission-mode bypassPermissions --message '{safe_msg}' > {log_file} 2>&1"
                            else:
                                # Default / openclaw fallback path
                                shell_cmd = f"cd {WORK_DIR} && openclaw agent --agent {agent_id} --message '{safe_msg}' > {log_file} 2>&1"

                            tmux_cmd = f'tmux new-session -d -s {task_run_uuid} "{shell_cmd}"'

                            log("=" * 60)
                            log(" 启动子代理 tmux 会话:")
                            log("")
                            log(f"  {tmux_cmd}")
                            log("")
                            log(f"  session: {task_run_uuid}")
                            log(f"  日志:    {log_file}")
                            log("=" * 60)

                            exit_code = os.system(tmux_cmd)
                            if exit_code == 0:
                                log(f" 子代理已启动 | session={task_run_uuid}")
                                processed += 1
                                host_processed[msg_host] = host_processed.get(msg_host, 0) + 1
                            else:
                                log(f" 子代理启动失败 | exit_code={exit_code}", "ERROR")

                    elif command == "shutdown":
                        log(f"关闭 Worker | agent={agent_id}", "WARN")

                    elif command == "stop_daemon":
                        log("收到停止指令", "WARN")
                        stop_ws_client()
                        stop_dsh_supervisor()
                        stop_watchdog()
                        return

                    else:
                        log(f"未知命令: {command}", "WARN")

                except Exception as e:
                    log(f"队列 {queue_key} 异常: {e}", "ERROR")
                    traceback.print_exc()

            # -- End of round: any prefetched BLPOP message left unconsumed -> put it back, it must not be lost --
            #   The old implementation relied on a single-host `break` to requeue immediately inside the loop; after switching to
            #   multi-host per-queue `continue`, a full slot or an exception can leave a prefetched message to the very end, hence this backstop.
            if _prefetched:
                for _qk, _qraw in _prefetched.items():
                    try:
                        r.lpush(_qk, _qraw)
                    except Exception as _rq_err:
                        log(f"回队失败(消息可能丢失): {_rq_err}", "ERROR")
                _prefetched.clear()

            # -- Check WS task results every 5 rounds --
            # -- Check watchdog/WS client liveness and sync active_task_count every 10 rounds --
            loop_count += 1
            if loop_count % 5 == 0 and not TEST_MODE:
                for _h in hosts:
                    ws_done = check_ws_results(_h, r)
                    if ws_done > 0:
                        log(f" 本轮收集 {ws_done} 个 WS 任务结果 | host={_h}")
            if loop_count % 10 == 0:
                if with_watchdog:
                    check_watchdog()
                check_ws_client()
                check_dsh_supervisor()
                check_state_relay()
                for _h in hosts:
                    sync_active_task_count(_h, r)

            # -- Host heartbeat: time-driven (default 30s), not tied to loop_count --
            if HEARTBEAT_INTERVAL > 0 and (time.time() - _last_heartbeat_ts) >= HEARTBEAT_INTERVAL:
                for _h in hosts:
                    write_heartbeat(_h)
                _last_heartbeat_ts = time.time()

            # P0-1: no more sleep(POLL_INTERVAL) polling -- the next round blocks in BLPOP.
            # Only when this round took no message at all (BLPOP timeout) is there also no need for an extra sleep.
            # Keep a tiny sleep to prevent extreme spinning (e.g. a busy loop caused by the queue list racing BLPOP).
            if _blpop_hit is None and processed == 0:
                time.sleep(0.05)

        except SystemExit:
            log("守护进程退出")
            stop_ws_client()
            stop_dsh_supervisor()
            stop_watchdog()
            break
        except KeyboardInterrupt:
            log("收到 Ctrl+C", "WARN")
            stop_ws_client()
            stop_dsh_supervisor()
            stop_watchdog()
            break
        except Exception as e:
            log(f"主循环异常: {e}", "ERROR")
            traceback.print_exc()
            try:
                log(" [🛡️ 安全防线] 正在强行重置 Redis 全局连接池,清理潜在的死连接...", "WARN")
                _GLOBAL_REDIS_POOL.disconnect()
            except Exception as pool_err:
                log(f"重置连接池失败: {pool_err}", "WARN")

            time.sleep(POLL_INTERVAL * 3)


# -- Entry point --------------------------------------------------


def main():
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, shutdown_handler)
    atexit.register(_atexit_cleanup)

    # -- Run mode --
    with_watchdog = True  # core mode by default
    for arg in sys.argv[1:]:
        if arg == "--host":
            with_watchdog = False
        elif arg == "--core":
            with_watchdog = True

    host_id = get_host_id()
    if not host_id:
        log("无法识别本机 host_id,请确认 env.sh 中已设置 AGX_HOST_ID", "FATAL")
        sys.exit(1)

    network_id = get_network_id()
    if not network_id:
        log("未设置 AGX_NETWORK_ID,将使用环境变量或默认值连接", "WARN")

    mode_label = "core(daemon + watchdog + ws)" if with_watchdog else "host(daemon + ws)"
    log(f"本机 host_id={host_id}  network_id={network_id or '(未设置)'}  mode={mode_label}  test_mode={TEST_MODE}")

    # -- Test mode: clean up old WS result keys so leftovers do not mislead --
    # * 2026-10-26 DH-070: the original implementation deleted everything matched by `keys("agx:ws:result:run_*")`;
    #   in a multi-host topology, if any host turned on test_mode by mistake it would delete execution results of *other hosts*
    #   -> the results are lost forever (the WS result key is the only source ws_client writes back to the DB).
    #   Split into two steps:
    #     (1) delete only this host's keys (new naming agx:ws:result:{host_id}:run_*)
    #     (2) for the old naming (no host segment) delete only keys belonging to *this host's own executions*
    #   -- neither path dares to do an indiscriminate bulk delete again.
    if TEST_MODE:
        try:
            import redis as _rd

            _rt = _rd.Redis(
                host=config.redis.host,
                port=int(config.redis.port),
                db=int(config.redis.db),
                username=config.redis.username or None,
                password=config.redis.password or None,
                decode_responses=True,
            )
            deleted = 0

            # (1) new naming scoped to this host: delete directly
            if host_id:
                for k in _rt.keys(f"agx:ws:result:{host_id}:run_*"):
                    _rt.delete(k)
                    deleted += 1

            # (2) old naming without a host segment: delete only keys of *this machine's executions*
            #     Basis: run_uuid can be traced back to its owner via agx_task_execution.host_id
            legacy = _rt.keys("agx:ws:result:run_*")
            if legacy:
                own = 0
                for k in legacy:
                    run_uuid = k.rsplit(":", 1)[-1] if ":" in k else ""
                    if not run_uuid or not host_id:
                        continue
                    try:
                        rows = _get_mysql_rows(
                            f"SELECT host_id FROM agx_task_execution "
                            f"WHERE task_run_uuid='{_escape(run_uuid)}' LIMIT 1"
                        )
                    except Exception:
                        continue
                    # Delete only keys owned by this machine, or orphan keys with no DB record; other hosts' keys are never touched
                    if not rows or (rows[0].get("host_id") or "") == host_id:
                        _rt.delete(k)
                        own += 1
                deleted += own
                if own < len(legacy):
                    log(
                        f" [TEST] 跳过他机 legacy WS 结果 key {len(legacy) - own} 个（保护别机结果）"
                    )

            if deleted:
                log(f" [TEST] 清理 {deleted} 个旧 WS 结果 key（仅本机）")
        except Exception as e:
            log(f" [TEST] 清理旧 WS 结果失败: {e}", "WARN")

    # -- Start the watchdog child process (core mode only) --
    if with_watchdog:
        start_watchdog()
    else:
        log(" host 模式:不启动看门狗")

    # -- Start the WS client child process --
    start_ws_client()

    # -- Start the DSH bridge child process (same lifecycle as ws_client) --
    start_dsh_supervisor()

    # -- Start the session state collector launcher (one collector per config.gateways entry) --
    start_state_relay()

    try:
        run_daemon(host_id, with_watchdog)
    finally:
        stop_ws_client()
        stop_dsh_supervisor()
        stop_state_relay()
        if with_watchdog:
            stop_watchdog()


if __name__ == "__main__":
    main()
