"""settings routes -- "which services this deployment has, and which is in use now".

## Why a separate settings router instead of stuffing it all into audio

Feng, 2026-11-07: "under settings there is only one thing, checking MCP... see if you can make a window where
you can both check MCP and set the speech host. There may be other services later, because you cannot keep
hanging them off the menu."

So the layout is: **the window is the container (frontend), the API is grouped by service (backend)**.
Today there is only the asr group; later transcription/OCR/vector DB add another prefix and the frontend adds a
tab -- the menu item stays a single "service settings".

## Relationship with /v1/audio/health

`/v1/audio/health` is what the **speech component itself** needs (polling during recording whether the current
host is reachable); its semantics are "can I record right now".
This router is what the **settings window** needs (all hosts + their states + switching); its semantics are
"what can I choose from, and which is chosen".
Both read the same in-memory state and neither re-probes; only the viewpoint differs.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import asr, service_settings
from ..auth import current_user

log = logging.getLogger("win3.bff.settings")

router = APIRouter(tags=["settings"])
_auth = Depends(current_user)


class AsrActiveIn(BaseModel):
    host_id: str


@router.get("/settings/asr")
def get_asr_settings(_u: dict = _auth) -> dict:
    """All speech hosts + their availability + the current selection.

    ★ Reads the background probe's in-memory conclusion and **does not hit the service** -- opening the
      settings window must not knock on N machines. To know right now whether one host is reachable:
      `POST /settings/asr/{host_id}/probe`.
"""
    return asr.hosts_snapshot()


@router.put("/settings/asr")
async def set_asr_active(body: AsrActiveIn, _u: dict = _auth) -> dict:
    """Switch the speech host currently in use.

    Two actions must happen together; missing either breaks the experience:
      (1) persist the preference (still this host after a restart)
      (2) **probe this host immediately** -- otherwise it still reads "unknown" in the UI and the person must
          wait a whole probe round to learn what they just selected.
"""
    try:
        host = service_settings.set_active(body.host_id)
    except ValueError as e:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "ASR_HOST_NOT_FOUND",
                    "message": str(e),
                    "details": {"available": [h["id"] for h in service_settings.hosts()]},
                }
            },
        ) from e

    # changing the config changes the "can it work" question: the old state means nothing for the new host
    asr._reset_state(host["id"])  # noqa: SLF001 -- same-package internal state; deliberately no extra public interface
    await asr.probe(host["id"])
    return asr.hosts_snapshot()


@router.post("/settings/asr/{host_id}/probe")
async def probe_asr_host(host_id: str, _u: dict = _auth) -> dict:
    """Force a probe of one host (the cost of a human clicking "check": one WS handshake)."""
    if not service_settings.get(host_id):
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "ASR_HOST_NOT_FOUND",
                    "message": f"未知的语音主机：{host_id}",
                    "details": {"available": [h["id"] for h in service_settings.hosts()]},
                }
            },
        )
    await asr.probe(host_id)
    return asr.hosts_snapshot()
