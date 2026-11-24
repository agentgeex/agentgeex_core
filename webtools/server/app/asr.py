"""Speech-to-text (ASR) channel -- browser -> BFF -> local ASR service.

## Why go through the BFF instead of the browser connecting to ASR directly

The ASR service (`reasoning_service/audio/audio_service_server.py`, :8888) does set
`allow_origins=["*"]`, so a direct browser connection is technically possible. But that has three problems:

1. **The address leaks into the frontend**: `ws://127.0.0.1:8888` hardcoded in the bundle means repackaging
   whenever the machine/port changes, and the commercial console is ultimately for remote customers, where
   `127.0.0.1` does not exist at all.
2. **It bypasses authentication**: `/v1/*` all require a Bearer token, and a direct ASR connection has no such
   gate -- anyone who opens the page can hammer transcription.
3. **Error attribution scatters**: unreachable / timeout / model not loaded can only be caught in the browser,
   with no way to say whose problem it is.

So: **the BFF is the only front door**, the address exists only in server-side config (`.env`'s
`BFF_ASR_WS_URL`, see `.env.example`), and the frontend only ever knows `/v1/audio/transcribe`.

## The ASR service protocol (measured 2026-11-06, not guessed)

```
WS ws://127.0.0.1:8888/ws/audio/audio2text/{client_id}
   -> send arbitrarily chunked binary audio (the server sniffs the format itself: webm/wav/m4a/ogg/amr/raw PCM all work)
   -> send one **empty byte frame b""** as the end marker
   <- text (a plain string)
   <- "AUDIO_PROCESSING_COMPLETE" (the end-of-protocol marker)
   <- on error it is "ERROR: ..." (**but the WS is not closed and no end marker is sent** -- so a timeout is
      mandatory, otherwise it hangs forever)
```

Measured: 1.7s of audio in warm state returns in **0.8-0.9s**; cold (first model load) about 10s.
So the timeout is 30s: enough for a cold start, without leaving the UI spinning forever.

## What "auto-reconnect on disconnect" actually reconnects (no self-deception)

This service is **a short question-and-answer connection**: every recognition opens a WS, sends, and closes.
So **there is no "long connection" sitting there needing keepalive** -- there is no "reconnect to the original
session".

What actually bites is two situations, each handled separately:

| Situation | Symptom | Countermeasure |
|---|---|---|
| **Transient blip** (service just restarted, port swapping) | one recognition fails and the person must repeat themselves | `transcribe` retries internally (2 by default, 250ms increasing interval) |
| **Service genuinely down / not started** | every click fails | a **background health check** (every 8s by default) keeps probing; the UI shows "reconnecting" and recovers automatically when the service returns, without a page refresh |

The health check is **independent of requests**: it probes whether or not anyone is using it. So "the service
is back" is discovered by the system first, instead of waiting for someone to click once more and fail.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from . import service_settings
from .config import settings

log = logging.getLogger("win3.bff.asr")

# end marker (the server recognises this)
_END_MARKER = b""
_COMPLETE = "AUDIO_PROCESSING_COMPLETE"

# Chunk size: the server accumulates by block; 8192 was measured to work well (not a protocol requirement)
_CHUNK = 8192

# Connect retries: a transient blip should not make the person repeat themselves. Starts at 250ms; not too many --
# if it really is not up, 10 retries only lengthens the wait and delays the "service is not up" conclusion.
_RETRY_TIMES = 2
_RETRY_BASE_DELAY = 0.25


class AsrError(Exception):
    """ASR failure. code is passed straight to the frontend as the error code, which uses it to say different human-readable things."""

    def __init__(self, code: str, message: str, status: int = 400, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable


# ── service availability state (maintained by the background health check; endpoints only read it) ────────────
# ★ 2026-11-07 multi-host: one state -> one state per host (key = host id).
#   Why they must be separate: two machines' availability **differ at the same instant** (the local one is up, the remote one is not),
#   and sharing one state makes the UI paste "this one works" onto that one -- worse than showing nothing.
_DEFAULT_STATE = {
    "available": None,      # None = not probed yet / True / False
    "checked_at": 0.0,      # when the last probe completed (monotonic)
    "ok_at": 0.0,           # when the last successful connection happened
    "error": "",            # last failure reason
    "fail_streak": 0,       # consecutive failure count (used for judgements like "just recovered")
}
_states: dict[str, dict] = {}


def _state(host_id: str) -> dict:
    st = _states.get(host_id)
    if st is None:
        st = dict(_DEFAULT_STATE)
        _states[host_id] = st
    return st


def _reset_state(host_id: str) -> None:
    _states.pop(host_id, None)


def _new_url(host_id: str | None = None, suffix: str = "") -> str:
    """Use a fresh client_id every time -- the server keeps buffers keyed by client_id,
    and reusing one id lets leftover audio from the previous round mix in.
"""
    h = service_settings.get(host_id) if host_id else service_settings.active()
    base = (h or service_settings.active())["url"].rstrip("/")
    return f"{base}/{suffix or uuid.uuid4().hex[:12]}"


def snapshot(host_id: str | None = None) -> dict:
    """State snapshot for endpoints (does not hit the service; pure memory read). Defaults to the currently selected host."""
    h = service_settings.get(host_id) if host_id else service_settings.active()
    h = h or service_settings.active()
    st = _state(h["id"])
    return {
        "ok": st["available"] is True,
        "state": (
            "unknown" if st["available"] is None
            else "up" if st["available"]
            else "down"
        ),
        "host_id": h["id"],
        "host_name": h["name"],
        "url": h["url"],
        "error": st["error"],
        "fail_streak": st["fail_streak"],
        "checked_ago_ms": (
            int((time.monotonic() - st["checked_at"]) * 1000) if st["checked_at"] else None
        ),
        "up_ago_ms": (
            int((time.monotonic() - st["ok_at"]) * 1000) if st["ok_at"] else None
        ),
    }


def hosts_snapshot() -> dict:
    """All hosts + their states + the current selection. For the settings window (also does not hit the service)."""
    act = service_settings.active_id()
    return {
        "active": act,
        "hosts": [
            {**h, **snapshot(h["id"]), "active": h["id"] == act}
            for h in service_settings.hosts()
        ],
        "active_url": service_settings.active()["url"],
    }


async def probe(host_id: str | None = None, timeout: float = 3.0) -> bool:
    """Actually connect once (handshake only, then disconnect) and write the result into that host's state.

    ★ Why connect the WS instead of probing the TCP port: a reachable port does not mean a usable service
"""
    import websockets

    h = service_settings.get(host_id) if host_id else service_settings.active()
    h = h or service_settings.active()
    st = _state(h["id"])

    url = _new_url(h["id"], "probe_" + uuid.uuid4().hex[:6])
    try:
        async with websockets.connect(url, open_timeout=timeout):
            pass
        first_ok = st["available"] is not True
        st.update(
            available=True,
            checked_at=time.monotonic(),
            ok_at=time.monotonic(),
            error="",
            fail_streak=0,
        )
        if first_ok:
            log.info("[asr] 服务可用 → %s（%s）", h["url"], h["id"])
        return True
    except Exception as e:  # noqa: BLE001 -- any reason for a probe failure counts as unavailable
        was_up = st["available"] is not False
        st.update(
            available=False,
            checked_at=time.monotonic(),
            error=f"{type(e).__name__}: {e}",
            fail_streak=st["fail_streak"] + 1,
        )
        if was_up:
            # log only when the state flips, otherwise one line every 8s floods the log
            log.warning("[asr] 服务不可用（%s）：%s（后台会自动重试）", h["id"], st["error"])
        return False


async def health_loop(interval: float | None = None) -> None:
    """Background health check: keep probing **every host** until the process ends.

    ★ Its only reason to exist is "the service coming back must be discovered by the system first".
      Without it a person can only click once more, fail once more, and then learn it recovered -- that is
      luck, not self-healing.

    ★ Why probe the hosts not in use too: the settings window must show "is each one reachable right now".
      Probing only the selected host means switching to another shows "unknown" for a moment and one more
      round must be waited -- even though it could have been probing in the background. The cost is N
      handshakes per 8s with N typically 2-3, not worth a priority scheme.
"""
    gap = settings.asr_health_interval if interval is None else interval
    # On cold start wait one beat first: the ASR service often starts alongside the BFF, so an immediate probe always fails and records one bogus error.
    await asyncio.sleep(1.0)
    while True:
        try:
            for h in service_settings.hosts():
                try:
                    await probe(h["id"])
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 -- one host failing the probe must not stop the others
                    log.error("[asr] 巡检异常（%s）：%s", h["id"], e)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 -- the health check itself must not kill the loop
            log.error("[asr] 巡检循环异常（继续）：%s", e)
        await asyncio.sleep(max(1.0, gap))


async def _collect(url: str, audio: bytes, timeout: float, host: dict | None = None) -> tuple[str, float]:
    """Connect to the ASR service, feed the audio in, read the text back. Returns (text, elapsed seconds)."""
    import websockets

    t0 = time.monotonic()
    try:
        async with websockets.connect(url, max_size=None, open_timeout=min(timeout, 15.0)) as ws:
            for i in range(0, len(audio), _CHUNK):
                await ws.send(audio[i : i + _CHUNK])
            await ws.send(_END_MARKER)

            text = ""
            while True:
                left = timeout - (time.monotonic() - t0)
                if left <= 0:
                    raise AsrError("ASR_TIMEOUT", f"等待识别结果超过 {timeout:.0f}s", 504)
                msg = await asyncio.wait_for(ws.recv(), timeout=left)
                if isinstance(msg, bytes):
                    continue  # this endpoint does not return binary; ignore it
                if msg == _COMPLETE:
                    return text, round(time.monotonic() - t0, 3)
                if msg.startswith("ERROR"):
                    raise AsrError("ASR_FAILED", msg, 422)
                text = msg  # normal case: text first, then the completion marker
    except AsrError:
        raise
    except TimeoutError as e:
        raise AsrError("ASR_TIMEOUT", f"等待识别结果超过 {timeout:.0f}s", 504) from e
    except (OSError, ConnectionError) as e:
        raise AsrError(
            "ASR_UNAVAILABLE", f"连不上语音服务（{_who(host)}）：{e}", 503, retryable=True
        ) from e
    except Exception as e:  # noqa: BLE001 -- even the fallback must attribute the cause; do not let it become a 500
        # websockets' handshake failure is often WebSocketException (not in the OSError family); count it as "unreachable" too
        if type(e).__name__.startswith("WebSocket") or "handshake" in str(e).lower():
            raise AsrError(
                "ASR_UNAVAILABLE", f"语音服务握手失败（{_who(host)}）：{e}", 503, retryable=True
            ) from e
        raise AsrError("ASR_FAILED", f"{type(e).__name__}: {e}", 422) from e


def _who(host: dict | None) -> str:
    """Which host to name in the error -- with multiple hosts a bare address leaves people unable to tell local from remote."""
    if not host:
        return "语音服务"
    return f"{host['name']} {host['url']}"


async def transcribe(audio: bytes) -> dict:
    """Audio bytes -> text. Uses **the currently selected** host. Empty audio returns an empty string directly (does not disturb the service)."""
    if not audio:
        return {"text": "", "duration_ms": 0, "bytes": 0}

    host = service_settings.active()
    st = _state(host["id"])
    last: AsrError | None = None
    for attempt in range(_RETRY_TIMES + 1):
        try:
            text, cost = await _collect(_new_url(host["id"]), audio, settings.asr_timeout, host)
            st.update(
                available=True, checked_at=time.monotonic(), ok_at=time.monotonic(),
                error="", fail_streak=0,
            )
            log.info("[asr] %s %d bytes → %r (%.2fs%s)",
                     host["id"], len(audio), text[:60], cost,
                     f", retry#{attempt}" if attempt else "")
            return {
                "text": text,
                "duration_ms": int(cost * 1000),
                "bytes": len(audio),
                "engine": "sensevoice",
                "host_id": host["id"],
                "host_name": host["name"],
                "attempts": attempt + 1,
            }
        except AsrError as e:
            if not (e.retryable and attempt < _RETRY_TIMES):
                # Record failures too: that way the UI's "unavailable" is discovered by the request itself, without waiting for the next probe round
                st.update(
                    available=False, checked_at=time.monotonic(),
                    error=e.message, fail_streak=st["fail_streak"] + 1,
                )
                raise
            last = e
            await asyncio.sleep(_RETRY_BASE_DELAY * (attempt + 1))

    raise last or AsrError("ASR_FAILED", "未知错误")
