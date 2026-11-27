"""actions routes -- operation endpoints such as "run task" on the agent detail page.

Convention: `POST /v1/agents/{agent_id}/actions/run-task`
    body: { session_key, instruction, task_id, task_name? }
    Semantics: **wrap the task into a task** (agx_dispatch_task) and deliver it to the given session --
         the task template's x_desc (task description) + instruction are merged into exec_instruction,
         an execution is created and lands in that session, and the daemon runs it in an independent session.
    (No more raw text sends; the raw-send entry point is the session page, see the notes in app/actions.py.)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import actions, realdata
from ..actions import ActionError
from ..auth import current_user

log = logging.getLogger("win3.bff.actions")

router = APIRouter(tags=["actions"])
_auth = Depends(current_user)


class RunTaskBody(BaseModel):
    # ★ receiver (fixed) = the path agent; this dialog only chooses (1) sender identity (2) sender session (3) receiving session
    session_key: str = Field(..., description="接收会话 key（须属【接收方 agent】= 任务落点）")
    instruction: str = Field("", description="任务执行指令 / 说明（与 task 模板 x_desc 合并）")
    task_id: str = Field("", description="关联任务 id（包装成 task 必需）")
    task_name: str = Field("", description="关联任务名（可读文本 + 审计）")
    # [2026-10-10 Feng: super terminal] sender identity passed explicitly (defaults to the path agent)
    caller_agent_id: str = Field("", description="发送方 Agent 身份（缺省回退路径 agent）")
    # [2026-10-10] sender session (defaults to agent:{caller}:main; must belong to the sender)
    send_session_key: str = Field("", description="发送方会话 key（缺省回退发送方主会话）")
    # [2026-10-10] agx_dispatch_task common optional parameters (needs_subagent removed -- a legacy attribute)
    needs_reply: int | None = Field(None, description="回执需求 0/1（None=不传）")
    # [2026-10-10] explicitly disable the receipt (higher priority than task.reply_mode derivation). Pass true when the user picks "no reply needed".
    force_no_reply: bool = Field(False, description="显式关闭回执（压过 task.reply_mode 推导）")
    timeout_seconds: int | None = Field(None, description="看门狗超时秒数（None=不传）")
    exec_name: str = Field("", description="执行实例短标题（空=不传）")
    # [2026-10-18 Feng] skills to reference in this task (a list of agx_skill.skill_id, valid skills only).
    #   The server renders them into a "reference skills" note appended to instruction; any invalid id -> 400 SKILL_NOT_FOUND.
    skill_ids: list[str] = Field(default_factory=list, description="引用技能 id 列表（仅有效技能）")
    # [2026-10-19 P5 Feng] reply = a forced choice, no middle ground.
    #   Non-empty -> go through the kernel's unified facade agx_universal_send (the BFF no longer converts force_no_reply).
    #   Empty -> keep the old path (compatibility for older dialogs not yet migrated).
    reply: str = Field("", description="回执：'yes' | 'no'（空=旧路径，按模板推导）")
    # [2026-10-19 P2] idempotency key: retries of the same intent reuse the same value -> no duplicate dispatch.
    client_request_id: str = Field("", description="幂等键（同值重试返回首次结果）")
    # [2026-11-06 Feng] human approval: 1 = land in awaiting_confirm when done; only after a human clicks approve does it count as complete.
    #   None/0 = not needed (default, same as before). Semantics are defined only in the kernel; the BFF passes it down as-is.
    needs_confirm: int | None = Field(None, description="人类放行 0/1（None=不传=0）")


class ScheduleTaskBody(BaseModel):
    """[2026-11-27 Feng] "定时执行" input: same fields as run-task, plus the moment to run at.

    scheduled_at is the operator's wall-clock time ("2026-11-27T09:30" from datetime-local, or
    "2026-11-27 09:30:00"). The BFF normalises it and refuses the past -- see actions._parse_scheduled_at.
    """

    session_key: str = Field("", description="接收会话 key（空=新会话；非空须属接收方 agent）")
    instruction: str = Field("", description="任务执行指令 / 说明（与 task 模板 x_desc 合并）")
    task_id: str = Field("", description="关联任务 id（包装成 task 必需）")
    task_name: str = Field("", description="关联任务名（可读文本 + 审计）")
    caller_agent_id: str = Field("", description="发送方 Agent 身份（缺省回退路径 agent）")
    send_session_key: str = Field("", description="发送方会话 key（缺省回退发送方主会话）")
    scheduled_at: str = Field(..., description="执行时间（本地时间 YYYY-MM-DDTHH:MM 或 YYYY-MM-DD HH:MM:SS，必须晚于当前）")
    reply: str = Field("", description="回执：'yes' | 'no'（空=按模板推导）")
    timeout_seconds: int | None = Field(None, description="看门狗超时秒数（None=不传）")
    exec_name: str = Field("", description="执行实例短标题（空=不传）")
    skill_ids: list[str] = Field(default_factory=list, description="引用技能 id 列表（仅有效技能）")
    needs_confirm: int | None = Field(None, description="人类放行 0/1（None=不传=0）")


class DispatchExemptBody(BaseModel):
    """Temporary dispatch exemption input.

    [2026-10-31 Feng] the console submits to the kernel **as agx_core**
    (see realdata.EDITOR_AGENT_ID), so caller_agent_id is not exposed here --
    the calling identity is fixed by the BFF; the logged-in user is only an audit trace.

    Choose one of two semantics (matching the kernel; here only shape constraints, no business validation):
      - minutes > 0            -> relative duration (max 43200 minutes = 30 days)
      - until non-empty        -> absolute instant "YYYY-MM-DD HH:MM:SS"
      - both empty             -> clear the exemption (equivalent to off)
    Passing both minutes and until -> the kernel errors, and the BFF relays it verbatim (this rule is not copied locally).
"""

    minutes: int = Field(0, description="相对时长（分钟）；0 = 不用相对时长")
    until: str = Field("", description='绝对到期时刻 "YYYY-MM-DD HH:MM:SS"')
    reason: str = Field("", description="为什么开（写进内核审计日志）")


class SendTextBody(BaseModel):
    session_key: str = Field(..., description="目标会话 key（须属发送者 Agent）")
    text: str = Field("", description="要发送的纯文本内容（直发，不包装成 task）")
    # [2026-10-18 Feng] text messages can reference skills too: append the "reference skills" block to the body
    skill_ids: list[str] = Field(default_factory=list, description="引用技能 id 列表（仅有效技能）")
    # [2026-10-10 Feng: super terminal] sender identity passed explicitly (defaults to the path agent)
    caller_agent_id: str = Field("", description="发送者 Agent 身份（缺省回退路径 agent）")


@router.post("/agents/{agent_id}/actions/run-task")
def run_task(agent_id: str, body: RunTaskBody, user: dict = _auth) -> dict:
    """Click "run" -> wrapped into a task delivered to the session, executed by the agent in an independent session.

    [2026-10-10 Feng: super terminal] distinguish sender/receiver:
      agent_id (path) = receiver (fixed); body.session_key = receiving session (must belong to the receiver);
      body.caller_agent_id = sender identity; body.send_session_key = sender session.
    The logged-in user (user.name) = the audit subject (actor_user; trace only, never an authorisation binding).
"""
    actor = str(user.get("name") or user.get("agent_id") or "")
    try:
        return actions.run_task(
            agent_id=agent_id,
            session_key=body.session_key,
            instruction=body.instruction,
            task_name=body.task_name,
            task_id=body.task_id,
            caller_agent_id=body.caller_agent_id,
            send_session_key=body.send_session_key,
            actor_user=actor,
            needs_reply=body.needs_reply,
            force_no_reply=body.force_no_reply,
            timeout_seconds=body.timeout_seconds,
            exec_name=body.exec_name,
            skill_ids=body.skill_ids,
            reply=body.reply,
            client_request_id=body.client_request_id,
            needs_confirm=body.needs_confirm,
        )
    except ActionError as e:
        raise HTTPException(400, {"error": {"code": e.code, "message": e.message, "details": e.details}}) from e


@router.post("/agents/{agent_id}/actions/schedule-task")
def schedule_task(agent_id: str, body: ScheduleTaskBody, user: dict = _auth) -> dict:
    """[2026-11-27 Feng] "定时执行": create the execution now, let the watchdog run it at scheduled_at.

    Nothing is delivered at submit time (run_status stays 'pending', the agent queue is untouched);
    the watchdog activates it when the time is due. The response therefore echoes `status: pending`
    and `scheduled_at` -- the console shows "scheduled for <time>" instead of "sent".
    """
    actor = str(user.get("name") or user.get("agent_id") or "")
    try:
        return actions.schedule_task(
            agent_id=agent_id,
            session_key=body.session_key,
            instruction=body.instruction,
            scheduled_at=body.scheduled_at,
            task_name=body.task_name,
            task_id=body.task_id,
            caller_agent_id=body.caller_agent_id,
            send_session_key=body.send_session_key,
            actor_user=actor,
            reply=body.reply,
            timeout_seconds=body.timeout_seconds,
            exec_name=body.exec_name,
            skill_ids=body.skill_ids,
            needs_confirm=body.needs_confirm,
        )
    except ActionError as e:
        raise HTTPException(400, {"error": {"code": e.code, "message": e.message, "details": e.details}}) from e


class NudgeBody(BaseModel):
    """Manual intervention input (nudge / nudge acknowledgement).

    [2026-10-31 Feng] the console submits to the kernel **as agx_core**
    (realdata.EDITOR_AGENT_ID), so caller_agent_id is not exposed here --
    the acting identity is fixed by the BFF; the logged-in user is only an audit trace.

    kind, choose one:
      - auto       -> the kernel decides from the current state (default)
      - urge       -> nudge; goes to the **executor**
      - ack_remind -> nudge for acknowledgement; goes to the **originator**
"""

    kind: str = Field("auto", description="auto / urge / ack_remind")
    note: str = Field("", description="备注（写进事件 reason，便于事后追溯为什么催）")


class ConfirmBody(BaseModel):
    """Human approve / reject input.

    [2026-11-06 Feng] A task with needs_confirm=1 lands in awaiting_confirm when done, and only a human
    who has seen the result and clicked "approve" completes it (downstream chains do not advance and the
    watchdog does not time it). Without this entry point the UI can only "see" and not "handle" -- a dead end.

    action, choose one, no middle ground:
      - approve -> succeeded (chains advance, plan completion is re-evaluated)
      - reject  -> failed (needs_reply=1; a rejection is never silent to the originator)

    The acting identity is fixed by the BFF as agx_core (workshop_manager), same as nudge;
    the logged-in user is only an audit trace. The kernel forbids an executor approving its own work, but
    global roles are exempt -- otherwise when the task being executed happens to belong to agx_core, the
    human clicks and nothing happens.
"""

    action: str = Field("approve", description="approve（放行）/ reject（驳回）")


@router.post("/executions/{run_uuid}/confirm")
def confirm_execution(run_uuid: str, body: ConfirmBody, user: dict = _auth) -> dict:
    """Human approve / reject -- passes through to the kernel's `agx_confirm_task`.

    ★ Error codes are returned separately because they call for completely different actions:
        404 NOT_FOUND        -> no such execution (possibly already cleaned up)
        409 STATE_MISMATCH   -> no longer awaiting confirmation (someone approved already / it was rejected / not finished yet)
        403 PERMISSION_DENIED-> the current identity may not approve
        400 BAD_ACTION       -> action is wrong (frontend/backend mismatch)
        503 KERNEL_NOT_READY -> the kernel method is not deployed yet (the BFF/UI is ready)
"""
    log.info("confirm run=%s action=%s actor=%s", run_uuid, body.action, user.get("name"))
    try:
        return realdata.confirm_execution(run_uuid=run_uuid, action=body.action)
    except realdata.ToolRejected as e:
        status = {
            "NOT_FOUND": 404,
            "STATE_MISMATCH": 409,
            "PERMISSION_DENIED": 403,
            "BAD_ACTION": 400,
            "KERNEL_NOT_READY": 503,
        }.get(e.code, 400)
        raise HTTPException(
            status, {"error": {"code": e.code, "message": e.message, "details": e.details}}
        ) from e
    except Exception as e:  # noqa: BLE001
        log.exception("confirm failed")
        raise HTTPException(
            503, {"error": {"code": "KERNEL_UNAVAILABLE", "message": str(e)}}
        ) from e


@router.post("/executions/{run_uuid}/nudge")
def nudge_execution(run_uuid: str, body: NudgeBody, user: dict = _auth) -> dict:
    """Manual intervention -- passes through to the kernel's `agx_nudge_execution`.

    Why this entry point: the watchdog's wake / alert / resend **all have attempt caps**, so after they are
    spent the task is still running, the UI says "still running" while nobody pushes it again, and a human
    can only guess from elapsed time.

    ★ Error codes are returned separately, not collapsed into "operation failed":
        429 RATE_LIMITED      -> repeated nudge within 60 seconds; wait
        409 STATE_MISMATCH    -> the task has finished / state mismatch; no need to nudge
        404 NOT_FOUND         -> no such run
        503 KERNEL_NOT_READY  -> the kernel method is not deployed yet (the BFF/UI is ready)
      because they call for completely different actions.

    ★ Creates no execution (that would just dispatch another task that also times out) and
      does not change run_status / needs_reply (protected fields; only agx_finish_task may end a task).
"""
    log.info("nudge run=%s kind=%s actor=%s", run_uuid, body.kind, user.get("name"))
    try:
        return realdata.nudge_execution(run_uuid=run_uuid, kind=body.kind, note=body.note)
    except realdata.ToolRejected as e:
        status = {
            "RATE_LIMITED": 429,
            "STATE_MISMATCH": 409,
            "NOT_FOUND": 404,
            "KERNEL_NOT_READY": 503,
            "PERMISSION_DENIED": 403,
            "BAD_KIND": 400,
        }.get(e.code, 400)
        raise HTTPException(
            status, {"error": {"code": e.code, "message": e.message, "details": e.details}}
        ) from e
    except Exception as e:  # noqa: BLE001
        log.warning("nudge failed: %s", e)
        raise HTTPException(
            502, {"error": {"code": "MCP_UNAVAILABLE", "message": f"内核通道不可用：{e}"}}
        ) from e


@router.post("/agents/{agent_id}/actions/dispatch-exempt")
def set_dispatch_exempt(agent_id: str, body: DispatchExemptBody, user: dict = _auth) -> dict:
    """Temporary dispatch exemption -- passes through to the kernel's `agx_set_dispatch_exempt` (no direct DB write).

    Why this entry point: worker's role level is below L5, so the three dispatch tools including
    `agx_dispatch_task` can never be called. This opens the door temporarily, and it **closes automatically
    on expiry** (the field is a timestamp, not a switch).

    ★ Only the three "dispatch" tools are opened; creating projects / changing agents / hosts / system
      administration are not -- hence the UI copy says "dispatch allowed", never "permission elevated".

    Kernel rejection (self-escalation / insufficient permission / conflicting arguments) -> 403 or 400,
    with message being the kernel's own words, shown verbatim by the frontend: "permission denied" and
    "bad arguments" are different things and must not both collapse into "operation failed".
"""
    log.info(
        "dispatch_exempt agent=%s actor=%s minutes=%s until=%s",
        agent_id, user.get("name"), body.minutes, body.until or "-",
    )
    try:
        return realdata.set_dispatch_exempt(
            agent_id=agent_id,
            minutes=body.minutes,
            until=body.until,
            reason=body.reason,
            # [2026-11-01] the operator is written into the notice body ("operator") --
            # the granted agent needs to know **who** opened it, so they can find that person if something goes wrong.
            actor=str(user.get("name") or ""),
        )
    except realdata.ToolRejected as e:
        status = 404 if e.code == "NOT_FOUND" else 403 if e.code == "PERMISSION_DENIED" else 400
        raise HTTPException(
            status,
            {"error": {"code": e.code, "message": e.message, "details": e.details}},
        ) from e
    except Exception as e:  # noqa: BLE001
        log.warning("dispatch_exempt failed: %s", e)
        raise HTTPException(
            502, {"error": {"code": "MCP_UNAVAILABLE", "message": f"内核通道不可用：{e}"}}
        ) from e


@router.post("/agents/{agent_id}/actions/send-text")
def send_text(agent_id: str, body: SendTextBody, user: dict = _auth) -> dict:
    """Send plain text directly to a session (agx_send_text): no task wrapping, no execution created.

    Semantics = speaking (as opposed to run-task's "dispatching work").
    [2026-10-10] sender identity is passed explicitly by the frontend (body.caller_agent_id).
"""
    actor = str(user.get("name") or user.get("agent_id") or "")
    try:
        return actions.send_text(
            agent_id=agent_id,
            session_key=body.session_key,
            text=body.text,
            caller_agent_id=body.caller_agent_id,
            actor_user=actor,
            skill_ids=body.skill_ids,
        )
    except ActionError as e:
        raise HTTPException(400, {"error": {"code": e.code, "message": e.message, "details": e.details}}) from e
