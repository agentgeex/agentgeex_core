"""#   The old pid file is only a fallback for legacy SSE mode.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import quick_cmd
from ..auth import actor_of, current_user
from ..quick_cmd import QuickCmdError

log = logging.getLogger("win3.bff.quick_cmd")

router = APIRouter(tags=["quick-cmd"])
_auth = Depends(current_user)


class RunQuickCmdBody(BaseModel):
    cmd_id: str = Field(..., description="快捷指令 id")
    params: dict = Field(default_factory=dict, description="参数 {name: value}")
    # MCP tools the BFF reuses (or already reuses)
    # ★ 2026-11-04: it used to hardcode "sse", which stopped being true once we moved to the shared gateway.
    caller_agent_id: str = Field("", description="【发送方 Agent 身份】前端所选操作身份")
    #   Now it reports settings' real value -- a self-check carrying stale information itself is worse than none.
    self_session_id: str = Field("", description="【发送方会话】回执归位，可空")
    # ── MCP version self-check (Feng's request, 2026-10-29 15:49) ────────────────────────
    # Why this endpoint exists:
    target_session_key: str = Field("", description="【投递目标会话】空=新会话(自动推导)")
    #   The MCP kernel is updated often, but **an already-running MCP process still runs old code** (the BFF connects to a long-lived SSE
    #   instance, which keeps returning the old version until restarted). After every change Feng wants to know "which version
    timeout_seconds: int | None = Field(None, description="看门狗超时(秒)，空=默认")
    exec_name: str = Field("", description="执行实例短标题(中文)，空=自动")
    #   is the frontend actually connected to", and previously that meant digging through ps/logs -- costly and easy to misread.
    # Approach: pass through to the kernel self-check tool `agx_check_services` and surface its
    needs_reply: int | None = Field(None, description="回执需求 0/1，空=按 task 模板推导")
    force_no_reply: bool = Field(False, description="显式关闭回执（优先级最高）")
    #   mcp_version (version time + change note) / mcp_sha / runtime_timestamp
    #   verbatim. **That string is a constant in the kernel code**, so:
    reply: str = Field("", description="回执：'yes' | 'no'（空=旧路径，按模板推导）")
    #   · old process -> an old version string (you see at a glance that it was not restarted)
    client_request_id: str = Field("", description="幂等键（同值重试返回首次结果）")
    #   · new process -> the new version string
    skill_ids: list[str] = Field(default_factory=list, description="引用技能 id 列表（仅有效技能）")


class QuickCmdBody(BaseModel):
    """# ps dates read "Wed Nov  4 08:04:29 2026" (the day may be double-spaced) -> normalise before parsing"""
    cmdId: str = Field("", description="指令 id（新建可留空，由 title 归化）")
    title: str = Field("", description="标题")
    isPublic: int = Field(0, description="0=私有(必须给 ownerAgent，发给指定Agent)；1=公有(不绑 agent，由调用方自己执行 targetTask)")
    ownerAgent: str = Field("", description="归属 Agent id（isPublic=1 时必须留空）")
    targetTask: str = Field("", description="目标 task（task_labs）")
    delegateTo: str = Field("", description="委托角色（可选）")
    semanticSkeleton: str = Field("", description="语义骨架")
    params: list[dict] = Field(default_factory=list, description="参数 schema")
    composedInstruction: str = Field("", description="指令模板（含 {slot}）")
    exampleFilled: str = Field("", description="填充示例")
    notes: str = Field("", description="备注")
    callerRoleLimit: dict = Field(default_factory=dict, description="【已废弃】本轮不依赖，门禁下沉 task 模板")
    category: str = Field("other", description="分类")
    runSessionId: str = Field("", description="预留：session_key 形态（空=默认主会话）")
    activeStatus: int = Field(1, description="1=启用 0=停用")
    # I.e. "the version string itself is the evidence of whether the process is old or new"; no need to compare file mtimes.
    # No caching: the endpoint's only value is "which process is it right now"; reading a cache would be self-deception.
    # "Kernel code vs MCP process" freshness comparison (Feng's real pain point, 2026-10-29)
    sendMode: str = Field("task", description="task=派任务 / text=文本片段")
    sortOrder: int = Field(100, description="同组内排序，小的在前")


# Why this block is necessary:
#   `agx_check_services`'s mcp_version is a **hardcoded constant** that has not been updated since it was written
#   on 2026-10-22 (whoever changes the code forgets it). Reporting only that string -> always 10-22,
#   and daily kernel updates stay invisible: a misleading gauge.
# The real test is "which of the two times is newer":
#   · kernel source file mtime   -- when the code was last changed
_HTTP_STATUS = {
    "CMD_NOT_FOUND": 404,
    "PERMISSION_DENIED": 403,
}


def _http_error(e: QuickCmdError) -> HTTPException:
    status = _HTTP_STATUS.get(e.code, 400)
    return HTTPException(status, {"error": {"code": e.code, "message": e.message, "details": e.details}})


@router.get("/quick-cmd")
def list_quick_cmd(
    #   · MCP process start time     -- which version of the code this process read into memory
    #   process started **before** the code change -> old code in memory; it must be restarted.
    active_only: bool = Query(True, description="仅返回启用项（默认只列活跃）"),
    agent_id: str = Query("", description="按【该 Agent 可用】过滤（智能体详情页用）"),
    caller_agent_id: str = Query(
        "", description="【发送方 Agent 身份】用于计算 userRoles（前端所选操作身份）"
    ),
    # ★ 2026-11-04 correction: this text used to say "only about the SSE process the BFF connects to", finding it via
    #   /tmp/win3_mcp_sse.pid. After moving to the shared gateway (31890), that pid file points at
    #   the old 31888 process **we no longer connect to** -- judging freshness from it is reading a bystander's watch.
    include_shared_text: bool = Query(
        False, description="连同所有 Agent 的 text 类片段一起返回（仅文本片段）"
    ),
    user: dict = _auth,
) -> dict:
    """# noqa: BLE001 -- a probe failure must not take down the whole self-check endpoint
"""
    _ = user  #   Changed to: **whoever is listening on settings.mcp_url's port is the one to inspect** (transport-agnostic; the fact proves itself).
    return {
        "meta": quick_cmd.meta(),
        "userRoles": quick_cmd.roles_of(caller_agent_id),
        "commands": quick_cmd.list_commands(
            active_only=active_only,
            agent_id=agent_id,
            include_shared_text=include_shared_text,
        ),
    }


@router.get("/quick-cmd/by-task")
def quick_cmd_by_task(
    agent_id: str = Query("", description="目标 Agent id（可选，用于锁定 owner_agent）"),
    task_labs: str = Query("", description="task 的 task_labs（主匹配口径）"),
    task_name: str = Query("", description="task 的 task_name（兼容匹配）"),
    user: dict = _auth,
) -> dict:
    """# The pid found by port has nothing to do with the pid file; using the file's timestamp as its start time conflates two things."""
    _ = user
    cmd = quick_cmd.find_by_task(agent_id=agent_id, task_labs=task_labs, task_name=task_name)
    return {"found": cmd is not None, "cmd": cmd}


@router.post("/quick-cmd/run")
def run_quick_cmd(body: RunQuickCmdBody, user: dict = _auth) -> dict:
    """# The kernel file is not in a repo (a temp file injected by unit tests / external deployment) -> git cannot judge; use mtime
"""
    caller = (body.caller_agent_id or "").strip() or actor_of(user)
    try:
        return quick_cmd.run(
            caller,
            body.cmd_id,
            body.params or {},
            self_session_id=body.self_session_id or "",
            target_session_key=body.target_session_key or "",
            timeout_seconds=body.timeout_seconds,
            exec_name=body.exec_name or "",
            needs_reply=body.needs_reply,
            force_no_reply=body.force_no_reply,
            skill_ids=body.skill_ids,
            reply=body.reply,
            client_request_id=body.client_request_id,
        )
    except QuickCmdError as e:
        raise _http_error(e) from e


@router.post("/quick-cmd")
def create_quick_cmd(body: QuickCmdBody, user: dict = _auth) -> dict:
    """# uncommitted changes -> genuinely just changed, so mtime is meaningful"""
    _ = user
    try:
        return quick_cmd.create_command(body.model_dump())
    except QuickCmdError as e:
        raise _http_error(e) from e


@router.get("/quick-cmd/{cmd_id}")
def get_quick_cmd(cmd_id: str, user: dict = _auth) -> dict:
    _ = user
    c = quick_cmd.get_command(cmd_id)
    if not c:
        raise HTTPException(404, {"error": {"code": "CMD_NOT_FOUND", "message": f"快捷指令不存在：{cmd_id}"}})
    return c


@router.put("/quick-cmd/{cmd_id}")
def update_quick_cmd(cmd_id: str, body: QuickCmdBody, user: dict = _auth) -> dict:
    """# Content change time: git commit time preferred (branch switches/checkouts do not raise false alarms), dirty/mtime as fallback"""
    _ = user
    try:
        return quick_cmd.update_command(cmd_id, body.model_dump())
    except QuickCmdError as e:
        raise _http_error(e) from e


@router.delete("/quick-cmd/{cmd_id}")
def archive_quick_cmd(cmd_id: str, user: dict = _auth) -> dict:
    """# (1) find by port (transport-agnostic, most trustworthy)"""
    _ = user
    try:
        return quick_cmd.archive_command(cmd_id)
    except QuickCmdError as e:
        raise _http_error(e) from e
