#!/usr/bin/env python3
"""
2026-06-11 15:53:25

agx_ws_client.py -- AgentGeeX OpenClaw WebSocket child process (all-platform, high-resilience hard-timeout defence)

Started by agx_daemon.py through subprocess, it keeps a long-lived connection with the OpenClaw Gateway,
receives tasks from the daemon main process, creates a session over WebSocket RPC and waits for execution
to complete, then passes the result back to the main process.

IPC protocol (over Redis):
  +- Task queue (daemon -> ws_client)
  |   Key:   agx:ws:task:{host_id}
  |   Value: JSON {run_uuid, agent_id, instruction}
  |   Ops:   daemon RPUSH, ws_client BLPOP
  |
  +- Result queue (ws_client -> daemon)
      Key:   agx:ws:result:{run_uuid}
      Value: JSON {run_uuid, status, result, session_key}

Cross-platform safety matrix (fully compatible with Windows / Mac / Linux):
  1. Local proxy scrubbing: automatically mask SOCKS/HTTP proxy interference from env vars, removing the python-socks dependency.
  2. Watchdog hard timeout: a wrapping wait_for interceptor, preventing a hung gateway or a missing terminator from occupying concurrency slots.
  3. Newer Redis compatible: removed the deprecated retry_on_timeout parameter, so logs stay clean and warning-free.
  4. Cross-platform graceful exit: a main exit mechanism specialised for the Windows Proactor loop, eradicating dirty RuntimeError logs on shutdown.
"""

import asyncio
import json
import os
import sys
import time
import traceback

import redis as redis_mod

# Make sure PROJECT_ROOT is on sys.path (config_loader / common etc. live at the project root)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import websockets
from websockets.exceptions import ConnectionClosed

from config_loader import AGENT_TYPE_OPENCLAW, config, gateway_entries

# * 2026-10-05: session_key character-safety check (same source as agx_core._is_safe_session_key)
#   Import it from agx_core first; on import failure fall back to a local equivalent (ws_client is a core bridge
#   and must not crash over an import failure). Only character safety is enforced, not platform structure.
try:
    from agx_mcp.agx_core import _is_safe_session_key
except Exception:  # pragma: no cover - equivalent fallback implementation
    def _is_safe_session_key(session_id) -> bool:
        if session_id is None:
            return False
        sid = str(session_id)
        if not sid or not sid.strip():
            return False
        for ch in sid:
            o = ord(ch)
            if o < 0x20 or o == 0x7F:
                return False
            if o in (0x200B, 0x200C, 0x200D, 0xFEFF, 0x00A0):
                return False
        if any(c.isspace() for c in sid):
            return False
        for bad in ("\u2026", "%"):
            if bad in sid:
                return False
        return True

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

# -- Config (passed in by the daemon through env vars) ----------------------

HOST_ID = os.environ.get("AGX_DAEMON_HOST_ID", config.get("host_id", ""))
# * 2026-11-04: the singular gateway is on the deprecation path (gateways is the config source).
#   It must be accessed safely with .get() here -- _Config has no __getattr__, so on a missing key
#   `config.gateway` raises AttributeError, and this line runs at module import time,
#   meaning "deleting the gateway node from the config = ws_client will not start".
_GW_SINGLE = config.get("gateway") or {}
WS_URL = (_GW_SINGLE.get("ws_url") or "ws://127.0.0.1:18789")
WS_TOKEN = _GW_SINGLE.get("token") or ""

# Redis connection parameters
REDIS_HOST = config.redis.get("host", "127.0.0.1")
REDIS_PORT = int(config.redis.get("port", 6379))
REDIS_DB = int(config.redis.get("db", 0))
REDIS_USERNAME = config.redis.get("username", "default")
REDIS_PASSWORD = config.redis.get("password", "")

# Task queue key
WS_TASK_QUEUE = f"agx:ws:task:{HOST_ID}"

# Reconnect configuration
MAX_RECONNECT_DELAY = int(config.ws.get("reconnect_delay", 60))
RECONNECT_BACKOFF = int(config.ws.get("reconnect_backoff", 2))
RECONNECT_FAIL_THRESHOLD = int(config.ws.get("reconnect_fail_threshold", 3))
INITIAL_BACKOFF = int(config.ws.get("initial_backoff", 2))

# WebSocket / general timeouts
HEALTH_CHECK_INTERVAL = int(config.ws.get("health_interval", 15))
PING_INTERVAL = int(config.ws.get("ping_interval", 20))
PING_TIMEOUT = int(config.ws.get("ping_timeout", 15))
MAX_MSG_SIZE = int(config.ws.get("max_msg_size", 10 * 1024 * 1024))
CALL_TIMEOUT = int(config.ws.get("call_timeout", 30))
# * 2026-10-29 incident fix: a dedicated timeout for register_agent.
#   Root cause: agents.create and the outer asyncio.wait_for were both 30s (CALL_TIMEOUT / hard-coded in MCP),
#   so the outer one fired first and killed the coroutine -> the inner except never got to log -> the callback
#   was lost forever and the execution stuck at running (run_cagent_deploy_ab02c5bcf83d, 13:04:33).
#   The current value 150s < the outer 180s (the timeout_seconds MCP dispatches with), guaranteeing
#   "inner times out first -> log + return failed -> outer settles"; never silent.
REGISTER_CALL_TIMEOUT = int(config.ws.get("register_call_timeout", 150))

# * 2026-11-12 R3: verify the registration result (a lost response is not a failed registration).
#   Root cause: an exception/timeout from agents.create was judged failed, while the gateway may have registered it fine.
#   Evidence: run_cagent_deploy_ab02c5bcf83d (agents.create had succeeded, nobody collected the response)
#            and run_cproject_deploypm_88c07186acc6 (the deploy callback was lost).
#   Fix: before judging failure, verify the real state (WS agents.list, falling back to the local openclaw.json).
#   * The time budget must be smaller than "outer hard timeout 180s - REGISTER_CALL_TIMEOUT 150s = 30s",
#     otherwise verification drags the outer coroutine into being killed by wait_for and the verification is wasted.
VERIFY_BUDGET = int(config.ws.get("verify_budget", 12))
VERIFY_POLL_INTERVAL = float(config.ws.get("verify_poll_interval", 2.0))

# * 2026-11-12 R4: callback delivery must survive a disconnect.
#   Root cause: _send_callback reused the same (possibly dead) connection and only wrote one log line on failure.
#   Consequence: the register_agent callback was lost -> the host agent never learned ->
#   the execution stuck at running until the watchdog timed out, while deployment had actually completed.
#   Fix: (1) wait for the connection to recover before retrying (not give up on a dead connection)
#        (2) retry several times and, on failure, put it into a Redis pending-delivery queue
#        (3) flush it automatically when the connection recovers or during the per-minute sweep
CALLBACK_ATTEMPTS = int(config.ws.get("callback_attempts", 5))
CALLBACK_RETRY_BACKOFF = float(config.ws.get("callback_retry_backoff", 3.0))
CALLBACK_WAIT_RECONNECT = float(config.ws.get("callback_wait_reconnect", 30.0))
CALLBACK_BACKLOG_TTL = int(config.ws.get("callback_backlog_ttl", 1800))
CALLBACK_BACKLOG_MAX = int(config.ws.get("callback_backlog_max", 100))

EVENT_TIMEOUT = int(config.ws.get("event_timeout", 10))
SESSION_RETRIES = int(config.ws.get("session_retries", 10))
TASK_TIMEOUT = int(config.get("task_timeout", 300))  # Default 5-minute hard-timeout interception threshold

# * 2026-11-17 (Feng evidence run_general_daeee48885c2 hanging):
#   `session_busy` is a *soft rejection* meaning "the receiver is temporarily occupied", not a delivery failure.
#   The old implementation returned retryable=True the moment it saw busy in _pre_send_guard,
#   while on the daemon side MAX_DELIVERY_RETRIES=5 with *no backoff between retries* --
#   measured: all 5 landed within about 30 seconds (15:44:51 / 15:45:07 / 15:45:22 ...),
#   so as long as the session stayed busy for half a minute the task was abandoned permanently (the run could
#   never start, and only 18 minutes later did the watchdog raise an L2 alert).
#
#   Fix: **probe again with an in-place backoff**, and only hand it back to the daemon after accumulating PRE_SEND_BUSY_WAIT_TOTAL.
#   Why not a new delayed queue: that needs a Redis ZSET plus a scanner plus migration logic,
#   whereas here we only need to await sleep in the same coroutine -- no blocking of the event loop,
#   and the task already occupies this session slot, so waiting for it to go idle is exactly the point.
#   Coverage duration = daemon retry count x this value, roughly 5 x 120s = 10 minutes.
PRE_SEND_BUSY_WAIT_TOTAL = int(config.ws.get("pre_send_busy_wait_total", 120))
# Initial backoff step, multiplied by 1.5 each time, capped at PRE_SEND_BUSY_WAIT_STEP_MAX
PRE_SEND_BUSY_WAIT_STEP = int(config.ws.get("pre_send_busy_wait_step", 5))
PRE_SEND_BUSY_WAIT_STEP_MAX = int(config.ws.get("pre_send_busy_wait_step_max", 20))

# * 2026-10-25 DH-069: silence probe interval in seconds.
#   The semantics are not "kill it after this much silence" but "after this much silence, ask the gateway whether this run is still alive".
#   Failure is only judged when the run is confirmed finished yet no final was ever received (lost events).
#   Why 60s: measured, the silence of a single tool call or long think can reach minutes, and blind killing would hurt healthy tasks;
#   while the probe itself is cheap (one sessions.describe), and once every 60s is enough to bring "waiting 3600s" down to minutes.
SILENCE_PROBE = int(config.ws.get("silence_probe", 60))

# --- Cross-platform environment adaptation (Windows-specific defences) ---
if sys.platform == "win32":
    # Guarantee that under Windows hosting the stdio pipe is flushed promptly and the encoding does not cause an abort
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # Windows ProactorEventLoop easily raises RuntimeError on exit because of unclosed handles; change the policy or prepare here
    # Keep the default ProactorLoop but implement the final interception in main()

import contextlib

from log_setup import setup_logging

# * DH-069: disconnect sentinel. On WS disconnect, push one into every waiting queue so the waiters fail out at once
#   with "connection closed" semantics instead of hanging silently until the hard timeout (the old code called _session_queues.clear()).
_AO_CONN_CLOSED = {"__ao_conn_closed__": True}

log = setup_logging("WS", log_dir=os.path.join(PROJECT_ROOT, "logs"))


def _write_run_result(r, run_uuid, payload: dict, where: str = "") -> bool:
    """Write the execution result key agx:ws:result:{run_uuid}, and gate an empty run_uuid.

    * 2026-11-04 evidence (a malformed key agx:ws:result: with ttl=-1 appeared in Redis, produced by test_srv_b):
      when the task message lacked run_uuid, all 5 write sites in the old code produced "agx:ws:result:".
      The daemon's result scan pattern is agx:ws:result:run_*, which **never matches an empty suffix** --
      so that task got neither a close-out nor a result, with no log and no alert the whole time: a silent loss.
    * Returns True when written; False when intercepted (a missing key is safer than a malformed one).
"""
    ru = str(run_uuid or "").strip()
    if not ru:
        log(
            f" ❌ 拒绝写结果: run_uuid 为空, 避免 agx:ws:result: 畸形键 | 来源={where or '?'}",
            "ERROR",
        )
        return False
    if r is None:
        log(f" ❌ 拒绝写结果: redis 客户端不可用 | run_uuid={ru} | 来源={where or '?'}", "WARN")
        return False
    try:
        r.set(f"agx:ws:result:{ru}", json.dumps(payload, ensure_ascii=False))
        return True
    except Exception as e:
        log(f" ❌ 写结果失败 | run_uuid={ru} | 来源={where or '?'} | {e}", "WARN")
        return False


def build_redis():
    """Build the high-resilience cross-platform Redis client"""

    # return redis_mod.Redis(
    #     host=REDIS_HOST,
    #     port=REDIS_PORT,
    #     db=REDIS_DB,
    #     username=REDIS_USERNAME,
    #     password=REDIS_PASSWORD,
    #     decode_responses=True,
    #     socket_timeout=15,  # allow a larger socket read/write buffer
    #     socket_keepalive=True,  # enable the kernel-level triple TCP probe keepalive (Mac/Linux/Windows)
    #     health_check_interval=10,  # force an application-level PING every 10s, breaking link freezes
    # )

    return redis_mod.Redis(connection_pool=_GLOBAL_REDIS_POOL)


class AgxWsClient:
    """
    OpenClaw Gateway WebSocket client (full hard-timeout defence version).
"""

    def __init__(self, host_id: str = "", ws_url: str = "", ws_token: str = ""):
        # * 2026-11-04 one machine, several instances: one instance per gateway connection.
        #   When not passed, the module-level default applies (old behaviour, single instance).
        self.host_id = (host_id or HOST_ID).strip()
        self.ws_url = (ws_url or WS_URL).strip()
        self.ws_token = ws_token or WS_TOKEN
        self.ws_task_queue = f"agx:ws:task:{self.host_id}"
        self.running_key = f"agx:ws:running:{self.host_id}"
        self.ws = None
        self.req_id = 0
        self.ws_dead = False

        # RPC response routing: call_id -> Future
        self._pending_calls: dict[str, asyncio.Future] = {}

        # Event routing: session_key -> Queue
        # * DH-069: still a single queue, but _session_gates guarantees "only one waiter per session
        #   at a time" (serialisation), and _unregister_queue's ownership check prevents deleting someone else's queue.
        self._session_queues: dict[str, asyncio.Queue] = {}

        # Same-session serial gate: session_key -> [asyncio.Semaphore(1), refcount]
        # The refcount guarantees the entry is not removed while someone is waiting/running, so there is no delete race.
        self._session_gates: dict[str, list] = {}

        # Host sessions awaiting a callback: run_uuid -> host_session_id
        self._pending_host_sessions: dict[str, str] = {}

        # * 2026-11-12 R3: register_agent runs that the fallback verification has already "claimed".
        #   Purpose: a late gateway response must not write an already closed-out run as failed.
        self._claimed_register_runs: list[str] = []

        # * 2026-11-12 R4: sweep throttling for the callback pending-delivery queue
        self._cb_backlog_dirty = False
        self._cb_flushing = False
        self._last_cb_flush = 0.0

        # Strong-reference set for background tasks: a create_task without a retained reference gets GC'd (official asyncio warning)
        self._bg: set[asyncio.Task] = set()

        # Redis client (lazily built; used by the callback circuit breaker to write agx:ws:result)
        self._r = None

        # Background reader
        self._reader_task: asyncio.Task | None = None

        # Concurrency limiting
        max_conc = int(config.ws.get("max_concurrent", 4))
        self._semaphore = asyncio.Semaphore(max_conc)
        log(f"   并发限流: max={max_conc}")

    def _next_id(self) -> str:
        self.req_id += 1
        return f"ws_{self.req_id}"

    def _redis(self):
        """Lazily build the Redis client (used by the callback circuit breaker to write agx:ws:result). Returns None on failure without blocking."""
        if self._r is None:
            try:
                self._r = build_redis()
            except Exception as e:
                log(f" 惰性 Redis 构建失败(回调熔断降级): {e}", "WARN")
                self._r = None
        return self._r

    # -- Connection management --------------------------------------

    async def connect(self) -> bool:
        """Establish the WebSocket connection and complete the handshake auth (with the environment proxy scrubbing mechanism built in)."""
        try:
            # -- Cross-platform scrubbing: scrub local variable interference, breaking the python-socks error curse --
            for key in ["http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"]:
                if key in os.environ:
                    del os.environ[key]

            self.ws = await websockets.connect(
                self.ws_url,
                ping_interval=PING_INTERVAL,
                ping_timeout=PING_TIMEOUT,
                max_size=MAX_MSG_SIZE,
                proxy=None,
            )
            # Receive hello
            await self.ws.recv()

            # Handshake
            await self.ws.send(
                json.dumps(
                    {
                        "type": "req",
                        "id": "conn",
                        "method": "connect",
                        "params": {
                            "minProtocol": 3,
                            "maxProtocol": 4,
                            "client": {
                                "id": "gateway-client",
                                "version": "0.1",
                                "platform": "python",
                                "mode": "backend",
                            },
                            "role": "operator",
                            "scopes": ["operator.read", "operator.write", "operator.admin"],
                            "caps": [],
                            "commands": [],
                            "permissions": {},
                            "auth": {"token": self.ws_token},
                            "locale": "zh-CN",
                            "userAgent": "agx-ws-daemon",
                        },
                    }
                )
            )
            resp = await self.ws.recv()
            data = json.loads(resp)
            if data.get("ok") and data.get("payload", {}).get("type") == "hello-ok":
                log(f" WS 连接成功 | {self.host_id} @ {self.ws_url}")
                # Start the background reader
                self._reader_task = asyncio.create_task(self._reader())
                return True
            else:
                log(f" 握手失败: {data}", "ERROR")
                return False
        except Exception as e:
            log(f" 连接失败: {e}", "ERROR")
            return False

    async def _stop_reader(self):
        """Stop the background reader and clean up waiting tasks."""
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        self._reader_task = None

        for future in self._pending_calls.values():
            if not future.done():
                future.set_exception(ConnectionClosed(None, None))
        self._pending_calls.clear()

        # * DH-069: the old implementation called clear() directly, so waiting queues never received any signal ->
        #   the task could only hang until the 3600s hard timeout (and the counter never returned to zero).
        #   Now push a disconnect sentinel into every waiting queue first, then clear the table (bounded memory, clear semantics).
        _woken = 0
        for _q in list(self._session_queues.values()):
            try:
                _q.put_nowait(dict(_AO_CONN_CLOSED))
                _woken += 1
            except Exception:
                pass
        self._session_queues.clear()
        if _woken:
            log(f" WS 断连：已唤醒 {_woken} 个等待队列（显式失败，不再静默挂死）", "WARN")

    # -- Background message dispatch ----------------------------------

    async def _reader(self):
        """Background coroutine: keep reading messages from the WS and dispatch by type."""
        while True:
            try:
                raw = await self.ws.recv()
                data = json.loads(raw)
                msg_type = data.get("type", "")

                if msg_type == "res":
                    call_id = data.get("id", "")
                    future = self._pending_calls.get(call_id)
                    if future and not future.done():
                        # log(f"WS res 0 detail | id={call_id} | payload={json.dumps(data.get('payload'), ensure_ascii=False)} | top_keys={list(data.keys())} ")
                        # log(f"WS res 1 detail | id={call_id} | payload={json.dumps(data.get('payload'), ensure_ascii=False)} | full_data={json.dumps(data, ensure_ascii=False)[:500]}")
                        log(
                            f"WS res 详情 | id={call_id} | payload={json.dumps(data.get('payload'), ensure_ascii=False)} | top_keys={list(data.keys())} | full_data={json.dumps(data, ensure_ascii=False)[:500]}"
                        )
                        payload = data.get("payload", {})
                        # Parse the rid format: ws_N##run_uuid##task_type
                        parts = call_id.split("##") if "##" in call_id else [call_id]
                        if len(parts) == 3:
                            payload["_ao_run_uuid"] = parts[1]
                            payload["_ao_task_type"] = parts[2]
                            log(f"拆解rid | ws_id={parts[0]} | run_uuid={parts[1]} | task_type={parts[2]}")
                        future.set_result(payload)

                        # -- Callback branch per task_type --
                        if len(parts) == 3:
                            task_type = parts[2]
                            run_uuid = parts[1]
                            host_sid = self._pending_host_sessions.pop(run_uuid, "")
                            log(
                                f"回调分支|task_type={task_type} | run_uuid={run_uuid} | host_sid={'***' if host_sid else '(none)'} | ok={data.get('ok')} | agentId={payload.get('agentId')} | id={payload.get('id')}"
                            )
                            # * 2026-11-05 (XHOST measurement): `already exists` is an idempotent success, not a failure.
                            #   Root cause: when redeploying an already registered agent with force=true, agents.create always returns
                            #   ok=false + INVALID_REQUEST "agent xxx already exists";
                            #   ws_client treated it as a failure -> sent no callback -> the host agent never learned.
                            #   Evidence run_agent_deploy_28b3cae62adf (118/test_srv_a2): the host agent could only
                            #   run `openclaw agents list` itself as a fallback check and report "never received agx_deploy_callback".
                            #   I.e. the first deploy callback works and a redeploy always loses it -- the fixed reproduction path of BUG-A2.
                            _err_obj = data.get("error") or {}
                            _is_already_exists = (
                                not data.get("ok")
                                and isinstance(_err_obj, dict)
                                and "already exists"
                                in str(_err_obj.get("message", "")).lower()
                            )
                            if task_type == "register_agent" and host_sid and (
                                data.get("ok") or _is_already_exists
                            ):
                                # * 2026-11-05 (XHOST measurement): put "what to do next" directly into the
                                #   callback instead of hoping the host agent recalls the task description.
                                #   Evidence: the file was written, registration succeeded, the config was hot-reloaded, and only because
                                #   agx_update_agent(deploy_status=deployed) was missing the agent stayed at
                                #   deploying, rejected by 7 dispatch gates, with zero system alerts.
                                #   An LLM forgets and gets overridden by other instructions -- so put it in front of it every time.
                                if _is_already_exists:
                                    _next_action = (
                                        "该 agent 在 OpenClaw 中【已存在】（本次为重复部署，"
                                        "agents.create 返回 already exists，属幂等结果，不是失败）。\n"
                                        "请先核验两项：\n"
                                        "  ① workspace 路径是否与本次部署一致\n"
                                        "  ② 配置内容是否已按本次部署更新\n"
                                        "核验通过后立即执行（缺一不可）：\n"
                                        "1) agx_update_agent(caller_agent_id=<你自己的 ID>, "
                                        "agent_id=<上面 agent_id>, "
                                        "updates={'deploy_status': 'deployed'})\n"
                                        "2) agx_finish_task(task_run_uuid=<上面 run_uuid>, "
                                        "status='succeeded', summary='部署完成(已存在): <agent_name>')\n"
                                        "⚠️ 若核验发现配置与本次不一致，如实报告差异，不要强行翻状态。\n"
                                        "⚠️ 无论哪种情况都请勿重跑 agx_deploy_agent。"
                                    )
                                else:
                                    _next_action = (
                                        "注册已完成，请立即执行下面两步（缺一不可）：\n"
                                        "1) agx_update_agent(caller_agent_id=<你自己的 ID>, "
                                        "agent_id=<上面 agent_id>, "
                                        "updates={'deploy_status': 'deployed'})  ← 标记已部署\n"
                                        "2) agx_finish_task(task_run_uuid=<上面 run_uuid>, "
                                        "status='succeeded', summary='部署成功: <agent_name>')\n"
                                        "⚠️ 第 1 步不可跳过：deploy_status 若停在 deploying，"
                                        "该 agent 会被 7 处派活门禁拒绝（dispatch/schedule/"
                                        "quick_cmd/plan step），等于部署出来收不到任何任务，"
                                        "而系统不会给任何告警。也请勿改其它字段、勿重跑部署。"
                                    )
                                callback_msg = json.dumps(
                                    {
                                        "type": "agx_deploy_callback",
                                        "status": "succeeded"
                                        if data.get("ok")
                                        else "already_exists",
                                        "run_uuid": run_uuid,
                                        "agent_id": payload.get("agentId", ""),
                                        "agent_name": payload.get("name", ""),
                                        "next_action": _next_action,
                                        "result": payload,
                                    },
                                    ensure_ascii=False,
                                )
                                log(
                                    f" 🚀 触发 sessions.send 回调 | session={host_sid} | agent={payload.get('agentId')}"
                                )
                                asyncio.create_task(self._send_callback(host_sid, callback_msg, run_uuid=run_uuid))
                            elif task_type == "register_agent":
                                # * 2026-11-12 R3: this run was already claimed and closed out by the fallback verification,
                                #   so a late response (especially ok=false / already exists) must not
                                #   write it as failed again -- that would flip "already succeeded" into "failed".
                                if run_uuid in self._claimed_register_runs:
                                    log(
                                        f" 迟到响应已忽略（该 run 已由核验路径结案） | {run_uuid} | "
                                        f"ok={data.get('ok')}"
                                    )
                                    continue
                                # * 2026-10-13 FIX (BUG-A2 plan B): eliminate the silence.
                                #   Old implementation: when no branch matched it dropped the result silently -> the execution stayed running forever
                                #   (BUG-A2 on 2026-09-22; recurrence in pipe006 on 2026-10-13).
                                #   Now: write a WARN log plus a failed result into agx:ws:result, so the failure is traceable.
                                _reason = (
                                    "host_session_id 缺失(调用 agx_deploy_agent 时未传/推导不出 task_run_uuid)"
                                    if not host_sid
                                    else f"Gateway 响应 ok=false (payload={json.dumps(payload, ensure_ascii=False)[:200]})"
                                )
                                log(
                                    f" ⚠️ 部署回调无法投递(静默已消除) | run_uuid={run_uuid} | "
                                    f"agentId={payload.get('agentId')} | 原因={_reason}",
                                    "WARN",
                                )
                                _write_run_result(
                                    self._redis(),
                                    run_uuid,
                                    {
                                        "run_uuid": run_uuid,
                                        "status": "failed",
                                        "result": f"部署回调无法投递: {_reason}",
                                    },
                                    where="deploy_callback",
                                )

                elif msg_type == "event":
                    payload = data.get("payload", {})
                    session_key = payload.get("sessionKey", "")
                    queue = self._session_queues.get(session_key)
                    if queue:
                        await queue.put(data)

            except ConnectionClosed as e:
                self.ws_dead = True
                log(f" WS 连接断开 (_reader): {e}", "WARN")
                for future in self._pending_calls.values():
                    if not future.done():
                        future.set_exception(ConnectionClosed(None, None))
                self._pending_calls.clear()
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                err_str = str(e)
                if "Timeout" in err_str or "timeout" in err_str.lower():
                    self.ws_dead = True
                    log(f"WS 读取超时，标记断连 (_reader): {e}", "WARN")
                    break
                log(f"_reader 异常: {e}", "WARN")

    # -- RPC calls --------------------------------------

    async def call(self, method: str, params: dict = None, timeout: float = CALL_TIMEOUT, tag: str = "") -> dict:
        """
        Send a JSON-RPC request.

        tag: a pass-through marker appended to the rid (format `ws_N##tag`),
             the response echoes it back verbatim via data.get("id"), used to decode business information.
"""
        base_rid = self._next_id()
        rid = f"{base_rid}##{tag}" if tag else base_rid
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending_calls[rid] = future

        try:
            await asyncio.wait_for(
                self.ws.send(
                    json.dumps(
                        {
                            "type": "req",
                            "id": rid,
                            "method": method,
                            "params": params or {},
                        }
                    )
                ),
                timeout=timeout,
            )
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except TimeoutError:
            self.ws_dead = True
            raise TimeoutError(f"RPC {method} 超时 ({timeout}s)") from None
        except ConnectionClosed:
            self.ws_dead = True
            raise
        except OSError as e:
            self.ws_dead = True
            raise ConnectionClosed(None, None) from e
        finally:
            self._pending_calls.pop(rid, None)

    def _spawn(self, coro, where: str = "") -> None:
        """Throw a coroutine into the background safely (keep a strong reference so the GC does not swallow it)."""
        try:
            t = asyncio.create_task(coro, name=f"bg:{where or 'task'}")
        except Exception as e:
            log(f" 后台任务创建失败 | {where} | {e}", "ERROR")
            return
        self._bg.add(t)
        t.add_done_callback(self._bg.discard)

    @staticmethod
    def _failure_meta(outcome: dict, ws_dead: bool) -> tuple[bool, str]:
        """Machine-readable signal of a failure result: (retryable, reason_code).

        * 2026-11-12 (R5, Feng picked plan A): no longer judge "should we retry" from the wording.
          Root cause: the daemon's _is_transient_delivery_error only recognises English keywords,
          while this file wrote "WS disconnected, no final received" -- not one matches
          -> judged a permanent error, no retry -> the task hung until the watchdog timeout, while the connection recovered a few seconds later.
          Fix: ws_client writes "is it retryable" as a field and the daemon reads the field directly.
          The keyword table remains only as a fallback for older clients / the DSH bridge.

        Semantics (at-least-once): a retryable=True redelivery means executing once more,
        so a half-done task may be executed again -- that is inherent to the existing retry mechanism,
        not introduced here; the frequency ceiling is controlled by the daemon's MAX_DELIVERY_RETRIES.
"""
        if (outcome or {}).get("error"):
            return False, "agent_error"  # An error reported by the agent itself: a redelivery gives the same result
        if (outcome or {}).get("silence_lost"):
            return True, "event_lost"  # The run already finished but the final was lost -> a redelivery can get the result back
        if ws_dead:
            return True, "ws_disconnected"  # The connection dropped -> reconnect and redeliver
        return False, "no_final_timeout"  # Pure timeout: the daemon has a dedicated timeout branch

    @staticmethod
    def _has_usable_final(outcome: dict) -> bool:
        """Arriving at a final frame is not enough; it must have content.

        * 2026-11-12 DH-050 patch: a 0-character final is a false success.
          118 measurement: pushing a second message into a running session -> the gateway rejects it (changed while
          starting work) -> a final arrives but its content is empty. The old implementation looked only at
          final_received and recorded succeeded -> succeeded in the DB with an empty result,
          while that run had actually done nothing.
"""
        if not (outcome or {}).get("final_received"):
            return False
        return bool(str((outcome or {}).get("result_text") or "").strip())

    @staticmethod
    def _empty_final_retry(outcome: dict) -> tuple[bool, str]:
        """Retryability of an empty final -- depends on whether this run actually ran.

        * Why not always redeliver and not always give up (2026-11-12):
          the ultimate source of truth for the conclusion is the agx_finish_task the agent itself calls (writes the DB directly, not via WS),
          so the WS final text only says "what this message said", not "whether the work got done".
          - saw_ours=False (the run produced no event at all = never started)
            -> nothing was executed, redelivery is safe -> retryable=True (so the queued one can eventually run)
          - saw_ours=True (already started, only the final content is empty)
            -> it may really have done work, redelivery = duplicate execution -> retryable=False
"""
        if (outcome or {}).get("saw_ours"):
            return False, "empty_final"
        return True, "empty_final_not_started"

    async def _gate_retry(
        self, session_key: str, run_id: str, retryable: bool, code: str
    ) -> tuple[bool, str]:
        """Redelivery gate (DH-070): only allow a redelivery after a liveness probe.

        * Why this step is mandatory (118 drill measurement, 2026-11-12):
          after a disconnect the original run is often **still alive** -- a gateway restart does not kill the run, the agent keeps
          running in the background and calls agx_finish_task itself to write the DB (drill-1 / drill-3 both did).
          Redelivering then means pushing another message into a running session -> `Session ... changed while
          starting work` -> the new delivery ends with a 0-character final, saving nothing.

          Control evidence: 2 concurrent messages on the same session both broke; 4 non-concurrent ones all worked (zero exceptions).

        Decision:
          - this run is still in activeRunIds -> retryable=False, code=own_run_active
            (it will finish itself; a redelivery only collides)
          - cannot probe (unreachable / describe failed) -> **keep the original decision**, add no new blocking
            (better to keep the old behaviour than to give up retrying forever because the probe failed)
"""
        if not retryable:
            return retryable, code
        if self.ws is None or self.ws_dead:
            # * Never "wait for reconnect" inside the failure path (measured pitfall 2026-11-12):
            #   the main loop's connection logic is "disconnected -> wait for all active tasks to exit (60s cap) -> then reconnect",
            #   so if this function waited for ws recovery here it would deadlock: the task waits for the reconnect, the reconnect waits for the task to finish.
            #   Measured: entered the gate at 15:04:41 -> at 15:05:37 the main loop logged "wait timed out, force-cancelling 1 remaining task"
            #   -> the task was killed by CancelledError and not even a failure result got written out.
            #   The right thing: return immediately on disconnect (still retryable=True) and leave the liveness probe to *before the next delivery*.
            log(f" 重投闸门：连接已断，跳过探活（留给重投前） | code={code}", "WARN")
            return retryable, code
        try:
            sess = await self.describe_session(session_key)
        except Exception as e:
            log(f" 重投闸门：探活异常，沿用原判断 | {session_key} | {e}", "WARN")
            return retryable, code
        if not isinstance(sess, dict) or sess.get("error"):
            log(f" 重投闸门：探活无结果，沿用原判断 | {session_key}", "WARN")
            return retryable, code
        active_ids = sess.get("activeRunIds") or []
        last_run = sess.get("lastRunId") or ""
        st = str(sess.get("status") or "").lower()
        looks_active = sess.get("hasActiveRun") is True or st == "running"

        # (1) This run is clearly in the active list -> it will close itself out, redelivery is forbidden
        if run_id and run_id in active_ids:
            log(
                f" 重投闸门：本 run 仍活跃 → 不重投，等它自己收口 | {run_id} | "
                f"原判={code}",
                "WARN",
            )
            return False, "own_run_active"

        # (2) No activeRunIds but the session is clearly running and lastRunId is this run
        #    -> still "this run is alive", handled the same way
        if run_id and last_run == run_id and looks_active:
            log(
                f" 重投闸门：lastRunId=本 run 且会话在跑 → 不重投 | {run_id} | 原判={code}",
                "WARN",
            )
            return False, "own_run_active"

        # (3) Another run occupies this session -> a redelivery **now** would definitely collide, but later is fine,
        #     so keep retryable=True (let the daemon drain it, do not give up permanently)
        if active_ids or looks_active:
            log(
                f" 重投闸门：会话仍被其他 run 占用 → 稍后重投 | {session_key} | "
                f"active={active_ids} | status={st or '-'}",
                "WARN",
            )
            return True, "session_busy"

        return retryable, code

    async def _wait_ws_alive(self, timeout: float) -> bool:
        """Wait for the connection to recover (returns True at once when already connected)."""
        if self.ws is not None and not self.ws_dead:
            return True
        deadline = time.time() + max(0.0, timeout)
        while time.time() < deadline:
            if self.ws is not None and not self.ws_dead:
                return True
            await asyncio.sleep(0.5)
        return self.ws is not None and not self.ws_dead

    def _cb_backlog_key(self) -> str:
        return f"agx:ws:callback:pending:{self.host_id}"

    def _enqueue_callback(self, session_key: str, message: str, run_uuid: str, reason: str) -> None:
        """When a callback really cannot be sent, write it into the Redis pending-delivery queue (not lost, not silent)."""
        r = self._redis()
        item = json.dumps(
            {
                "session_key": session_key,
                "message": message,
                "run_uuid": run_uuid or "",
                "at": time.time(),
                "host_id": self.host_id,
                "reason": str(reason)[:200],
            },
            ensure_ascii=False,
        )
        if r is None:
            log(
                f" ❌ 回调彻底失败且 Redis 不可用（无法入队） | session={session_key} | run={run_uuid} | {reason}",
                "ERROR",
            )
            return
        try:
            key = self._cb_backlog_key()
            r.lpush(key, item)
            r.ltrim(key, 0, CALLBACK_BACKLOG_MAX - 1)
            r.expire(key, CALLBACK_BACKLOG_TTL)
            self._cb_backlog_dirty = True
            log(
                f" 📥 回调已入待投递队列（连上后自动补投） | session={session_key} | "
                f"run={run_uuid} | 原因={reason}",
                "WARN",
            )
        except Exception as e:
            log(f" ❌ 回调入队失败（回调可能丢失） | session={session_key} | {e}", "ERROR")

    async def _flush_callback_backlog(self, trigger: str = "") -> int:
        """Flush the pending callbacks. LREM one per success; drop entries that report expiry."""
        if self._cb_flushing:
            return 0
        r = self._redis()
        if r is None:
            return 0
        key = self._cb_backlog_key()
        try:
            raw_items = r.lrange(key, 0, -1)
        except Exception as e:
            log(f" 回调队列读取失败 | {e}", "WARN")
            return 0
        if not raw_items:
            self._cb_backlog_dirty = False
            return 0
        self._cb_flushing = True
        sent = 0
        try:
            log(f" 🔄 开始补投历史回调 | 待投 {len(raw_items)} 条 | trigger={trigger or '-'}")
            for raw in raw_items:
                try:
                    item = json.loads(raw)
                except Exception:
                    r.lrem(key, 1, raw)  # Dirty data: delete it, do not stall the queue
                    continue
                age = time.time() - float(item.get("at") or 0)
                sk = str(item.get("session_key") or "")
                if age > CALLBACK_BACKLOG_TTL or not sk:
                    r.lrem(key, 1, raw)
                    log(
                        f" ⌛ 丢弃过期/无效回调 | age={int(age)}s | session={sk} | "
                        f"run={item.get('run_uuid', '')}",
                        "ERROR",
                    )
                    continue
                if not await self._wait_ws_alive(5):
                    break
                try:
                    await self.call(
                        "sessions.send",
                        {"key": sk, "message": str(item.get("message") or "")},
                        timeout=CALL_TIMEOUT,
                    )
                except Exception as e:
                    log(f" 补投仍然失败，留待下次 | session={sk} | {e}", "WARN")
                    break
                r.lrem(key, 1, raw)
                sent += 1
                log(f" ✅ 补投成功 | session={sk} | run={item.get('run_uuid', '')}")
            self._cb_backlog_dirty = bool(r.llen(key))
        finally:
            self._cb_flushing = False
        if sent:
            log(f" 🔄 补投完成 | 成功 {sent} 条")
        return sent

    async def _send_callback(self, session_key: str, message: str, run_uuid: str = "") -> bool:
        """Deliver a callback message to the given session.

        * 2026-11-12 R4 rewrite: the original implementation was "send once on the current connection, and on failure just log".
          But the moment a callback most needs to arrive is exactly when the connection has just dropped (config hot reload / gateway restart) --
          the old implementation gave up precisely when failure was most likely.
          Now: wait for the connection to recover -> retry N times -> if it still fails, put it into the Redis pending-delivery queue,
          flushed by the next reconnect or the per-minute sweep. Better late than silently lost.
        Returns True when delivered.
"""
        last_err = ""
        for attempt in range(1, CALLBACK_ATTEMPTS + 1):
            # Give the first attempt enough time to wait for a reconnect (typically 1~3s), then shorten each time --
            # do not let one callback burn a whole concurrency slot (max_concurrent=16).
            wait_s = CALLBACK_WAIT_RECONNECT if attempt == 1 else 15.0
            if not await self._wait_ws_alive(wait_s):
                last_err = f"等待连接恢复超时({int(wait_s)}s)"
                log(f" ⏳ 回调等待连接超时（第 {attempt} 次） | session={session_key}", "WARN")
                break
            try:
                if attempt == 1:
                    log(f" 📤 _send_callback 开始 | session={session_key} | msg_len={len(message)}")
                await self.call(
                    "sessions.send",
                    {"key": session_key, "message": message},
                    timeout=CALL_TIMEOUT,
                    tag=f"{run_uuid}##callback" if run_uuid else "",
                )
                log(f" ✅ _send_callback 成功 | session={session_key} | 第 {attempt} 次")
                return True
            except Exception as e:
                last_err = str(e)
                # Do not set ws_dead here: call() already sets it uniformly for
                # TimeoutError / ConnectionClosed / OSError;
                # setting it again here would hurt "business errors" and cause pointless reconnects.
                log(
                    f" ⚠️ _send_callback 第 {attempt}/{CALLBACK_ATTEMPTS} 次失败 | "
                    f"session={session_key} | {e}",
                    "WARN" if attempt < CALLBACK_ATTEMPTS else "ERROR",
                )
                if attempt < CALLBACK_ATTEMPTS:
                    await asyncio.sleep(CALLBACK_RETRY_BACKOFF * attempt)
        self._enqueue_callback(session_key, message, run_uuid, last_err or "unknown")
        return False

    # -- Session queries --------------------------------------------------

    async def describe_session(self, session_key: str) -> dict:
        """Query session state (sessions.describe).

        Returned fields: kind, status, model, agentId, totalTokens, contextTokens,
        inputTokens, outputTokens, startedAt, updatedAt, endedAt,
        runtimeMs, estimatedCostUsd, hasActiveRun, abortedLastRun

        * 2026-10-15: session-level model rework -- also expose the three live-model fields:
        modelProvider / model / modelOverrideSource.
        (Previously only status/tokens were logged; model was already in the session dict but not explicitly labelled;
          now the three are normalised into top-level keys for agx_get_session_model to read directly.)
"""
        try:
            result = await self.call(
                "sessions.describe",
                {"key": session_key},
                timeout=CALL_TIMEOUT,
            )
            session = (result or {}).get("session") or {}
            if not session:
                log(
                    f" sessions.describe 返回无 session 字段（会话可能尚未创建）| key={session_key}",
                    "WARN",
                )
                return {"error": "no session in describe response", "session_key": session_key}
            log(
                f" sessions.describe | key={session_key} | "
                f"status={session.get('status')} | tokens={session.get('totalTokens')} | "
                f"model={session.get('model')} | provider={session.get('modelProvider')}"
            )
            # Normalise the three live-model fields (explicitly None when missing, so the caller can decide)
            session.setdefault("model", None)
            session.setdefault("modelProvider", None)
            session.setdefault("modelOverrideSource", None)
            return session
        except Exception as e:
            log(f" sessions.describe 失败 | key={session_key} | {e}", "ERROR")
            return {"error": str(e)}

    async def patch_session_model(self, session_key: str, model: str) -> dict:
        """Set/clear the session-level model (sessions.patch).

        model as a non-empty string -> set an override (e.g. "deepseek/deepseek-v4-flash").
        model as an empty string/None -> clear the override, falling back to the framework default.

        Returns the Gateway result (including entry.modelOverride + modelOverrideSource + resolved).
"""
        try:
            payload = {
                "key": session_key,
                "model": model or None,
            }
            # Include agentId when non-empty (the Gateway needs it to locate sessions.json)
            agent_id = None
            if session_key.startswith("agent:"):
                parts = session_key.split(":")
                if len(parts) >= 2 and parts[1]:
                    agent_id = parts[1]
            if agent_id:
                payload["agentId"] = agent_id
            result = await self.call(
                "sessions.patch",
                payload,
                timeout=CALL_TIMEOUT,
            )
            entry = (result.get("entry") or {}) if isinstance(result, dict) else {}
            log(
                f" sessions.patch | key={session_key} | model={model!r} | "
                f"override={entry.get('model')} | src={entry.get('modelOverrideSource')} | "
                f"resolved={entry.get('resolved')}"
            )
            return result
        except Exception as e:
            log(f" sessions.patch 失败 | key={session_key} | {e}", "ERROR")
            return {"error": str(e)}

    async def patch_session_pin(self, session_key: str, pinned: bool = True) -> dict:
        """Pin/unpin a session (the pinned field of sessions.patch).

        * Feng, 2026-10-18: assistant sessions are pinned by default (no AgentGeeX field added; projected straight onto the platform).
        Platform facts (OpenClaw 2026.8.35, agx_platform_openclaw reconnaissance + measurement):
          - no dedicated sessions.pin; pinning = the `pinned: bool` first-class field of sessions.patch
          - the server writes pinnedAt (SQLite pinned_at); when true it does `pinnedAt ??= now` (idempotent)
          - an archived session cannot be pinned (returns invalid); archiving clears pinnedAt automatically
          - permission operator.write; WS JSON-RPC only, no sessions route on the HTTP side

        Returns the Gateway result (including entry.pinned / entry.pinnedAt).
        On failure returns {"error": ...} -- the caller treats it as "ignore" and it does not affect the main flow.
"""
        try:
            payload = {
                "key": session_key,
                "pinned": bool(pinned),
            }
            # Include agentId when non-empty (the Gateway needs it to locate sessions.json)
            agent_id = None
            if session_key.startswith("agent:"):
                parts = session_key.split(":")
                if len(parts) >= 2 and parts[1]:
                    agent_id = parts[1]
            if agent_id:
                payload["agentId"] = agent_id
            result = await self.call(
                "sessions.patch",
                payload,
                timeout=CALL_TIMEOUT,
            )
            entry = (result.get("entry") or {}) if isinstance(result, dict) else {}
            log(
                f" sessions.patch(pin) | key={session_key} | pinned={pinned} | "
                f"entry.pinned={entry.get('pinned')} | pinnedAt={entry.get('pinnedAt')}"
            )
            return result
        except Exception as e:
            log(f" sessions.patch(pin) 失败 | key={session_key} | {e}", "ERROR")
            return {"error": str(e)}

    async def ensure_session_label(self, session_key: str) -> None:
        """After creating a session, fill in the label (= the last segment of the key). Idempotent; failure does not block; never overwrites an existing non-empty label.

        * Feng, 2026-10-15: AgentGeeX session creation only wrote agx_session.session_name,
          never the OpenClaw-side label -> the Control UI showed only a machine-readable string.
          Contract (label = the segment after the last colon of session_key):
            agent:X:case_agx_dev           -> case_agx_dev
            agent:X:universal:run_univ_xxx -> run_univ_xxx
          Only called additionally when "the session was just created successfully"; a continuation of an existing session never queries or writes.
        Hard requirement: never let an exception escape the function (failure is only a WARN); patch timeout 3s; never overwrite an existing non-empty label.
"""
        try:
            label = session_key.rsplit(":", 1)[-1]
            if not label or len(label) > 512:
                return
            # Read first: skip when a non-empty label already exists (do not overwrite personal names such as 'Cron: xxx' / 'Automation: xxx').
            cur = await self.describe_session(session_key)
            if cur.get("error"):
                # When the read fails, do not risk overwriting (hard requirement: never overwrite an existing non-empty label)
                log(f" ensure_session_label 跳过(describe 失败, 不冒险覆盖) | {session_key}", "WARN")
                return
            if cur.get("label"):
                return
            payload = {"key": session_key, "label": label}
            # agentId derivation: same as patch_session_model (when the key starts with agent:, take parts[1])
            if session_key.startswith("agent:"):
                parts = session_key.split(":")
                if len(parts) >= 2 and parts[1]:
                    payload["agentId"] = parts[1]
            result = await self.call("sessions.patch", payload, timeout=3)
            ok = isinstance(result, dict) and result.get("ok")
            log(f" sessions.patch(label) | key={session_key} | label={label!r} | ok={ok}")
        except Exception as e:
            log(f" ensure_session_label 失败(不阻塞) | {session_key} | {e}", "WARN")

    # -- Task execution ----------------------------------------------

    # -- Identity file protection around registration (2026-11-05) ------------------
    # Root cause: the Gateway's agents.create -> createAgent() -> writeIdentityFile() reads
    #   workspace/IDENTITY.md and merges identity.name back in as `- Name: <agent_id>`
    #   (dist/agent-create-*.js:93-106; it only recognises the four English labels name/theme/emoji/avatar,
    #     while our Chinese bold-label form does not match -> judged a missing field, a line inserted directly).
    #   So the file written from the DB during deployment gets one extra line, disk != DB, and no component restores it.
    #   Evidence: 30+ agents on the Mac had long drifted; iter_pm DB 1B(`#`) / disk 18B (the `#` plus a Name line, 18 bytes total).
    #   Also: when the agent already exists, createAgent returns early and writes nothing -> only the *first registration* gets dirtied,
    #   which explains why a redeployed w1 is clean while a once-deployed w2 is fully dirty.
    # Fix: snapshot the file before registration and restore it verbatim afterwards (delete it if it did not exist).
    #   Touch only this one file; a restore failure only warns and never changes the registration result.
    #   Why not in the host agent callback: that is an LLM, not deterministic; and the callback may never arrive.
    #   Why not in MCP: agx_deploy_agent has already returned *before* the registration happens.
    @staticmethod
    def _snapshot_identity(workspace: str):
        """Snapshot workspace/IDENTITY.md before registration. Returns (path, bytes|None)."""
        path = os.path.join(workspace, "IDENTITY.md")
        try:
            with open(path, "rb") as f:
                return (path, f.read())
        except FileNotFoundError:
            return (path, None)
        except Exception as e:
            log(f"   IDENTITY.md 快照失败(忽略): {e}", "WARN")
            return (None, None)

    @staticmethod
    def _restore_identity(snap) -> None:
        """Restore workspace/IDENTITY.md after registration, undoing the Gateway's `- Name:` write."""
        path, data = snap
        if not path:
            return
        try:
            if data is None:
                if os.path.exists(path):
                    os.remove(path)
                    log("   已清除注册期新增的 IDENTITY.md")
                return
            with open(path, "rb") as f:
                cur = f.read()
            if cur != data:
                with open(path, "wb") as f:
                    f.write(data)
                log(f"   已还原 IDENTITY.md（{len(cur)}B → {len(data)}B，撤销注册期写入）")
        except Exception as e:
            log(f"   IDENTITY.md 还原失败(不影响注册): {e}", "WARN")

    # -- Registration result verification (R3, 2026-11-12) -----------------------------
    #   Root cause: an exception/timeout from agents.create -> judged failed directly. But the gateway side may already have registered it,
    #         with only "nobody collecting the response". Result: the agent exists while the DB sits at pending -- the two disagree.
    #   Evidence: run_cagent_deploy_ab02c5bcf83d (agents.create succeeded, response timed out),
    #        run_cproject_deploypm_88c07186acc6 (callback lost, deployment actually finished).
    #   Fix: verify before judging failure. The two channels complement each other:
    #     (1) WS agents.list (authoritative, but needs a live connection)
    #     (2) local openclaw.json (persisted once the gateway accepts, independent of the connection)
    @staticmethod
    def _openclaw_config_candidates() -> list[str]:
        """Candidate openclaw configs on this machine: the main instance plus each profile instance (~/.openclaw-*/)."""
        raw: list[str] = []
        p = os.environ.get("OPENCLAW_CONFIG_PATH")
        if p:
            raw.append(os.path.expanduser(p))
        raw.append(os.path.expanduser("~/.openclaw/openclaw.json"))
        try:
            import glob as _glob

            raw.extend(sorted(_glob.glob(os.path.expanduser("~/.openclaw-*/openclaw.json"))))
        except Exception:
            pass
        seen: set[str] = set()
        out: list[str] = []
        for x in raw:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    @staticmethod
    def _ws_path_match(a: str, b: str) -> bool:
        """Compare two workspace paths (normalise symlinks first, then compare)."""
        if not a or not b:
            return False
        try:
            return os.path.realpath(os.path.expanduser(a)) == os.path.realpath(
                os.path.expanduser(b)
            )
        except Exception:
            return a == b

    def _verify_from_config_file(self, agent_id: str, workspace: str) -> dict:
        """Fallback check against the local config. Returns {found, checked, workspace, workspace_match, via}."""
        checked = 0
        for path in self._openclaw_config_candidates():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                continue
            entries = (cfg.get("agents") or {}).get("entries") or {}
            if not isinstance(entries, dict):
                continue
            checked += 1
            hit = entries.get(agent_id)
            if not isinstance(hit, dict):
                continue
            _ws = str(hit.get("workspace") or "")
            return {
                "found": True,
                "checked": checked,
                "via": f"config:{path}",
                "workspace": _ws,
                "workspace_match": self._ws_path_match(_ws, workspace),
            }
        return {
            "found": False,
            "checked": checked,
            "via": "",
            "workspace": "",
            "workspace_match": False,
        }

    async def _verify_registered(self, agent_id: str, workspace: str) -> dict:
        """Verify whether the agent really is registered on the gateway.

        Returns state: exists (registered) / absent (confirmed missing) / unknown (both channels failed).
        * Capped at VERIFY_BUDGET seconds and it must be shorter than the margin "outer timeout - REGISTER_CALL_TIMEOUT",
          otherwise the verification itself gets killed by the outer wait_for, which makes it pointless.
"""
        deadline = time.time() + max(3, VERIFY_BUDGET)
        tried_ws = False
        ws_error = ""
        config_checked = 0
        while True:
            # (1) Authoritative source: WS agents.list (only answerable while the connection is alive)
            if self.ws is not None and not self.ws_dead:
                remaining = max(3.0, min(8.0, deadline - time.time()))
                try:
                    res = await self.call("agents.list", {}, timeout=remaining)
                    tried_ws = True
                    ags = res.get("agents") if isinstance(res, dict) else None
                    if isinstance(ags, list):
                        for a in ags:
                            if str(a.get("id") or "") != agent_id:
                                continue
                            _ws = str(a.get("workspace") or "")
                            return {
                                "state": "exists",
                                "via": "agents.list",
                                "workspace": _ws,
                                "workspace_match": self._ws_path_match(_ws, workspace),
                            }
                except Exception as e:
                    ws_error = str(e)

            # (2) Fallback: the local config (the truth is visible even when unreachable)
            hit = self._verify_from_config_file(agent_id, workspace)
            config_checked = max(config_checked, int(hit.get("checked") or 0))
            if hit.get("found"):
                return {
                    "state": "exists",
                    "via": hit.get("via", ""),
                    "workspace": hit.get("workspace", ""),
                    "workspace_match": bool(hit.get("workspace_match")),
                }

            if time.time() >= deadline:
                break
            # Config persistence and the response may be offset; ask several rounds before concluding
            await asyncio.sleep(VERIFY_POLL_INTERVAL)

        if tried_ws or config_checked:
            return {
                "state": "absent",
                "via": "agents.list+config" if tried_ws else "config",
                "workspace": "",
                "workspace_match": False,
                "note": ws_error,
            }
        return {
            "state": "unknown",
            "via": "",
            "workspace": "",
            "workspace_match": False,
            "note": ws_error or "两条核验通道都不可用",
        }

    async def register_agent(
        self, run_uuid: str, agent_id: str, agent_name: str, agent_dir: str, workspace: str
    ) -> dict:
        """Call the Gateway WS agents.create to register the agent.

        run_uuid is passed through the rid into the Gateway response and echoed back, used to correlate the callback.
"""
        log(f" register_agent | agent={agent_id} | name={agent_name} | workspace={workspace}")
        _id_snap = self._snapshot_identity(workspace)
        try:
            result = await self.call(
                "agents.create",
                {
                    "name": agent_id,  # OpenClaw registers with agent_id (a CLI argument), not agent_name (the Chinese display name)
                    "workspace": workspace,
                },
                timeout=REGISTER_CALL_TIMEOUT,
                tag=f"{run_uuid}##register_agent",  # rid = ws_N##run_xxx##register_agent
            )
            log(f"   agents.create 返回: {json.dumps(result, ensure_ascii=False)[:200]}")
            return {
                "run_uuid": run_uuid,
                "status": "succeeded",
                "agent_id": agent_id,
                "agent_name": agent_name,
                "result": result,
            }
        except Exception as e:
            log(f"   agents.create 失败: {e}", "ERROR")
            # * 2026-11-12 R3: a lost response is not a failed registration; verify before concluding.
            verdict = await self._verify_registered(agent_id, workspace)
            _state = verdict.get("state")
            log(
                f"   注册核验 | state={_state} | via={verdict.get('via') or '-'} | "
                f"workspace_match={verdict.get('workspace_match')} | "
                f"note={verdict.get('note', '')}",
                "WARN" if _state != "exists" else "INFO",
            )
            if _state == "exists":
                # Claim this run: a late response (if any) must not trigger another callback / write failed
                self._claimed_register_runs.append(run_uuid)
                if len(self._claimed_register_runs) > 200:
                    del self._claimed_register_runs[:-200]
                _host_sid = self._pending_host_sessions.pop(run_uuid, "")
                if _host_sid:
                    _next_action = (
                        "agents.create 的响应丢了，但经独立核验：该 agent 已在 OpenClaw 中存在。\n"
                        f"核验通道: {verdict.get('via')}；仓库路径匹配: "
                        f"{'是' if verdict.get('workspace_match') else '否'}。\n"
                        "请先核实一项：workspace 路径与本次部署是否一致。\n"
                        "一致 → 立即执行（缺一不可）：\n"
                        "1) agx_update_agent(caller_agent_id=<你自己的 ID>, "
                        "updates={'deploy_status': 'deployed'})\n"
                        "2) agx_finish_task(task_run_uuid=" + run_uuid + ", "
                        "status='succeeded', summary='部署完成(响应丢失、已核验)')\n"
                        "不一致 → 先报差异，不要强行翻状态。\n"
                        "⚠️ 无论哪种，请勿重跑 agx_deploy_agent。"
                    )
                    _cb = json.dumps(
                        {
                            "type": "agx_deploy_callback",
                            "status": "recovered",
                            "run_uuid": run_uuid,
                            "agent_id": agent_id,
                            "agent_name": agent_name,
                            "next_action": _next_action,
                            "verify": verdict,
                            "result": {"error": str(e)[:400]},
                        },
                        ensure_ascii=False,
                    )
                    # No await: a lot of time has already been spent here and it must not eat the outer hard-timeout budget;
                    # when it cannot be sent it automatically goes into the Redis pending-delivery queue (R4).
                    self._spawn(
                        self._send_callback(_host_sid, _cb, run_uuid=run_uuid),
                        where="register_agent_recovered",
                    )
                else:
                    log(f"   回调已由其它途径（迟到响应）送达，本路径不重复回调 | {run_uuid}")
                return {
                    "run_uuid": run_uuid,
                    "status": "succeeded",
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "recovered": True,
                    "verify": verdict,
                    "result": f"响应丢失但核验已注册: {e}",
                }
            return {
                "run_uuid": run_uuid,
                "status": "failed",
                "agent_id": agent_id,
                "agent_name": agent_name,
                "verify": verdict,
                "result": (
                    f"{e}（核验: {_state}"
                    + (f"/{verdict.get('via')}" if verdict.get("via") else "")
                    + "）"
                ),
            }
        finally:
            # Undo the Gateway's write to IDENTITY.md inside agents.create (see the comment above)
            self._restore_identity(_id_snap)

    # -- Same-session serial gate / queue ownership / silence probe (DH-069) --------------
    #
    # Root cause (concluded 2026-10-25):
    #   _session_queues used to be dict[session_key -> a single Queue], so a second waiter on the same session
    #   simply overwrote the first (zero alerts); events were routed only by sessionKey, never by runId;
    #   and finally unconditionally pop(session_key) -> a later task removed someone else's queue.
    #   Result: some tasks received another task's final (a 0-character false success), others never received a final,
    #   hanging all the way to the 3600s hard timeout while pinning agx:ws:running:{host} so the counter never returned to zero.
    #
    # Fix (Feng decided: serialise): same-session serialisation + runId filtering + queue ownership check.
    #   Why serialise rather than "broadcast + runId filtering":
    #     1) the Gateway is already serial/steer for the same session (measured effectiveQueueMode=steer),
    #        two concurrent sends get merged into one run -- in that case broadcast+filter cannot tell them apart either,
    #        because the two instructions simply do not have separate finals.
    #     2) serialising introduces no new event-dispatch semantics; it merely turns "only one waiter per session"
    #        from an implicit assumption into an explicit invariant -- smallest change surface, least likely to add a new race.
    #     3) runId filtering is kept as a second safety net: it stops a late final from the previous round being taken as this round's result.

    async def _acquire_session_slot(self, session_key: str, wait_seconds: float) -> bool:
        """Same-session serial gate: True when acquired, False on queue timeout (the caller judges failure rather than waiting forever)."""
        entry = self._session_gates.get(session_key)
        if entry is None:
            entry = [asyncio.Semaphore(1), 0]
            self._session_gates[session_key] = entry
        entry[1] += 1  # Count first, then await: guarantees the entry is not removed during the wait
        try:
            await asyncio.wait_for(entry[0].acquire(), timeout=max(1.0, float(wait_seconds)))
            return True
        except TimeoutError:
            entry[1] -= 1
            if entry[1] <= 0 and self._session_gates.get(session_key) is entry:
                self._session_gates.pop(session_key, None)
            return False

    def _release_session_slot(self, session_key: str) -> None:
        entry = self._session_gates.get(session_key)
        if not entry:
            return
        entry[0].release()
        entry[1] -= 1
        if entry[1] <= 0 and self._session_gates.get(session_key) is entry:
            self._session_gates.pop(session_key, None)

    def _mark_active_run(self, agent_id: str, run_uuid: str, timeout_seconds: int = 0) -> None:
        """Mark "this agent really was delivered this round", for agx_get_execution to check when it lazily writes started_at.

        Why it is needed (Feng, 2026-10-27, part of plan B)
        ------------------------------------------
        `started_at` is plan B's only predicate (empty = sent but not started, the watchdog stays out).
        But the lazy write in agx_get_execution only checks (1)(2)(3) (caller==agent_id / started_at empty /
        run_status==running) -- it only rules out "someone else queries", not "the agent queries another task of its own".

        Measured contamination: the watchdog sent a "not started" alert -> while handling it the agent happened to call
        agx_get_execution on the original -> caller was itself and the original's agent_id was itself -> matched ->
        a task that never started was written as "started at 09:49:54".

        Gate signal: this method is called after sessions.send returns a runId -- a runId means
        "the gateway really started a round for that session", certain evidence that "the message did reach
        the agent context", and no query action can fake it.

        TTL = timeout_seconds + 1800: the mark disappears naturally after the task finishes/times out, no cleanup needed.
"""
        try:
            r = self._redis()
            if r is None:
                return
            key = f"agx:active:run:{agent_id}"
            try:
                r.hset(key, run_uuid, "1")
            except Exception:
                # The key was written with a non-hash type (should not happen in production; rebuild defensively) --
                # otherwise the mark fails silently, the MCP side thinks "not delivered", and started_at is never written.
                r.delete(key)
                r.hset(key, run_uuid, "1")
            r.expire(key, max(int(timeout_seconds or 0) + 1800, 1800))
        except Exception as e:
            # A failed mark does not block delivery: the MCP side degrades to the old behaviour (allow when Redis is unavailable)
            log(f" 活动标记写入失败 | {run_uuid}: {e}", "WARN")

    def _register_queue(self, session_key: str, run_uuid: str, queue: asyncio.Queue) -> None:
        """Register a wait queue (with an overwrite alert plus logging).

        * DH-069: the overwrite alert is the direct answer to "why did this drag on so long" this time --
          the old implementation was completely silent on overwrite, and nothing abnormal could be seen in the logs.
"""
        prev = self._session_queues.get(session_key)
        if prev is not None and prev is not queue:
            log(
                f" ⚠️ 同会话事件队列被覆盖 | session={session_key} | run={run_uuid} | "
                f"旧队列仍有人在等(串行闸应已阻止，出现即说明有绕过路径)",
                "WARN",
            )
        self._session_queues[session_key] = queue
        self._session_queues[run_uuid] = queue
        log(f" 事件队列注册 | session={session_key} | run={run_uuid}")

    def _unregister_queue(self, session_key: str, run_uuid: str, queue: asyncio.Queue) -> None:
        """Delete only one's own queue (verified by object identity) and leave a trace."""
        for key in (session_key, run_uuid):
            cur = self._session_queues.get(key)
            if cur is queue:
                self._session_queues.pop(key, None)
            elif cur is not None:
                log(f" 队列未回收(归属他人，保留) | key={key} | run={run_uuid}", "WARN")
        log(f" 事件队列回收 | session={session_key} | run={run_uuid}")

    async def _run_state(self, session_key: str, expected_run_id: str, require_started: bool = False) -> str:
        """Judge the current state of the target run: active / finished / unknown.

        * Purpose: to tell "the agent is thinking for a long time (silent but alive)" from "the final was lost".
          active   = activeRunIds contains that runId / hasActiveRun=True / session status=running
          finished = lastRunId is already that runId and no longer active -> the run ended but no final was received;
                     when the gateway returns no lastRunId, fall back to "the session is terminal + this run had started"
          unknown  = not found / still queued -> failure cannot be judged from this

        require_started: whether this run has produced any event. Only a "started" run may infer finished from
        the session terminal state -- otherwise a queued run would be misjudged as lost.
"""
        if not expected_run_id:
            return "unknown"
        try:
            sess = await self.describe_session(session_key)
        except Exception as e:
            log(f" 静默探测失败(忽略) | {session_key} | {e}", "WARN")
            return "unknown"
        if not isinstance(sess, dict) or sess.get("error"):
            return "unknown"
        active_ids = sess.get("activeRunIds") or []
        last_run = sess.get("lastRunId")
        st = str(sess.get("status") or "").lower()
        if expected_run_id in active_ids:
            return "active"
        if sess.get("hasActiveRun") is True and not active_ids:
            return "active"
        if last_run == expected_run_id:
            return "finished"
        if st == "running":
            return "active"
        # When the gateway returns no lastRunId/activeRunIds (measured: describe may carry only status):
        # as long as this run started and the session is already terminal, it finished without giving a final.
        if require_started and st in ("done", "idle", "completed", "aborted", "failed", "error", "stopped"):
            return "finished"
        return "unknown"

    async def _collect_token_info(self, agent_id: str) -> dict:
        try:
            session_resp = await self.call("sessions.list", {"agentId": agent_id, "limit": 1})
            sessions = session_resp.get("sessions", [])
            if not sessions:
                return {}
            s = sessions[0]
            info = {
                "total_tokens": s.get("totalTokens"),
                "context_tokens": s.get("contextTokens"),
                "input_tokens": s.get("inputTokens"),
                "output_tokens": s.get("outputTokens"),
            }
            log(
                f" token | total={info['total_tokens']} ctx={info['context_tokens']} in={info['input_tokens']} out={info['output_tokens']}"
            )
            return info
        except Exception as e:
            log(f" sessions.list 失败，无 token 数据: {e}", "WARN")
            return {}

    async def _await_run_result(
        self,
        queue: asyncio.Queue,
        run_uuid: str,
        expected_run_id: str,
        session_key: str,
        agent_id: str,
        timeout_seconds: int,
        stage: str = "",
    ) -> dict:
        """Wait for this round's final frame. Returns {result_text, final_received, error, silence_lost, skipped}.

        Three hard rules (DH-050 + DH-069):
          1. any event whose runId does not match is skipped (so a late final from the previous round cannot pass as this round's);
          2. after SILENCE_PROBE seconds of silence -> probe whether the run is still alive, and judge failure only
             when "it already finished yet gave no final", never killing a task merely because of "silence";
          3. no final means no success -- the caller records it as failed.
"""
        result_text = ""
        final_received = False
        error_text = ""
        silence_lost = False
        skipped = 0
        saw_ours = False   # Whether this run produced any event at all (to tell "queued" from "lost after starting")
        deadline = time.time() + timeout_seconds
        last_signal = time.time()
        next_probe = time.time() + SILENCE_PROBE

        while time.time() < deadline and not self.ws_dead:
            try:
                data = await asyncio.wait_for(queue.get(), timeout=EVENT_TIMEOUT)
            except TimeoutError:
                data = None

            if data is not None:
                if data.get("__ao_conn_closed__"):
                    # Disconnect sentinel: hand it to the outer layer as ConnectionClosed (no more silent hanging)
                    raise ConnectionClosed(None, None)

                ev = data.get("event", "")
                p = data.get("payload", {}) or {}   # * chat frames are flat: runId/state/message all live at this level
                ev_run = p.get("runId")

                if expected_run_id and ev_run and ev_run != expected_run_id:
                    skipped += 1
                    if skipped <= 3:
                        log(
                            f" 跳过非本轮事件 | {stage}{run_uuid} | 期望 runId={expected_run_id} "
                            f"实到={ev_run} | event={ev} state={p.get('state')}",
                            "WARN",
                        )
                    continue

                saw_ours = True

                if ev == "chat":
                    state = p.get("state", "")
                    if state == "delta":
                        last_signal = time.time()
                        # BUG-R1 fix: protocol v4 chat delta frames carry BOTH
                        # payload.deltaText (incremental) and payload.message.content
                        # (cumulative snapshot). Accumulate the INCREMENTAL source;
                        # some older frames may lack deltaText, so fall back to
                        # message.content (prefix-consistent in the non-replace case).
                        delta_text = p.get("deltaText")
                        if delta_text:
                            result_text += delta_text
                        elif not p.get("replace") and isinstance(p.get("message", {}).get("content"), list):
                            _snap = "".join(
                                c.get("text", "") for c in p["message"]["content"] if isinstance(c, dict)
                            )
                            # cumulative snapshot: only take the not-yet-accumulated tail
                            result_text = (
                                _snap
                                if len(_snap) >= len(result_text) and _snap.startswith(result_text)
                                else result_text + _snap
                            )
                    elif state == "final":
                        # BUG-R1 fix: the final frame's message.content holds the
                        # complete text of this run — use it as the authoritative
                        # result, discarding any delta-accumulation drift.
                        content = p.get("message", {}).get("content", "")
                        if isinstance(content, list):
                            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
                        if content:
                            result_text = content
                        final_received = True
                        log(
                            f" {run_uuid} {stage}完成 ({len(result_text)} 字符) | "
                            f"runId={ev_run or '(none)'} seq={p.get('seq')}"
                        )
                        break
                    elif state in ("error", "aborted"):
                        # * DH-050: a runtime error is no longer treated as a normal result (historic behaviour was succeeded)
                        error_text = (
                            p.get("errorMessage")
                            or p.get("stopReason")
                            or p.get("errorKind")
                            or f"run {state}"
                        )
                        log(f" ❌ {run_uuid} {stage}终止于 {state} | {error_text}", "WARN")
                        break
                elif ev in ("agent", "session.message"):
                    # Other activity signals of the same run (tool calls / status changes) -> it is still alive
                    last_signal = time.time()

            # -- Silence probe --
            now = time.time()
            if now >= next_probe:
                next_probe = now + SILENCE_PROBE
                if now - last_signal >= SILENCE_PROBE:
                    st = await self._run_state(session_key, expected_run_id, require_started=saw_ours)
                    if st == "active":
                        last_signal = now
                        log(f" 静默 {SILENCE_PROBE}s 但 run 仍活跃 | {stage}{run_uuid} | 继续等待")
                    elif st == "finished":
                        silence_lost = True
                        log(
                            f" ❌ run 已结束但未收到 final（事件丢失）| {stage}{run_uuid} | "
                            f"runId={expected_run_id} → 判失败",
                            "WARN",
                        )
                        break
                    else:
                        log(
                            f" 静默 {SILENCE_PROBE}s，run 状态未知(可能仍在排队) | {stage}{run_uuid}",
                            "WARN",
                        )

        return {
            "result_text": result_text,
            "final_received": final_received,
            "error": error_text,
            "silence_lost": silence_lost,
            "skipped": skipped,
            # * 2026-11-12: whether this run produced any event at all.
            #   The retryability of an empty final depends on it: not started -> redelivery is safe (nothing executed);
            #   already started -> do not redeliver (it may really have done work; redelivering would execute twice).
            "saw_ours": saw_ours,
        }

    async def execute_task(
        self, run_uuid: str, agent_id: str, instruction: str, timeout_seconds: int = TASK_TIMEOUT
    ) -> dict:
        session_key = f"agent:{agent_id}:{run_uuid}"

        log(f" 创建 session | key={session_key} | agent={agent_id}")
        log(f"   指令: {instruction[:100]}...")

        if not await self._acquire_session_slot(session_key, timeout_seconds):
            log(f" ❌ 同会话串行排队超时 | session={session_key} | run={run_uuid}", "WARN")
            return {
                "run_uuid": run_uuid,
                "session_key": session_key,
                "status": "failed",
                "result": f"同会话串行排队超时 ({timeout_seconds}s)",
            }

        queue: asyncio.Queue = asyncio.Queue()
        self._register_queue(session_key, run_uuid, queue)

        try:
            max_create_retries = SESSION_RETRIES
            create_resp = {}
            for attempt in range(max_create_retries):
                create_resp = await self.call(
                    "sessions.create",
                    {
                        "key": session_key,
                        "agentId": agent_id,
                        "message": instruction,
                    },
                )
                if create_resp.get("ok"):
                    break
                if attempt < max_create_retries - 1:
                    wait = min(2**attempt, 30)
                    log(f"   sessions.create 失败 (attempt {attempt + 1})，{wait}s 后重试...", "WARN")
                    await asyncio.sleep(wait)
            else:
                return {
                    "run_uuid": run_uuid,
                    "status": "failed",
                    "result": f"session 创建失败(已重试{max_create_retries}次): {create_resp}",
                }

            session_id = create_resp.get("sessionId", "")
            run_id = create_resp.get("runId", "")
            log(f"   sessionId={session_id}  runId={run_id}")
            # * 2026-10-27: mark "this round really was delivered to that agent" --
            #   for the lazy started_at write in agx_get_execution to check against, preventing "one query makes it look started".
            self._mark_active_run(agent_id, run_uuid, timeout_seconds)

            # * 2026-10-15: after a successful sessions.create, fill in the OpenClaw label (= the last segment of the key); idempotent / silent on failure
            await self.ensure_session_label(session_key)

            await self.call("sessions.messages.subscribe", {"key": session_key})

            log(f"⏳ 等待 {run_uuid} 完成...")
            outcome = await self._await_run_result(
                queue, run_uuid, run_id, session_key, agent_id, timeout_seconds
            )
            if self.ws_dead and not outcome["final_received"]:
                log(f" WS 断连，{run_uuid} 提前退出", "WARN")

            if self._has_usable_final(outcome):
                token_info = await self._collect_token_info(agent_id)
                return {
                    "run_uuid": run_uuid,
                    "session_key": session_key,
                    "session_id": session_id,
                    "run_id": run_id,
                    "status": "succeeded",
                    "final_received": True,
                    "result": outcome["result_text"],
                    **token_info,
                }

            # * DH-050 patch (2026-11-12): the final arrived but its **content is empty**, which does not count as success.
            #   118 measurement: pushing a second message into a session that is still running -> the gateway rejects it (changed while
            #   starting work) -> a final arrives but content is 0 characters -> the old logic recorded succeeded (a false success),
            #   while run_status stayed running and nobody could tell afterwards.
            empty_final = bool(outcome.get("final_received"))

            # * DH-050 / DH-069: no final always means failed; never a "false success" again
            reason = (
                "空 final（0 字符）：run 结束了但没产出任何结果"
                if empty_final
                else (
                    outcome["error"]
                    or ("静默失败：run 已结束但未收到 final（事件丢失）" if outcome["silence_lost"] else "")
                    or ("WS 断连，未收到 final" if self.ws_dead else "")
                    or f"未收到 final 帧（等待 {timeout_seconds}s 无终止事件）"
                )
            )
            _retryable, _code = self._failure_meta(outcome, self.ws_dead)
            if empty_final:
                _retryable, _code = self._empty_final_retry(outcome)
                if _retryable is False:
                    log(f" {run_uuid} 空 final 但本 run 已开跑 → 不重投（防重复执行）", "WARN")
            _retryable, _code = await self._gate_retry(session_key, run_id, _retryable, _code)
            log(f" ❌ {run_uuid} 未收到有效 final → failed | code={_code} | {reason}", "WARN")
            return {
                "run_uuid": run_uuid,
                "session_key": session_key,
                "session_id": session_id,
                "run_id": run_id,
                "status": "failed",
                "final_received": False,
                "empty_final": empty_final,
                "result": reason,
                "retryable": _retryable,
                "reason_code": _code,
            }

        except ConnectionClosed as e:
            self.ws_dead = True
            log(f" WS 连接断开 (execute_task): {e}", "WARN")
            raise
        except Exception as e:
            log(f"事件处理异常 | {run_uuid}: {e}", "WARN")
            return {
                "run_uuid": run_uuid,
                "session_key": session_key,
                "status": "failed",
                "result": f"事件处理异常: {e}",
            }
        finally:
            self._unregister_queue(session_key, run_uuid, queue)
            self._release_session_slot(session_key)

    async def _pre_send_guard(self, session_key: str, run_uuid: str) -> dict | None:
        """Pre-delivery liveness probe (the body of DH-070): returns None = may deliver; otherwise returns the "do not deliver yet" failure result.

        * Why the gate sits *before delivery* rather than after a failure (settled by measurement 2026-11-12):
          at the moment of failure the connection happens to be down, so liveness cannot be probed; and a reconnect has to wait for
          active tasks to exit, so waiting for a reconnect inside the failure path deadlocks (measured: killed by the 60s timeout).
          But right before a redelivery actually happens the connection must be alive -- probing here is cheapest and most accurate.

        Decision: the session still has an active run (ours or someone else's) -> do not deliver this round.
          - it is our run still alive -> it will close itself out with finish_task; delivering would only collide
          - it is someone else's run occupying it -> delivering would also be rejected by the gateway (changed while starting work)
          Both cases unify into retryable=True + session_busy, leaving it to the daemon to come back later;
          the daemon side has another backstop "skip the redelivery when run_status is no longer running".

        * 2026-11-17 addition: after judging busy it **no longer returns at once** but backs off and probes again (step from 5s,
        1.5x growth, capped at 20s), handing it back to the daemon only after accumulating PRE_SEND_BUSY_WAIT_TOTAL (default 120s) of busy.
        Reason, measured: the daemon's 5 retries have no backoff and burn the budget within 30 seconds, so as long as the session stays busy
        for half a minute this task can never be delivered (run_general_daeee48885c2 hung 18 minutes until the watchdog raised L2).
"""
        if self.ws is None or self.ws_dead:
            return {
                "run_uuid": run_uuid,
                "session_key": session_key,
                "status": "failed",
                "result": "WS 未连接，暂不投递",
                "retryable": True,
                "reason_code": "ws_disconnected",
            }
        # * 2026-11-17: when busy, probe again with a backoff, and hand it back to the daemon only after accumulating PRE_SEND_BUSY_WAIT_TOTAL.
        deadline = time.monotonic() + max(0, PRE_SEND_BUSY_WAIT_TOTAL)
        waited = 0
        step = max(1, PRE_SEND_BUSY_WAIT_STEP)
        while True:
            try:
                sess = await self.describe_session(session_key)
            except Exception as e:
                log(f" 投递前探活异常（忽略，照常投） | {session_key} | {e}", "WARN")
                return None
            if not isinstance(sess, dict) or sess.get("error") or not sess:
                return None
            active_ids = sess.get("activeRunIds") or []
            st = str(sess.get("status") or "").lower()
            if not (sess.get("hasActiveRun") is True or st == "running" or active_ids):
                if waited:
                    log(
                        f" 投递前探活：等待 {waited}s 后会话已空闲，继续投递 | {session_key}"
                    )
                return None
            # Still busy -- if the total wait budget is not exhausted, back off and probe again
            remain = deadline - time.monotonic()
            if remain <= 0:
                log(
                    f" 投递前探活：会话忙碌超过 {waited}s，交回 daemon 稍后重投 | {session_key} | "
                    f"active={active_ids} | status={st or '-'}",
                    "WARN",
                )
                return {
                    "run_uuid": run_uuid,
                    "session_key": session_key,
                    "status": "failed",
                    "result": (
                        f"会话忙碌超过 {waited}s，暂不投递（避免撞车）: "
                        f"active={active_ids} status={st or '-'}"
                    ),
                    "retryable": True,
                    "reason_code": "session_busy",
                }
            nap = min(step, int(remain) or 1)
            await asyncio.sleep(nap)
            waited += nap
            step = min(int(step * 1.5) or step + 1, PRE_SEND_BUSY_WAIT_STEP_MAX)

    # -- Continue a dialogue --------------------------------------

    async def continue_task(
        self, run_uuid: str, agent_id: str, instruction: str, session_key: str, timeout_seconds: int = TASK_TIMEOUT
    ) -> dict:
        log(f" 续接对话 | session={session_key} | agent={agent_id}")
        log(f"   指令: {instruction[:100]}...")

        # * DH-069: same-session serial gate -- only one waiter per session at a time.
        #   The queueing limit uses this task's own timeout; on timeout it fails explicitly rather than occupying a slot forever.
        if not await self._acquire_session_slot(session_key, timeout_seconds):
            log(f" ❌ 同会话串行排队超时 | session={session_key} | run={run_uuid}", "WARN")
            return {
                "run_uuid": run_uuid,
                "session_key": session_key,
                "status": "failed",
                "result": f"同会话串行排队超时 ({timeout_seconds}s): {session_key}",
            }

        queue: asyncio.Queue = asyncio.Queue()
        self._register_queue(session_key, run_uuid, queue)

        try:
            # * DH-070: probe before delivery -- do not deliver while the session is still busy (it would definitely collide)
            _busy = await self._pre_send_guard(session_key, run_uuid)
            if _busy is not None:
                return _busy

            # * 2026-10-19 fix: sessions.send *implicitly creates* a non-existent session,
            #   while the old implementation only filled in the label after the explicit sessions.create branch -> the implicit path never got a label
            #   (measured agent:agentgeex_agent:case_defect_hunter label=None).
            #   So add one backstop here in advance: idempotent, skipped when a non-empty label exists, silent on failure.
            await self.ensure_session_label(session_key)

            await self.call("sessions.messages.subscribe", {"key": session_key})

            resp = await self.call(
                "sessions.send",
                {
                    "key": session_key,
                    "message": instruction,
                },
            )
            if "runId" not in resp:
                # * 2026-10-05 security fix: an illegal session_key must not be created (prevents dirty values being persisted)
                #   Only character safety is enforced (whitespace / control characters / ... / %), not platform structure
                if not _is_safe_session_key(session_key):
                    log(f" ❌ 拒绝创建非法会话 | session={session_key!r}", "ERROR")
                    return {
                        "run_uuid": run_uuid,
                        "session_key": session_key,
                        "status": "failed",
                        "result": f"invalid session_key (unsafe chars), refuse to create: {session_key!r}",
                    }
                log(f" 会话不存在，自动创建 | session={session_key}")
                _create_resp = await self.call(
                    "sessions.create",
                    {
                        "key": session_key,
                        "agentId": agent_id,
                        "message": instruction,
                    },
                )
                if isinstance(_create_resp, dict) and _create_resp.get("ok"):
                    # * 2026-10-15: after the create fallback branch succeeds, fill in the label (= the last segment of the key)
                    await self.ensure_session_label(session_key)
                # * 2026-10-26 fix for "the same instruction delivered twice":
                #   sessions.create with a message *starts a round immediately* and returns runId (runStarted=true).
                #   The old implementation called sessions.send again after create -> the same instruction was delivered twice,
                #   producing two runIds (the events of the create round were discarded as "not this round's events" -> result lost / 0 characters),
                #   and the agent really executed twice. Evidence run_merge_ch01_ca208a (21:15:39):
                #   create=79aa082c / send=a71c413d, and the local log accumulated 236 "skipped a non-current-round event" lines.
                #   Rule: if create already started this round, adopt its runId directly and never send again.
                if isinstance(_create_resp, dict) and _create_resp.get("runId"):
                    resp = _create_resp
                else:
                    resp = await self.call(
                        "sessions.send",
                        {
                            "key": session_key,
                            "message": instruction,
                        },
                    )
            if "runId" not in resp:
                return {
                    "run_uuid": run_uuid,
                    "session_key": session_key,
                    "status": "failed",
                    "result": f"sessions.send 失败: {resp}",
                }

            run_id = resp.get("runId", "")
            log(f"   runId={run_id}")
            # * 2026-10-27: as above, a continuation is also a real run
            self._mark_active_run(agent_id, run_uuid, timeout_seconds)

            log(f"⏳ 等待续接 {run_uuid} 完成...")
            outcome = await self._await_run_result(
                queue, run_uuid, run_id, session_key, agent_id, timeout_seconds, stage="续接"
            )
            if self.ws_dead and not outcome["final_received"]:
                log(f" WS 断连，{run_uuid} 续接提前退出", "WARN")

            if self._has_usable_final(outcome):
                token_info = await self._collect_token_info(agent_id)
                return {
                    "run_uuid": run_uuid,
                    "session_key": session_key,
                    "run_id": run_id,
                    "status": "succeeded",
                    "final_received": True,
                    "result": outcome["result_text"],
                    **token_info,
                }

            # * DH-050 patch (2026-11-12): same as execute_task, an empty final is not a success.
            empty_final = bool(outcome.get("final_received"))

            # * DH-050 / DH-069: no final always means failed (the old implementation returned succeeded + empty string here)
            reason = (
                "空 final（0 字符）：run 结束了但没产出任何结果"
                if empty_final
                else (
                    outcome["error"]
                    or ("静默失败：run 已结束但未收到 final（事件丢失）" if outcome["silence_lost"] else "")
                    or ("WS 断连，未收到 final" if self.ws_dead else "")
                    or f"未收到 final 帧（等待 {timeout_seconds}s 无终止事件）"
                )
            )
            _retryable, _code = self._failure_meta(outcome, self.ws_dead)
            if empty_final:
                _retryable, _code = self._empty_final_retry(outcome)
                if _retryable is False:
                    log(f" {run_uuid} 空 final 但本 run 已开跑 → 不重投（防重复执行）", "WARN")
            _retryable, _code = await self._gate_retry(session_key, run_id, _retryable, _code)
            log(f" ❌ {run_uuid} 续接未收到有效 final → failed | code={_code} | {reason}", "WARN")
            return {
                "run_uuid": run_uuid,
                "session_key": session_key,
                "run_id": run_id,
                "status": "failed",
                "final_received": False,
                "empty_final": empty_final,
                "result": reason,
                "retryable": _retryable,
                "reason_code": _code,
            }

        except ConnectionClosed as e:
            self.ws_dead = True
            log(f" WS 连接断开 (continue_task): {e}", "WARN")
            raise
        except Exception as e:
            log(f"事件处理异常 | {run_uuid}: {e}", "WARN")
            return {
                "run_uuid": run_uuid,
                "session_key": session_key,
                "status": "failed",
                "result": f"事件处理异常: {e}",
            }
        finally:
            self._unregister_queue(session_key, run_uuid, queue)
            self._release_session_slot(session_key)

    # -- Direct text send (added 2026-10-05, fire-and-forget) ----------

    async def send_text(
        self, run_uuid: str, agent_id: str, text: str, session_key: str, timeout_seconds: int = 30
    ) -> dict:
        """Send a plain text straight to the given session.

        Difference from continue_task: does not subscribe, does not wait for a final, does not accumulate result_text.
        Delivery (sessions.send returning a runId) counts as success and returns at once.

        Args:
            run_uuid: may be empty (this side path creates no execution; used only as a log marker).
            agent_id: target agent.
            text: the plain text to send.
            session_key: the addressing key of the target session.
"""
        log(f" 直接发文本 | session={session_key} | agent={agent_id} | {text[:100]}")

        # Character safety check (reusing the _is_safe_session_key introduced on 2026-10-05)
        if not _is_safe_session_key(session_key):
            log(f" ❌ send_text 拒绝非法 session_key | {session_key!r}", "ERROR")
            return {
                "run_uuid": run_uuid,
                "session_key": session_key,
                "status": "failed",
                "result": f"invalid session_key (unsafe chars): {session_key!r}",
            }

        try:
            # * 2026-10-19 fix (same as continue_task): sessions.send does not fill in the label when it implicitly creates a session.
            #   Add one backstop in advance: idempotent, skipped when a non-empty label exists, silent on failure.
            await self.ensure_session_label(session_key)

            resp = await self.call(
                "sessions.send",
                {
                    "key": session_key,
                    "message": text,
                },
            )
            if "runId" not in resp:
                # Session does not exist -> create it automatically (the same fallback as continue_task)
                log(f" 会话不存在，自动创建 | session={session_key}")
                _create_resp = await self.call(
                    "sessions.create",
                    {
                        "key": session_key,
                        "agentId": agent_id,
                        "message": text,
                    },
                )
                if isinstance(_create_resp, dict) and _create_resp.get("ok"):
                    # * 2026-10-15: after the create fallback branch succeeds, fill in the label (= the last segment of the key)
                    await self.ensure_session_label(session_key)
                # * 2026-10-26 same as above (send_text path): create with a message has already started a round ->
                #   adopt its runId directly and do not send again, avoiding delivering the same text twice.
                if isinstance(_create_resp, dict) and _create_resp.get("runId"):
                    resp = _create_resp
                else:
                    resp = await self.call(
                        "sessions.send",
                        {
                            "key": session_key,
                            "message": text,
                        },
                    )
            if "runId" not in resp:
                return {
                    "run_uuid": run_uuid,
                    "session_key": session_key,
                    "status": "failed",
                    "result": f"sessions.send 失败: {resp}",
                }
            log(f" ✅ 文本已发送 | session={session_key}")
            return {
                "run_uuid": run_uuid,
                "session_key": session_key,
                "run_id": resp.get("runId", ""),
                "status": "succeeded",
                "result": "",
            }
        except Exception as e:
            log(f" send_text 异常 | {session_key}: {e}", "WARN")
            return {
                "run_uuid": run_uuid,
                "session_key": session_key,
                "status": "failed",
                "result": f"send_text 异常: {e}",
            }

    # -- Core rate-limit wrapper ----------------------------------

    async def _handle_task(
        self,
        r,
        run_uuid: str,
        agent_id: str,
        instruction: str,
        timeout_s: int,
        session_key: str = "",
        mode: str = "create",
        command: str = "",
        host_session_id: str = "",
        rpc_id: str = "",
    ):
        """Lifecycle wrapper for a single task.

        register_agent command: after agents.create completes, report back to the host agent through sessions.send.
"""
        if self.ws_dead:
            outcome = {
                "run_uuid": run_uuid,
                "status": "failed",
                "result": "WS connection lost (pre-check)",
            }
            _write_run_result(r, run_uuid, outcome, where="ws_dead_precheck")
            return

        async with self._semaphore:
            try:
                actual_timeout = float(timeout_s) if timeout_s else float(TASK_TIMEOUT)

                async def _run_inner_business():
                    if self.ws_dead:
                        return {
                            "run_uuid": run_uuid,
                            "status": "failed",
                            "result": "WS connection lost (semaphore check)",
                        }
                    # register_agent command: call the agents.create WS RPC directly
                    # The callback logic is branched by _ao_task_type inside _reader
                    if command == "register_agent":
                        log(
                            f" 🎯 register_agent 入口 | run_uuid={run_uuid} | host_sid={'***' if host_session_id else '(none)'}"
                        )
                        task = (
                            json.loads(instruction)
                            if isinstance(instruction, str) and instruction.startswith("{")
                            else {}
                        )
                        # Register the callback target; _reader deletes it once used
                        if host_session_id:
                            self._pending_host_sessions[run_uuid] = host_session_id
                            log(f" 📝 已缓存回调目标 | run_uuid={run_uuid} → host_sid")
                        return await self.register_agent(
                            run_uuid,
                            task.get("agent_id", agent_id),
                            task.get("agent_name", ""),
                            task.get("agent_dir", ""),
                            task.get("workspace", ""),
                        )
                    if mode in ("continue", "default") and session_key:
                        return await self.continue_task(
                            run_uuid, agent_id, instruction, session_key, int(actual_timeout)
                        )
                    elif mode == "send_text":
                        # Side path added 2026-10-05: send text directly (fire-and-forget; creates no execution, subscribes to nothing, waits for no answer)
                        return await self.send_text(run_uuid, agent_id, instruction, session_key)
                    elif command == "session_model_get":
                        # * 2026-10-15 synchronous RPC: read the session live model (sessions.describe)
                        #   Does not go through agx:ws:result (that is execution semantics); the result is written to agx:ws:rpc:{rpc_id}
                        task = (
                            json.loads(instruction)
                            if isinstance(instruction, str) and instruction.startswith("{")
                            else {}
                        )
                        _sk = task.get("session_key", session_key)
                        sess = await self.describe_session(_sk)
                        rpc_payload = {
                            "status": "succeeded" if "error" not in sess else "failed",
                            "session_key": _sk,
                            "model": sess.get("model"),
                            "modelProvider": sess.get("modelProvider"),
                            "modelOverrideSource": sess.get("modelOverrideSource"),
                            "agentRuntime": sess.get("agentRuntime"),
                            "error": sess.get("error"),
                        }
                        if rpc_id:
                            _rk = f"agx:ws:rpc:{rpc_id}"
                            # * RPUSH (list push) is mandatory rather than SET: BLOOP is only woken by a list push,
                            #   while SET creates a string key that BLOOP can never see (measured proof, 2026-10-15).
                            r.rpush(_rk, json.dumps(rpc_payload, ensure_ascii=False))
                            r.expire(_rk, 60)
                        log(f" session_model_get 完成 | rpc_id={rpc_id} | session={_sk} | model={rpc_payload.get('model')}")
                        return rpc_payload
                    elif command == "session_model_set":
                        # * 2026-10-15 synchronous RPC: write/clear the session model (sessions.patch)
                        task = (
                            json.loads(instruction)
                            if isinstance(instruction, str) and instruction.startswith("{")
                            else {}
                        )
                        _sk = task.get("session_key", session_key)
                        _model = task.get("model", None)  # Empty string/None -> clear the override
                        resp = await self.patch_session_model(_sk, _model)
                        entry = (resp.get("entry") or {}) if isinstance(resp, dict) else {}
                        ok = isinstance(resp, dict) and not resp.get("error")
                        rpc_payload = {
                            "status": "succeeded" if ok else "failed",
                            "session_key": _sk,
                            "model": entry.get("model", _model),
                            "modelProvider": entry.get("modelProvider"),
                            "modelOverrideSource": entry.get("modelOverrideSource"),
                            "resolved": entry.get("resolved"),
                            "error": resp.get("error") if isinstance(resp, dict) else str(resp),
                        }
                        if rpc_id:
                            _rk = f"agx:ws:rpc:{rpc_id}"
                            # * RPUSH (list push) is mandatory rather than SET (same as session_model_get)
                            r.rpush(_rk, json.dumps(rpc_payload, ensure_ascii=False))
                            r.expire(_rk, 60)
                        log(f" session_model_set 完成 | rpc_id={rpc_id} | session={_sk} | model={_model!r} | ok={ok}")
                        return rpc_payload
                    elif command == "session_pin_set":
                        # * 2026-10-18 synchronous RPC: pin/unpin a session (sessions.patch pinned)
                        #   Semantics: assistant sessions are pinned by default (no AgentGeeX field added; projected straight onto the platform).
                        task = (
                            json.loads(instruction)
                            if isinstance(instruction, str) and instruction.startswith("{")
                            else {}
                        )
                        _sk = task.get("session_key", session_key)
                        _pinned = task.get("pinned", True)
                        resp = await self.patch_session_pin(_sk, bool(_pinned))
                        entry = (resp.get("entry") or {}) if isinstance(resp, dict) else {}
                        ok = isinstance(resp, dict) and not resp.get("error")
                        rpc_payload = {
                            "status": "succeeded" if ok else "failed",
                            "session_key": _sk,
                            "pinned": entry.get("pinned"),
                            "pinnedAt": entry.get("pinnedAt"),
                            "error": resp.get("error") if isinstance(resp, dict) else str(resp),
                        }
                        if rpc_id:
                            _rk = f"agx:ws:rpc:{rpc_id}"
                            # * RPUSH (list push) is mandatory rather than SET (same as session_model_get)
                            r.rpush(_rk, json.dumps(rpc_payload, ensure_ascii=False))
                            r.expire(_rk, 60)
                        log(f" session_pin_set 完成 | rpc_id={rpc_id} | session={_sk} | pinned={_pinned} | ok={ok}")
                        return rpc_payload
                    else:
                        return await self.execute_task(run_uuid, agent_id, instruction, int(actual_timeout))

                outcome = await asyncio.wait_for(_run_inner_business(), timeout=actual_timeout)

            except TimeoutError:
                log(f"❌ 任务运行超时硬拦截 | {run_uuid} | 超过设定的全局限制 {actual_timeout}s", "ERROR")
                outcome = {
                    "run_uuid": run_uuid,
                    "session_key": session_key if session_key else f"agent:{agent_id}:{run_uuid}",
                    "status": "failed",
                    "result": f"任务执行超时，已被客户端硬卫士强行截断并释放插槽 ({actual_timeout}s)",
                    "retryable": False,
                    "reason_code": "task_timeout",
                }
            except ConnectionClosed:
                self.ws_dead = True
                outcome = {
                    "run_uuid": run_uuid,
                    "status": "failed",
                    "result": "WS connection lost",
                    "retryable": True,
                    "reason_code": "ws_disconnected",
                }
            except Exception as e:
                log(f" 任务执行异常 | {run_uuid}: {e}", "ERROR")
                outcome = {
                    "run_uuid": run_uuid,
                    "status": "failed",
                    "result": str(e),
                    "retryable": False,
                    "reason_code": "exception",
                }

            # ** 2026-10-29 incident root fix (C): a failed register_agent must always send a callback.
            #   Root cause: the callback was only sent when _reader received the WS response (ok=true);
            #   once the outer asyncio.wait_for timed out first and killed the coroutine, the response was handled by nobody,
            #   the callback was lost forever -> the host waited forever -> the deploy execution stuck at running.
            #   Evidence: run_cagent_deploy_ab02c5bcf83d, entered at 13:04:03 -> at 13:04:33
            #   the hard interception fired, with 30s of zero logs and zero responses in between; while agents.create had actually
            #   succeeded on the gateway side (the agent was registered), only nobody said so.
            #   Fix: whatever the failure reason (inner timeout / outer timeout / exception / ok=false),
            #   as long as a pending callback target remains, always send a failed callback plus a write to
            #   agx:ws:result, so both the frontend and the watchdog see "deployment unfinished" instead of silence.
            if command == "register_agent" and (outcome or {}).get("status") != "succeeded":
                _fail_host_sid = self._pending_host_sessions.pop(run_uuid, "")
                _fail_reason = str((outcome or {}).get("result", ""))[:400] or "unknown"
                log(
                    f" ⚠️ register_agent 未成功，补发失败回调 | run_uuid={run_uuid} "
                    f"| host_sid={'***' if _fail_host_sid else '(none)'} | 原因={_fail_reason}",
                    "WARN",
                )
                _write_run_result(
                    r,
                    run_uuid,
                    {
                        "run_uuid": run_uuid,
                        "status": "failed",
                        "result": f"register_agent 未成功: {_fail_reason}",
                        "host_id": self.host_id,
                    },
                    where="register_agent",
                )
                if _fail_host_sid:
                    try:
                        await self._send_callback(
                            _fail_host_sid,
                            json.dumps(
                                {
                                    "type": "agx_deploy_callback",
                                    "status": "failed",
                                    "run_uuid": run_uuid,
                                    "agent_id": agent_id,
                                    "agent_name": "",
                                    "verify": (outcome or {}).get("verify"),
                                    "result": {
                                        "error": f"register_agent 未成功: {_fail_reason}",
                                        "hint": (
                                            "已做过注册核验（agents.list + 本地 openclaw.json），"
                                            "verify 字段为核验结果：state=absent 表示确认未注册，可安全重试；"
                                            "state=unknown 表示两条通道都不可用、真实状态未知，"
                                            "请先用 openclaw agents list 人工核实再决定是否重跑部署。"
                                        ),
                                    },
                                },
                                ensure_ascii=False,
                            ),
                            run_uuid=run_uuid,
                        )
                        log(f" 📮 已补发失败回调 | session={_fail_host_sid} | run_uuid={run_uuid}")
                    except Exception as _e:
                        log(f" ❌ 补发失败回调也失败 | {_e}", "ERROR")

            # register_agent already reported to the host agent through sessions.send, so nothing is written to Redis
            # * 2026-10-15: session_model_get/set are synchronous RPCs; the result is written to agx:ws:rpc:{rpc_id},
            #   and likewise *not* to agx:ws:result (that is execution semantics; avoid contaminating it).
            # * 2026-10-18: session_pin_set is also a synchronous RPC and likewise does not write agx:ws:result.
            if command not in (
                "register_agent",
                "session_model_get",
                "session_model_set",
                "session_pin_set",
            ) and mode != "send_text":
                # * 2026-10-25 DH-067 fix: the result must carry *the host that produced it*.
                #   Root cause: agx:ws:result:run_* is a global key (no host isolation), so any machine's
                #   daemon scans another host's results -> triggers a retry delivery -> pushes with the consumer's own
                #   host_id into agx:ws:task:{itself} -> executes locally and judges succeeded.
                #   Measured: the scheduling record had host_id=test_srv_b while it actually ran and succeeded on test_host_mac.
                #   The host_id field is added here so the daemon can decide ownership (key structure unchanged, backward compatible).
                if isinstance(outcome, dict):
                    outcome["host_id"] = self.host_id
                if _write_run_result(r, run_uuid, outcome, where="task_result"):
                    log(
                        f" 结果已写入 agx:ws:result:{run_uuid} "
                        f"| status={outcome.get('status')} | host={self.host_id}"
                    )

    # -- Main loop ----------------------------------------

    async def run(self):
        if not self.ws_token:
            log(" AGX_GATEWAY_TOKEN 未设置，无法启动 WS 客户端", "FATAL")
            return

        log(f" AgentGeeX WS 客户端启动 | host_id={self.host_id} | {self.ws_url}")

        # -- Initial connection: unlimited retries + exponential backoff (3->6->12->24->30, capped) ---------
        # Fixed 2026-11-12: it used to be a fixed 5 tries x 3s and then FATAL exit of the "connection thread".
        # Evidence (188 booted 2026-11-12 07:16): the openclaw main gateway on 18789 came up about 12s
        # after this process -> all 5 tries failed -> that connection thread died; the process stayed alive and the same process's
        # other two instances were fine, yet nobody on that host consumed agx:ws:task:*; the task appeared as
        # "dispatched but never started" (empty started_at, phase=queued).
        # Semantics corrected: this process's duty is "connect and consume"; failing to connect is waiting, not a terminal state.
        # Limited retries + only the thread dying = a fake liveness of "process alive but no consumer", the worst of all states.
        # Log throttling: log every one of the first 10; after that once every 20 (about 10 minutes), to avoid long-term noise.
        FIRST_BACKOFF = 3
        FIRST_BACKOFF_MAX = 30
        attempt = 0
        while not await self.connect():
            attempt += 1
            delay = min(FIRST_BACKOFF * (2 ** (attempt - 1)), FIRST_BACKOFF_MAX)
            if attempt <= 10 or attempt % 20 == 0:
                log(f"⏳ 初始连接失败 (第 {attempt} 次)，{delay}s 后重试（不放弃）")
            await asyncio.sleep(delay)
        if attempt:
            log(f" 初始连接在第 {attempt} 次重试后成功")

        # * R4: once connected, flush the callbacks that previously failed to send (including the backlog from before a process restart)
        self._spawn(self._flush_callback_backlog("initial_connect"), where="cb_flush_init")

        r = build_redis()
        active_tasks: set[asyncio.Task] = set()
        fail_count = 0

        # -- Report the running count to Redis (2026-10-11 P0-2/P2) ----------------------
        # Source of truth: active_tasks = the real number of concurrently running tasks (also the basis of the concurrency limit).
        # ws_client is a child process and the daemon (parent) cannot read its memory -> the main loop writes the current
        # active count into Redis every round, for the daemon's get_running_task_count() to read.
        # TTL=30s backstop: after the process dies abnormally the key expires by itself, leaving no dirty "fake running count".
        RUNNING_KEY = self.running_key

        while True:
            try:
                if self.ws_dead:
                    raise ConnectionClosed(None, None) from None

                active_tasks = {t for t in active_tasks if not t.done()}

                # Report the active count every round (the real value after dead tasks are cleared)
                try:
                    r.set(RUNNING_KEY, len(active_tasks), ex=30)
                except Exception:
                    pass

                # * R4: sweep the callback pending-delivery queue once a minute (almost zero cost when empty)
                if time.time() - self._last_cb_flush >= 60:
                    self._last_cb_flush = time.time()
                    self._spawn(
                        self._flush_callback_backlog("periodic"), where="cb_flush_periodic"
                    )

                max_conc = int(config.ws.get("max_concurrent", 4))
                if len(active_tasks) >= max_conc:
                    done, _ = await asyncio.wait(active_tasks, return_when=asyncio.FIRST_COMPLETED)
                    active_tasks = {t for t in active_tasks if not t.done()}
                    continue

                # Cross-platform mixed high-throughput adaptation: set the blocking read to 1 second, keeping the main loop highly responsive to disconnects
                result = await asyncio.to_thread(r.blpop, self.ws_task_queue, timeout=1)

                if result is None:
                    if self.ws_dead:
                        raise ConnectionClosed(None, None) from None
                    # Force a 10ms timeslice yield, solving high-load starvation in Windows Proactor / Mac Selector
                    await asyncio.sleep(0.01)
                    continue

                _, raw = result
                log(f" 原始队列消息 | len={len(raw)} | 首100={raw[:100]}")
                task = json.loads(raw)
                # * 2026-11-04: run_uuid is the unique identifier of the result key; an empty value must be intercepted up front.
                #   The old code carried on even when task["run_uuid"] was "" / None, so every place that wrote a result
                #   produced "agx:ws:result:" -- while the daemon scans agx:ws:result:run_*,
                #   an empty suffix is never scanned, so the task was silently lost (evidence: key ttl=-1, test_srv_b).
                #   Drop it here and keep the original text: better to drop one unidentified dirty message than to create a malformed key.
                run_uuid = str(task.get("run_uuid") or "").strip()
                _mode_guard = str(task.get("mode") or "")
                if not run_uuid and _mode_guard != "send_text":
                    log(
                        f" ❌ 丢弃无 run_uuid 的任务消息(避免 agx:ws:result: 畸形键) | "
                        f"queue={self.ws_task_queue} | 原文={raw[:300]}",
                        "ERROR",
                    )
                    continue
                if not run_uuid:
                    # * 2026-11-04 fix: send_text is a fire-and-forget side path,
                    #   it creates no execution and writes no agx:ws:result -- it naturally has no run_uuid
                    #   (the daemon packs task_run_uuid as empty). The gate above exists for
                    #   execute_task-class messages; blocking this one too would simply kill text sending.
                    #   Evidence: two send_text messages at 22:55 / 22:57 were both dropped, and from the user side it looked like
                    #   "text cannot be sent, but dispatching tasks works fine".
                    log(
                        f"  send_text 旁路放行(无 run_uuid, 不写结果键) | "
                        f"session={task.get('session_key', '')}"
                    )
                agent_id = task.get("agent_id", "")
                instruction = task.get("instruction", "")
                timeout_s = task.get("timeout_seconds", TASK_TIMEOUT)

                session_key = task.get("session_key", "")
                mode = task.get("mode", "create")
                command = task.get("command", "")
                host_session_id = task.get("host_session_id", "")  # register_agent callback target
                rpc_id = task.get("rpc_id", "")  # * 2026-10-15 synchronous RPC correlation id (session_model_get/set)

                log(f" 收到任务 | {run_uuid} | {agent_id} | mode={mode} | cmd={command} | 活跃={len(active_tasks)}")

                # ** 2026-10-25 DH-067 last gate: verify the task's owning host.
                #   The first two gates (daemon result ownership check + retry delivery ownership) are on the daemon side,
                #   but should some other path push a task that is not this machine's into agx:ws:task:{this machine},
                #   the old code would create a session and execute it directly -- looking only at agent_id,
                #   never at whether this machine should be responsible. The measured incident was exactly that (scheduling host_id=test_srv_b,
                #   actually executed and judged succeeded on test_host_mac).
                #   This gate reads target_host_id written by the daemon (sourced from DB execution.host_id);
                #   on mismatch it refuses and writes back a failed result carrying the real producing host, leaving it to the owning host.
                #   Backward compatible: an old daemon does not write that field -> no check (allow).
                #   * No MySQL: ws_client has no DB dependency (measured: the import has no mysql module).
                _tgt_host = (task.get("target_host_id") or "").strip()
                if _tgt_host and self.host_id and _tgt_host != self.host_id and not command:
                    log(
                        f" ❌ 拒收非本机任务 | {run_uuid} | 任务归属={_tgt_host} "
                        f"本机={self.host_id} | 不创建 session（见 DH-067）",
                        "WARN",
                    )
                    _write_run_result(
                        r,
                        run_uuid,
                        {
                            "run_uuid": run_uuid,
                            "status": "failed",
                            "result": (
                                f"任务归属主机 {_tgt_host}，本机 {self.host_id} 拒收"
                                "（DH-067 归属校验）"
                            ),
                            "host_id": self.host_id,
                        },
                        where="host_mismatch",
                    )
                    continue

                fail_count = 0

                t = asyncio.create_task(
                    self._handle_task(
                        r, run_uuid, agent_id, instruction, timeout_s, session_key, mode, command, host_session_id, rpc_id
                    )
                )
                active_tasks.add(t)

                # Explicitly yield control to speed up coroutine switching
                await asyncio.sleep(0.01)

            except Exception as e:
                fail_count += 1
                log(f"主循环异常 [{fail_count}]: {e}", "ERROR")
                log(f"详细报错调用栈:\n{traceback.format_exc()}", "ERROR")

                should_reconnect = (
                    isinstance(e, ConnectionClosed) or self.ws_dead or fail_count >= RECONNECT_FAIL_THRESHOLD
                )
                if not should_reconnect:
                    await asyncio.sleep(2)
                    continue

                if isinstance(e, ConnectionClosed) or self.ws_dead:
                    log(" WS 断连，立即重连...", "WARN")
                else:
                    log(f"连续失败 {fail_count} 次，尝试重连...", "WARN")

                fail_count = 0

                if active_tasks:
                    log(f"⏳ 等待 {len(active_tasks)} 个活跃任务完成...")
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*active_tasks, return_exceptions=True),
                            timeout=CALL_TIMEOUT * 2,
                        )
                    except TimeoutError:
                        log(f"   等待超时，强制取消剩余 {len(active_tasks)} 个任务", "WARN")
                        for t in active_tasks:
                            if not t.done():
                                t.cancel()
                    active_tasks.clear()

                await self._stop_reader()

                try:
                    if self.ws:
                        await self.ws.close()
                except Exception:
                    pass

                self.ws_dead = False
                # Reconnect: also unlimited retries (the same fix as 2026-11-12).
                # It used to `return` after `range(MAX_RECONNECT_DELAY+1)` tries -- the same disease as the initial connection:
                # run() returns, that gateway has no consumer from then on, while the process and other gateways carry on.
                current_delay = INITIAL_BACKOFF
                attempt = 0
                while not await self.connect():
                    attempt += 1
                    if attempt <= 10 or attempt % 20 == 0:
                        log(f"重连尝试 {attempt} 失败，{current_delay}s 后再试（不放弃）", "WARN")
                    await asyncio.sleep(current_delay)
                    current_delay = min(current_delay * RECONNECT_BACKOFF, MAX_RECONNECT_DELAY)

                # * R4: reconnect succeeded -> immediately flush the backlog of callbacks
                self._spawn(self._flush_callback_backlog("reconnect"), where="cb_flush_reconnect")

                await asyncio.sleep(2)


def load_gateways() -> list[dict]:
    """Resolve the gateway list this machine should connect to (2026-11-04, one machine with several instances).

    Why an array rather than a single URL:
      one machine can run several openclaw instances (different --profile / port),
      each instance being an independent "logical host" -- a different host_id,
      each with its own gateway token (one token per gateway, not interchangeable).

    So the ws_client strategy is: **one process pinned to N connections**,
    rather than starting N processes (about 38MB each, and sharing one Redis/MySQL pool is cheaper).

    Config shape (agx_config.json)::

        "gateways": [
          {"host_id": "test_srv_a",  "ws_url": "ws://127.0.0.1:18789", "token": "..."},
          {"host_id": "test_srv_a2", "ws_url": "ws://127.0.0.1:18799", "token": "..."}
        ]

    Backward compatible: with no gateways array it falls back to the old single-value gateway.ws_url + gateway.token,
    behaving exactly as before the rework.
"""
    raw = config.get("gateways")
    out: list[dict] = []
    if isinstance(raw, list) and raw:
        # * 2026-11-17: switched to config_loader.gateway_entries() for one normalised source.
        #   Only entries with agent_type=openclaw are taken; dsh entries are managed by agx_dsh_supervisor,
        #   and are **silently skipped** here (not a misconfiguration, and certainly not a reason to spam WARN logs).
        for e in gateway_entries():
            if e["agent_type"] != AGENT_TYPE_OPENCLAW:
                continue
            if e["skip"]:
                log(f" gateways 条目被跳过 | host_id={e['host_id'] or '-'} | {e['skip']}", "WARN")
                continue
            hid = e["host_id"] or HOST_ID
            if not e["token"]:
                log(f" gateways 条目缺 token，已跳过 | host_id={hid}", "WARN")
                continue
            out.append({"host_id": hid, "ws_url": e["ws_url"], "token": e["token"]})
    if out:
        return out
    return [{"host_id": HOST_ID, "ws_url": WS_URL, "token": WS_TOKEN}]


def main():
    try:
        gws = load_gateways()
        for g in gws:
            log(f" 目标 gateway | host_id={g['host_id']} | {g['ws_url']}")

        if len(gws) == 1:
            # Single instance: keep exactly the same call shape as before the rework
            asyncio.run(
                AgxWsClient(gws[0]["host_id"], gws[0]["ws_url"], gws[0]["token"]).run()
            )
        else:
            log(f" 多实例模式：本进程钉 {len(gws)} 条 gateway 连接")

            async def _run_all():
                # One independent task per connection: one dying does not drag the others down (each has its own reconnect logic)
                await asyncio.gather(
                    *[
                        AgxWsClient(g["host_id"], g["ws_url"], g["token"]).run()
                        for g in gws
                    ]
                )

            asyncio.run(_run_all())
    except (KeyboardInterrupt, SystemExit):
        pass
    except RuntimeError as e:
        # -- Final cross-platform defence: intercept the dirty "Event loop is closed" error Windows throws on shutdown because of an unclosed socket handle --
        if "Event loop is closed" not in str(e):
            raise


if __name__ == "__main__":
    main()
