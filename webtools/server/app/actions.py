"""actions.py -- Win3 BFF action layer (writes / actions).

[2026-10-07 Feng's architecture correction: execution must be "wrapped into a task"]

Feng's words:
    "What executes is a task, and the content must go inside that task, because the task itself has a
     description (x_desc). Only after it is wrapped does it go to that session. You cannot send it
     directly; the direct-send entry point is not here, it is in the separate session."

So this layer **no longer sends plain text with agx_send_text** and uses `agx_dispatch_task` (MCP L3) instead:
    - AgentGeeX automatically merges the task template's x_desc (task description) with the user's
      instruction into exec_instruction (_build_exec_instruction);
    - it creates an agx_task_execution record and lands it in the given session (session_key),
      where the daemon executes it in an independent session -- that is what "wrapping into a task" means.

`agx_send_text` is kept for the "separate session" entry point (sending a message directly from the session
page); do not mix the two.

[2026-10-10 Feng: super-terminal definition -- the caller is no longer locked to the path agent]
    Win3 is a "super terminal for people", and a person may act as any agent (that is the point).
    So this layer no longer fixes caller_agent_id to the path agent; instead the **frontend passes
    caller_agent_id explicitly** (= the "sender agent identity" chosen in the dialog, defaulting to the
    path agent).
    ★ The platform red line (no agent-to-agent chat) protects Agent<->Agent, and does not apply to
      "a human operating through Win3", so opening up the caller here does not violate it.

    [2026-10-10 audit, confirmed by Feng] The "current logged-in user" is kept as the acting subject:
      - actor_user: the login name (a human), the future anchor for user-table permissions;
      - caller_agent_id: the chosen agent identity (the proxied execution subject).
      Both are written into the execution audit (send_agent_id records the chosen agent; actor goes into
      the log/exec).

All writes go through MCP (kernel.call_tool), reusing its permission and cache chain.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import kernel
from .kernel import McpToolError, McpUnavailable
from .skill_ref import SkillRefError, resolve_skill_block
from .text_compose import compose_with_suffix

log = logging.getLogger("win3.bff.actions")


class ActionError(Exception):
    """Action-layer error (the route turns it into the unified error body)."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _parse_mcp_json(raw: Any) -> dict[str, Any]:
    """MCP tools mostly return JSON strings; parse them into a dict uniformly (wrap as-is on failure)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"result": parsed}
        except Exception:  # noqa: BLE001
            return {"raw": raw}
    return {"raw": raw}


# ══════════════════════════════════════════════════════════════════
# Unified send facade (agx_universal_send) -- the BFF's **only** write entry point
# ══════════════════════════════════════════════════════════════════
# [2026-10-19 P5, Feng's ruling] The frontend only expresses one thing: "want a receipt / do not want one":
#   reply = "yes" | "no" (required, no middle ground).
#   The conversion of needs_reply / force_no_reply (including the agx_dispatch_task patch for
#   "int defaults to 0 so an explicit 0 is indistinguishable from absent") is all absorbed by the kernel facade,
#   so force_no_reply never appears in the BFF or the frontend again.

REPLY_VALUES = ("yes", "no")


def normalize_reply(reply: str) -> str:
    """reply normalisation: only yes/no accepted (empty string / anything else -> REPLY_REQUIRED; never guess a default)."""
    r = (reply or "").strip().lower()
    if r not in REPLY_VALUES:
        raise ActionError(
            "REPLY_REQUIRED",
            "reply 必须是 'yes' 或 'no'（二选一，不支持省略 / 空串 / inherit）",
            {"reply": reply},
        )
    return r


def call_universal_send(**kwargs: Any) -> dict[str, Any]:
    """Call the kernel's unified facade agx_universal_send (shared by all three modes).

    Success criteria differ by mode: text -> has msg_id; task/quickcmd -> has run_uuid.
    Failure is always raised explicitly as ActionError -- never "query the DB and reuse an old run_uuid" as a
    fallback (that disguises an undelivered message as success; hit on 2026-10-10).

    An occasional empty MCP SSE result -> retry once (same strategy as run_task).
"""
    mode = str(kwargs.get("mode") or "")
    last_err = ""
    for attempt in (1, 2):
        try:
            raw = kernel.call_tool("agx_universal_send", **kwargs)
        except McpUnavailable as e:
            last_err = f"内核不可达：{e}"
            log.warning("universal_send 第 %d 次 McpUnavailable: %s", attempt, e)
            continue
        except McpToolError as e:
            raise ActionError("MCP_ERROR", str(e)) from e

        res = _parse_mcp_json(raw)
        if res.get("error"):
            raise ActionError(
                str(res.get("error")),
                str(res.get("detail") or res.get("error")),
                res,
            )
        ok = bool(res.get("msg_id")) if mode == "text" else bool(res.get("run_uuid"))
        if res.get("ok") and ok:
            return res
        if res.get("ok") and res.get("idempotent"):
            # idempotent hit: run_uuid is present, but msg_id may be empty (nothing was re-delivered)
            return res
        last_err = f"门面返回不可用（第 {attempt} 次）: {json.dumps(res, ensure_ascii=False)[:200]}"
        log.warning("universal_send %s", last_err)

    raise ActionError("MCP_EMPTY_RESULT", f"发送未获得有效回执，操作可能未生效：{last_err}")


def run_task(
    agent_id: str,
    session_key: str,
    instruction: str,
    task_name: str = "",
    task_id: str = "",
    caller_agent_id: str = "",
    send_session_key: str = "",
    actor_user: str = "",
    needs_reply: int | None = None,
    force_no_reply: bool = False,
    timeout_seconds: int | None = None,
    exec_name: str = "",
    skill_ids: list[str] | None = None,
    reply: str = "",
    client_request_id: str = "",
    needs_confirm: int | None = None,
) -> dict[str, Any]:
    """Wrap "execute some task" **into a task** and deliver it to the given session.

    Semantics = agx_dispatch_task.

    [2026-10-10 Feng: super terminal -- distinguish "sender" from "receiver"]
        Receiver (fixed): agent_id = the agent you clicked into (who should do the work).
        Receiving session: session_key = which of the **receiver agent's** sessions the task runs in.
        Sender identity  : caller_agent_id = who sends (a super user may represent any agent);
                           defaults to the receiver agent.
        Sender session   : send_session_key = which of the sender's sessions it is sent from;
                           defaults to the sender's main session (agent:{caller}:main).
        All three are independent: the receiving session pins the receiver, while the sender and the
        sender session are freely choosable.

    Args:
        agent_id: receiver agent id (= task template owner + execution subject).
        session_key: receiving session (must belong to the **receiver agent**).
        instruction: the task instruction / description the user typed (merged with the task template's x_desc).
        task_name: task name (readable, for receipt display).
        task_id: associated task id (**required** -- wrapping into a task needs this template).
        caller_agent_id: [sender identity] passed explicitly; defaults to agent_id.
        send_session_key: [sender session] passed explicitly; defaults to agent:{caller}:main.
        actor_user: [audit] the current login name (a human); audit only (**never used as an authorisation binding**).
        needs_reply: reply requirement 0/1 (None = omit, follow the backend default).
        timeout_seconds: watchdog timeout in seconds (None = omit).
        exec_name: short title for the execution instance (empty = omit).
        skill_ids: [skills referenced by this task] a list of agx_skill.skill_id (valid skills only).
            After selection in the unified input bar they are passed in -> the server renders them into a
            "reference skills" note appended to instruction; when non-empty, any invalid/retired id raises
            SKILL_NOT_FOUND (never silently dropped).
        reply: [2026-10-19 P5] "yes" | "no" (the frontend forces one of the two; no middle ground).
            Non-empty -> go through the kernel's unified facade agx_universal_send (all
            needs_reply/force_no_reply conversion happens inside the facade), so force_no_reply never
            appears in the BFF or the frontend again.
            Empty -> keep the old path (agx_dispatch_task + template derivation) for older dialogs not yet migrated.
        client_request_id: [2026-10-19 P2] idempotency key. Retries of the same intent reuse the same value ->
            the kernel returns the first result (idempotent=true) and does not dispatch twice.
        needs_confirm: [2026-11-06 Feng] human approval: 1 = do not write succeeded when done, land in
            awaiting_confirm, and only a human calling agx_confirm_task turns it successful.
            None/0 = not needed (default, exactly as before the change).
            ★ The value is passed down as-is; the BFF does not judge business meaning -- the semantics are
            defined in exactly one place, the kernel.

    Returns:
        { dispatched, run_uuid, msg_id?, queue?, queue_length?, session_key,
          send_session_key, target_agent, caller_agent_id, actor_user, task_ref }

    Raises:
        ActionError: missing argument / session does not belong to the matching subject / MCP rejection.
"""
    agent_id = (agent_id or "").strip()          # receiver
    session_key = (session_key or "").strip()     # receiving session
    instruction = (instruction or "").strip()
    task_id = (task_id or "").strip()
    # sender identity: passed explicitly by the frontend; defaults to the receiver agent (backwards compatible)
    caller = (caller_agent_id or "").strip() or agent_id
    # sender session: defaults to the sender's main session
    send_session = (send_session_key or "").strip() or f"agent:{caller}:main"
    if not agent_id:
        raise ActionError("BAD_REQUEST", "缺少 agent_id")
    # [2026-10-11] session_key may be empty = new session: it means this task needs a new session created,
    # so no receiving session id is passed -> MCP agx_dispatch_task derives one and creates the session
    # (dedicated_session task -> agent:{aid}:{task_labs}; otherwise agent:{aid}:{run_uuid}).
    # In that case the "receiving session ownership" guard is skipped (there is no concrete session to check).
    new_session = not session_key
    if not instruction:
        raise ActionError("BAD_REQUEST", "缺少 instruction（任务指令不能为空）")
    if not task_id:
        raise ActionError("BAD_REQUEST", "缺少 task_id（执行必须包装成 task，需指定任务模板）")

    # ── skill reference: rendered as a "reference skills" note block (valid skills only; failure is an error) ──
    #   [2026-10-19 P5 fix] No longer merged into instruction here:
    #     the facade agx_universal_send already assembles it in its text/task branches (text + "\n\n" + suffix).
    #     Merging once here first would make it appear **twice**.
    #     -> on the facade path only instruction_suffix is passed; only the old path (empty reply) merges here.
    suffix = ""
    if skill_ids:
        try:
            suffix = resolve_skill_block(skill_ids)
        except SkillRefError as e:
            raise ActionError(e.code, e.message) from e

    # ── guard 1: the receiving session must belong to the **receiver agent** (task landing point) ──
    # A new session (empty session_key) skips this guard -- MCP creates the session and there is none to check.
    recv_prefix = f"agent:{agent_id}:"
    if not new_session and not (session_key == f"agent:{agent_id}:main" or session_key.startswith(recv_prefix)):
        raise ActionError(
            "SESSION_NOT_OWNED",
            f"接收会话与接收方智能体不匹配：{session_key} 不属于 {agent_id}",
            {"session_key": session_key, "agent_id": agent_id, "role": "receiver"},
        )

    # ── guard 2: the sender session must belong to the **sender agent** (originating context) ──
    send_prefix = f"agent:{caller}:"
    if not (send_session == f"agent:{caller}:main" or send_session.startswith(send_prefix)):
        raise ActionError(
            "SESSION_NOT_OWNED",
            f"发送方会话与发送方智能体不匹配：{send_session} 不属于 {caller}",
            {"send_session_key": send_session, "caller_agent_id": caller, "role": "sender"},
        )

    # ── guard 3: the task must be a task template owned by the **receiver agent** ──
    try:
        rows = kernel.query(
            "SELECT agent_id FROM agx_task_base WHERE task_id = %s LIMIT 1",
            (task_id,),
        )
    except Exception as e:  # noqa: BLE001
        raise ActionError("KERNEL_UNAVAILABLE", f"任务归属校验失败：{e}") from e
    if not rows:
        raise ActionError("TASK_NOT_FOUND", f"任务不存在：{task_id}", {"task_id": task_id})
    owner = str(rows[0].get("agent_id") or "")
    if owner != agent_id:
        raise ActionError(
            "TASK_NOT_OWNED",
            f"任务与接收方智能体不匹配：task {task_id} 归属 {owner}，非 {agent_id}",
            {"task_id": task_id, "agent_id": agent_id, "owner": owner},
        )

    # ── [2026-10-19 P5] go through the unified facade (reply is explicit): one agx_universal_send dispatches ──
    #   ★ The three guards above (receiving-session ownership / sender-session ownership / task ownership) are already done,
    #     so the facade only handles "receipt normalisation + dispatch" and does not repeat business guards.
    if reply:
        r = normalize_reply(reply)
        res = call_universal_send(
            caller_agent_id=caller,
            mode="task",
            reply=r,
            from_session=send_session,
            to_agent=agent_id,
            to_session=session_key,
            text=instruction,
            task_id=task_id,
            session_name=task_name or "",
            **({"instruction_suffix": suffix} if suffix else {}),
            **({"timeout_seconds": int(timeout_seconds)} if timeout_seconds is not None else {}),
            **({"exec_name": exec_name.strip()} if exec_name.strip() else {}),
            **(
                {"client_request_id": client_request_id.strip()}
                if client_request_id.strip()
                else {}
            ),
            # [2026-11-06] human approval: passed only when explicitly 1 (0/None omitted -> kernel defaults to 0)
            **({"needs_confirm": 1} if needs_confirm else {}),
        )
        run_uuid = str(res.get("run_uuid") or "")
        log.info(
            "run_task(facade) dispatched: actor_user=%s caller=%s send_session=%s recv=%s "
            "session=%s task=%s run=%s reply=%s idempotent=%s",
            actor_user or "-", caller, send_session, agent_id, session_key,
            task_name or task_id, run_uuid, r, bool(res.get("idempotent")),
        )
        return {
            "dispatched": True,
            "run_uuid": run_uuid,
            "msg_id": res.get("msg_id") or "",
            "queue": res.get("queue") or "",
            "queue_length": None,
            "session_key": res.get("session_key") or session_key,
            "new_session": new_session,
            "send_session_key": send_session,
            "target_agent": agent_id,
            "caller_agent_id": caller,
            "actor_user": actor_user,
            "task_ref": {"task_id": task_id, "task_name": task_name},
            "reply": r,
            "needs_reply": int(res.get("needs_reply", 1 if r == "yes" else 0)),
            "idempotent": bool(res.get("idempotent")),
            "options": {"reply": r, "needs_confirm": 1 if needs_confirm else 0},
            "needs_confirm": 1 if needs_confirm else 0,
        }

    # [assemble optional parameters] pass only explicitly given ones (None is omitted, avoiding overriding backend defaults)
    # ★ old path: the skill block is merged into instruction by the BFF itself (the facade path already handled above)
    if suffix:
        instruction = f"{instruction}\n\n{suffix}"
    extra: dict[str, Any] = {}
    if needs_reply is not None:
        extra["needs_reply"] = int(needs_reply)
    # [2026-10-10] explicitly disable the receipt (overrides task.reply_mode derivation); passed only when True.
    if force_no_reply:
        extra["force_no_reply"] = True
    if timeout_seconds is not None:
        extra["timeout_seconds"] = int(timeout_seconds)
    if exec_name.strip():
        extra["exec_name"] = exec_name.strip()
    # [2026-11-06] human approval (the old path supports it too -- agx_dispatch_task already has this parameter)
    if needs_confirm:
        extra["needs_confirm"] = 1

    # [call MCP + retry] MCP SSE occasionally returns an empty result (no run_uuid) -> retry once;
    # ★ Never "query the DB and reuse an old run_uuid" as a fallback -- that disguises a failure as success (hit on 2026-10-10).
    res: Any = {}
    last_err = ""
    for attempt in (1, 2):
        try:
            raw = kernel.call_tool(
                "agx_dispatch_task",
                caller_agent_id=caller,   # ★ sender identity (explicit from the frontend)
                agent_id=agent_id,        # ★ receiver agent (task owner / execution subject)
                task_id=task_id,
                instruction=instruction,   # ★ required (MCP agx_dispatch_task requires it)
                session_key=session_key,  # ★ receiving session (empty string for a new session; MCP derives and creates it)
                self_session_id=send_session,  # ★ sender session (so receipts land correctly)
                session_name=task_name or "",
                **extra,
            )
        except McpUnavailable as e:
            last_err = f"内核不可达：{e}"
            log.warning("run_task 第 %d 次 McpUnavailable: %s", attempt, e)
            continue
        except McpToolError as e:
            raise ActionError("MCP_ERROR", str(e)) from e

        res = _parse_mcp_json(raw)
        if isinstance(res, dict) and res.get("error"):
            raise ActionError("MCP_REJECTED", str(res.get("error")), res)
        if isinstance(res, dict) and res.get("run_uuid"):
            break  # success
        last_err = f"MCP 返回无 run_uuid（第 {attempt} 次）: {json.dumps(res, ensure_ascii=False)[:200]}"
        log.warning("run_task %s", last_err)
    else:
        # both attempts empty -> explicit error (no fake success)
        raise ActionError("MCP_EMPTY_RESULT", f"派发未获得 run_uuid，任务可能未投递：{last_err}")

    run_uuid = str(res.get("run_uuid") or "")
    res = res if isinstance(res, dict) else {}
    log.info(
        "run_task dispatched: actor_user=%s caller=%s send_session=%s recv=%s session=%s task=%s run=%s extra=%s",
        actor_user or "-", caller, send_session, agent_id, session_key, task_name or task_id, run_uuid, extra,
    )
    return {
        "dispatched": True,
        "run_uuid": run_uuid,
        "msg_id": res.get("msg_id") or "",
        "queue": res.get("queue") or "",
        "queue_length": res.get("queue_length"),
        "session_key": res.get("session_key") or session_key,
        "new_session": new_session,
        "send_session_key": send_session,
        "target_agent": agent_id,
        "caller_agent_id": caller,
        "actor_user": actor_user,
        "task_ref": {"task_id": task_id, "task_name": task_name},
        "options": extra,
    }


def send_text(
    agent_id: str,
    session_key: str,
    text: str,
    caller_agent_id: str = "",
    actor_user: str = "",
    skill_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Send a plain-text message directly to the given session (agx_send_text).

    Semantics = agx_send_text (L3):
        **[no task wrapping, no execution created, no task template assembly]**: the text is delivered
        straight to the target session via the underlying sessions.send. Semantics = "speaking", not "dispatching work".

    Difference from run_task:
        run_task   -> dispatch work (agx_dispatch_task; wrapped into a task with a description)
        send_text  -> speak (agx_send_text; plain text, fire-and-forget)

    Safety (honouring the platform red line "no agent-to-agent chat"):
        agx_send_text has its own ownership guard: session_key must be the caller's own session
        (agent:{caller}:main or starting with agent:{caller}:).
        [2026-10-10] caller_agent_id became an explicit frontend argument (defaulting to agent_id)
        so that "a human sending through Win3 as any agent" is supported.

    Args:
        agent_id: target agent id (the fallback caller, and the identity subject when absent).
        session_key: target session (must belong to the caller).
        text: the plain text to send (non-empty).
        caller_agent_id: [sender identity, 2026-10-10] passed explicitly; defaults to agent_id.
        actor_user: [audit 2026-10-10] the current login name (a human); audit only.
        skill_ids: [2026-10-18] skills referenced along with the message (valid skills only). When non-empty,
            the "reference skills" block is appended to the end of the message body (a raw text send has no
            execution, so it can only be said alongside the body). Any invalid/retired id -> SKILL_NOT_FOUND
            (never silently dropped).

    Returns:
        { sent, session_key, target_agent, text_len }

    Raises:
        ActionError: missing argument / the session does not belong to that agent / MCP rejection.
"""
    agent_id = (agent_id or "").strip()
    session_key = (session_key or "").strip()
    text = (text or "").strip()
    if not agent_id:
        raise ActionError("BAD_REQUEST", "缺少 agent_id")
    if not session_key:
        raise ActionError("BAD_REQUEST", "缺少 session_key（需选择会话）")
    if not text:
        raise ActionError("BAD_REQUEST", "缺少 text（消息内容不能为空）")

    # ── skill reference: rendered as a "reference skills" note block (valid skills only; failure is an error) ──
    #   [2026-10-19 P5 fix] Do not merge into text here: the facade's text branch assembles it itself
    #   (text + "\n\n" + suffix). Assembling again in the BFF = duplicated twice.
    suffix = ""
    if skill_ids:
        try:
            suffix = resolve_skill_block(skill_ids)
        except SkillRefError as e:
            raise ActionError(e.code, e.message) from e

    # The session must belong to the **sender agent** (agx_send_text has its own guard, but a friendly error is given earlier here)
    caller = (caller_agent_id or "").strip() or agent_id
    own_prefix = f"agent:{caller}:"
    if not (session_key == f"agent:{caller}:main" or session_key.startswith(own_prefix)):
        raise ActionError(
            "SESSION_NOT_OWNED",
            f"会话与发送者智能体不匹配：{session_key} 不属于 {caller}",
            {"session_key": session_key, "caller_agent_id": caller},
        )

    # [2026-10-19 P5] switched to the unified facade (mode=text):
    #   - the skill block goes in instruction_suffix and the facade splices it into the body (single splice point)
    #   - reply is meaningless for text; pass an empty string (the facade allows it)
    try:
        res = call_universal_send(
            caller_agent_id=caller,
            mode="text",
            reply="",
            to_agent=caller,
            to_session=session_key,
            text=text,
            **({"instruction_suffix": suffix} if suffix else {}),
        )
    except ActionError as e:
        # Facade error codes are passed straight through (TEXT_EMPTY / TEXT_MODE_SELF_ONLY / TEXT_SEND_FAILED ...)
        raise ActionError(e.code, e.message, e.details) from e

    # [2026-11-17 15:41 Feng] "I select a skill, then click send... the skill has a description,
    #   should that description also appear in there? Right now you send it straight from the backend,
    #   but on the page you cannot see the content with the skill."
    # -> the frontend's local echo of "just sent" uses **the raw text from the input box**, while the skill
    #   block was spliced in by the facade, so it is invisible at that moment and only appears after the
    #   collector stores it and it is fetched again. So **the text that was actually sent** is returned here (rather than making the frontend assemble it):
    #   the splice rule lives in the kernel (agx_universal_send (6): f"{text}\n\n{suffix}", with suffix stripped),
    #   and a frontend copy would drift from the kernel sooner or later. Return the result, not the rule.
    # The splice rule exists once (app/text_compose.py, kept in sync with kernel (6))
    #
    # [2026-11-19 Feng] "Whatever you send to OpenClaw, put that above"
    #   -> after the kernel added assistant-identity injection (2b5e3e5), the final body = identity hint + body + skill block,
    #     and the BFF's own compose_with_suffix() only reaches "body + skill block", missing one segment.
    #   -> prefer **text_full as returned by the kernel** (that is what really went into the envelope);
    #     when the kernel does not return it, fall back to the local assembly (old-kernel compatibility, same behaviour as before).
    #   ★ Do not copy the injection rule into the BFF: the single source of truth for the hint copy is
    #     agx_core._inject_assistant_hint, and a copy would drift from the kernel sooner or later (same conclusion as the 2026-11-17 skill block).
    _kernel_full = str(res.get("text_full") or "")
    text_full = _kernel_full or compose_with_suffix(text, suffix)
    log.info(
        "send_text(facade): actor_user=%s caller=%s session=%s len=%d suffix=%d kernel_full=%s",
        actor_user or "-", caller, session_key, len(text), len(suffix), bool(_kernel_full),
    )
    return {
        "sent": True,
        "session_key": res.get("session_key") or session_key,
        "target_agent": caller,
        "caller_agent_id": caller,
        "actor_user": actor_user,
        # Actual sent length: the skill block (if any) is spliced on by the facade
        "text_len": len(text_full),
        # [2026-11-17] the segment that really landed in the DB (including the skill block) -- for the frontend's local echo
        "text_full": text_full,
    }
