#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
agx_dsh_sdk_client.py - client for the DSH "SDK session surface" (stdio JSON-RPC)

Why this file exists
================
From 2026-11 the dsh 0.2.1-rc.5 / 0.2.2 series **removed the HTTP `POST /api/<method>` session
surface**: api-gateway now registers only the WebSocket upgrade route (`/api/remote.mux`), and the
HTTP calls in the old bridge `agx_dsh_client.py` return 401/404 when measured, breaking the whole
AgentGeeX -> DSH channel.

The replacement DSH officially offers is **newline-delimited JSON-RPC over stdio**:
  `dsh sdk`  -> profile loads `@deepseek-ai/dsh-sdk-jsonrpc-server`
  handshake `initialize({cwd, provider, model})`, then `session/prompt`, `session/wait`,
  `session/resume`, `session/working-directory/*`, with events streamed back as
  `session.event` / `session.status` notifications.

Key differences from the old HTTP surface (they determine this module's shape)
==========================================
1. Transport: HTTP short connection -> **long-lived child process on stdio**
2. Session placement: grouped by `workspaceId` -> **by `initialize.cwd`**, one directory per process
   -> so the bridge needs "one sdk process per agent", see DshSdkPool
3. Getting the answer: poll `session.history` -> **subscribe to notification events** (accumulated per sessionId here)
4. Method names: `session.prompt` -> `session/prompt` (slashes)

Measured (2026-11-14, dsh 0.2.1-rc.5, node v22.22.3)
==================================================
  initialize -> {serverInfo:{name:"deepseek-harness-sdk-runtime"}}
  session/prompt -> {messageId}
  session/wait   -> {}, with 13 session.event + 1 session.status received meanwhile,
                   among them an assistant/message carrying the model's answer text.

Boundaries
====
. Connects only to a locally installed dsh (find_dsh_bin); bundles nothing and self-upgrades nothing.
. Does not modify DSH sources; protocol adaptation only.
. Does not take over the old bridge's Redis queue logic - this file only provides "one round of Q&A"
  client capability; how the upper layer (AgxDshClient / daemon) schedules it is up to the caller.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional, Set
from contextlib import contextmanager

SDK_SERVER_NAME = "deepseek-harness-sdk-runtime"
DEFAULT_PROVIDER = "deepseek-official"
DEFAULT_MODEL = "deepseek-v4-flash"


class DshSdkError(Exception):
    """The SDK session surface returned an error, or the transport failed."""


class DshSdkTimeout(DshSdkError):
    """Timed out waiting for a response/event."""


def find_node_bin() -> str:
    """Locate the node executable.

    * 2026-11-16 (measured on test_srv_b): the daemon is started from a separate environment whose
    PATH may not contain the nvm-managed node, causing `[Errno 2] No such file or directory: 'node'`.
    Hence the fallback chain AGX_DSH_NODE -> PATH -> highest version under ~/.nvm.
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
    raise DshSdkError("找不到 node 可执行文件（AGX_DSH_NODE / PATH / ~/.nvm 都没有）")


def _dsh_version_of(bin_path: str) -> str:
    """Read the package version behind a dsh executable (empty string when unreadable; never raises)."""
    # bin_path looks like <root>/lib/node_modules/@deepseek-ai/dsh/lib/bin.js
    # or <root>/node_modules/.bin/dsh (symlink). Search upward on both sides for package.json.
    real = os.path.realpath(bin_path)
    d = os.path.dirname(real)
    for _ in range(6):
        pj = os.path.join(d, "package.json")
        if os.path.isfile(pj):
            try:
                with open(pj, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("name", "").endswith("dsh"):
                    return str(data.get("version") or "")
            except Exception:
                pass
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return ""


def _ver_tuple(v: str) -> tuple:
    """Turn a version string into a comparable tuple (ignoring prerelease suffix differences; numeric parts only)."""
    nums = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in nums[:3]) if nums else (0,)


def find_dsh_bin() -> str:
    """Locate an executable dsh.

    Fallback chain (* revised 2026-11-16: the old implementation missed the nvm global install, so on
    test_srv_b it fell back to the old 0.2.1-rc.5 in the npx cache although 0.2.2-rc.1 was installed):
      1. env var AGX_DSH_BIN (explicit, highest priority)
      2. dsh on PATH (respects the current environment; on this Mac this is the path taken)
      3. nvm global install (highest version wins)
      4. ~/.npm/_npx cache (newest directory wins)
    """
    env_bin = (os.environ.get("AGX_DSH_BIN") or "").strip()
    if env_bin and os.path.isfile(env_bin):
        return env_bin

    p = shutil.which("dsh")
    if p:
        return p

    # nvm global install: ~/.nvm/versions/node/<ver>/lib/node_modules/@deepseek-ai/dsh/lib/bin.js
    nvm_base = os.path.expanduser("~/.nvm/versions/node")
    global_cands: List[str] = []
    if os.path.isdir(nvm_base):
        for name in os.listdir(nvm_base):
            b = os.path.join(nvm_base, name, "lib", "node_modules", 
                             "@deepseek-ai", "dsh", "lib", "bin.js")
            if os.path.isfile(b):
                global_cands.append(b)
    if global_cands:
        global_cands.sort(key=lambda x: _ver_tuple(_dsh_version_of(x)), reverse=True)
        return global_cands[0]

    cache = os.path.expanduser("~/.npm/_npx")
    cands: List[str] = []
    if os.path.isdir(cache):
        for name in os.listdir(cache):
            b = os.path.join(cache, name, "node_modules", ".bin", "dsh")
            if os.path.isfile(b):
                cands.append(b)
    if not cands:
        raise DshSdkError(
            "找不到 dsh 可执行文件（AGX_DSH_BIN / PATH / ~/.nvm 全局 / ~/.npm/_npx 都没有）"
        )
    return sorted(cands, key=os.path.getmtime)[-1]


class DshSdkClient:
    """One sdk child process = the session surface of one cwd.

    Usage::

        with DshSdkClient(workspace="/path/to/agent/workspace") as c:
            c.prompt("agent:foo:main", "hello", timeout_s=120)
    """

    def __init__(
        self,
        workspace: str,
        provider: str = DEFAULT_PROVIDER,
        model: str = DEFAULT_MODEL,
        dsh_bin: Optional[str] = None,
        stderr_path: Optional[str] = None,
    ) -> None:
        # * realpath is mandatory: DSH derives the session directory name from the cwd (projectKey).
        # `~/.agx/agx_agents/<id>/workspace` is a symlink; unresolved it lands in a separate `.agx-...` set of directories,
        # splitting sessions away from the web side (workspace.path = the project repo realpath).
        self.workspace = os.path.realpath(os.path.abspath(os.path.expanduser(workspace)))
        self.provider = provider
        self.model = model
        self.node_bin = find_node_bin()
        self.dsh_bin = dsh_bin or find_dsh_bin()
        self.stderr_path = stderr_path or os.path.join(
            os.path.expanduser("~"), ".dsh", "sdk_bridge_stderr.log"
        )
        self._proc: Optional[subprocess.Popen] = None
        self._err_file = None
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._pending: Dict[int, "queue.Queue[dict]"] = {}
        self._events: Dict[str, List[dict]] = {}
        self._events_lock = threading.Lock()
        self._reader: Optional[threading.Thread] = None
        self.server_info: Dict[str, Any] = {}

    # -- lifecycle ------------------------------------------------
    def start(self) -> "DshSdkClient":
        if self._proc is not None:
            return self
        os.makedirs(os.path.dirname(self.stderr_path), exist_ok=True)
        self._err_file = open(self.stderr_path, "a", buffering=1)
        self._err_file.write(f"\n=== sdk start {time.strftime('%F %T')} cwd={self.workspace} ===\n")
        self._proc = subprocess.Popen(
            [self.node_bin, self.dsh_bin, "sdk"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._err_file,
            text=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_loop, name="dsh-sdk-reader", daemon=True)
        self._reader.start()
        r = self._request(
            "initialize",
            {"cwd": self.workspace, "provider": self.provider, "model": self.model},
            timeout=90,
        )
        self.server_info = r.get("serverInfo") or {}
        if self.server_info.get("name") != SDK_SERVER_NAME:
            raise DshSdkError(f"握手返回非预期 serverInfo: {self.server_info}")
        return self

    def stop(self) -> None:
        p = self._proc
        self._proc = None
        if p is None:
            return
        try:
            p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 0, "method": "shutdown", "params": {}}) + "\n")
            p.stdin.flush()
            p.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass
            if self._err_file:
                try:
                    self._err_file.close()
                except Exception:  # noqa: BLE001
                    pass
                self._err_file = None

    def __enter__(self) -> "DshSdkClient":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # -- transport ------------------------------------------------
    def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            rid = msg.get("id")
            if rid is not None and rid in self._pending:
                self._pending.pop(rid).put(msg)
                continue
            # notifications: session.event / session.status
            sid = (msg.get("params") or {}).get("sessionId")
            if sid:
                with self._events_lock:
                    self._events.setdefault(sid, []).append(msg)

    def _request(self, method: str, params: dict, timeout: float = 60) -> dict:
        if self._proc is None or self._proc.stdin is None:
            raise DshSdkError("sdk 进程未启动")
        with self._id_lock:
            self._next_id += 1
            rid = self._next_id
        box: "queue.Queue[dict]" = queue.Queue(maxsize=1)
        self._pending[rid] = box
        payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        try:
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
        except Exception as e:  # noqa: BLE001
            self._pending.pop(rid, None)
            raise DshSdkError(f"写入 sdk 失败: {e}") from e
        try:
            msg = box.get(timeout=timeout)
        except queue.Empty:
            self._pending.pop(rid, None)
            raise DshSdkTimeout(f"{method} 超时 {timeout}s")
        if "error" in msg:
            err = msg["error"] or {}
            raise DshSdkError(f"{method} 失败: {err.get('code')} {err.get('message')}")
        return msg.get("result") or {}

    # -- session capabilities -------------------------------------
    def events(self, session_id: str) -> List[dict]:
        with self._events_lock:
            return list(self._events.get(session_id, []))

    def drain_events(self, session_id: str) -> List[dict]:
        with self._events_lock:
            return self._events.pop(session_id, [])

    def resume(self, session_id: str, timeout: float = 120) -> dict:
        """Load a persisted session (sends no input); explicitly fails when the session is missing/incompatible."""
        return self._request("session/resume", {"sessionId": session_id}, timeout=timeout)

    def prompt(self, session_id: str, text: str, timeout_s: int = 300) -> dict:
        """One round of Q&A: submit the prompt -> wait for the turn to end -> extract the assistant text.

        Returns ``{ok, text, message_id, elapsed_s, turn, stop_reason, error}``.
        A missing session is created by the SDK side (getOrCreateSession) at the initialize cwd.
        """
        t0 = time.time()
        before = len(self.events(session_id))
        payload = {"sessionId": session_id, "contentBlocks": [{"type": "text", "text": text}]}
        try:
            r = self._request("session/prompt", payload, timeout=60)
        except DshSdkError as e:
            # after a cross-process restart the session already exists in storage -> create is rejected (already exists),
            # so resume must run first to reload history, then submit the prompt. Within one process it hits the cache and never reaches this.
            if "already exists" in str(e):
                try:
                    self._request("session/resume", {"sessionId": session_id}, timeout=180)
                    r = self._request("session/prompt", payload, timeout=60)
                except DshSdkError as e2:
                    return {
                        "ok": False,
                        "error": f"resume+prompt 失败: {e2}",
                        "text": "",
                        "elapsed_s": round(time.time() - t0, 1),
                    }
            else:
                return {"ok": False, "error": str(e), "text": "", "elapsed_s": round(time.time() - t0, 1)}
        message_id = r.get("messageId", "")
        try:
            self._request("session/wait", {"sessionId": session_id}, timeout=timeout_s)
        except DshSdkError as e:
            return {
                "ok": False,
                "error": str(e),
                "text": "",
                "message_id": message_id,
                "elapsed_s": round(time.time() - t0, 1),
            }

        evs = self.events(session_id)[before:]
        texts: List[str] = []
        turn = None
        stop_reason = ""
        for note in evs:
            ev = (note.get("params") or {}).get("event") or {}
            et = ev.get("type")
            data = ev.get("data") or {}
            if et == "assistant/message":
                turn = data.get("turn", turn)
                for block in (data.get("message") or {}).get("content") or []:
                    if block.get("type") == "text" and block.get("text"):
                        texts.append(block["text"])
            elif et == "turn/end":
                stop_reason = data.get("stopReason") or data.get("reason") or ""
        answer = "\n".join(t for t in texts if t.strip())
        return {
            "ok": bool(answer) or bool(stop_reason),
            "text": answer,
            "message_id": message_id,
            "turn": turn,
            "stop_reason": stop_reason,
            "elapsed_s": round(time.time() - t0, 1),
            "events": len(evs),
        }


class DshSdkPool:
    """Manage sdk processes per agent workspace (one process per directory, capped by both capacity and idleness).

    Rationale: the new protocol binds session placement to `initialize.cwd`, so one process can serve
    only one working directory; to serve multiple agents the bridge must use one process per directory.

    * Why caps are needed (measured 2026-11-16)
    ================================
    A resident child process is not cheap - one dsh sdk process measured **240MB RSS**.
    With "one process per agent" and no reclamation, 100 agents running once each means 24GB,
    which blows up a 31G machine. Hence two gates:

      . idle_ttl_s   -- reclaim after this many idle seconds (0 = exit as soon as the turn ends)
      . max_clients  -- upper bound on pool size; beyond it the least recently used is evicted

    Both are enforced through reap_idle() / borrow(). * The caller **must** call reap_idle()
    periodically, otherwise idle_ttl is a dead parameter -- which is exactly the bug fixed on
    2026-11-16: reap_idle was defined but never called anywhere in the repo, and measured child
    processes lived two hours without exiting.

    * busy protection
    ===========
    A borrowed client is recorded in _busy and reap / LRU **always skip it**.
    Otherwise a long task in flight could be stopped together with its process when another agent
    arrives and triggers eviction.
    """

    def __init__(
        self,
        provider: str = DEFAULT_PROVIDER,
        model: str = DEFAULT_MODEL,
        idle_ttl_s: int = 300,
        max_clients: int = 8,
    ) -> None:
        self.provider = provider
        self.model = model
        self.idle_ttl_s = idle_ttl_s
        self.max_clients = max_clients
        self._clients: Dict[str, DshSdkClient] = {}
        self._last_used: Dict[str, float] = {}
        self._busy: Set[str] = set()
        self._lock = threading.Lock()

    # -- internals ---------------------------------------------------
    @staticmethod
    def _key(workspace: str) -> str:
        return os.path.abspath(os.path.expanduser(workspace))

    def _evict_for_capacity_locked(self) -> List["DshSdkClient"]:
        """Called under the lock: make room for one more, returning the victims to stop."""
        victims: List[DshSdkClient] = []
        if self.max_clients <= 0:
            return victims
        while len(self._clients) >= self.max_clients:
            cand = [k for k in self._last_used if k not in self._busy]
            if not cand:
                break  # everything is busy - rather exceed the cap briefly than kill a running task
            lru = min(cand, key=lambda k: self._last_used[k])
            c = self._clients.pop(lru, None)
            self._last_used.pop(lru, None)
            if c is not None:
                victims.append(c)
        return victims

    # -- public API ---------------------------------------------------
    @contextmanager
    def borrow(self, workspace: str):
        """Borrow a client for one round of Q&A; return it and refresh the activity time on exit.

        * A context manager instead of a bare get() so the busy mark has a definite scope:
          this client cannot be reaped / LRU-evicted while the prompt runs.
        """
        key = self._key(workspace)
        victims: List[DshSdkClient] = []
        with self._lock:
            c = self._clients.get(key)
            if c is not None and c.alive:
                self._last_used[key] = time.time()
            else:
                if c is not None:  # dead process, drop it first
                    self._clients.pop(key, None)
                    self._last_used.pop(key, None)
                    victims.append(c)
                victims.extend(self._evict_for_capacity_locked())
                c = DshSdkClient(key, provider=self.provider, model=self.model).start()
                self._clients[key] = c
                self._last_used[key] = time.time()
            self._busy.add(key)
        # stop outside the lock: killing a process blocks, and must never happen while holding the lock
        for v in victims:
            try:
                v.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            yield c
        finally:
            with self._lock:
                self._busy.discard(key)
                self._last_used[key] = time.time()

    def get(self, workspace: str) -> DshSdkClient:
        """[Legacy callers] Return a client but **without busy protection**.

        * New code should use borrow(). This method exists only to avoid breaking existing callers.
        """
        key = self._key(workspace)
        victims: List[DshSdkClient] = []
        with self._lock:
            c = self._clients.get(key)
            if c is not None and c.alive:
                self._last_used[key] = time.time()
                return c
            if c is not None:
                self._clients.pop(key, None)
                self._last_used.pop(key, None)
                victims.append(c)
            victims.extend(self._evict_for_capacity_locked())
            c = DshSdkClient(key, provider=self.provider, model=self.model).start()
            self._clients[key] = c
            self._last_used[key] = time.time()
        for v in victims:
            try:
                v.stop()
            except Exception:  # noqa: BLE001
                pass
        return c

    def reap_idle(self) -> int:
        """Reclaim processes idle beyond their timeout; return how many were reclaimed.

        * The caller must call this periodically (the bridge main loop, every DSH_REAP_INTERVAL seconds).
          Not calling it makes idle_ttl_s completely ineffective.
        """
        now = time.time()
        victims: List[DshSdkClient] = []
        with self._lock:
            for key, ts in list(self._last_used.items()):
                if key in self._busy:
                    continue
                if self.idle_ttl_s > 0 and now - ts < self.idle_ttl_s:
                    continue
                c = self._clients.pop(key, None)
                self._last_used.pop(key, None)
                if c is not None:
                    victims.append(c)
        for v in victims:
            try:
                v.stop()
            except Exception:  # noqa: BLE001
                pass
        return len(victims)

    def stats(self) -> Dict[str, Any]:
        """Pool overview (for logs / inspection)."""
        with self._lock:
            return {
                "clients": len(self._clients),
                "busy": len(self._busy),
                "max_clients": self.max_clients,
                "idle_ttl_s": self.idle_ttl_s,
            }

    def close_all(self) -> None:
        with self._lock:
            clients = list(self._clients.values())
            self._clients.clear()
            self._last_used.clear()
            self._busy.clear()
        for c in clients:
            try:
                c.stop()
            except Exception:  # noqa: BLE001
                pass
