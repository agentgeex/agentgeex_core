#!/usr/bin/env python3
"""L1: live session-state sidecar relay (gateway websocket -> Redis).

It does exactly one thing: fold the gateway's realtime events into "what state each session is in
right now", write that to Redis, and let the console / mobile tools look up the status / agent / host /
run / context share of any session within a second.

Design boundaries (approved by the owner 2026-11-03, L1 phase)
  * Read only: calls only sessions.subscribe / sessions.messages.subscribe, never sessions.create / send
  * Connects only to local 127.0.0.1:18789; does not touch 188; does not restart the gateway
  * Touches no sqlite at all (this process does not need it)
  * Touches neither ws_client nor daemon: an independent sidecar, merging can be discussed once it is stable
  * Writes only agx:gw:* and agx:idx:gw:* in Redis, never existing keys such as aoe:entity:* / agx:idx:*
  * The token is read only inside ~/.openclaw/openclaw.json; never in the command line / env / logs

Event semantics (measured 2026-11-03)
  sessions.changed  = authoritative snapshot: a single frame carries status / hasActiveRun / activeRunIds /
                      lastRunId / lastRunError / contextTokens / startedAt / model
                      measured reasons: create / send / agent.run.started / agent.input.settled /
                      subagent-status
  chat              = streaming result of one turn: state = delta / final / error / aborted,
                      carrying runId / sessionKey / stopReason / message
  agent             = inner activity stream of one turn: stream = thinking / item / command_output / usage
  session.tool      = tool-call stream
  session.message   = persisted message, carrying message.usage (the only source for context share)
  health / tick / presence / task / heartbeat = auxiliary, not folded into state

There is no separate run.started / run.ended event family - the lifecycle is expressed by
sessions.changed(hasActiveRun, status) together with chat(state), and this process folds them.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from collections import Counter
from pathlib import Path

import redis.asyncio as aioredis
import websockets

# -- paths and constants ---------------------------------------------------

OPENCLAW_CFG = Path.home() / ".openclaw" / "openclaw.json"
# AgentGeeX config search chain: the newer ~/.agx/agx_config.json wins, old installs are the fallback.
AGX_CFG_CANDIDATES = (
    Path.home() / ".agx" / "agx_config.json",
)
# runtime state directory: always follows the install root ~/.agx.
STATE_DIR = Path.home() / ".agx" / "runtime_watch"

DEFAULT_NAMESPACE = "agx:gw:"

SESSION_TTL = 24 * 3600          # refreshed by active overwrite, not by expiry
RUN_TTL = 3600                   # a finished run has no long-term value
ERROR_TTL = 7 * 24 * 3600        # error ledger kept for 7 days (refreshed on every write)
ERROR_LEDGER_MAX = 1000          # at most 1000 entries per machine (LTRIM as the backstop)

# self-protection threshold (used on the first run on the 188 production box; normal values are far below)
MAX_RSS_MB = 200                 # exceeding it means a leak; exit on our own
FRAME_BURST_PER_SEC = 500        # more than this many frames in one second is abnormal; drop the excess
MONITOR_INTERVAL = 10            # self-monitoring interval (seconds)
RECONNECT_MIN = 1.0
# * 2026-11-14 silent calibration: run completion is only announced by sessions.changed(chat.run.settled).
#   If that frame is lost (the collector is reconnecting, or the gateway has no subscriber at that moment),
#   status stays at running until the 5-minute stalled scan flips it - so the UI shows "stopped but still running".
#   Here, for sessions with status=running and no event for longer than QUIET, we pull sessions.list every EVERY
#   and correct with the authoritative snapshot (hasActiveRun), shrinking the window from 5 minutes to tens of seconds.
RECONCILE_EVERY_MS = 15_000
RECONCILE_QUIET_MS = 45_000
RECONNECT_MAX = 30.0

# Only these methods are allowed. Any mutating call (create / send / abort / patch) is stopped right here -
# not by "I will remember not to send it" but structurally unable to go out.
READONLY_METHODS = frozenset(
    {
        "connect",
        "sessions.subscribe",
        "sessions.messages.unsubscribe",
        "sessions.messages.subscribe",
        "sessions.list",
        "sessions.describe",
    }
)


# -- error classification (error fingerprint -> type) -----------------------
# Order sensitive: the first hit wins. context_overflow must precede timeout,
# otherwise "auto-compaction timed out" is misjudged as an ordinary timeout - and the two have
# completely different prescriptions (the former needs compaction / a new session, the latter just waiting).
# Deliberately no bare numbers (401/429/503): error text also carries run ids and token counts,
# so matching bare numbersinevitably misjudges. Only semantically meaningful words are matched.
ERROR_FINGERPRINTS = (
    ("context_overflow", (
        "context is too large", "auto-compaction timed out", "compaction timed out",
        "context length", "maximum context", "prompt is too long",
        "context window", "too many tokens",
    )),
    ("rate_limit", (
        "rate limit", "rate_limit", "too many requests", "quota exceeded",
        "insufficient_quota", "throttl",
    )),
    ("auth", (
        "unauthorized", "invalid api key", "invalid_api_key", "authentication",
        "permission denied", "forbidden", "no auth credentials",
    )),
    ("timeout", (
        "timed out", "timeout", "etimedout", "deadline exceeded",
    )),
    ("transport", (
        "econnreset", "connection reset", "socket hang up", "websocket",
        "disconnect", "network error", "econnrefused", "socket closed",
    )),
    ("model_unavailable", (
        "overloaded", "service unavailable", "model not found",
        "no available channel", "bad gateway", "upstream error",
    )),
    ("aborted", ("aborted", "cancelled", "canceled", "interrupted")),
    ("tool_error", ("tool call", "tool_error", "function call failed")),
)


def classify_error(msg: str, stop_reason: str = "") -> str:
    """Map the error text onto a stable type. Unknown when it cannot be classified - no guessing."""
    blob = (str(msg or "") + " " + str(stop_reason or "")).lower()
    if not blob.strip():
        return "unknown"
    for kind, needles in ERROR_FINGERPRINTS:
        for n in needles:
            if n in blob:
                return kind
    return "unknown"


class Keys:
    """All Redis key names are derived from the namespace - passing `agx:gw:test188:` during testing keeps production keys clean.

    Index keys follow the namespace:
        agx:gw:            -> agx:idx:gw:
        agx:gw:test188:    -> agx:idx:gw:test188:
    In one sentence: strip the `agx:` prefix of the namespace and append it after `agx:idx:`.
    """

    def __init__(self, namespace: str = DEFAULT_NAMESPACE):
        ns = (namespace or DEFAULT_NAMESPACE).strip()
        if not ns.endswith(":"):
            ns += ":"
        self.ns = ns
        self.session = ns + "session:"
        self.run = ns + "run:"
        self.error_ledger = ns + "errors:"   # list: <ns>errors:<host_id>
        self.conflict = ns + "conflict:"
        self.conflict_count = ns + "conflict_count:"
        tail = ns[3:] if ns.startswith("agx:") else ns
        self.idx = "agx:idx:" + tail
        self.idx_running = self.idx + "session:running"
        self.idx_stalled = self.idx + "session:stalled"
        self.idx_by_agent = self.idx + "session:by_agent:"
        self.idx_by_host = self.idx + "session:by_host:"
        # by_host index of the isolation area: lets readers list every session of this machine at once
        # * must follow self.idx - indexes live uniformly under agx:idx:, so ns cannot be concatenated directly
        self.idx_conflict_by_host = self.idx + "session:conflict_by_host:"

    def clear_patterns(self) -> list[str]:
        """All keys of this tool are here; cleanup scans only these prefixes."""
        return [self.session + "*", self.run + "*", self.conflict + "*",
                self.conflict_count + "*", self.idx + "*",
                self.idx_running, self.idx_stalled]

CONNECT = {
    "minProtocol": 3,
    "maxProtocol": 4,
    "client": {
        # Must be one of the gateway's allowed client ids; "gateway-client" is the
        # generic operator one (userAgent below distinguishes this process).
        "id": "gateway-client",
        "version": "0.1.0",
        "platform": "python",
        "mode": "backend",
    },
    "role": "operator",
    "scopes": ["operator.read"],
    "caps": [],
    "commands": [],
    "permissions": {},
    "locale": "zh-CN",
    "userAgent": "agx-state-relay",
}

log = logging.getLogger("relay")


# -- config reading (token never leaks) ------------------------------------


def load_gateway_token(config_path: Path) -> str:
    """Read the gateway token from local config only.

    Never accept it from argv or env: those leak into ps / shell history / logs.
    The value is used once for the connect frame and never printed.
    """
    try:
        with Path(config_path).expanduser().open(encoding="utf-8") as fh:
            cfg = json.load(fh)
    except OSError as exc:
        raise SystemExit(f"cannot read openclaw config {config_path}: {exc}")
    token = ((cfg.get("gateway") or {}).get("auth") or {}).get("token") or ""
    if not token:
        raise SystemExit(f"no gateway token in {config_path}")
    return token


def resolve_agx_cfg(explicit: str = "") -> tuple[Path, dict]:
    """Look for a usable config in the order --redis-config (if given) -> ~/.agx.

    Returns (path found, content). When nothing is found returns (<empty Path>, {}) and the caller decides
    whether to fail or degrade.
    """
    cands = [Path(explicit).expanduser()] if explicit else []
    cands += list(AGX_CFG_CANDIDATES)
    for path in cands:
        try:
            with path.open(encoding="utf-8") as fh:
                return path, json.load(fh)
        except Exception:
            continue
    return Path(), {}


# -- session state machine -------------------------------------------------


class SessionStore:
    """In-memory mirror of what we wrote to Redis, plus the derived status.

    Redis is the read surface; this dict is the decision surface. Keeping them
    separate means a Redis hiccup cannot corrupt the status logic.
    """

    def __init__(self, host_id: str, stall_seconds: int):
        self.host_id = host_id
        self.stall_ms = stall_seconds * 1000
        self.sessions: dict[str, dict] = {}
        # Runs we already saw end. Late/out-of-order activity frames for a
        # finished run must not resurrect it to running (observed as a
        # running->idle->running->idle flicker on a 5s subagent run).
        self.terminal_runs: dict[str, int] = {}
        self.stats = Counter()
        self.timing: list[dict] = []
        # error records pending persistence: handlers only collect, _handle writes them uniformly (one egress point is easier to count)
        self.pending_errors: list[dict] = []

    def _mark_terminal(self, run_id: str, now_ms: int) -> None:
        if not run_id:
            return
        self.terminal_runs[run_id] = now_ms
        if len(self.terminal_runs) > 500:
            for k in list(self.terminal_runs)[:100]:
                self.terminal_runs.pop(k, None)

    def record_error(
        self,
        key: str,
        msg: str,
        *,
        stop_reason: str = "",
        source: str = "",
        run_id: str = "",
        recv_ms: int = 0,
        mark_failed: bool = True,
        to_ledger: bool = True,
    ) -> None:
        """Record one error: update the session fields + append to the ledger (persisted by the caller).

        Why dedup is needed: every `sessions.changed` snapshot carries lastRunError, so a failed session
        repeats the same error for hours afterwards. Without dedup the ledger fills up with one error and
        `error_count` becomes a meaningless number.
        Criterion: same message and same run -> the same fact, counted once.

        to_ledger=False is used for bootstrap: the lastRunError in a snapshot is **history**, filled into
        the session fields for humans but not appended to the ledger - otherwise every restart replays
        the historical failures.
        """
        msg = str(msg or "").strip()
        if not msg:
            return
        if not recv_ms:
            recv_ms = int(time.time() * 1000)
        st = self._ensure(key)
        prev_msg = st.get("last_error") or ""
        same_run = bool(run_id) and run_id == (st.get("error_run_id") or "")
        if msg[:500] == prev_msg and (same_run or not run_id):
            return
        kind = classify_error(msg, stop_reason)
        st["last_error"] = msg[:500]
        st["terminal_error"] = msg[:500]
        st["error_kind"] = kind
        st["error_at"] = recv_ms
        st["error_run_id"] = run_id or ""
        st["error_count"] = int(st.get("error_count") or 0) + 1
        if mark_failed:
            st["status"] = "failed"
        st["dirty"] = True
        if not to_ledger:
            return
        self.pending_errors.append(
            {
                "ts": recv_ms,
                "host_id": self.host_id,
                "session_key": key,
                "agent_id": st.get("agent_id") or "",
                "run_id": run_id or st.get("active_run_id") or st.get("last_run_id") or "",
                "kind": kind,
                "source": source,
                "stop_reason": str(stop_reason or "")[:200],
                "message": msg[:500],
            }
        )

    # ---- status derivation ----

    def _ensure(self, key: str, agent_id: str | None = None) -> dict:
        st = self.sessions.get(key)
        if st is None:
            st = {
                "status": "unknown",
                "agent_id": agent_id or key.split(":")[1] if key.count(":") >= 2 else None,
                "active_run_id": "",
                "last_run_id": "",
                "started_at": 0,
                "last_event_at": 0,
                "last_event_ts": 0,
                "last_event_type": "",
                "context_tokens": 0,
                "total_tokens": 0,
                "compaction_count": None,
                "last_error": "",
                "terminal_error": "",
                "error_kind": "",
                "error_at": 0,
                "error_count": 0,
                "error_run_id": "",
                "model": "",
                "dirty": True,
            }
            self.sessions[key] = st
        elif agent_id and not st.get("agent_id"):
            st["agent_id"] = agent_id
        return st

    @staticmethod
    def _status_from_snapshot(p: dict) -> str:
        """Fold a sessions.changed snapshot into one of our status values.

        * hasActiveRun is the only authoritative criterion; raw status is supplementary (fixed 2026-11-04).
          A frame emitted right after a turn ends still carries the leftover status="running", while
          hasActiveRun is already false - judging by raw would record a finished session as running and
          then wait 5 minutes for the stalled scan to flip it (flipping to stalled is wrong as well).
          Measured: agent:agx_ui_lead:main had ended at 22:49:05.785, yet the relay still recorded
          running after receiving the frame at 22:49:05.863.
        """
        if p.get("hasActiveRun"):
            return "running"
        raw = (p.get("status") or "").lower()
        if p.get("lastRunError"):
            return "failed"
        if raw in ("error", "failed", "aborted", "killed"):
            return "failed"
        if raw in ("done", "idle", "completed"):
            return "idle"
        if raw in ("running", "active", "queued"):
            # hasActiveRun explicitly false with no active run id -> this running is a stale snapshot,
            # not to be believed. Only a real activeRunIds means it is running.
            explicit_no_run = ("hasActiveRun" in p) and not p.get("hasActiveRun")
            if explicit_no_run and not p.get("activeRunIds"):
                return "idle"
            return "running"
        return "unknown"

    # ---- event entry ----

    def on_sessions_changed(self, p: dict, recv_ms: int) -> list[str]:
        key = p.get("sessionKey")
        if not key:
            return []
        st = self._ensure(key, p.get("agentId"))
        touched = []

        # * Fixed 2026-11-14 (third layer): the gateway debounces sessions.changed,
        #   so one batch can carry both an "old snapshot (hasActiveRun=True)" and "chat.run.settled(hasActiveRun=False)",
        #   arriving in the same millisecond with no ordering guarantee (measured: two coexisting at 00:16:28).
        #   If the running one comes last, the state flips back, and it also refreshes last_event_at,
        #   bypassing both stalled and reconcile - this is the source of the "UI flickers idle->running within 6ms".
        #   A leftover snapshot of an already-terminal run must not light the session up again.
        if p.get("hasActiveRun"):
            lr = p.get("lastRunId") or ""
            if lr and lr in self.terminal_runs:
                p = dict(p)
                p["hasActiveRun"] = False
                p["activeRunIds"] = []

        status = self._status_from_snapshot(p)
        st["status"] = status
        st["last_event_type"] = f"sessions.changed:{p.get('reason') or '-'}"
        st["last_event_at"] = recv_ms
        st["dirty"] = True
        run_ids = p.get("activeRunIds") or []
        if run_ids:
            st["active_run_id"] = run_ids[-1]
        elif p.get("hasActiveRun") and p.get("lastRunId"):
            st["active_run_id"] = p["lastRunId"]
        elif not p.get("hasActiveRun"):
            if st.get("active_run_id"):
                st["last_run_id"] = st["active_run_id"]
            st["active_run_id"] = ""
            # snapshot says "no active run": whatever ended is terminal now
            self._mark_terminal(p.get("lastRunId") or st.get("last_run_id"), recv_ms)

        if p.get("lastRunId"):
            st["last_run_id"] = p["lastRunId"]
        if p.get("startedAt"):
            st["started_at"] = p["startedAt"]
        if p.get("contextTokens"):
            st["context_tokens"] = p["contextTokens"]
        if p.get("model"):
            st["model"] = p["model"]
        if p.get("lastRunError"):
            # the snapshot carries status itself; only the "why" is added here, the status decision is not overridden
            self.record_error(
                key,
                p["lastRunError"],
                source="sessions.changed",
                run_id=p.get("lastRunId") or "",
                recv_ms=recv_ms,
                mark_failed=False,
            )

        touched.append(key)
        return touched

    def on_chat(self, p: dict, recv_ms: int) -> list[str]:
        key = p.get("sessionKey")
        run_id = p.get("runId")
        if not key or not run_id:
            return []
        state = (p.get("state") or "").lower()
        # * Fixed 2026-11-14 (evidence from ui_lead): a late delta frame for an already-terminal run must be dropped entirely.
        #   The old implementation treated every delta unconditionally as "running": status=running + refresh last_event_at.
        #   The consequence: one out-of-order delta after a run ended flips idle back to running, and since it also
        #   refreshes last_event_at, both the stalled(5min) and reconcile(45s) fallbacks are bypassed -
        #   the fake running state then persists until the next terminal frame (measured: 21 minutes).
        #   on_agent / on_tool already had the terminal_runs check; only this spot was missing.
        if run_id in self.terminal_runs:
            return []
        st = self._ensure(key, p.get("agentId"))
        st["last_event_at"] = recv_ms
        st["last_event_type"] = f"chat:{state}"

        if state == "delta":
            st["status"] = "running"
            st["active_run_id"] = run_id
            st["started_at"] = st.get("started_at") or recv_ms
        elif state == "final":
            st["status"] = "idle"
            st["last_run_id"] = run_id
            st["active_run_id"] = ""
            self._mark_terminal(run_id, recv_ms)
        elif state in ("error", "aborted"):
            st["status"] = "failed"
            st["last_run_id"] = run_id
            st["active_run_id"] = ""
            self.record_error(
                key,
                p.get("errorMessage") or p.get("stopReason") or state,
                stop_reason=p.get("stopReason") or "",
                source=f"chat:{state}",
                run_id=run_id,
                recv_ms=recv_ms,
            )
            self._mark_terminal(run_id, recv_ms)
        st["dirty"] = True
        return [key]

    def on_agent(self, p: dict, recv_ms: int) -> list[str]:
        key = p.get("sessionKey")
        run_id = p.get("runId")
        if not key:
            return []
        st = self._ensure(key, p.get("agentId"))
        stream = p.get("stream") or "?"
        st["last_event_at"] = recv_ms
        st["last_event_type"] = f"agent:{stream}"
        if run_id and stream != "usage" and run_id not in self.terminal_runs:
            # Activity on a new run means it is running; a usage frame also
            # arrives at the tail of a run, so it must not flip status back.
            if st["status"] in ("unknown", "idle", "stalled") or not st.get("active_run_id"):
                st["status"] = "running"
                st["active_run_id"] = run_id
                st["started_at"] = st.get("started_at") or recv_ms
        st["dirty"] = True
        return [key]

    def on_tool(self, p: dict, recv_ms: int) -> list[str]:
        key = p.get("sessionKey")
        if not key:
            return []
        st = self._ensure(key, p.get("agentId"))
        st["last_event_at"] = recv_ms
        st["last_event_type"] = "session.tool"
        if p.get("runId") and not st.get("active_run_id") and p.get("runId") not in self.terminal_runs:
            st["active_run_id"] = p["runId"]
            st["status"] = "running"
        st["dirty"] = True
        return [key]

    def on_message(self, p: dict, recv_ms: int) -> list[str]:
        key = p.get("sessionKey")
        if not key:
            return []
        st = self._ensure(key, p.get("agentId"))
        st["last_event_at"] = recv_ms
        st["last_event_type"] = "session.message"
        st["dirty"] = True

        msg = p.get("message") or {}
        usage = msg.get("usage") or {}
        # usage.totalTokens is the whole prompt+completion of this model call,
        # i.e. how much of the context window this turn actually used.
        total = usage.get("totalTokens")
        if isinstance(total, (int, float)) and total > 0:
            st["total_tokens"] = int(total)
        if msg.get("model"):
            st["model"] = msg["model"]
        return [key]

    # ---- periodic: stalled detection (computed here, no polling of the gateway) ----

    def sweep_stalled(self, now_ms: int) -> list[tuple[str, str]]:
        """Flip running sessions with no event for N minutes to stalled."""
        changes: list[tuple[str, str]] = []
        for key, st in self.sessions.items():
            if st["status"] == "running" and st.get("last_event_at"):
                idle = now_ms - st["last_event_at"]
                if idle > self.stall_ms:
                    st["status"] = "stalled"
                    st["dirty"] = True
                    changes.append((key, "stalled"))
        return changes


# -- Redis writes -------------------------------------------------------


class RedisWriter:
    """The single egress for writing Redis.

    * Key-conflict protection (188 and the Mac share agent names: main / xplane_toliss_dev):
      both machines' relays writing `agx:gw:session:agent:main:main` would overwrite each other.
      So before the first write of each key, read its host_id field:
        * empty / equal to self -> write normally
        * present and someone else's -> do not write the original key; write <ns>conflict:<host>:<key> and INCR a counter
      Ownership results are cached in self._owner, so steady state costs no extra reads.
    """

    def __init__(self, client, host_id: str, timing_path, keys: "Keys" = None):
        self.r = client
        self.host_id = host_id
        self.keys = keys or Keys()
        # accept str or Path: Relay is constructed from argparse values
        self.timing_path = Path(timing_path) if timing_path else None
        self.writes = 0
        self.errors = 0
        # * distinct from self.errors: that one means "I failed to write Redis",
        #   this one means "I successfully recorded N errors". Same name would make both numbers unreadable.
        self.error_records = 0
        self.conflicts = 0
        # key -> True (mine) / False (someone else's)
        self._owner: dict[str, bool] = {}

    async def resolve_owners(self, keys: list[str]) -> None:
        """Resolve ownership in a batch: one pipeline covers a batch, avoiding per-key round trips."""
        todo = [k for k in keys if k not in self._owner]
        for i in range(0, len(todo), 200):
            chunk = todo[i : i + 200]
            try:
                pipe = self.r.pipeline(transaction=False)
                for k in chunk:
                    pipe.hget(self.keys.session + k, "host_id")
                res = await pipe.execute()
            except Exception as exc:
                log.warning("owner probe failed (%d keys): %s", len(chunk), exc)
                for k in chunk:
                    self._owner.setdefault(k, True)  # when it cannot be probed, write as my own; never silently drop data
                continue
            for k, owner in zip(chunk, res):
                self._owner[k] = (not owner) or (owner == self.host_id)

    async def _i_own(self, key: str) -> bool:
        if key not in self._owner:
            try:
                owner = await self.r.hget(self.keys.session + key, "host_id")
            except Exception:
                owner = ""
            self._owner[key] = (not owner) or (owner == self.host_id)
        return self._owner[key]

    async def flush_session(self, key: str, st: dict, event_ts: int, recv_ms: int) -> None:
        mine = await self._i_own(key)
        if not mine:
            await self._flush_conflict(key, st, recv_ms)
            return
        body = {
            "status": st["status"],
            "agent_id": st.get("agent_id") or "",
            "host_id": self.host_id,
            "active_run_id": st.get("active_run_id") or "",
            "last_run_id": st.get("last_run_id") or "",
            "last_event_at": st.get("last_event_at") or 0,
            "last_event_type": st.get("last_event_type") or "",
            "total_tokens": st.get("total_tokens") or 0,
            "context_tokens": st.get("context_tokens") or 0,
            # ratio only means something once we know the window; 0 = unknown,
            # never a fake huge number from dividing by 1.
            "context_ratio": (
                round((st.get("total_tokens") or 0) / st["context_tokens"], 4)
                if (st.get("context_tokens") or 0) > 0
                else 0
            ),
            "last_error": st.get("last_error") or "",
            "terminal_error": st.get("terminal_error") or "",
            "error_kind": st.get("error_kind") or "",
            "error_at": st.get("error_at") or 0,
            "error_count": st.get("error_count") or 0,
            "model": st.get("model") or "",
            "source": st.get("source") or "event",
            "started_at": st.get("started_at") or 0,
            "updated_at": recv_ms,
            "updated_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        if st.get("compaction_count") is not None:
            body["compaction_count"] = st["compaction_count"]

        rkey = self.keys.session + key
        try:
            pipe = self.r.pipeline(transaction=False)
            pipe.hset(rkey, mapping=body)
            pipe.expire(rkey, SESSION_TTL)
            if body["status"] == "running":
                pipe.sadd(self.keys.idx_running, key)
            else:
                pipe.srem(self.keys.idx_running, key)
            if body["status"] == "stalled":
                pipe.zadd(self.keys.idx_stalled, {key: body["last_event_at"] or recv_ms})
            else:
                pipe.zrem(self.keys.idx_stalled, key)
            if body["agent_id"]:
                pipe.sadd(self.keys.idx_by_agent + body["agent_id"], key)
            pipe.sadd(self.keys.idx_by_host + self.host_id, key)
            await pipe.execute()
            self.writes += 1
        except Exception as exc:
            self.errors += 1
            log.warning("redis write failed for %s: %s", key, exc)
            return

        if self.timing_path:
            rec = {
                "kind": "session",
                "key": key,
                "status": body["status"],
                "event_ts": event_ts,
                "recv_ms": recv_ms,
                "written_ms": int(time.time() * 1000),
            }
            with self.timing_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    async def flush_error(self, rec: dict) -> None:
        """Error ledger: one list per machine, LTRIM backstop + TTL auto-reclaim.

        Why a list and not a hash: errors are a stream that must be read in time order. The same error
        repeats across different runs; hash keys would overwrite each other and lose "how many times it happened".
        """
        lkey = self.keys.error_ledger + self.host_id
        try:
            pipe = self.r.pipeline(transaction=False)
            pipe.rpush(lkey, json.dumps(rec, ensure_ascii=False))
            pipe.ltrim(lkey, -ERROR_LEDGER_MAX, -1)
            pipe.expire(lkey, ERROR_TTL)
            await pipe.execute()
            self.error_records += 1
        except Exception as exc:
            self.errors += 1
            log.warning("redis error-ledger write failed: %s", exc)

    async def _flush_conflict(self, key: str, st: dict, recv_ms: int) -> None:
        """A session with the same name belongs to another host: write it into the isolation area, never overwrite the original key.

        * Added fields 2026-11-05: the isolation area used to hold only status/agent_id/last_event_at,
          without total_tokens/context_tokens/context_ratio - and these happen to be each machine's main
          session (every machine has agent:main:main), so "the most important session" was exactly the
          one whose context share could not be looked up.
          Now the body is fully isomorphic with the main-key write, so readers parse it with the same fields.
        """
        ckey = self.keys.conflict + self.host_id + ":" + key
        try:
            total = int(st.get("total_tokens") or 0)
            ctx = int(st.get("context_tokens") or 0)
            pipe = self.r.pipeline(transaction=False)
            pipe.hset(
                ckey,
                mapping={
                    "status": st.get("status") or "",
                    "agent_id": st.get("agent_id") or "",
                    "host_id": self.host_id,
                    "shadowed_key": self.keys.session + key,
                    "session_key": key,
                    "active_run_id": st.get("active_run_id") or "",
                    "last_run_id": st.get("last_run_id") or "",
                    "last_event_type": st.get("last_event_type") or "",
                    "last_event_at": st.get("last_event_at") or 0,
                    "total_tokens": total,
                    "context_tokens": ctx,
                    "context_ratio": round(total / ctx, 4) if ctx > 0 else 0,
                    "model": st.get("model") or "",
                    "updated_at": recv_ms,
                    "updated_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                },
            )
            pipe.expire(ckey, SESSION_TTL)
            pipe.sadd(self.keys.idx_conflict_by_host + self.host_id, key)
            pipe.incr(self.keys.conflict_count + self.host_id)
            await pipe.execute()
            self.conflicts += 1
        except Exception as exc:
            self.errors += 1
            log.warning("redis conflict write failed for %s: %s", key, exc)
            return
        if self.conflicts <= 1 or self.conflicts % 100 == 0:
            log.warning(
                "conflict: %s 已属另一台主机，本机写入隔离键（累计 %d）", key, self.conflicts
            )

    async def flush_run(self, run_id: str, fields: dict) -> None:
        if not run_id:
            return
        rkey = self.keys.run + run_id
        try:
            pipe = self.r.pipeline(transaction=False)
            pipe.hset(rkey, mapping={k: "" if v is None else v for k, v in fields.items()})
            pipe.expire(rkey, RUN_TTL)
            await pipe.execute()
            self.writes += 1
        except Exception as exc:
            self.errors += 1
            log.warning("redis run write failed for %s: %s", run_id, exc)


# -- main loop -----------------------------------------------------------


class Relay:
    def __init__(self, args, token: str, redis_client, host_id: str, keys: "Keys" = None):
        self.args = args
        self.token = token
        self.keys = keys or Keys(getattr(args, "namespace", DEFAULT_NAMESPACE))
        self.store = SessionStore(host_id, args.stall_seconds)
        self.writer = RedisWriter(redis_client, host_id, args.timing_log, self.keys)
        self.connected = False
        self.frame_counts = Counter()
        # first observation time of a run. A chat delta usually arrives only after the model emits its first token,
        # so using it as started_at pushes the run start back by seconds; instead use the earliest event_ts
        # across all frames of that runId.
        self.run_first_seen: dict[str, int] = {}
        self._last_reconcile = 0.0
        self.started_at = time.time()
        self._stop = asyncio.Event()
        # rate-limit self-protection: the current one-second window and how many frames it has handled
        self._burst_window = 0
        self._burst_count = 0
        self.dropped_burst = 0

    # -- self-protection -----------------------------------------------------

    def _rss_mb(self) -> float:
        """Current process RSS (MB). /proc exists only on Linux; on the Mac fall back to resource.

        188 (3.10) and the Mac (3.13) must both run, so both paths are implemented.
        """
        try:
            with open("/proc/self/status", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024.0
        except OSError:
            pass
        try:
            import resource

            v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # Linux: KB ; macOS: bytes
            return v / 1024.0 / 1024.0 if sys.platform == "darwin" else v / 1024.0
        except Exception:
            return 0.0

    def _cpu_seconds(self) -> float:
        """Cumulative CPU time (user+sys, seconds). Read /proc when present, otherwise use resource."""
        try:
            with open("/proc/self/stat", encoding="utf-8") as fh:
                parts = fh.read().split()
            # comm may contain spaces; counting from the right is safer: utime=14, stime=15 (1-based)
            return (int(parts[13]) + int(parts[14])) / os.sysconf("SC_CLK_TCK")
        except Exception:
            pass
        try:
            import resource

            u = resource.getrusage(resource.RUSAGE_SELF)
            return u.ru_utime + u.ru_stime
        except Exception:
            return 0.0

    async def _self_monitor(self):
        """Look at itself every 10s. Exits on its own when RSS exceeds MAX_RSS_MB - it does not wait to be checked."""
        last_cpu = self._cpu_seconds()
        last_t = time.time()
        while not self._stop.is_set():
            await asyncio.sleep(MONITOR_INTERVAL)
            rss = self._rss_mb()
            cpu = self._cpu_seconds()
            now = time.time()
            wall = max(now - last_t, 1e-6)
            cpu_pct = max(0.0, (cpu - last_cpu) / wall * 100.0)
            last_cpu, last_t = cpu, now
            log.info(
                "self: rss=%.1fMB cpu=%.1f%% frames=%d writes=%d errors=%d "
                "err_records=%d conflicts=%d burst_dropped=%d",
                rss, cpu_pct, sum(self.frame_counts.values()), self.writer.writes,
                self.writer.errors, self.writer.error_records,
                self.writer.conflicts, self.dropped_burst,
            )
            if rss > MAX_RSS_MB:
                log.error("RSS %.1fMB > %dMB，疑似泄漏，自行退出以保护主机", rss, MAX_RSS_MB)
                self._stop.set()
                return

    def _admit_frame(self, now_s: int) -> bool:
        """Above FRAME_BURST_PER_SEC in one second, stop piling into memory - record and drop the excess."""
        if now_s != self._burst_window:
            self._burst_window = now_s
            self._burst_count = 0
        self._burst_count += 1
        if self._burst_count > FRAME_BURST_PER_SEC:
            self.dropped_burst += 1
            if self.dropped_burst in (1, 10, 100, 1000) or self.dropped_burst % 5000 == 0:
                log.warning(
                    "frame burst: %d frames in 1s > %d，丢弃多余（累计丢 %d）",
                    self._burst_count, FRAME_BURST_PER_SEC, self.dropped_burst,
                )
            return False
        return True

    async def _sweeper(self):
        while not self._stop.is_set():
            await asyncio.sleep(5)
            now = int(time.time() * 1000)
            for key, why in self.store.sweep_stalled(now):
                st = self.store.sessions[key]
                await self.writer.flush_session(key, st, now, now)
                log.info("stalled: %s (%s)", key, why)

    async def _reconcile(self, ws) -> None:
        """Correct sessions that are "silent but still running" with the authoritative sessions.list snapshot.

        Only session state is filled in; started_at / run streams are untouched; persisted immediately after correction.
        """
        now = int(time.time() * 1000)
        suspects = [
            k
            for k, st in self.store.sessions.items()
            if st.get("status") == "running"
            and now - (st.get("last_event_at") or 0) >= RECONCILE_QUIET_MS
        ]
        if not suspects:
            return
        buf: list[dict] = []
        try:
            res = await self._call(
                ws, f"rc{now}", "sessions.list", {"limit": self.args.bootstrap_limit}, buf
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("reconcile: sessions.list 失败: %s", exc)
        else:
            rows = ((res.get("payload") or {}).get("sessions")) or []
            by_key = {r.get("key"): r for r in rows if r.get("key")}
            fixed = 0
            for key in suspects:
                r = by_key.get(key)
                if not r:
                    continue
                st = self.store.sessions.get(key)
                if not st:
                    continue
                new = self.store._status_from_snapshot(
                    {
                        "hasActiveRun": r.get("hasActiveRun"),
                        "status": r.get("status"),
                        "lastRunError": r.get("lastRunError") or None,
                    }
                )
                if new == st.get("status"):
                    continue
                quiet = (now - (st.get("last_event_at") or now)) // 1000
                log.info(
                    "reconcile: %s %s -> %s（静默 %ds）", key, st.get("status"), new, quiet
                )
                st["status"] = new
                st["last_run_id"] = r.get("lastRunId") or st.get("last_run_id") or ""
                if new != "running":
                    st["active_run_id"] = ""
                st["dirty"] = True
                await self.writer.flush_session(key, st, now, now)
                fixed += 1
            if fixed:
                log.info("reconcile: 纠正 %d 个会话（静默嫌疑 %d 个）", fixed, len(suspects))
        # live events arriving during _call must not be dropped
        for msg in buf:
            await self._handle(
                msg.get("event") or "", msg.get("payload") or {}, int(time.time() * 1000)
            )

    async def _handle(self, event: str, payload: dict, recv_ms: int) -> None:
        self.frame_counts[event] += 1
        event_ts = payload.get("ts") if isinstance(payload.get("ts"), int) else recv_ms

        # out-of-order protection: a gateway snapshot frame can arrive later than a subsequently emitted event (measured:
        # idle -> running -> idle flicker in the same session). chat is the authoritative source of terminal state and
        # is exempt; every other frame is dropped when older than the known last_event_at.
        skey = payload.get("sessionKey")
        # * Fixed 2026-11-14: the exemption was written as `event != "chat"`, which also let chat:delta (an intermediate state) through.
        #   The stated reason "chat is the authoritative source of terminal state" holds only for final/error/aborted.
        #   Narrowed to exempt terminal frames only - delta is protected against reordering like everything else.
        chat_terminal = event == "chat" and (
            (payload.get("state") or "").lower() in ("final", "error", "aborted")
        )
        if skey and not chat_terminal:
            known = self.store.sessions.get(skey)
            # compare against event_ts (the gateway generation time), not recv_ms (when we received it) -
            # mixing two different clocks misjudges many normal frames as late.
            if known and event_ts < (known.get("last_event_ts") or 0):
                self.frame_counts["late_dropped"] += 1
                return

        handler = {
            "sessions.changed": self.store.on_sessions_changed,
            "chat": self.store.on_chat,
            "agent": self.store.on_agent,
            "session.tool": self.store.on_tool,
            "session.message": self.store.on_message,
        }.get(event)

        if handler is None:
            return

        for key in handler(payload, recv_ms):
            st = self.store.sessions[key]
            if not st.get("started_at"):
                # gateway did not state a start time: use first observation
                st["started_at"] = recv_ms
            if event_ts > (st.get("last_event_ts") or 0):
                st["last_event_ts"] = event_ts
            if not st.get("dirty"):
                continue
            await self.writer.flush_session(key, st, event_ts, recv_ms)
            st["dirty"] = False

        # error ledger: handlers only collect, persisted centrally here
        if self.store.pending_errors:
            for rec in self.store.pending_errors:
                await self.writer.flush_error(rec)
            self.store.pending_errors.clear()

        # run-level detail: only chat's terminal state is authoritative, other events only update phase
        run_id = payload.get("runId")
        if run_id:
            prev = self.run_first_seen.get(run_id)
            if prev is None or event_ts < prev:
                self.run_first_seen[run_id] = event_ts
            if len(self.run_first_seen) > 1000:
                for k in list(self.run_first_seen)[:300]:
                    self.run_first_seen.pop(k, None)
        if run_id and event == "chat":
            state = (payload.get("state") or "").lower()
            phase = {"delta": "running", "final": "ended"}.get(state, state)
            fields = {
                "session_key": payload.get("sessionKey") or "",
                "agent_id": payload.get("agentId") or "",
                "phase": phase,
                "status": {"final": "success", "error": "failed", "aborted": "aborted"}.get(
                    state, "running"
                ),
                "stop_reason": payload.get("stopReason") or "",
                "terminal_error": "",
                "flags": "",
            }
            if state == "final":
                fields["ended_at"] = recv_ms
            elif state in ("error", "aborted"):
                fields["ended_at"] = recv_ms
            fields["started_at"] = self.run_first_seen.get(run_id, recv_ms)
            await self.writer.flush_run(run_id, fields)

    async def run(self) -> None:
        asyncio.create_task(self._sweeper())
        asyncio.create_task(self._self_monitor())
        backoff = RECONNECT_MIN
        while not self._stop.is_set():
            try:
                await self._session()
                backoff = RECONNECT_MIN
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._stop.is_set():
                    break
                # exponential backoff 1->2->4->8->16->30s. The 188 gateway is already at 72% CPU;
                # hammering it would drag production down, better to wait longer.
                log.warning("connection lost: %s (retry in %.0fs)", exc, backoff)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, RECONNECT_MAX)

    def seed_sessions(self, rows: list[dict], recv_ms: int) -> int:
        """Seed the store from a sessions.list snapshot (read-only RPC).

        Only fills sessions we have not seen yet: a live event is always
        fresher than a snapshot taken at connect time. Without this, coverage
        equals "sessions that happened to emit an event while we were up".
        """
        seeded = 0
        for s in rows:
            key = s.get("key")
            if not key or key in self.store.sessions:
                continue
            st = self.store._ensure(key, s.get("agentId"))
            # * this used to hard-code None here, which dropped the reason for historical failures at the door.
            #   The result: you know the session failed but never why.
            last_err = s.get("lastRunError") or ""
            st["status"] = self.store._status_from_snapshot(
                {
                    "hasActiveRun": s.get("hasActiveRun"),
                    "status": s.get("status"),
                    "lastRunError": last_err or None,
                }
            )
            runs = s.get("activeRunIds") or []
            st["active_run_id"] = runs[-1] if runs else ""
            st["last_run_id"] = s.get("lastRunId") or ""
            st["started_at"] = s.get("startedAt") or 0
            st["context_tokens"] = s.get("contextTokens") or 0
            st["total_tokens"] = s.get("totalTokens") or 0
            st["model"] = s.get("model") or ""
            st["last_event_at"] = recv_ms
            st["snapshot_updated_at"] = s.get("updatedAt") or 0
            st["last_event_type"] = "bootstrap"
            st["source"] = "bootstrap"
            st["dirty"] = True
            if last_err:
                # filled into the session fields for humans, but not into the ledger (history is not "what happened just now")
                self.store.record_error(
                    key,
                    last_err,
                    source="bootstrap",
                    run_id=st.get("last_run_id") or "",
                    recv_ms=recv_ms,
                    mark_failed=False,
                    to_ledger=False,
                )
            seeded += 1
        return seeded

    async def _req(self, ws, rid: str, method: str, params: dict) -> None:
        """Send one request. Non-read-only methods are structurally blocked here rather than by remembering not to send."""
        if method not in READONLY_METHODS:
            raise RuntimeError(f"refused non-readonly method: {method}")
        await ws.send(json.dumps({"type": "req", "id": rid, "method": method, "params": params}))

    async def _call(self, ws, rid: str, method: str, params: dict, buf: list) -> dict:
        await self._req(ws, rid, method, params)
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
            msg = json.loads(raw)
            if msg.get("type") == "res" and msg.get("id") == rid:
                return msg
            if msg.get("type") == "event":
                buf.append(msg)

    async def _session(self) -> None:
        async with websockets.connect(
            self.args.ws_url,
            max_size=64 * 1024 * 1024,
            ping_interval=None,
            open_timeout=15,
        ) as ws:
            json.loads(await ws.recv())  # connect.challenge
            params = dict(CONNECT)
            params["auth"] = {"token": self.token}
            await self._req(ws, "c1", "connect", params)
            res = json.loads(await ws.recv())
            if not res.get("ok"):
                raise RuntimeError(f"connect rejected: {res.get('error')}")

            await self._req(ws, "c2", "sessions.subscribe", {})

            buf: list[dict] = []
            auto_key = ""
            if self.args.bootstrap:
                res = await self._call(
                    ws, "b0", "sessions.list", {"limit": self.args.bootstrap_limit}, buf
                )
                rows = ((res.get("payload") or {}).get("sessions")) or []
                seeded = self.seed_sessions(rows, int(time.time() * 1000))
                # ask for ownership once up front (one pipeline) and then write in bulk,
                # avoiding 1600 single-key round trips.
                dirty = [k for k, st in self.store.sessions.items() if st.get("dirty")]
                if dirty:
                    await self.writer.resolve_owners(dirty)
                for key in dirty:
                    st = self.store.sessions[key]
                    await self.writer.flush_session(
                        key, st, st["last_event_at"], st["last_event_at"]
                    )
                    st["dirty"] = False
                if self.store.pending_errors:
                    for rec in self.store.pending_errors:
                        await self.writer.flush_error(rec)
                    self.store.pending_errors.clear()
                log.info(
                    "bootstrap: sessions.list 返回 %d 条，新种子 %d 条，跳过已知 %d 条",
                    len(rows), seeded, len(rows) - seeded,
                )
                # auto-open the gate: without an explicit key, pick an existing one from the snapshot,
                # avoiding dying on an unfamiliar host because of a non-existent key.
                if not self.args.messages_key and rows:
                    auto_key = rows[0].get("key") or ""

            gate_key = self.args.messages_key or auto_key
            if gate_key:
                await self._req(
                    ws, "c3", "sessions.messages.subscribe", {"key": gate_key}
                )
                log.info("messages gate opened with key=%s (%s)",
                         gate_key, "auto" if auto_key and not self.args.messages_key else "explicit")
            else:
                log.warning("未调 sessions.messages.subscribe —— 将收不到 chat/session.message，"
                            "只有 sessions.changed / agent 流（用 --bootstrap 可自动开闸）")

            self.connected = True
            log.info("connected to %s, subscribed (global + messages)", self.args.ws_url)

            # first drain the realtime events buffered during bootstrap
            for msg in buf:
                await self._handle(
                    msg.get("event") or "", msg.get("payload") or {},
                    int(time.time() * 1000),
                )

            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    # the idle window is exactly when silent calibration runs (no frames to process, so it does not steal recv)
                    if time.time() * 1000 - self._last_reconcile >= RECONCILE_EVERY_MS:
                        self._last_reconcile = time.time() * 1000
                        try:
                            await self._reconcile(ws)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("reconcile 异常: %s", exc)
                    continue
                recv_ms = int(time.time() * 1000)
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if msg.get("type") != "event":
                    continue
                if not self._admit_frame(recv_ms // 1000):
                    continue
                await self._handle(msg.get("event") or "", msg.get("payload") or {}, recv_ms)


# -- entry point ---------------------------------------------------------


def setup_logging(level: str, daemon: bool) -> None:
    fmt = "%(asctime)s %(levelname)s %(message)s"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        stream=sys.stdout if not daemon else sys.stderr,
    )


def daemonize(pid_file: Path, log_file: Path) -> None:
    """Classic double fork. Deliberately not systemd/launchd - the resident
    mechanism is still an open decision, this only detaches from the shell."""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = open(log_file, "a", encoding="utf-8")
    os.dup2(fh.fileno(), sys.stdout.fileno())
    os.dup2(fh.fileno(), sys.stderr.fileno())


def parse_args(argv):
    p = argparse.ArgumentParser(description="L1 会话实时状态中继：gateway -> Redis（只读）")
    p.add_argument("--ws-url", "--url", dest="ws_url", default="ws://127.0.0.1:18789",
                   help="gateway websocket 地址，默认 ws://127.0.0.1:18789")
    p.add_argument("--config", default=str(OPENCLAW_CFG),
                   help="openclaw 配置路径（token 从这里读，不从命令行传）")
    p.add_argument("--redis-config", default="",
                   help="AgentGeeX 配置路径；留空则用 ~/.agx/agx_config.json")
    p.add_argument("--host-id", required=True,
                   help="必填。test_host_mac / test_host_srv，写进每个 hash 的 host_id 字段")
    p.add_argument("--namespace", default=DEFAULT_NAMESPACE,
                   help="Redis 键前缀，默认 agx:gw:；测试期用 agx:gw:test188:")
    p.add_argument("--duration", "--seconds", dest="duration", type=float, default=0,
                   help="跑多少秒后优雅退出（0=不限）")
    p.add_argument("--stalled-min", type=float, default=5.0,
                   help="无事件多久判 stalled（分钟），默认 5")
    p.add_argument("--stall-seconds", type=float, default=0,
                   help="同 --stalled-min 但用秒（给了就覆盖它，调试用）")
    p.add_argument(
        "--bootstrap",
        action="store_true",
        help=("启动时用 sessions.list 拉一份全量快照做种子（只读）。"
              "不开则只有发过事件的会话会进 Redis"),
    )
    p.add_argument("--bootstrap-limit", type=int, default=3000)
    p.add_argument(
        "--messages-key",
        default="",
        help=("sessions.messages.subscribe 用的 key（一次开闸，不是过滤）。"
              "留空则不调，只靠 sessions.subscribe 的全局流"),
    )
    p.add_argument("--daemon", action="store_true")
    p.add_argument("--pid-file", default=str(STATE_DIR / "relay.pid"))
    p.add_argument("--log-file", default=str(STATE_DIR / "relay.log"))
    p.add_argument("--timing-log", default=str(STATE_DIR / "relay_timing.jsonl"),
                   help="逐次写 Redis 的延迟明细（排障用），传空串关闭")
    p.add_argument("--log-level", default="INFO")

    args = p.parse_args(argv)
    # both entry points can express the stall threshold; seconds take priority (no decimals to compute when trying 30s while debugging)
    args.stall_seconds = (
        int(args.stall_seconds) if args.stall_seconds > 0 else int(args.stalled_min * 60)
    )
    if args.stall_seconds < 10:
        p.error("--stalled-min/--stall-seconds 太小（最小 10 秒）")
    return args


async def amain(args) -> int:
    token = load_gateway_token(Path(args.config))
    cfg_path, cfg = resolve_agx_cfg(args.redis_config)
    rcfg = cfg.get("redis") or {}
    host_id = args.host_id.strip()
    keys = Keys(args.namespace)

    if not rcfg.get("host"):
        print(
            "[ERROR] 找不到 redis 配置。已尝试：%s"
            % ", ".join(
                [args.redis_config] if args.redis_config else []
                + [str(p) for p in AGX_CFG_CANDIDATES]
            )
        )
        return 1

    timing = Path(args.timing_log).expanduser() if args.timing_log else None
    if timing:
        timing.parent.mkdir(parents=True, exist_ok=True)

    # * Fixed 2026-11-13: the password moved from agx_config.json to ~/.agx/agx_creds.env,
    #   leaving only host/port/db in the JSON. Reading only the JSON gave password=None -> unauthenticated,
    #   Redis returned AuthenticationError, the collector crashed roughly every 10s,
    #   and session state froze at the last successful write (measured: the Mac froze for 13+ hours).
    #   daemon / ws_client go through config_loader (which injects the creds env),
    #   while this collection chain read the JSON directly - that is the root of the inconsistency.
    client = aioredis.Redis(
        host=rcfg.get("host", "127.0.0.1"),
        port=int(rcfg.get("port", 6379)),
        db=int(rcfg.get("db", 0)),
        username=rcfg.get("username") or os.environ.get("AGX_REDIS_USERNAME") or None,
        password=rcfg.get("password") or os.environ.get("AGX_REDIS_PASSWORD") or None,
        decode_responses=True,
        socket_timeout=5,
    )
    try:
        await client.ping()
    except Exception as exc:
        # report only "which step failed", never the connection string or password
        print(f"[ERROR] Redis 不可用（{rcfg.get('host')}:{rcfg.get('port')}）: {type(exc).__name__}")
        return 1

    relay = Relay(args, token, client, host_id, keys)
    log.info(
        "relay starting: host=%s ns=%s stall=%ss duration=%ss ws=%s cfg=%s",
        host_id, keys.ns, args.stall_seconds, args.duration, args.ws_url,
        cfg_path or "(none)",
    )

    runner = asyncio.create_task(relay.run())
    stopper = asyncio.create_task(_wait_stop(relay, args.duration))
    done, pending = await asyncio.wait(
        {runner, stopper}, return_when=asyncio.FIRST_COMPLETED
    )
    relay._stop.set()
    for t in pending:
        t.cancel()
    for t in done | pending:
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    sessions = len(relay.store.sessions)
    print(
        f"\n=== relay 退出 | 会话 {sessions} 个 | Redis 写 {relay.writer.writes} 次 "
        f"| 失败 {relay.writer.errors} 次 | 冲突 {relay.writer.conflicts} 次 "
        f"| 突发丢弃 {relay.dropped_burst} 帧 | 时长 {time.time() - relay.started_at:.0f}s"
    )
    print("事件计数: " + ", ".join(f"{k}={v}" for k, v in relay.frame_counts.most_common()))
    await client.aclose()
    return 0


async def _wait_stop(relay: Relay, seconds: float) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with _suppress_not_impl():
            loop.add_signal_handler(sig, relay._stop.set)
    if seconds and seconds > 0:
        try:
            await asyncio.wait_for(relay._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
    else:
        await relay._stop.wait()


class _suppress_not_impl:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is NotImplementedError


def main(argv) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level, args.daemon)
    if args.daemon:
        daemonize(Path(args.pid_file), Path(args.log_file))
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
