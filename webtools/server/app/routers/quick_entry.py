"""Quick-entry routes -- /v1/quick-entries.

[2026-11-13 22:03 Feng] mark frequently used projects / agents / sessions as quick and jump straight to them
from the "quick entries" page; the send-message dialog also has a button to jump there.

    GET    /v1/quick-entries          list of marked entries
    POST   /v1/quick-entries          mark as quick (idempotent)
    DELETE /v1/quick-entries          unmark

★ [2026-11-13 22:25 Feng changed the definition] no standalone favourites table and no per-person split:
    the marker lives in the `is_quick` column of `agx_project` / `agx_agent_registry` / `agx_session`, and it is
    **one global set**. So this router no longer takes an owner or writes an owner --
    per-person bucketing died with the standalone table.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import quick_entry
from ..auth import current_user

log = logging.getLogger("win3.bff.quick_entry")

router = APIRouter(tags=["quick-entry"])
_auth = Depends(current_user)


class AddEntryBody(BaseModel):
    entry_type: str = Field(..., description="project | agent | session")
    entry_key: str = Field(..., description="目标唯一键")
    label: str = Field("", description="兼容旧签名；名字现取目标表，不再存快照")


@router.get("/quick-entries")
def list_quick_entries(user: dict = _auth) -> dict:
    _ = user  # authentication only; the list is global (the field is on the core tables; there is no owner)
    return {"entries": quick_entry.list_entries(), "owner": ""}


@router.post("/quick-entries")
def add_quick_entry(body: AddEntryBody, user: dict = _auth) -> dict:
    _ = user
    try:
        entry = quick_entry.add_entry(body.entry_type, body.entry_key, body.label)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        # the columns are not in place yet: return an explicit 503 rather than 500 -- the frontend then says "waiting for the columns"
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"entry": entry}


@router.delete("/quick-entries")
def remove_quick_entry(
    entry_type: str = Query(..., min_length=1),
    entry_key: str = Query(..., min_length=1),
    user: dict = _auth,
) -> dict:
    _ = user
    ok = quick_entry.remove_entry(entry_type, entry_key)
    return {"removed": ok}
