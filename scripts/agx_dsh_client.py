#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agx_dsh_client.py -- AgentGeeX <-> DeepSeek-Harness (DSH) bridge process (same shape as ws_client)

Positioning
===========
The "bridge" that connects the DSH platform to AgentGeeX: it talks to the `dsh web` `/api` session
surface and turns AO executions into DSH session/turn, structurally identical to
`scripts/agx_ws_client.py` (the OpenClaw bridge) -- same Redis queue protocol, same result keys,
same active-count reporting.

Contract (see the "bridge main logic" section at the end of the file)
=====================================================================
  in : agx:dsh:task:{host_id}      <- daemon RPUSHes here when agent_type=='dsh'
  out: agx:ws:result:{run_uuid}    -> daemon polls and persists (the **same key space** as ws_client)
  active: agx:dsh:running:{host_id}  = concurrency (ex=30)

Source and absorption notes
===========================
This file has two parts:
  (1) [absorbed section] DshClient -- absorbed line by line from the PoC
      docs/agent_docs/platform_adapt/deepseek-harness/bridge_poc/dsh_client.py
      (absorbed 2026-10-15. It carries its **own copy** so the bridge can be deployed standalone to
       ~/.agx/scripts/; if the upstream PoC evolves, this copy must be synced on both sides.)
  (2) [bridge main logic] AgxDshClient -- newly added here (same shape as ws_client).

Boundary (Feng, 2026-10-15)
===========================
  - Connect only to an **already running** `dsh web` (loopback); when it cannot connect the task
    fails at once, and it **never spawns one by itself**.
  - Do not modify the DSH source; do not touch OpenClaw's sessions.* paths.
  - Version lock: DSH must be 0.1.0-rc.7 (verified on connect).
"""
from __future__ import annotations

# ════════════════════════════════════════════════════════════════════════════
# Start of the absorbed section: DshClient (source: bridge_poc/dsh_client.py, kept line by line)
# ════════════════════════════════════════════════════════════════════════════
"""
dsh_client.py —— DeepSeek-Harness (DSH) `/api` 会话面客户端（桥接 API 封装）

定位
====
把 DSH 常驻服务 `dsh web` 的 `/api`（HTTP + JSON）会话面，封装成一个纯 Python 模块，
供 AgentGeeX 桥接层调用。**本模块不做 MCP 化、不做 daemon 集成、不改 DSH 源码。**

契约来源（实测，见同目录 `DSH_BRIDGE_POC.md` 与
`docs/agent_docs/platform_adapt/deepseek-harness/{WEB_API_PROBE,SESSION_QUEUE_PROBE}.md`）
=====================================================================================
版本锁定：`@deepseek-ai/dsh` **0.1.0-rc.7**（客户端启动时做版本校验）。

    session.create  { sessionId? }                     -> { value.sessionId, value.agentPreset }
    session.prompt  { sessionId, mode, content:[{type:"text",text}] } -> { value.accepted:true }
    session.history { sessionId }                      -> { value.events[] }
    session.list    {}                                 -> { value.items[] }   # 每项含 running / asOfSeq
    session.cancel  { sessionId }                      -> { value.accepted:true }

传输约定（实测契约）
--------------------
* 端点：``POST http://<host>:<port>/api/<method>``，`Content-Type: application/json` 强制（非 JSON -> 415）。
* 请求体：``{"type":"client-request","rpcId":"<id>","method":"<method>","payload":{...}}``
* 响应体：``{"type":"server-response","rpcId":"<id>","result":{ok:true,value:{...}} | {ok:false,error:{...}}}``
* `Host` 头必须等于监听 host:port（loopback browser-trust 围栏），否则被拒。
* 冷启动会自动加载持久化会话（不需要显式 resume 调用，同 sessionId 再 prompt 即续接）。
* **同会话严格串行 FIFO**：`mode:"queue"` 的消息排为下一个 turn，客户端无需自建队列。
* **无背压信号**：`session.prompt` 只回 `accepted:true`，不出队列长度 —— 桥接层需自建限流。
* 回答出口：轮询 `session.history`，取**本轮新增**的 `assistant/message` 事件（见 ``_extract_assistant_text``）。

作者：agx_platform_deepseek ｜ 日期：2026-09-29
"""


import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import http.cookiejar
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "DshClient",
    "DshClientError",
    "DshVersionError",
    "DshTimeoutError",
    "DSH_TARGET_VERSION",
    "find_dsh_bin",
]

# ----------------------------------------------------------------------------
# Constants / version lock
# ----------------------------------------------------------------------------

DSH_TARGET_VERSION = "0.2.1-rc.5"  # DSH version this client measured and pinned (dsh --version measured 2026-11-17)


# -- Session id mapping: OpenClaw side <-> DSH side (each side keeps its own id) --------
# * Background (Feng, 2026-11-17): an AO session id looks like `agent:<agent_id>:<name>`,
#   i.e. it contains colons. The DSH storage layer has a hard constraint:
#       const SAFE_KEY_RE = /^[a-zA-Z0-9_-]+$/;
#       if (!SAFE_KEY_RE.test(key)) return;      // silently skipped, no error raised
#   an id with colons can never be written into the projection cache -> the title is lost on restart and the sidebar shows "unnamed"
#   Conclusion: do not force both sides to agree; **do the mapping once in the bridge**.
#       OpenClaw  agent:dshlab_pm:main     (colon form, for AO)
#       DSH       dshlab_pm-main           (safe characters, for DSH)
#   `-` acts as the separator: the agent_id validation regex is ^[a-z][a-z0-9_]*$, which excludes `-`.
_DSH_SID_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]")
_DSH_SID_SEP = "-"


def to_dsh_sid(session_key: str) -> str:
    """OpenClaw session id -> DSH-safe sessionId.

    ``agent:<agent_id>:<name>`` -> ``<agent_id>-<name>``
    Any other shape: replace every illegal character with ``-`` (idempotent: a value that is
    already safe is returned unchanged).
"""
    sk = (session_key or "").strip()
    if not sk:
        return ""
    if sk.startswith("agent:") and sk.count(":") >= 2:
        _, agent_id, name = sk.split(":", 2)
        raw = f"{agent_id}{_DSH_SID_SEP}{name}"
    else:
        raw = sk
    return _DSH_SID_UNSAFE.sub(_DSH_SID_SEP, raw)


def to_session_key(dsh_sid: str) -> str:
    """DSH-safe sessionId -> OpenClaw session id (the inverse of ``to_dsh_sid``).

    ``<agent_id>-<name>`` -> ``agent:<agent_id>:<name>``
    agent_id never contains ``-``, so the first ``-`` is the split point. A value already
    prefixed with ``agent:`` is returned unchanged.
"""
    sid = (dsh_sid or "").strip()
    if not sid or sid.startswith("agent:"):
        return sid
    if _DSH_SID_SEP in sid:
        agent_id, name = sid.split(_DSH_SID_SEP, 1)
        return f"agent:{agent_id}:{name}"
    return sid

# Verified location of dsh bin.js (npx cache). Falls back to a search when resolution fails.
_KNOWN_NPX_DIRS = [
    "~/.npm/_npx/1e7f6d9597241db0/node_modules",
]


class DshClientError(Exception):
    """The DSH `/api` layer returned result.ok=false, or the transport layer raised."""

    def __init__(self, message: str, code: str = "", details: Any = None):
        super().__init__(message)
        self.code = code
        self.details = details


class DshVersionError(DshClientError):
    """The actual DSH version does not match the pinned version."""


def _platform_hint(run_uuid: str, agent_id: str) -> str:
    """Start/close-out hints for the DSH platform (appended to the end of every prompt).

    * 2026-11-16 gap 2 note: writing the agx:active:run mark in the bridge alone is not enough --
      on the MCP side started_at is written lazily, only persisted when the agent itself calls
      agx_get_execution on its own execution (conditions: caller==agent_id, started_at empty,
      run_status==running, and _claimed_by_agent hits).
      A DSH model never queries execution -> started_at stays empty -> the watchdog wrongly
      decides "not started". So the hint tells the model to call agx_get_execution once at
      start-up, letting it trigger that lazy write itself.
      Evidence run_general_c47d0d97adbd (2026-11-16 12:53):
        the mark was written (hash / TTL 5361) but started_at was still None -- exactly the
        missing step.

    * 2026-11-16 gap 5 fix:
      an openclaw agent closes itself out by calling agx_finish_task per the hard rule in AGENTS.md;
      a DSH model **does not read AGENTS.md** and has no idea this duty exists ->
      the task runs to completion and the result is delivered, yet agx_task_execution.run_status
      stays stuck at running (evidence run_general_ab81d940fdc6: finished_at written, run_status
      still running, and only a manual close-out by the initiator finally settled it).

    Why put this in the bridge instead of editing every task template:
      there are dozens of task templates and editing them one by one is bound to miss some;
      "close out when done" is a **platform-level convention** and should be injected by the
      platform in one place -- the same duty AGENTS.md performs for openclaw.
"""
    if not run_uuid:
        return ""
    return (
        "\n\n---\n"
        "【平台约定 · 必做】本任务在 DSH 平台执行，请在首尾各调一次 MCP 工具：\n"
        "\n"
        "① 开工——执行本任务的第一件事就调用：\n"
        f'   agx_get_execution(caller_agent_id="{agent_id}", task_run_uuid="{run_uuid}")\n'
        "   作用：把本任务的开工时刻记入系统。不调 → 系统认为它从未开工，\n"
        "   会在超时后发出「已发送 N 秒仍未开工」的误报告警。\n"
        "\n"
        "② 收口——任务完成后调用：\n"
        f'   agx_finish_task(caller_agent_id="{agent_id}", task_run_uuid="{run_uuid}", '
        'status="succeeded" 或 "failed", summary="<一句话结论>", result="<原始产出>")\n'
        "   不结案会让任务永久停在 running，触发虚假超时告警并阻塞下游。\n"
        '   任务失败同样要用 status="failed" 结案，并写清失败原因。\n'
    )


def _notice_hint(instruction: str, agent_id: str) -> str:
    """Handling hints for pure messages (receipts / alerts) -- mutually exclusive with _platform_hint.

    * Why this is needed (measured 2026-11-17):
      an openclaw agent handles a receipt by the three steps in AGENTS.md (read / judge / ack);
      a DSH model does not read AGENTS.md -> on a receipt it only "replies with one sentence"
      and never calls agx_ack_receipt -> the initiator never learns it was read
      (evidence run_general_464df3d84dc0: ack_state stuck at delivered, reply_read=0).

    * A receipt does *not* create an execution, so the hint must never mention agx_finish_task --
      that is the vocabulary of a task.
"""
    txt = (instruction or "").strip()
    meta = {}
    if txt.startswith("{"):
        try:
            meta = json.loads(txt)
        except Exception:  # noqa: BLE001
            meta = {}
    run_uuid = str(meta.get("task_run_uuid") or "").strip()
    msg_type = str(meta.get("msg_type") or "").strip()
    send_agent = str(meta.get("send_agent") or "").strip()

    if msg_type == "receipt" and run_uuid:
        return (
            "\n\n---\n"
            "【平台约定 · 这是一条任务回执，必做两步】\n"
            f"回执来自 {send_agent or '某个 Agent'}，对应任务 {run_uuid}。\n"
            "\n"
            "① 读——调用：\n"
            f'   agx_get_execution(caller_agent_id="{agent_id}", task_run_uuid="{run_uuid}")\n'
            "   看 summary / result，判断对方做得对不对、要不要你后续动作。\n"
            "\n"
            "② 签——调用：\n"
            f'   agx_ack_receipt(caller_agent_id="{agent_id}", task_run_uuid="{run_uuid}")\n'
            "   不签收 = 系统认为你没读过，发起方看到的永远是「已送达未读」。\n"
            "\n"
            "注意：回执不是任务，**不要**调 agx_finish_task。\n"
        )
    if msg_type == "urge" and run_uuid:
        return (
            "\n\n---\n"
            "【平台约定 · 这是一条催办】\n"
            f"对应任务 {run_uuid}。请先调 agx_get_execution(caller_agent_id=\"{agent_id}\", "
            f'task_run_uuid="{run_uuid}") 看清要求，再推进该任务并 agx_finish_task 结案。\n'
        )
    return (
        "\n\n---\n"
        "【平台约定 · 这是一条纯通知】\n"
        "本消息不产生执行记录，**不需要**调用 agx_finish_task。\n"
        "若通知内容要求你做某件事，按内容处置即可。\n"
    )


class DshTimeoutError(DshClientError):
    """Timeout waiting for one round to finish."""


# ----------------------------------------------------------------------------
# dsh bin resolution
# ----------------------------------------------------------------------------

def find_dsh_bin() -> str:
    """Locate the `lib/bin.js` of `@deepseek-ai/dsh`.

    Prefer the ``DSH_BIN`` env var; then known npx cache directories; then ``which dsh`` resolution.
    Raises DshClientError when nothing is found.
"""
    env = os.environ.get("DSH_BIN")
    if env and Path(env).expanduser().exists():
        return str(Path(env).expanduser())

    for d in _KNOWN_NPX_DIRS:
        p = Path(d).expanduser() / "@deepseek-ai/dsh/lib/bin.js"
        if p.exists():
            return str(p)

    # Global-install fallback: resolve the bin wrapper script back to the real file
    exe = shutil.which("dsh")
    if exe:
        return exe

    raise DshClientError(
        "找不到 dsh bin.js；请设置环境变量 DSH_BIN 指向 "
        "<node_modules>/@deepseek-ai/dsh/lib/bin.js"
    )


# ----------------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------------

def find_node_bin() -> str:
    """Locate the node executable (same convention as agx_dsh_sdk_client.find_node_bin).

    * 2026-11-16 (measured on test_srv_b): the daemon is started from an independent environment
    whose PATH may lack nvm-managed node, so the child process reports
    `[Errno 2] No such file or directory: 'node'`.
    Fall back in order: AGX_DSH_NODE -> PATH -> the highest version under ~/.nvm.
"""
    env_bin = (os.environ.get("AGX_DSH_NODE") or "").strip()
    if env_bin and os.path.isfile(env_bin):
        return env_bin
    p = shutil.which("node")
    if p:
        return p
    base = os.path.expanduser("~/.nvm/versions/node")
    if os.path.isdir(base):
        cands = []
        for name in os.listdir(base):
            b = os.path.join(base, name, "bin", "node")
            if os.path.isfile(b):
                cands.append((name, b))
        if cands:
            def _key(item):
                nums = re.findall(r"\d+", item[0])
                return [int(x) for x in nums[:3]] or [0]

            cands.sort(key=_key)
            return cands[-1][1]
    raise DshClientError("找不到 node 可执行文件（AGX_DSH_NODE / PATH / ~/.nvm 都没有）")


def _resolve_dsh_token() -> str:
    """Resolve the DSH admin token: env -> config dsh.token -> dsh.token_file.

    * The 0.2.2 web HTTP surface requires a login cookie (older versions had no auth; measured: now 401).
      The token is generated when `dsh web` starts and changes on every restart, so reading
      token_file at runtime is the most reliable option.
"""
    env_tok = (os.environ.get("AGX_DSH_TOKEN") or "").strip()
    if env_tok:
        return env_tok
    # * _DSH_CFG is a config_loader._Config object (it has .get), not a dict --
    #   do not type-check it as a dict, or the whole config section is silently skipped (measured, already hit once).
    try:
        tok = str(_DSH_CFG.get("token") or "").strip()
        if tok:
            return tok
        # * 2026-11-17: support per-entry overrides (the supervisor passes env per gateway entry).
        #   Only {"dsh": {"token_file": ...}} written inside the entry takes effect; otherwise
        #   machines running several DSH instances cannot each point at their own token file.
        tf = str(
            os.environ.get("AGX_DSH_TOKEN_FILE")
            or _DSH_CFG.get("token_file")
            or ""
        ).strip()
        if tf:
            return Path(tf).expanduser().read_text(encoding="utf-8").strip()
    except Exception:  # noqa: BLE001
        return ""
    return ""


class DshClient:
    """Client for the DeepSeek-Harness `/api` session surface.

    Typical usage::

        with DshClient(profile="bridge_probe", port=3199) as c:
            sid = c.create_session("my-custom-id")     # a custom id takes effect as given
            r1 = c.prompt(sid, "remember the token XYZ")
            r2 = c.resume(sid, "what did I say in the last round?")
            print(r2["text"])

    Lifecycle
    ---------
    With ``autostart=True`` (the default), constructing the client spawns an isolated resident
    ``dsh web`` service; ``close()`` / leaving the ``with`` block stops it and reclaims the port.
    With ``autostart=False`` the object is only a client of an existing service (connectivity check only).
"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3199,
        *,
        profile: str = "web",
        autostart: bool = True,
        dsh_bin: Optional[str] = None,
        node_bin: Optional[str] = None,
        cwd: Optional[str] = None,
        boot_timeout_s: int = 90,
        check_version: bool = True,
        extra_args: Optional[list[str]] = None,
        log_path: Optional[str] = None,
        token: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.profile = profile
        self.autostart = autostart
        self.dsh_bin = dsh_bin or find_dsh_bin()
        self.node_bin = node_bin or find_node_bin()
        self.cwd = str(Path(cwd).expanduser()) if cwd else str(Path.cwd())
        self.boot_timeout_s = boot_timeout_s
        self.check_version = check_version
        self.log_path = log_path or str(Path(self.cwd) / f"dsh_web_{profile}_{port}.log")
        self.extra_args = list(extra_args or [])

        self._proc: Optional[subprocess.Popen] = None
        self._base = f"http://{host}:{port}"
        self._started_by_us = False
        self._log_fh = None
        self.actual_version: Optional[str] = None
        self.token = token or _resolve_dsh_token()
        self._opener: Optional[Any] = None

        if autostart:
            self.start()

    # -- Lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Spawn `dsh web` and wait for the port to become ready (idempotent: when already running, only probe)."""
        if self._is_up():
            self._started_by_us = False
            if self.check_version:
                self.assert_version()
            return

        self._log_fh = open(self.log_path, "ab")
        argv = [
            self.node_bin,
            self.dsh_bin,
            "--profile",
            self.profile,
            "--no-open",
            "--port",
            str(self.port),
            *self.extra_args,
        ]
        self._proc = subprocess.Popen(
            argv,
            cwd=self.cwd,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # Own process group, so the whole group can be reclaimed at once
        )
        self._started_by_us = True

        deadline = time.time() + self.boot_timeout_s
        while time.time() < deadline:
            if self._is_up():
                break
            if self._proc.poll() is not None:
                tail = self._tail_log()
                self._cleanup_handle()
                raise DshClientError(
                    f"dsh web 启动即退出（code={self._proc.returncode}）。日志尾部:\n{tail}"
                )
            time.sleep(0.5)
        else:
            self.stop()
            raise DshClientError(
                f"dsh web 在 {self.boot_timeout_s}s 内未就绪（{self._base}）。"
                f"日志尾部:\n{self._tail_log()}"
            )

        if self.check_version:
            self.assert_version()

    def stop(self) -> None:
        """Stop the service this client spawned (reclaim only our own process group, never kill other processes)."""
        if self._proc is not None and self._started_by_us:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                self._proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        self._proc = None
        self._started_by_us = False
        self._cleanup_handle()
        # Wait for the port to be released
        deadline = time.time() + 10
        while time.time() < deadline and self._is_up():
            time.sleep(0.3)

    def _cleanup_handle(self) -> None:
        if self._log_fh is not None:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None

    def __enter__(self) -> "DshClient":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- Transport layer ---------------------------------------------------

    def _is_up(self) -> bool:
        try:
            self._rpc("session.list", {}, timeout=5)
            return True
        except Exception:
            return False

    def _ensure_opener(self):
        """Lazily build an opener carrying the cookie; on first use trade the token for a cookie (required by 0.2.2)."""
        if self._opener is not None:
            return self._opener
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        # * Re-read the token file on purpose: `dsh web` rotates its token on every restart,
        #   and a bridge that only honours the token captured at init gets 401 everywhere after one web restart.
        tok = (_resolve_dsh_token() or self.token or "").strip()
        self.token = tok
        if tok:
            url = f"{self._base}/?token={urllib.parse.quote(tok, safe='')}"
            try:
                with opener.open(url, timeout=20) as r:
                    r.read()
            except Exception as e:  # noqa: BLE001
                raise DshClientError(
                    f"换取 cookie 失败（{self._base}）: {e}", code="auth"
                ) from e
        self._opener = opener
        return opener

    def _rpc(self, method: str, payload: dict, *, timeout: float = 30) -> dict:
        """Send one RPC, return result.value (raise DshClientError when ok=false).

        * 2026-11-17 adaptation to the new DSH 0.2.2 protocol (three differences from the old implementation):
          1) the endpoint uses slashes: ``/api/<namespace>/<method>`` (the dotted form 404s)
          2) the payload is wrapped one level: ``payload.args.<argsKey>`` (session/list uses ``_request``)
          3) a cookie is required: ``GET /?token=...`` first (401 without auth)
"""
        endpoint = method.replace(".", "/")
        args_key = "_request" if endpoint == "session/list" else "request"
        rpc_id = "c" + uuid.uuid4().hex[:12]
        body = json.dumps(
            {
                "type": "client-request",
                "rpcId": rpc_id,
                "method": endpoint,
                "payload": {"args": {args_key: payload}},
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self._base}/api/{endpoint}",
            data=body,
            method="POST",
            headers={
                # The Host header must match the loopback fence (browser-trust)
                "Host": f"{self.host}:{self.port}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        opener = self._ensure_opener()
        try:
            with opener.open(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:  # 401 / 415 / 4xx / 5xx
            detail = e.read().decode("utf-8", "replace")
            if e.code == 401:
                # The cookie or token may have expired (a web restart rotates the token)
                # -> clear the token too, so the next _ensure_opener re-reads the file
                self._opener = None
                self.token = ""
            raise DshClientError(
                f"HTTP {e.code} calling {endpoint}: {detail[:400]}",
                code=f"http-{e.code}",
            ) from e
        except urllib.error.URLError as e:
            raise DshClientError(f"连接 {self._base} 失败: {e.reason}", code="transport") from e

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise DshClientError(
                f"{endpoint} 返回非 JSON: {raw[:300]}", code="bad-json"
            ) from e

        result = data.get("result", {})
        if result.get("ok") is True:
            return result.get("value", {})
        err = result.get("error", {}) or {}
        raise DshClientError(
            f"{endpoint} 失败: {err.get('code', '?')} - {err.get('message', '?')}",
            code=err.get("code", "?"),
            details=err.get("details"),
        )

    # -- Version ----------------------------------------------------------

    def actual_dsh_version(self) -> str:
        """Read `dsh --version` (the first non-empty line after merging stdout/stderr)."""
        out = subprocess.run(
            [self.node_bin, self.dsh_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        merged = (out.stdout or "") + "\n" + (out.stderr or "")
        for line in merged.splitlines():
            line = line.strip()
            if line:
                return line
        return ""

    def assert_version(self) -> str:
        """Verify DSH version == DSH_TARGET_VERSION, otherwise raise DshVersionError."""
        v = self.actual_dsh_version()
        self.actual_version = v
        if v != DSH_TARGET_VERSION:
            raise DshVersionError(
                f"DSH 版本不符：实测 {v!r} != 锁定 {DSH_TARGET_VERSION!r}。"
                "契约 schema 可能漂移，请重新验证后更新 DSH_TARGET_VERSION。"
            )
        return v

    def _tail_log(self, n: int = 40) -> str:
        try:
            lines = Path(self.log_path).read_text(errors="replace").splitlines()
            return "\n".join(lines[-n:])
        except Exception:
            return "<no log>"

    # -- Session surface --------------------------------------------------

    def create_session(
        self, session_id: Optional[str] = None, *, agent_preset: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> str:
        """Create a session, return the sessionId.

        Pass ``session_id`` -- DSH **keeps it verbatim** (no format constraint); that is the
        premise for "AO execution and DSH session ids aligned with zero mapping table". If not
        passed, DSH generates ``session-<uuid>``.

        * Pass ``workspace_id`` -- the session is grouped into that workspace (corrected on a live
        machine 2026-10-16: passing only cwd does not group it and it falls into "ungrouped";
        workspaceId must be passed).
"""
        payload: dict = {}
        if session_id is not None:
            payload["sessionId"] = session_id
        if agent_preset is not None:
            payload["agentPreset"] = agent_preset
        if workspace_id:
            payload["workspaceId"] = workspace_id
        value = self._rpc("session.create", payload)
        return value.get("sessionId", "")

    def ensure_title(self, session_id: str, title: str) -> bool:
        """Pin a deterministic title on the session (idempotent).

        * Why pinning is mandatory (pointed out by Feng on a live run, 2026-11-17):
          DSH ships an auto-naming plugin: a new session titles itself with a Chinese sentence
          derived from the prompt (measured examples: "awaiting confirmation reply",
          "reply with a single word: ok") -- in the sidebar you cannot tell whose session this is,
          which amounts to "the session id never landed".
          The session id is AO's hard identity, so the title must be derived from it and must not
          be left to the model's guess.

        * Write only when the title is empty: an existing title is left alone, avoiding a rename
          event on every round (each rename appends an event and bumps seq, and past versions had
          a lock-handle risk).
"""
        t = (title or "").strip()
        if not session_id or not t:
            return False
        try:
            value = self._rpc("session/projections", {"sessionId": session_id}, timeout=15)
            rows = (value or {}).get("values") or {}
            cur = rows.get("title")
            cur_val = cur.get("val") if isinstance(cur, dict) else cur
            # * Cannot test for "empty" alone: DSH names a new session after the sessionId itself
            #   (measured: the title of dshlab_pm-universal-run_xxx equals that id) -> must compare against the target title
            if cur_val == t:
                return False                      # Already the target title
        except Exception:  # noqa: BLE001
            pass                                  # If lookup fails, just try to write it
        try:
            self._rpc("session/rename", {"sessionId": session_id, "title": t}, timeout=20)
            log(f" 会话标题已钉定 | {session_id} -> {t!r}")
            return True
        except Exception as e:  # noqa: BLE001
            log(f" 会话标题钉定失败（{session_id}）: {e}", "WARN")
            return False

    def lookup_workspace_id(self, workspace_path: str) -> str:
        """Fetch (or create) the workspaceId for a path; return "" on failure.

        * 0.2.2 has no ``workspace/list`` (404). ``workspace/create`` is idempotent: when the
          workspace already exists it returns ``created:false`` plus the existing object.
        * Creating a session must pass workspaceId to be grouped (cwd alone does not group it).
"""
        try:
            value = self._rpc("workspace.create", {"path": workspace_path}, timeout=15)
        except Exception:  # noqa: BLE001
            return ""
        ws = value.get("workspace") or {}
        return str(ws.get("workspaceId") or "")

    def prompt(self, session_id: str, text: str, timeout_s: int = 300) -> dict:
        """Send a message to a session, **wait until this round ends**, return a structured result.

        Returns::

            {"ok": bool, "text": str, "session_id": str,
             "turn": int|None, "error": str|None, "elapsed_s": float}

        * 2026-11-17 adaptation to 0.2.2:
          * ``session/prompt`` must carry a client-minted ``requestId``
          * completion detection switched to ``session/wait`` (the server blocks until quiet and
            returns output.records directly), no longer polling ``session/history`` (that endpoint
            is gone in 0.2.2)
"""
        t0 = time.time()
        rid = "c" + uuid.uuid4().hex[:12]
        self._rpc(
            "session.prompt",
            {
                "requestId": rid,
                "sessionId": session_id,
                "mode": "queue",
                "content": [{"type": "text", "text": text}],
                "clientTimeZone": "Asia/Shanghai",
            },
            timeout=60,
        )

        try:
            waited = self._rpc(
                "session.wait",
                {"sessionId": session_id, "timeoutMs": int(max(1, timeout_s) * 1000)},
                timeout=float(timeout_s) + 30.0,
            )
        except DshClientError as e:
            if "timeout" in str(e).lower() or "wait-timeout" in str(e).lower():
                raise DshTimeoutError(
                    f"session {session_id} 在 {timeout_s}s 内未产出答复", code="timeout"
                ) from e
            raise

        reason = str(waited.get("reason") or "")
        state = waited.get("state") or {}
        records = ((waited.get("output") or {}).get("records")) or []
        reply, turn, _seq, _ended = self._extract_assistant_text(records, after_seq=-1)
        elapsed = round(time.time() - t0, 3)

        if reply:
            ok = (reason == "idle") and not bool(state.get("running"))
            return {
                "ok": ok,
                "text": reply,
                "session_id": session_id,
                "turn": turn,
                "error": None if ok else f"wait reason={reason or '?'} running={state.get('running')}",
                "elapsed_s": elapsed,
            }
        if reason == "pending-input":
            return {
                "ok": False,
                "text": "",
                "session_id": session_id,
                "turn": None,
                "error": "session is waiting for human input (approval?)",
                "elapsed_s": elapsed,
            }
        raise DshTimeoutError(
            f"session {session_id} 在 {timeout_s}s 内未产出答复（reason={reason or '?'}）",
            code="timeout",
        )

    def resume(self, session_id: str, text: str, timeout_s: int = 300) -> dict:
        """Continue on top of previous rounds (reusing the same sessionId). Semantically identical to ``prompt``."""
        return self.prompt(session_id, text, timeout_s=timeout_s)

    # -- WebSocket subscription support (2026-11-17) ------------------------

    def cookie_header(self) -> str:
        """Return a Cookie header usable in the WS handshake.

        * Why WS reuses the HTTP cookie: ``remoteStreamUrl()`` of ``/api/remote.mux`` contains
          **no token at all** (the source only converts the protocol); the browser attaches the
          same-origin cookie automatically. Python does the same thing -- go through HTTP once to
          obtain the cookie, then flatten the jar into a header.
"""
        opener = self._ensure_opener()
        for h in getattr(opener, "handlers", []):
            jar = getattr(h, "cookiejar", None)
            if jar is not None:
                return "; ".join(f"{ck.name}={ck.value}" for ck in jar)
        return ""

    def ws_url(self) -> str:
        """The ws:// address of `/api/remote.mux`."""
        return self._base.replace("http://", "ws://").replace("https://", "wss://") + "/api/remote.mux"

    def list_sessions(self) -> list[dict]:
        """List all sessions (each entry carries projections such as sessionId / turns / running / cwd / asOfSeq)."""
        value = self._rpc("session.list", {})
        return value.get("items", []) or []

    def status(self, session_id: str) -> dict:
        """Query the state of one session: ``{"exists": bool, "running": bool, "session": {...}}``."""
        for item in self.list_sessions():
            if item.get("sessionId") == session_id:
                return {"exists": True, "running": bool(item.get("running")), "session": item}
        return {"exists": False, "running": False, "session": None}

    def history(self, session_id: str) -> list[dict]:
        """Fetch the full event stream of a session (ascending by seq).

        * Changed 2026-11-17: this used to go through `session/events`, but that RPC always returns
          0 rows in this version (the method exists but its semantics do not work; measured:
          dshlab_pm-main should have had 379 rows and returned 0).
          Switched to a paginated loop over `session/page` -- measured to return the complete event stream.
"""
        return self.all_events(session_id)

    # -- Session state and messages (added 2026-11-17, for the collector and the BFF) ------

    def session_state(self, session_id: str, *, with_stats: bool = True) -> dict:
        """Session runtime state: ``{exists, running, updated_at, title, stats, usage, plan}``.

        * Why a separate method instead of reusing status: `status()` returns the **raw entry** from
          session/list (including the whole projections blob, tens of KB). A question like
          "is this session busy right now, how many rounds has it run, how many tokens did it burn"
          deserves a flat result, otherwise every caller has to dig through projections.values itself.
"""
        st = self.status(session_id)
        if not st["exists"]:
            return {"exists": False, "running": False, "session_id": session_id}
        item = st.get("session") or {}
        pj = item.get("projections") or {}
        vals = pj.get("values") or {}

        def unwrap(key):
            raw = vals.get(key)
            return raw.get("val") if isinstance(raw, dict) and "val" in raw else raw

        # * session/list returns a *reduced projection*: only title / tokenUsage / plan etc.,
        #   without sessionStats / turnOutline / contextPressure. Full data needs a single
        #   session/projections query (a complete projection, costing one extra RPC).
        #   So fetch it only when the caller explicitly asks for stats -- a plain "is it busy" question must not pay for an extra round trip.
        if with_stats:
            try:
                full = self._rpc("session/projections", {"sessionId": session_id}, timeout=20)
                vals = (full or {}).get("values") or vals
            except DshError:  # noqa: BLE001
                pass

        return {
            "exists": True,
            "running": bool(item.get("running")),
            "session_id": session_id,
            "updated_at_ms": item.get("updatedAt"),
            "as_of_seq": pj.get("asOfSeq"),
            "title": unwrap("title"),
            "working_directory": unwrap("workingDirectory"),
            "model": unwrap("modelSelection"),
            "stats": unwrap("sessionStats"),
            "token_usage": unwrap("tokenUsage"),
            "context_pressure": unwrap("contextPressure"),
            "plan": unwrap("plan"),
            "todos": unwrap("todos"),
            "turn_outline": unwrap("turnOutline"),
            "agent_available": item.get("agentAvailable"),
            "blank": item.get("blank"),
        }

    def fetch_events(
        self, session_id: str, *, max_messages: int = 200, before_seq: int | None = None,
    ) -> dict:
        """Fetch one page of session events: ``{records, cursor, hasMore}``.

        * Difference from ``_history_events`` (corrected on a live machine 2026-11-17):
          `session/events` always returns 0 rows in this version (the method exists but its
          semantics do not work); what really returns the event stream is `session/page`, and it
          paginates by `beforeSeq` **from newest backwards** -- pulling everything requires a loop,
          it cannot be done in one call.

          The parameter is not a bare sessionId either but a ``SessionAddress``:
            {"address": {"kind": "session", "sessionId": ...},
             "maxMessages": N, "beforeSeq": <optional, exclusive upper bound>}

          The returned ``records[*].event`` looks like
            {"type": "user/message"|"assistant/message"|"tool/call"|..., "seq", "time", "data"}
"""
        args: dict = {
            "address": {"kind": "session", "sessionId": session_id},
            "maxMessages": int(max_messages),
        }
        if before_seq is not None:
            args["beforeSeq"] = int(before_seq)
        value = self._rpc("session/page", args, timeout=60)
        return {
            "records": value.get("records") or [],
            "cursor": value.get("cursor"),
            "hasMore": bool(value.get("hasMore")),
        }

    def all_events(
        self, session_id: str, *, since_seq: int = 0,
        page_size: int = 200, max_pages: int = 500,
    ) -> list[dict]:
        """Return events in ascending seq order, stopping at ``seq <= since_seq`` or when exhausted.

        * Why ``since_seq`` is needed: `session/page` only pages backwards, there is no afterSeq.
          Incremental collection can only "page backwards from the tail until the old watermark,
          then truncate" -- over-reading loses nothing, it just reads a few extra pages.
"""
        collected: list[dict] = []
        before = None
        for _ in range(max_pages):
            page = self.fetch_events(session_id, max_messages=page_size, before_seq=before)
            recs = page["records"] or []
            events = [r.get("event") for r in recs if isinstance(r, dict) and r.get("event")]
            if events:
                collected = events + collected
                lowest = min((e.get("seq") or 0) for e in events)
                if since_seq and lowest <= since_seq:
                    break
            if not page["hasMore"] or page["cursor"] is None:
                break
            before = page["cursor"]

        collected.sort(key=lambda e: e.get("seq") or 0)
        if since_seq:
            collected = [e for e in collected if (e.get("seq") or 0) > since_seq]
        return collected

    def delete_session(self, session_id: str) -> dict:
        """Clean up a session (used to wrap up the PoC).

        The DSH `/api` surface does not expose a session delete RPC, so this **cancels the current
        task and removes the persisted directory**.
        Returns ``{"cancelled": bool, "removed_dirs": [..]}``.
"""
        cancelled = False
        try:
            self._rpc("session.cancel", {"sessionId": session_id}, timeout=15)
            cancelled = True
        except DshClientError:
            pass  # The session may not be running; a failed cancel can be ignored
        removed = self._remove_session_dirs(session_id)
        return {"cancelled": cancelled, "removed_dirs": removed}

    # -- Internal helpers --------------------------------------------------

    def _as_of_seq(self, session_id: str) -> int:
        st = self.status(session_id)
        if not st["exists"]:
            return 0
        pj = (st["session"] or {}).get("projections") or {}
        try:
            return int(pj.get("asOfSeq") or 0)
        except Exception:  # noqa: BLE001
            return 0

    def _is_running(self, session_id: str) -> bool:
        return bool(self.status(session_id).get("running"))

    def _history_events(self, session_id: str) -> list[dict]:
        """Fetch the session event stream (0.2.2: session/events; returns an empty table on failure)."""
        try:
            value = self._rpc("session.events", {"sessionId": session_id}, timeout=60)
        except DshClientError:
            return []
        return value.get("events", []) or value.get("records", []) or []

    @staticmethod
    def _unwrap(ev: dict) -> dict:
        """Each item of the event stream looks like ``{"event": {type, seq, time, data}}``; this method normalises it.

        Measured (DSH 0.1.0-rc.7 `session.history`): the type is in ``ev["event"]["type"]``,
        the sequence number in ``ev["event"]["seq"]``, and the payload in ``ev["event"]["data"]``.
        For compatibility, an already flattened structure is accepted too.
"""
        inner = ev.get("event")
        return inner if isinstance(inner, dict) else ev

    @staticmethod
    def _extract_assistant_text(
        events: list[dict], after_seq: int = 0
    ) -> tuple[Optional[str], Optional[int], int, bool]:
        """Extract the assistant/message text of this round after "seq > after_seq" from the event stream.

        Returns ``(text, turn, seq, done_turn)``; text=None when nothing matches.
        ``done_turn`` means that turn already has a ``turn/end``.
"""
        text, turn, seq, ended = None, None, -1, False
        for raw_ev in events:
            ev = DshClient._unwrap(raw_ev)
            etype = ev.get("type") or ev.get("kind") or ""
            ev_seq = int(ev.get("seq", -1) or -1)
            if ev_seq <= after_seq:
                continue
            data = ev.get("data") if isinstance(ev.get("data"), dict) else ev
            if etype == "assistant/message":
                t = DshClient._flatten_text(data.get("message") or data.get("content"))
                if t:
                    text, turn, seq = t, data.get("turn"), ev_seq
            elif etype == "turn/end":
                if data.get("turn") == turn:
                    ended = True
        return text, turn, seq, ended

    @staticmethod
    def _flatten_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            if "text" in content and isinstance(content["text"], str):
                return content["text"]
            parts = content.get("content") or content.get("parts") or []
            return DshClient._flatten_text(parts)
        if isinstance(content, list):
            return "".join(DshClient._flatten_text(p) for p in content)
        return str(content)

    @staticmethod
    def _remove_session_dirs(session_id: str) -> list[str]:
        """Remove the ~/.dsh/sessions/*/<session_id> persisted directory (where the session is really written to disk)."""
        removed: list[str] = []
        root = Path(os.environ.get("DSH_HOME", "~/.dsh")).expanduser() / "sessions"
        if not root.exists():
            return removed
        for cwd_dir in root.iterdir():
            if not cwd_dir.is_dir():
                continue
            target = cwd_dir / session_id
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
                removed.append(str(target))
        return removed


# ----------------------------------------------------------------------------
# Minimal self-check: `python dsh_client.py`
# ----------------------------------------------------------------------------



# ════════════════════════════════════════════════════════════════════════════
# AgentGeeX <-> DSH bridge main logic (same shape as scripts/agx_ws_client.py)
# ════════════════════════════════════════════════════════════════════════════
#
# Contract (byte-for-byte aligned with ws_client; the daemon side reuses the same result key space):
#   in : agx:dsh:task:{host_id}   BLPOP  <- daemon RPUSHes here when agent_type=="dsh"
#        value JSON {run_uuid, agent_id, instruction, timeout_seconds, session_key, mode}
#   out: agx:ws:result:{run_uuid} SET    -> daemon polls "agx:ws:result:run_*" and persists
#        value JSON {run_uuid, status, result, session_key}
#        active: agx:dsh:running:{host_id} = concurrency (ex=30) (mirrors agx:ws:running in ws_client)
#
# Id alignment (spec 4.1, zero mapping table): DSH sessionId := run_uuid (idempotent create)
#   => one AO execution <-> one DSH session; create and continue go through the same path.
#
# * Boundary (Feng, 2026-10-15): the bridge **connects only to an already running dsh web** (loopback),
#   and when it cannot connect, that task is written as failed at once (never spawn dsh web itself, never drag the daemon down).
# ════════════════════════════════════════════════════════════════════════════

import asyncio
import json
import os
import signal as _signal
import sys
import traceback

import redis as redis_mod

# Parent directory of scripts/ = project root (repo root or ~/.agx), same approach as ws_client
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config_loader import config  # noqa: E402
from log_setup import setup_logging  # noqa: E402

log = setup_logging("DSH", log_dir=os.path.join(_PROJECT_ROOT, "logs"))

# -- Config -------------------------------------------------------------
HOST_ID = os.environ.get("AGX_DAEMON_HOST_ID", config.get("host_id", ""))

DSH_TASK_QUEUE = f"agx:dsh:task:{HOST_ID}"  # * New queue (defined identically in the daemon)
DSH_RESULT_KEY = "agx:ws:result:{run_uuid}"  # Shared with ws_client (the daemon only recognises this one)
DSH_RUNNING_KEY = f"agx:dsh:running:{HOST_ID}"  # Active-count reporting (mirrors ws_client)

# Resident DSH service (connection target; 2026-10-16: read from the `dsh` section of the config file, env may override)
_DSH_CFG = config.get("dsh", {})
DSH_HOST = os.environ.get("AGX_DSH_HOST", _DSH_CFG.get("host", "127.0.0.1"))
DSH_BRIDGE_PORT = int(os.environ.get("AGX_DSH_PORT", _DSH_CFG.get("port", 8791)))
DSH_PROFILE = os.environ.get("AGX_DSH_PROFILE", _DSH_CFG.get("profile", "web"))
DSH_AUTOSTART = str(os.environ.get("AGX_DSH_AUTOSTART", _DSH_CFG.get("autostart", False))).lower() in ("1", "true", "yes")
DSH_CHECK_VERSION = str(os.environ.get("AGX_DSH_CHECK_VERSION", _DSH_CFG.get("check_version", False))).lower() in ("1", "true", "yes")

# * 2026-11-15: access surface switch. Since DSH 0.2.1-rc.5 HTTP `POST /api/<method>` is gone,
#   the official replacement is stdio JSON-RPC (`dsh sdk`). Default is sdk; http is kept as a fallback
#   (if upstream restores the HTTP surface later, set AGX_DSH_BACKEND=http, no code change needed).
DSH_BACKEND = os.environ.get("AGX_DSH_BACKEND", _DSH_CFG.get("backend", "http")).strip().lower()
# sdk backend: model routing and process reuse window (0 = exit when done, >0 = idle retention in seconds)
DSH_SDK_PROVIDER = os.environ.get("AGX_DSH_PROVIDER", _DSH_CFG.get("provider", "deepseek-official"))
DSH_SDK_MODEL = os.environ.get("AGX_DSH_MODEL", _DSH_CFG.get("model", "deepseek-v4-flash"))
DSH_SDK_IDLE_TTL = int(os.environ.get("AGX_DSH_IDLE_TTL", _DSH_CFG.get("idle_ttl_s", 300)))
# * Pool size cap (Feng, 2026-11-16, plan C): one dsh sdk child process measures 240MB RSS,
#   max_clients=8 -> capped at about 1.9GB; beyond that evict the least recently used (skipping busy ones).
DSH_SDK_MAX_POOL = int(os.environ.get("AGX_DSH_MAX_POOL", _DSH_CFG.get("max_pool", 8)))
# How often the main loop reclaims idle child processes. Must be < idle_ttl, otherwise reclaiming is too coarse.
# * This loop is the only executor of idle_ttl -- delete it and idle_ttl becomes a dead parameter at once.
DSH_REAP_INTERVAL = int(os.environ.get("AGX_DSH_REAP_INTERVAL", 30))

# -- Session message collection (Feng, 2026-11-17: the collector lives and dies with the bridge, no separate service) --
#   interval=0 turns it off.
#
#   * 2026-11-18 00:20: lowered from 120s to 5s (Feng: "fine, go test it now").
#     Origin: Feng asked "how does the OpenClaw side handle live messages?" -- investigation showed
#     **the OpenClaw frontend is not push at all, everything is polling**:
#       - user_msg_collect.py --watch scans archives into the DB every 2s (fingerprint skips unchanged files)
#       - frontend session-history.tsx polls /sessions/messages every 15s (queries MySQL)
#       - the running-state badge polls every 4s; hidden pages do not poll, switching back refetches at once
#     The DSH structure is identical (collect into DB + frontend polling); the only difference is the period (120s vs 2s)
#     -> what the user perceives is 135s vs 17s, a factor of 8.
#
#     Measured (6 sessions, 3 consecutive rounds on 188): one full _collect_once takes only **0.135~0.173s**
#     (connecting accounts for just 5~23ms, so pooling brings nothing and was not adopted).
#     So a 5s period costs about 0.15/5 = 3%, and it runs in to_thread so task consumption is not blocked.
#
#     Why not 2s (matching OpenClaw): the frontend polls every 15s, so being 3x faster than that is enough;
#     worst-case perception is 5+15=20s, 3 seconds off OpenClaw's 17s, which nobody can feel,
#     while the cost drops from 7.5% to 3%. **The collection period only needs to be <= the frontend polling period; anything faster is wasted.**
DSH_COLLECT_INTERVAL = int(os.environ.get("AGX_DSH_COLLECT_INTERVAL", 5))
#     Startup delay: right after the bridge comes up it must first get queue consumption running, do not fight the first probe for connections
DSH_COLLECT_STARTUP_DELAY = int(os.environ.get("AGX_DSH_COLLECT_STARTUP", 30))
# -- Live state subscription (WS $events, 2026-11-17) ------------------------------
#   interval=0 turns the subscription off. It has no polling period; this value only serves as an off switch plus a reconnect backoff base.
DSH_STATE_WATCH_INTERVAL = int(os.environ.get("AGX_DSH_WATCH_INTERVAL", 1))
#   Startup delay: wait until the bridge has HTTP auth working, only then can WS obtain a cookie
DSH_STATE_WATCH_DELAY = int(os.environ.get("AGX_DSH_WATCH_DELAY", 20))
#   How long live state survives in Redis. Tasks last seconds to minutes, so one hour covers it,
#   and after expiry it naturally falls back to "unknown" instead of sitting forever on the last pushed stale value.
DSH_STATE_LIVE_TTL = int(os.environ.get("AGX_DSH_LIVE_TTL", 3600))
# * 2026-11-17 reader-side contract: live-state key prefix plus index set
#   The BFF (session_runtime) reads the index set to get the sid list, then HGETALLs each one --
#   it must not use KEYS/SCAN, which is an O(N) full-keyspace scan.
DSH_LIVE_KEY_PREFIX = "agx:dsh:live:"
DSH_LIVE_INDEX_KEY = "agx:idx:dsh:live"
#   Collector location (tools/ has dsh_msg_collect.py)
#   * The collector sits in the same directory as the bridge (scripts/) so they move together and never drift --
#     it once lived in tools/ while the bridge lived in scripts/, two deployment paths updated separately, bound to mismatch.
DSH_COLLECT_TOOLS_DIR = os.environ.get(
    "AGX_DSH_COLLECT_TOOLS_DIR", os.path.expanduser("~/.agx/scripts")
)
# Root of agent working directories (landing root for agents with agent_type=dsh; used for path injection)
# * Directory layout (Feng decision): ~/.agx/agx_agents
#   Decide by *on-disk existence*, never by remembered config; openclaw_agents is probed only for legacy-structure compatibility.
def _resolve_dsh_agent_root() -> str:
    for _wd_name in (".agx",):
        _wd = os.path.join(str(Path.home()), _wd_name)
        for _sub in ("agx_agents", "openclaw_agents"):
            _p = os.path.join(_wd, _sub)
            if os.path.isdir(_p):
                return _p
    return os.path.join(str(Path.home()), ".agx", "agx_agents")


DSH_AGENT_ROOT = os.environ.get("AGX_DSH_AGENT_ROOT") or _resolve_dsh_agent_root()

# * 2026-11-17: added the AGX_DSH_MAX_CONCURRENT override -- per-entry dsh config must be able to take effect,
#   otherwise the supervisor passes env and it is ignored here, i.e. silently dropped (worse than not passing it).
MAX_CONCURRENCY = int(
    os.environ.get("AGX_DSH_MAX_CONCURRENT")
    or _DSH_CFG.get("max_concurrent", config.ws.get("max_concurrent", 4))
)
TASK_TIMEOUT = int(config.get("task_timeout", 300))

_DSH_REDIS_POOL = redis_mod.ConnectionPool(
    host=config.redis.get("host", "127.0.0.1"),
    port=int(config.redis.get("port", 6379)),
    db=int(config.redis.get("db", 0)),
    username=config.redis.get("username", "default"),
    password=config.redis.get("password", ""),
    decode_responses=True,
    max_connections=8,
    socket_timeout=5,
    socket_connect_timeout=5,
    health_check_interval=10,
    socket_keepalive=True,
)


class DshBackend:
    """Access-surface abstraction: whether the bottom layer is HTTP or stdio JSON-RPC, the outside
only sees probe/run.

    Two implementations:
      SdkBackend  -- spawns a `dsh sdk` child process and talks over pipes (current default)
      HttpBackend -- the original `POST /api/<method>` path (usable only if upstream restores the HTTP surface)
"""

    name = "base"

    def probe(self) -> str:
        """Probe liveness; return the version string, raise when unavailable."""
        raise NotImplementedError

    def run(self, agent_id: str, session_key: str, instruction: str, timeout_s: int) -> dict:
        """One round of Q&A; returns {ok, text, error, turn, elapsed_s}."""
        raise NotImplementedError

    def reap(self) -> int:
        """Reclaim idle child processes; a backend without a pool returns 0."""
        return 0

    def close(self) -> None:
        pass


class SdkBackend(DshBackend):
    """stdio JSON-RPC backend: one sdk child process per agent working directory (pooled and reused).

    - Session ids reuse the AO session_key (e.g. agent:xxx:main), interoperable with web-side sessions.
    - cwd must be realpath'd: a symlink makes DSH split into two sets of session directories.
    - idle_ttl_s=0 -> exit when done; >0 -> keep idle.
"""

    name = "sdk"

    def __init__(self, agent_root: str, provider: str, model: str,
                 idle_ttl_s: int = 300, max_clients: int = 8):
        from agx_dsh_sdk_client import DshSdkPool, find_dsh_bin  # Module in the same directory

        self._find = find_dsh_bin
        self.agent_root = agent_root
        self.pool = DshSdkPool(provider=provider, model=model,
                               idle_ttl_s=idle_ttl_s, max_clients=max_clients)
        self._version = ""

    def probe(self) -> str:
        if self._version:
            return self._version
        bin_path = self._find()  # Raises DshSdkError when it cannot be found
        try:
            out = subprocess.run(
                [find_node_bin(), bin_path, "--version"],
                capture_output=True, text=True, timeout=20,
            )
        except Exception as e:  # noqa: BLE001
            raise DshClientError(f"dsh --version 执行失败: {e}") from e
        ver = ((out.stdout or "") + (out.stderr or "")).strip().splitlines()
        self._version = ver[0] if ver else "unknown"
        return self._version

    def _workspace(self, agent_id: str) -> str:
        ws = os.path.join(self.agent_root, agent_id, "workspace")
        ws = os.path.realpath(ws)
        if not os.path.isdir(ws):
            # DSH archives session directories by cwd; create the directory when missing so nothing lands in "ungrouped".
            os.makedirs(ws, exist_ok=True)
            log(f" workspace 不存在，已创建: {ws}", "WARN")
        return ws

    def run(self, agent_id: str, session_key: str, instruction: str, timeout_s: int) -> dict:
        sid = to_dsh_sid(session_key or f"agent:{agent_id}:main")
        ws = self._workspace(agent_id)
        # borrow rather than get: mark busy during the prompt so reap/LRU does not take the process away mid-turn.
        with self.pool.borrow(ws) as client:
            return client.prompt(sid, instruction, timeout_s=int(timeout_s))

    def reap(self) -> int:
        """Reclaim idle child processes (called periodically by the bridge main loop)."""
        return self.pool.reap_idle()

    def pool_stats(self) -> dict:
        try:
            return self.pool.stats()
        except Exception:  # noqa: BLE001
            return {}

    def close(self) -> None:
        try:
            self.pool.close_all()
        except Exception:  # noqa: BLE001
            pass


class HttpBackend(DshBackend):
    """The original HTTP backend (kept as a fallback). Depends on `POST /api/<method>`, which 404s since 0.2.1-rc.5."""

    name = "http"

    def __init__(self, client: "DshClient"):
        self._titled: set = set()
        self.client = client

    def probe(self) -> str:
        """Probe: being able to list means ready.

        * A version mismatch only warns and does not block -- with check_version=False it should not
          be blocked anyway; and the CLI self-reported version and the npm package version may be out
          of sync (measured CLI=0.2.1-rc.5 / package=0.2.2-rc.1), so using it as a hard gate would keep
          the bridge from ever starting.
"""
        self.client.list_sessions()
        try:
            return self.client.assert_version()
        except DshVersionError:
            actual = self.client.actual_version or "unknown"
            log(f" DSH 版本与锁定值不同（实测 {actual} / 锁定 {DSH_TARGET_VERSION}）→ 继续运行", "WARN")
            return actual

    def _session_exists(self, sid: str) -> bool:
        """Whether the session already exists (decides whether to create).

        * 2026-11-17: this check is the core of this fix.
          Measured DSH `session/create` semantics (see the endpoint definition result.description):
             "The created or reused Session, **which may differ from the requested
              sessionId**" -- it only reuses a "blank and reusable" session with the same name,
              otherwise it **creates a new one**. So:
            - session does not exist -> the passed id is adopted (measured dshlab_qa-probe0720 -> same value)
            - session exists and has content -> **the passed id is ignored and a new session-<uuid> shell is created**
          The original implementation called create unconditionally on every run -> one shell per task
          (measured: 38 tasks accumulated 34 shells, 59% of the session list).
          Fix: create only when it does not exist. When lookup fails, **treat it as existing**
          (better to not create than to create junk).
"""
        try:
            for item in self.client.list_sessions():
                if item.get("sessionId") == sid:
                    return True
            return False
        except Exception as e:  # noqa: BLE001
            log(f" 会话存在性查询失败（{e}）→ 按已存在处理，不 create", "WARN")
            return True

    def run(self, agent_id: str, session_key: str, instruction: str, timeout_s: int) -> dict:
        sid = to_dsh_sid(session_key or f"agent:{agent_id}:main")
        workspace = os.path.join(DSH_AGENT_ROOT, agent_id, "workspace")
        # * Create only when the session does not exist (otherwise DSH creates an empty session-<uuid> shell)
        if self._session_exists(sid):
            pass
        else:
            wid = ""
            try:
                wid = self.client.lookup_workspace_id(workspace)
            except Exception as e:  # noqa: BLE001
                log(f" workspaceId 查询失败（{e}）→ 本次不带 workspaceId", "WARN")
            try:
                got = self.client.create_session(sid, workspace_id=(wid or None))
                if got and got != sid:
                    log(f" 警告：create 返回 {got} ≠ 请求 {sid}（DSH 未采用指定 id）", "WARN")
                if not wid and os.path.isdir(workspace):
                    log(f" 未找到 '{workspace}' 对应的 workspaceId → 会话可能落未分组区", "WARN")
            except Exception as e:  # noqa: BLE001
                log(f" session.create 失败（{e}）→ 回退不带 workspaceId 重试", "WARN")
                self.client.create_session(sid)
        # * Derive the title from the session id (agent:dshlab_pm:main -> 'main'),
        #   so DSH auto-naming does not fill the sidebar with Chinese sentences.
        self._ensure_title(sid, session_key or f"agent:{agent_id}:main")
        return self.client.prompt(sid, instruction, timeout_s=int(timeout_s))

    def _ensure_title(self, sid: str, session_key: str) -> None:
        """Pin once per session (in-process memory), avoiding a projections query on every round."""
        if sid in self._titled:
            return
        sk = (session_key or "").strip()
        if sk.startswith("agent:") and sk.count(":") >= 2:
            _, _aid, rest = sk.split(":", 2)
            name = rest.replace(":", "-")
        else:
            name = sk.replace(":", "-")
        name = name.strip() or "main"
        if self.client.ensure_title(sid, name):
            self._titled.add(sid)
        else:
            self._titled.add(sid)   # An existing manual title also counts as handled

    def close(self) -> None:
        try:
            self.client.stop()
        except Exception:  # noqa: BLE001
            pass


class AgxDshClient:
    """Bridge between AO execution and DSH session (single process; asyncio main loop + one coroutine per task)."""

    def __init__(self):
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        self.active_tasks: set[asyncio.Task] = set()
        self.dsh: Optional[DshClient] = None          # http backend only
        self.backend: Optional[DshBackend] = None      # Access surface (sdk/http)
        self.server_up = False
        self.version_ok = False
        self._stopping = False

    # ── Redis ───────────────────────────────────────────────────────
    @staticmethod
    def _redis():
        return redis_mod.Redis(connection_pool=_DSH_REDIS_POOL)

    def _mark_active_run(self, agent_id: str, run_uuid: str, timeout_s: int) -> None:
        """Write the run_uuid field of agx:active:run:{agent_id} (same key and same semantics as ws_client).

        * 2026-11-16 gap 2 fix (details in the comment inside _run_one_turn):
          on the MCP side agx_get_execution uses this key to decide "was this run really delivered",
          and writes started_at lazily based on it. Without it, started_at stays empty forever -> the
          watchdog judges an already finished task as "sent N seconds ago and still not started" and
          raises an L2 alert.

        Failure does not block execution: at worst one started_at is lost (the old behaviour), and a
        single Redis hiccup must not block the task itself.
"""
        if not run_uuid:
            return
        try:
            r = self._redis()
            key = f"agx:active:run:{agent_id}"
            try:
                r.hset(key, run_uuid, "1")
            except Exception:
                # The key was written with a non-hash type (should not happen in production) -> rebuild, otherwise marking fails silently
                r.delete(key)
                r.hset(key, run_uuid, "1")
            r.expire(key, max(int(timeout_s or 0) + 1800, 1800))
        except Exception as e:  # noqa: BLE001
            log(f" 活动标记写入失败 | {run_uuid}: {e}", "WARN")

    # -- Access-surface management (sdk: spawn a child process; http: connect to an existing web) ------
    def _ensure_dsh(self) -> Optional[DshBackend]:
        """Return a usable backend; None when unavailable (do not block, retry next time)."""
        if self.backend is not None and self.server_up:
            return self.backend
        try:
            if self.backend is None:
                if DSH_BACKEND == "http":
                    self.dsh = DshClient(
                        host=DSH_HOST, port=DSH_BRIDGE_PORT,
                        autostart=DSH_AUTOSTART, check_version=DSH_CHECK_VERSION,
                    )
                    self.backend = HttpBackend(self.dsh)
                else:
                    self.backend = SdkBackend(
                        agent_root=DSH_AGENT_ROOT,
                        provider=DSH_SDK_PROVIDER,
                        model=DSH_SDK_MODEL,
                        idle_ttl_s=DSH_SDK_IDLE_TTL,
                        max_clients=DSH_SDK_MAX_POOL,
                    )
            ver = self.backend.probe()
            self.server_up = True
            if not self.version_ok:
                self.version_ok = True
                log(f" DSH 后端就绪 | backend={self.backend.name} | version={ver}")
            return self.backend
        except Exception as e:  # noqa: BLE001
            self.server_up = False
            log(f" DSH 后端不可用（backend={DSH_BACKEND}）: {e} —— 待其就绪后自动重连", "WARN")
            return None

    # -- One turn (blocking; executed inside to_thread) ---------------------------
    def _run_one_turn(self, run_uuid: str, agent_id: str, instruction: str, timeout_s: int, mode: str, session_key: str = "") -> dict:
        # * Id alignment (fixed 2026-10-16): sessionId reuses the AO session id (session_key),
        #   not run_uuid. Reason: run_uuid is a one-shot execution id -> it would create a new
        #   DSH session per task (pollution + broken context); session_key is the correct identity
        #   for "several tasks in the same session sharing context".
        #   - upstream only passes session_key for continue_task; create does not pass it -> fall back to main.
        #   - falling back to main rather than run_uuid guarantees a new task lands in the agent main session by default.
        #   * 2026-11-17: the two sides now have separate ids -- session_key keeps the OpenClaw shape
        #   (agent:<id>:main, used when writing receipts back), dsh_sid is the DSH-safe shape (<id>-main).
        session_key = session_key or f"agent:{agent_id}:main"
        dsh_sid = to_dsh_sid(session_key)

        backend = self._ensure_dsh()
        if backend is None:
            return {
                "run_uuid": run_uuid,
                "status": "failed",
                "result": f"DSH 后端不可用（backend={DSH_BACKEND}），桥不自行拉起",
                "session_key": session_key,
            }

        # * 2026-11-16 gap 2 fix: write the "this round really started" mark, same key and same semantics as ws_client.
        #   The watchdog uses started_at IS NULL to decide "not started", and started_at is lazily written by the
        #   MCP side in agx_get_execution -- provided this run_uuid exists in agx:active:run:{agent}
        #   as the "really delivered" gate signal. The bridge never wrote that mark before, so:
        #   a task finished in 6 seconds with its result delivered, yet 3602 seconds later the watchdog still judged "not started" and
        #   raised an L2 alert (evidence: run_general_ab81d940fdc6, 2026-11-16 12:40).
        #   TTL matches ws_client (timeout+1800); it disappears naturally after completion or timeout, no cleanup needed.
        self._mark_active_run(agent_id, run_uuid, int(timeout_s))

        # * 2026-11-16 gap 5 fix: send the close-out requirement along with the prompt (DSH does not read AGENTS.md)
        # * 2026-11-17: send_text (receipt/alert) does *not* create an execution, so the hint must change to
        #   "read + acknowledge", otherwise the model calls agx_finish_task and misses agx_ack_receipt.
        if mode == "send_text":
            effective_instruction = instruction + _notice_hint(instruction, agent_id)
        else:
            effective_instruction = instruction + _platform_hint(run_uuid, agent_id)

        res = backend.run(agent_id, dsh_sid, effective_instruction, int(timeout_s))
        ok = bool(res.get("ok"))
        log(
            f" turn 结束 | {run_uuid} | mode={mode} | ok={ok} | turn={res.get('turn')} "
            f"| {res.get('elapsed_s')}s | backend={backend.name}"
        )
        return {
            "run_uuid": run_uuid,
            "status": "succeeded" if ok else "failed",
            "result": (res.get("text") or res.get("error") or ""),
            "session_key": session_key,
        }

    # -- Single-task wrapper (timeout/exception backstop: never let a task hang) -----------------
    async def _handle_task(
        self,
        r,
        run_uuid: str,
        agent_id: str,
        instruction: str,
        timeout_s: int,
        session_key: str = "",
        mode: str = "create",
    ) -> None:
        async with self.semaphore:
            try:
                hard = float(timeout_s) + 60  # 60s wider than the DSH timeout itself, a hard backstop
                outcome = await asyncio.wait_for(
                    asyncio.to_thread(self._run_one_turn, run_uuid, agent_id, instruction, int(timeout_s), mode, session_key),
                    timeout=hard,
                )
            except TimeoutError:
                log(f"❌ 任务硬超时 | {run_uuid} | > {timeout_s}s", "ERROR")
                outcome = {
                    "run_uuid": run_uuid,
                    "status": "failed",
                    "result": f"任务执行超时，已被桥硬卫士截断并释放插槽 ({timeout_s}s)",
                    "session_key": session_key or f"agent:{agent_id}:main",
                }
            except Exception as e:  # noqa: BLE001
                log(f" 任务异常 | {run_uuid} | {type(e).__name__}: {e}\n{traceback.format_exc()}", "ERROR")
                outcome = {
                    "run_uuid": run_uuid,
                    "status": "failed",
                    "result": f"{type(e).__name__}: {e}",
                    "session_key": session_key or f"agent:{agent_id}:main",
                }

            try:
                # * 2026-10-25 DH-067 fix: aligned with ws_client, the result must carry the host that produced it.
                #   The daemon decides ownership from it, so another host cannot pick up this host's result and retry it against the wrong host.
                if isinstance(outcome, dict):
                    outcome["host_id"] = HOST_ID
                r.set(DSH_RESULT_KEY.format(run_uuid=run_uuid), json.dumps(outcome, ensure_ascii=False))
                log(
                    f" 结果已写入 agx:ws:result:{run_uuid} "
                    f"| status={outcome.get('status')} | host={HOST_ID}"
                )
            except Exception as e:  # noqa: BLE001
                log(f" 结果回写失败 | {run_uuid} | {e}", "ERROR")

    # -- Main loop (copied from the ws_client skeleton: BLPOP 1s + concurrency cap + active reporting) ----
    # -- Live session state subscription (WS, 2026-11-17) ---------------------------
    async def _watch_loop(self) -> None:
        """Subscribe to the DSH `$events` stream and maintain session runtime state in real time.

        * Why an in-bridge coroutine rather than a separate process/thread (Feng asked, 2026-11-17):
          `websockets` is an asyncio-native library and the bridge is already an asyncio main loop --
          a coroutine is the most natural place: no extra process, no repeated authentication, and a
          lifecycle identical to the bridge. A separate process would mean an extra cookie exchange
          plus an extra reconnect path plus one more object to babysit, buying only "isolation",
          while nothing here is heavy enough to need isolation (receive frame -> write Redis).

        * It subscribes to `$events` (the gateway's internal forwarding stream), not one stream per
          session: measured, it pushes the three kinds of events we need --
            api-session/status    [sessionId, true|false]   session busy/idle
            api-session/activity  [sessionId, ms]           last activity time
            api-session/added     [{...full projections}]   a new session was born
          One connection covers every session, far cheaper than 25 session/follow subscriptions.

        * Reconnect: an outer while plus a 5s backoff. A `dsh web` restart drops the connection, and
          this logic must exist, otherwise state would sit forever on the last push.
"""
        if DSH_STATE_WATCH_INTERVAL <= 0:
            log(" 实时状态订阅已关闭（AGX_DSH_WATCH_INTERVAL=0）")
            return
        try:
            import websockets  # noqa: F401
        except Exception as e:  # noqa: BLE001
            log(f" websockets 不可用，实时状态订阅跳过：{e}", "WARN")
            return

        await asyncio.sleep(DSH_STATE_WATCH_DELAY)
        while not self._stopping:
            try:
                await self._watch_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log(f" 实时状态订阅断开（{type(e).__name__}: {e}），5s 后重连", "WARN")
                await asyncio.sleep(5)

    async def _watch_once(self) -> None:
        """Lifecycle of one connection: open the stream -> receive frames -> until disconnect or stop."""
        import websockets

        if self.dsh is None:
            raise RuntimeError("DSH 客户端未就绪")

        url = self.dsh.ws_url()
        cookie = await asyncio.to_thread(self.dsh.cookie_header)
        log(f" 实时状态订阅连接中 | {url}")

        async with websockets.connect(
            url,
            additional_headers={"Cookie": cookie} if cookie else None,
            open_timeout=15,
            close_timeout=3,
            ping_interval=20,
            ping_timeout=20,
            max_size=None,
        ) as ws:
            await ws.send(json.dumps({
                "type": "open", "streamId": "agx-state",
                "endpoint": "$events", "payload": {"args": {}},
            }))
            log(" 实时状态订阅已建立 | 等 api-session/* 事件")

            while not self._stopping:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=60)
                except asyncio.TimeoutError:
                    continue                      # Idle is the normal state; keep waiting
                try:
                    frame = json.loads(raw)
                except Exception:  # noqa: BLE001
                    continue
                if frame.get("type") == "item":
                    self._on_watch_item(frame.get("value") or {})

    def _on_watch_item(self, value: dict) -> None:
        """Handle one frame. Only emit-type events are recognised; the rest (ready/end/error) are ignored."""
        if not isinstance(value, dict) or value.get("type") != "emit":
            return
        event = value.get("event") or ""
        args = value.get("args") or []
        try:
            if event == "api-session/status" and len(args) >= 2:
                self._set_live_state(str(args[0]), running=bool(args[1]))
            elif event == "api-session/activity" and len(args) >= 2:
                self._set_live_state(str(args[0]), last_active_ms=int(args[1]))
            elif event == "api-session/added" and args and isinstance(args[0], dict):
                self._set_live_state_from_item(args[0])
        except Exception as e:  # noqa: BLE001
            log(f" 状态事件处理失败 | {event}: {e}", "WARN")

    @staticmethod
    def _live_key(dsh_sid: str) -> str:
        return f"{DSH_LIVE_KEY_PREFIX}{dsh_sid}"

    def _set_live_state(self, dsh_sid: str, *, running=None, last_active_ms=None) -> None:
        """Write session live state into a Redis hash (the BFF can read it directly, bypassing MCP).

        * Why not write MySQL directly: `api-session/activity` is pushed on every model step, dozens
          of times per task -- one UPDATE each time is using the database as a counter.
          Redis absorbs the high-frequency writes and the DB merges at low frequency via the collector
          (which is already running).

        * 2026-11-17 (the reader-side contract added when Feng asked "how do you read the state"):
          inside DSH the same agent has two sessions coexisting (measured 22:00:11 / 22:00:26):
            - `dshlab_qa-main`          <- the one the bridge runs tasks in, can be mapped back to an AO session
            - `session-a4b75f1c-...`    <- a DSH-native session, empty title, cannot be mapped back
          their last_active_ms update at the same time (44ms apart), but only the former maps to
          `agent:<agent_id>:<name>`. So here we **only accept the AO shape**, and write the mapped
          result straight into the hash -- sparing the BFF from reimplementing the mapping rules
          (two copies of the rules would inevitably drift).
          A `agx:idx:dsh:live` set is also maintained so the BFF reads the set instead of scanning with KEYS.
"""
        if not dsh_sid:
            return
        # * Accept AO shape only: `{agent_id}-{name}` -> `agent:{agent_id}:{name}`
        session_key = to_session_key(dsh_sid)
        if not session_key.startswith("agent:"):
            return                      # DSH-native session-<uuid>, no correspondence with any AO session
        try:
            r = self._redis()
            key = self._live_key(dsh_sid)
            mapping: dict = {"session_key": session_key, "host_id": HOST_ID}
            if running is not None:
                mapping["running"] = "1" if running else "0"
            if last_active_ms is not None:
                mapping["last_active_ms"] = str(last_active_ms)
            r.hset(key, mapping=mapping)
            r.expire(key, DSH_STATE_LIVE_TTL)
            r.sadd(DSH_LIVE_INDEX_KEY, dsh_sid)
        except Exception as e:  # noqa: BLE001
            log(f" 实时状态写 Redis 失败 | {dsh_sid}: {e}", "WARN")

    def _set_live_state_from_item(self, item: dict) -> None:
        """`api-session/added` pushes a whole session/list entry -- refresh everything once.

        * Same filter as `_set_live_state` (2026-11-17): accept only the `{agent_id}-{name}` shape that
          maps back to an AO session; `session-<uuid>` is a DSH-native session with no correspondence
          to `agx_session`, and writing it would only produce junk rows on the reader side.
"""
        sid = str(item.get("sessionId") or "")
        if not sid:
            return
        session_key = to_session_key(sid)
        if not session_key.startswith("agent:"):
            return
        try:
            r = self._redis()
            key = self._live_key(sid)
            vals = ((item.get("projections") or {}).get("values") or {})
            title = vals.get("title")
            if isinstance(title, dict):
                title = title.get("val")
            mapping = {
                "session_key": session_key,
                "host_id": HOST_ID,
                "running": "1" if item.get("running") else "0",
                "last_active_ms": str(item.get("updatedAt") or ""),
            }
            if title:
                mapping["title"] = str(title)
            if item.get("cwd"):
                mapping["cwd"] = str(item.get("cwd"))
            r.hset(key, mapping=mapping)
            r.expire(key, DSH_STATE_LIVE_TTL)
            r.sadd(DSH_LIVE_INDEX_KEY, sid)
        except Exception as e:  # noqa: BLE001
            log(f" 新会话状态写 Redis 失败 | {sid}: {e}", "WARN")

    # -- Session message collection (built into the bridge, lives and dies with it) ----------------
    async def _collect_loop(self) -> None:
        """Collect DSH session messages in the background and write them into agx_user_message.

        * Why inside the bridge rather than a separate systemd timer (Feng, 2026-11-17):
          "if the bridge runs, it runs; if the bridge does not run, it does not run". The collector
          itself needs a usable DSH connection and one authentication, and the bridge already holds
          that connection. A separate service would repeat the authentication and would have to cope
          with "DSH is down but the timer keeps spinning" -- the presence of a bridge is what proves
          DSH exists, starting without a bridge is useless. So it belongs to the bridge and shares
          one lifecycle.

        * Why to_thread: collection is synchronous and blocking (pymysql + urllib); awaiting it
          directly would stall the main loop's task consumption. Running it in a thread keeps the two
          independent.

        * How to switch it off: DSH_COLLECT_INTERVAL=0.
"""
        if DSH_COLLECT_INTERVAL <= 0:
            log(" 会话消息采集已关闭（DSH_COLLECT_INTERVAL=0）")
            return

        # Wait one beat after startup -- let the bridge get queue consumption running, do not fight the first probe for connections
        await asyncio.sleep(DSH_COLLECT_STARTUP_DELAY)

        _idle_rounds = 0
        while not self._stopping:
            try:
                n = await asyncio.to_thread(self._collect_once)
                if n:
                    _idle_rounds = 0
                    log(f" 会话消息已采集 | +{n} 行", "INFO")
                else:
                    # Zero new rows would be totally silent, leaving ops unable to tell "it is still alive" --
                    # report a heartbeat every 15 rounds (about 30 minutes by default), enough to judge without spamming.
                    _idle_rounds += 1
                    if _idle_rounds % 15 == 0:
                        log(f" 会话消息采集心跳 | 连续 {_idle_rounds} 轮无新增", "INFO")
            except Exception as e:  # noqa: BLE001
                # A collection failure must never affect task dispatch -- log a warning and try again next round
                log(f" 会话消息采集失败（不影响任务）：{type(e).__name__}: {e}", "WARN")
            for _ in range(int(DSH_COLLECT_INTERVAL)):
                if self._stopping:
                    return
                await asyncio.sleep(1)

    def _collect_once(self) -> int:
        """Run one collection round and return the number of newly written rows. A synchronous function (called via to_thread)."""
        if self.dsh is None:
            return 0
        try:
            sys.path.insert(0, DSH_COLLECT_TOOLS_DIR)
            import dsh_msg_collect as collector
        except Exception as e:  # noqa: BLE001
            log(f" 采集器不可用（跳过）：{e}", "WARN")
            return 0

        cfg = collector.load_config(None)
        con = collector.connect_mysql(cfg)
        try:
            stats = collector.collect(
                self.dsh, con, HOST_ID,
                only_agent="", full=False, dry_run=False, verbose=False,
                sync_state=True,
            )
            return int(stats.get("rows") or 0)
        finally:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass

    async def run(self) -> None:
        log(
            f"AgentGeeX-DSH 桥启动 | host_id={HOST_ID} | queue={DSH_TASK_QUEUE} "
            f"| backend={DSH_BACKEND}"
            + (f" | target={DSH_HOST}:{DSH_BRIDGE_PORT}" if DSH_BACKEND == "http"
               else f" | model={DSH_SDK_PROVIDER}/{DSH_SDK_MODEL} | idle_ttl={DSH_SDK_IDLE_TTL}s")
            + f" | max_conc={MAX_CONCURRENCY}"
        )
        r = self._redis()
        # First probe (record only, do not block startup)
        self._ensure_dsh()

        # The collector coroutine starts with the bridge (and stops when the bridge stops)
        _collect_task = asyncio.create_task(self._collect_loop())
        # Live-state subscription coroutine (WS, lives and dies with the bridge)
        _watch_task = asyncio.create_task(self._watch_loop())

        _last_reap = 0.0
        while not self._stopping:
            self.active_tasks = {t for t in self.active_tasks if not t.done()}

            # * Periodic idle reaping (added 2026-11-16): idle_ttl only takes effect when it is actually called.
            #   Previously reap_idle was only defined and never called -> child processes were never released (measured: alive 2 hours / 240MB).
            if time.time() - _last_reap >= DSH_REAP_INTERVAL:
                _last_reap = time.time()
                try:
                    _bk = self.backend
                    if _bk is not None:
                        _n = _bk.reap()
                        if _n:
                            log(f" 回收空闲 dsh 子进程 {_n} 个 | 池={_bk.pool_stats()}")
                except Exception as e:  # noqa: BLE001
                    log(f" 空闲回收异常（忽略，不中断主循环）: {e}", "WARN")

            try:
                r.set(DSH_RUNNING_KEY, len(self.active_tasks), ex=30)
            except Exception:  # noqa: BLE001
                pass

            if len(self.active_tasks) >= MAX_CONCURRENCY:
                await asyncio.wait(self.active_tasks, return_when=asyncio.FIRST_COMPLETED)
                continue

            try:
                item = await asyncio.to_thread(r.blpop, DSH_TASK_QUEUE, timeout=1)
            except Exception as e:  # noqa: BLE001
                log(f"BLPOP 异常（2s 后重试）: {e}", "ERROR")
                await asyncio.sleep(2)
                continue

            if item is None:
                await asyncio.sleep(0.01)
                continue

            _, raw = item
            try:
                task = json.loads(raw)
            except Exception as e:  # noqa: BLE001
                log(f"任务 JSON 解析失败，丢弃: {e} | raw={raw[:200]}", "ERROR")
                continue

            run_uuid = task.get("run_uuid", "")
            t = asyncio.create_task(
                self._handle_task(
                    r,
                    run_uuid,
                    task.get("agent_id", ""),
                    task.get("instruction", ""),
                    int(task.get("timeout_seconds") or TASK_TIMEOUT),
                    task.get("session_key", ""),
                    task.get("mode", "create"),
                )
            )
            self.active_tasks.add(t)
            log(f" 收到任务 | {run_uuid} | agent={task.get('agent_id')} | 活跃={len(self.active_tasks)}")

        # Graceful exit: wait for in-flight tasks to finish
        if self.active_tasks:
            log(f"⏳ 等待 {len(self.active_tasks)} 个在途任务完成…")
            try:
                await asyncio.wait_for(asyncio.gather(*self.active_tasks, return_exceptions=True), timeout=TASK_TIMEOUT)
            except TimeoutError:
                log(" 等待超时，强制取消剩余任务", "WARN")
                for t in self.active_tasks:
                    if not t.done():
                        t.cancel()
        for _t in (_collect_task, _watch_task):
            _t.cancel()
            try:
                await _t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        log("桥已停止")


def main():
    bridge = AgxDshClient()

    def _sig(_signum, _frame):
        log("收到退出信号，准备优雅停止…", "WARN")
        bridge._stopping = True

    _signal.signal(_signal.SIGTERM, _sig)
    _signal.signal(_signal.SIGINT, _sig)

    try:
        asyncio.run(bridge.run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
