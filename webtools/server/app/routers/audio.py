"""audio routes -- speech to text.

Convention: `POST /v1/audio/transcribe`
    body: **raw audio binary** (no multipart wrapping)
    Content-Type: audio/webm / audio/wav / audio/mp4 / application/octet-stream (any works; the server sniffs)
    -> 200 `{ text, duration_ms, bytes, engine }`

Why raw bytes instead of multipart:
    there is only one field (the audio itself), and multipart would parse a boundary for nothing, adding another
    failure surface. The frontend needs one line: `fetch(url, {body: blob})`.

Error codes (the frontend uses them to say different human things, not one blanket "it failed"):
    400 EMPTY_AUDIO        -- no sound recorded (clicked and released / microphone muted)
    413 AUDIO_TOO_LARGE    -- over 20MB (about 20 minutes of audio; normal speech never gets there)
    422 ASR_FAILED         -- the server failed to recognise (bad audio / model error)
    503 ASR_UNAVAILABLE    -- cannot reach the speech service (not started / port closed)
    504 ASR_TIMEOUT        -- recognition timed out (cold model start or stuck)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import asr
from ..auth import current_user

log = logging.getLogger("win3.bff.audio")

router = APIRouter(tags=["audio"])
_auth = Depends(current_user)

# 20MB cap: 16k/16bit/mono is about 1.9MB per minute, webm/opus is smaller;
# a normal sentence is seconds to tens of seconds, so 20MB is about twenty minutes -- far beyond "one sentence".
MAX_BYTES = 20 * 1024 * 1024


@router.post("/audio/transcribe")
async def transcribe(request: Request, _u: dict = _auth) -> dict:
    audio = await request.body()

    if not audio:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "EMPTY_AUDIO", "message": "没有收到音频数据", "details": {}}},
        )
    if len(audio) > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "error": {
                    "code": "AUDIO_TOO_LARGE",
                    "message": f"音频 {len(audio) / 1048576:.1f}MB 超过上限 20MB",
                    "details": {"bytes": len(audio)},
                }
            },
        )

    try:
        return await asr.transcribe(audio)
    except asr.AsrError as e:
        # pass attribution through: 503/504/422 each carry "which link broke", never a blanket 500
        raise HTTPException(
            status_code=e.status,
            detail={
                "error": {
                    "code": e.code,
                    "message": e.message,
                    "details": {"bytes": len(audio)},
                }
            },
        ) from e


@router.get("/audio/health")
async def audio_health(_u: dict = _auth) -> dict:
    """Speech service state (reads the background probe's result; **does not hit the service**).

    Why cache instead of probing every time: the frontend polls this endpoint while recording.
    A real handshake on every poll would use someone's speech service as a status-bar heartbeat.
    The probe already runs every 8s, and reading it is enough; to know right now, `probe=true` forces one probe.
"""
    return asr.snapshot()


@router.post("/audio/health/probe")
async def audio_probe(_u: dict = _auth) -> dict:
    """Force one probe (used for actions like a human clicking "retry"; costs one handshake)."""
    await asr.probe()
    return asr.snapshot()
