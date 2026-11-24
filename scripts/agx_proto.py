#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AGX message envelope - the single construction point for every platform.

* Why this module exists (owner's decision, 2026-11-16: this must not happen again)
    What the receiver gets is a **compact envelope** carrying protocol fields only, no body
    (the receiver reads exec_instruction / summary back from the DB by task_run_uuid).
    This envelope used to be hand-written in 6 places (3 in daemon + 3 in watchdog); when msg_type
    was added on 2026-10-26 only "first dispatch" was updated, while "daemon retry dispatch" and
    "watchdog wake resend" were missed -> those two paths kept sending the old 3-field envelope.

    Measured (2026-11-16 13:43, found by the owner):
         run_general_6e0a76907c02 took two retry dispatches due to session_busy, and the receiver got
         {"task_run_uuid","send_agent","self_session_id"} -- no msg_type (cannot tell dispatch from
         receipt) and no resend_attempt (cannot tell it is a resend).
         Damage: if the resent message is a receipt, the receiver treats it as a new task and redoes
         it (wasted work + duplicate side effects).

    The fix is not to patch fields one by one (the next change will miss again) but to keep exactly
    one construction point: every path calls build_struct() / dumps_struct().

Field contract (receivers parse against this):
    task_run_uuid     id of this execution; the receiver reads instruction and result from the DB with it
    send_agent        sending Agent (dispatcher / receipt issuer)
    self_session_id   session id of the **receiver itself** (same source as the session-context block
                      at the top of exec_instruction; receipt routing does not read it, it reads DB send_session_id)
    msg_type          dispatch | receipt | urge
    resend_attempt    resend marker. 0 = first send, n>0 = the n-th resend.
                      Always included (no implicit "missing field means first send" convention)
    original_sent_at  optional; only on receipt resends, the ISO time of the first send
"""

from __future__ import annotations

import json

MSG_DISPATCH = "dispatch"
MSG_RECEIPT = "receipt"
MSG_URGE = "urge"
VALID_MSG_TYPES = (MSG_DISPATCH, MSG_RECEIPT, MSG_URGE)


def msg_type_for(is_reply_to: str = "") -> str:
    """Derive msg_type from is_reply_to.

    A non-empty is_reply_to means this execution replies to another task -> receipt;
    otherwise -> dispatch.
    (urge is not derived here: it is a watchdog/manual action on its own path.)
    """
    return MSG_RECEIPT if (is_reply_to or "").strip() else MSG_DISPATCH


def build_struct(task_run_uuid: str,
                 send_agent: str,
                 self_session_id: str,
                 msg_type: str = MSG_DISPATCH,
                 resend_attempt: int = 0,
                 original_sent_at: str | None = None) -> dict:
    """Build the envelope dict. Field order is fixed for byte-level log comparison."""
    mt = (msg_type or "").strip() or MSG_DISPATCH
    if mt not in VALID_MSG_TYPES:
        raise ValueError(
            f"invalid msg_type {msg_type!r}, expect one of {VALID_MSG_TYPES}"
        )
    d = {
        "task_run_uuid": (task_run_uuid or "").strip(),
        "send_agent": (send_agent or "").strip(),
        "self_session_id": (self_session_id or "").strip(),
        "msg_type": mt,
        "resend_attempt": int(resend_attempt or 0),
    }
    if original_sent_at:
        d["original_sent_at"] = str(original_sent_at)
    return d


def dumps_struct(task_run_uuid: str,
                 send_agent: str,
                 self_session_id: str,
                 msg_type: str = MSG_DISPATCH,
                 resend_attempt: int = 0,
                 original_sent_at: str | None = None) -> str:
    """Build the envelope JSON string (this is what the receiver actually gets)."""
    return json.dumps(
        build_struct(task_run_uuid, send_agent, self_session_id,
                     msg_type=msg_type, resend_attempt=resend_attempt,
                     original_sent_at=original_sent_at),
        ensure_ascii=False,
    )


def is_complete(struct: dict) -> tuple[bool, list[str]]:
    """Validate that an envelope has all fields. Returns (complete, missing_fields).

    For self-tests and inspection: any envelope captured from a queue can be run through this, and a
    missing field means a new hand-written construction point has appeared.
    """
    required = ("task_run_uuid", "send_agent", "self_session_id",
                "msg_type", "resend_attempt")
    missing = [k for k in required if k not in (struct or {})]
    if struct and struct.get("msg_type") not in VALID_MSG_TYPES:
        missing.append("msg_type(invalid)")
    return (not missing), missing
