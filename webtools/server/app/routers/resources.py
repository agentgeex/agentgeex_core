"""Resource routes -- correspond to contract sections 3.2-3.6.

This round (2026-10-01, the first "wire up read-only" batch):
- 7 business entity types + dashboard statistics, all switched to **real reads** from the AgentGeeX kernel (MySQL).
- Read-only: SELECT only; no DB writes, no touching daemon/watchdog (see app/realdata.py).
- Contract fields aligned with the frontend mock entity fields (goal: zero SPA page changes).

Note: `/agents` used to be inline; it now converges into realdata (response shape unchanged).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from .. import kernel, realdata, reveal, session_runtime
from ..auth import caller_agent_id_of, current_user

log = logging.getLogger("win3.bff.resources")

router = APIRouter(tags=["resources"])
_auth = Depends(current_user)


def _paginate(items: list[dict], page: int, limit: int) -> list[dict]:
    """Slice pagination.

    [2026-10-20 fix] The original `start = (page - 1) * limit` did not guard page<=0:
    `_page=0` -> start=-limit -> a negative Python slice returns [] (HTTP 200 with 0 rows),
    which looks exactly like "the backend has no data" and is very hard to diagnose. Both ends are clamped now.
"""
    p = max(1, page)
    size = min(max(1, limit), 5000)  # cap to prevent blow-ups, same definition as settings.fetch_limit
    start = (p - 1) * size
    return items[start : start + size]


def _list_envelope(response: Response, items: list[dict], page: int, limit: int) -> list[dict]:
    response.headers["X-Total-Count"] = str(len(items))
    return _paginate(items, page, limit)


# ── agents (contract section 3.2) ──────────────────────────────
@router.get("/agents")
def list_agents(
    response: Response,
    _page: int = Query(1, alias="_page"),
    _limit: int = Query(10, alias="_limit"),
    status: str | None = None,
    q: str | None = None,
    kind: str | None = None,
    template: str | None = Query(None, description="是否含模板：true/false/1/0/only"),
    role: str | None = None,
    project: str | None = None,
    user: dict = _auth,
) -> list[dict]:
    """Agent list + filters (2026-10-05 Feng's requirement: tags + filters, no sorting).

    Filter dimensions:
      - q        : keyword (matches id / name / project / role / model)
      - kind     : category (template / clone / native)
      - template : true=exclude templates (only those in use); false=include templates (all); only=templates only
      - status   : online / offline
      - role     : worker / project_manager / ...
      - project  : project id
"""
    caller = caller_agent_id_of(user)
    items = realdata.agents(caller)
    if status in {"online", "offline"}:
        items = [a for a in items if a["status"] == status]
    if kind in {"template", "clone", "native"}:
        items = [a for a in items if a["kind"] == kind]
    if template is not None:
        flag = str(template).strip().lower()
        if flag in {"true", "1", "yes"}:
            # Only "in use": exclude templates
            items = [a for a in items if not a["isTemplate"]]
        elif flag in {"only", "template"}:
            items = [a for a in items if a["isTemplate"]]
        elif flag in {"false", "0", "no"}:
            items = [a for a in items if not a["isTemplate"]]
    if role:
        items = [a for a in items if a["role"] == role]
    if project:
        items = [a for a in items if a["project_id"] == project]
    if q:
        kw = q.lower()
        items = [
            a for a in items
            if kw in a["name"].lower()
            or kw in a["id"].lower()
            or kw in (a.get("project_id") or "").lower()
            or kw in (a.get("role") or "").lower()
            or kw in (a.get("model") or "").lower()
        ]
    return _list_envelope(response, items, _page, _limit)


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str, user: dict = _auth) -> dict:
    """Single agent (contract section 3.2 GET /agents/:id)."""
    for a in realdata.agents(caller_agent_id_of(user)):
        if a["id"] == agent_id:
            return a
    from fastapi import HTTPException
    raise HTTPException(404, {"error": {"code": "NOT_FOUND", "message": f"agent {agent_id} 不存在"}})


@router.get("/agents/{agent_id}/profile")
def get_agent_profile(agent_id: str, user: dict = _auth) -> dict:
    """Full agent profile (detail page) -- beyond `/agents/{id}` it adds x_desc / system_prompt /
    the 8 persona files / scale counters / working directories, etc.

    ★ Why a separate endpoint instead of folding the fields into `/agents/{id}`:
      `/agents/{id}` data comes from `agents()`, which is part of the full snapshot (90 agents).
      Long text columns (x_desc / system_prompt / *_md) would add several MB to every snapshot while
      the list page uses none of them. The detail page requests once when opened, so a separate
      endpoint is the sensible choice.

    404 explicitly reports "does not exist" rather than returning an empty object -- otherwise the page
    renders "not found" as a blank profile.
"""
    _ = user
    d = realdata.agent_profile(agent_id)
    if not d:
        from fastapi import HTTPException
        raise HTTPException(
            404, {"error": {"code": "NOT_FOUND", "message": f"agent {agent_id} 不存在"}}
        )
    return d


# ── Agent editing (2026-11-07 Feng) ─────────────────────
# "First check whether the agent can be edited -- if not, add an edit button in the detail and edit via a single MCP method."
#
# The field list = the full set the kernel's agx_update_agent supports (Feng: make everything editable first, narrow later).
# **The BFF does not duplicate field grading rules**: which field anyone may change is decided by the kernel, and
# the rejection reason is passed back verbatim -- the frontend can then show "why you cannot change this" instead of guessing first and guessing wrong.
class UpdateAgentBody(BaseModel):
    agent_name: str | None = Field(None, description="表意名称")
    x_desc: str | None = Field(None, description="完整说明书（Markdown）")
    agent_role: str | None = Field(None, description="角色（契约字段，L4）")
    host_id: str | None = Field(None, description="归属主机（契约字段，L4）")
    system_prompt: str | None = Field(None, description="系统提示词（契约字段，L4）")
    active_status: str | None = Field(None, description="'1' 启用 / '0' 停用")
    # JSON columns: the frontend sends JSON text; parse it into list/dict here before handing it to the kernel
    #   (the kernel's _do_update json.dumps it itself; passing a string in produces "a string of a string").
    mcp_config: Any | None = Field(None, description="MCP 配置（JSON 对象）")
    skills_allowlist: Any | None = Field(None, description="技能白名单（JSON 数组）")
    agent_email: str | None = Field(None, description="邮箱")
    agent_email_config: Any | None = Field(None, description="邮箱配置（JSON）")
    agent_telegram_token: str | None = Field(None, description="Telegram Token（留空=不改）")
    agent_feishu_token: str | None = Field(None, description="飞书 Token（留空=不改）")


@router.patch("/agents/{agent_id}")
def patch_agent(agent_id: str, body: UpdateAgentBody, user: dict = _auth) -> dict:
    """Agent editing -- passes through to the kernel's `agx_update_agent` (no direct DB write).

    The submit identity is fixed as agx_core (same convention as host/project editing); the logged-in user
    is only recorded in the log. **Only fields the frontend actually diffed are submitted**: absent = unchanged.
    That way "columns the form does not show" are never overwritten with empty values.
    Kernel rejection -> 403 (permission/ownership) or 400 (arguments / not found).
"""
    updates: dict = {}
    for k, v in body.model_dump(exclude_unset=True).items():
        if v is None:
            continue
        # For "token-like" fields an empty string means "unchanged" (the user did not touch that box); other fields are submitted as usual --
        # clearing x_desc is a legitimate need, while "clear the Token" is not something this input box can express.
        if k in {"agent_telegram_token", "agent_feishu_token"} and not str(v).strip():
            continue
        updates[k] = v

    if not updates:
        raise HTTPException(400, {"error": {"code": "NO_VALID_FIELDS", "message": "没有要更新的字段"}})

    try:
        return realdata.update_agent(agent_id, updates)
    except realdata.ToolRejected as e:
        status = 403 if e.code in {"PERMISSION_DENIED", "OWNERSHIP_DENIED"} else 400
        if e.code == "NOT_FOUND":
            status = 404
        log.info("patch_agent rejected by %s on %s: %s", user.get("username"), agent_id, e.code)
        raise HTTPException(
            status,
            {"error": {"code": e.code, "message": e.message, "details": e.details}},
        ) from e
    except Exception as e:  # noqa: BLE001
        log.warning("patch_agent failed: %s", e)
        raise HTTPException(
            502, {"error": {"code": "MCP_UNAVAILABLE", "message": f"内核通道不可用：{e}"}}
        ) from e


# ── sessions ────────────────────────────────────────
@router.get("/sessions")
def list_sessions(
    response: Response,
    _page: int = Query(1, alias="_page"),
    _limit: int = Query(20, alias="_limit"),
    q: str | None = None,
    agent: str | None = None,
    user: dict = _auth,
) -> list[dict]:
    items = realdata.sessions(caller_agent_id_of(user))
    if agent:
        items = [s for s in items if s["agentId"] == agent]
    if q:
        kw = q.lower()
        items = [s for s in items if kw in s["name"].lower() or kw in s["id"].lower()]
    return _list_envelope(response, items, _page, _limit)


# ── session message history (2026-11-14 11:08 Feng) ────────────────
@router.get("/sessions/messages")
def list_session_messages(
    key: str = Query(..., min_length=1, description="会话寻址键 = agx_session.session_id，如 agent:xxx:main"),
    # [2026-11-14 20:40 Feng] "show every message in the DB" -- a limit of 100 forces 5 clicks,
    # so it is raised to 500, matching the upper bound in realdata.session_messages (both gates must be the same number,
    # otherwise the request is stopped by FastAPI 422 and the frontend sees "an error" rather than "not fully loaded").
    limit: int = Query(20, ge=1, le=500),
    before: int | None = Query(None, description="游标（旧）：比这个 pkid 更早的一页。已不推荐，见 before_at"),
    before_at: str | None = Query(None, description="游标：比这个时间（ISO，全精度）更早的一页"),
    kinds: str | None = Query(None, description="逗号分隔，human/envelope/notice/system/command；默认全量"),
    with_instruction: bool = Query(
        False,
        description="信封是否带 exec_instruction 全文（默认只带 600 字预览；全文用 /executions/{run}/instruction）",
    ),
    user: dict = _auth,
) -> dict:
    """A session's historical question-and-answer (chronological).

    ★ Why key by session_key rather than UUID: `agx_session.session_id` is already an addressing key
      like `agent:xxx:main`, and that is exactly the session id in the frontend store -- aligning directly
      removes the need for a mapping on both sides (and a mapping always ends up updated on one side only).
    ★ Read-only, direct MySQL, no MCP: the same data-fetch definition as skills/hosts/notices/plans.
"""
    # [2026-11-14 20:40 Feng] "Do not separate system messages from non-system messages; as long as it is conversational,
    # send it all over here" -> when kinds is absent the default is **everything**, no longer human only.
    kinds_tuple: tuple[str, ...] = realdata.MSG_KINDS
    if kinds:
        wanted = tuple(k.strip() for k in kinds.split(",") if k.strip())
        kinds_tuple = tuple(k for k in wanted if k in realdata.MSG_KINDS) or ("human",)
    return realdata.session_messages(
        key,
        limit=limit,
        before=before,
        before_at=before_at,
        kinds=kinds_tuple,
        with_instruction=with_instruction,
    )


@router.get("/executions/{run_uuid}/instruction")
def get_execution_instruction(run_uuid: str, user: dict = _auth) -> dict:
    """Fetch an execution's full information by run_uuid (used by the envelope detail dialog; includes the full exec_instruction).

    ★ Why a separate endpoint: the session list must return all 497 rows at once, but envelope bodies run
      to thousands of characters -- carrying full text = 1.57MB / 12.5s to render, while a person reads one
      envelope at a time. The list gives a preview; opening one fetches only that one's full text, taking
      the cost from O(all) down to O(1).
"""
    data = realdata.execution_instruction(run_uuid)
    if not data:
        raise HTTPException(status_code=404, detail="EXECUTION_NOT_FOUND")
    return data


# ── session live state (2026-11-05) ──────────────────────────
@router.get("/sessions/runtime/lookup")
def lookup_session_runtime(
    key: str = Query(..., min_length=1, description="session_key，如 agent:main:main"),
    user: dict = _auth,
) -> dict:
    """Look up a session_key's live state on **each host** (a read-only diagnostic endpoint).

    ★ Why it exists: same-named sessions across hosts (`agent:main:main` exists on Mac / 188 / 118) are
      where this data goes wrong most easily, and a list row can only show "whichever copy that row picked
      up". Without this endpoint there is no way to prove "it really read 3 hosts, rather than treating all
      3 as the Mac".
    All data comes from Redis agx:gw:* (primary key + isolation zone); no DB queries, writes nothing.
"""
    per_host: dict[str, dict] = {}
    for h in session_runtime.hosts_of(key):
        rt = session_runtime.lookup(key, h)
        if rt is not None:
            per_host[h] = rt
    return {
        "key": key,
        "hosts": sorted(per_host),
        "perHost": per_host,
        # The fromConflict=true hosts come from the isolation zone (their primary key is held by another machine)
        "conflictHosts": sorted(h for h, rt in per_host.items() if rt.get("fromConflict")),
    }


# ── tasks ───────────────────────────────────────────
@router.get("/tasks")
def list_tasks(
    response: Response,
    _page: int = Query(1, alias="_page"),
    _limit: int = Query(20, alias="_limit"),
    q: str | None = None,
    agent: str | None = None,
    labs: str | None = None,
    user: dict = _auth,
) -> list[dict]:
    items = realdata.tasks(caller_agent_id_of(user))
    if agent:
        items = [t for t in items if t["agentId"] == agent]
    if labs:
        items = [t for t in items if t["labs"] == labs]
    if q:
        kw = q.lower()
        items = [t for t in items if kw in t["name"].lower() or kw in t["description"].lower()]
    return _list_envelope(response, items, _page, _limit)


@router.post("/projects/{project_id}/actions/reveal")
def reveal_project_folder(project_id: str, request: Request, user: dict = _auth) -> dict:
    """Open the project's source directory in the file manager of the host running the BFF.

    ★ The path is not accepted from the frontend (it is looked up from the DB by project_id), so it cannot
      become a general-purpose execution entry point.
    ★ By default only loopback may trigger it (set BFF_REVEAL_ALLOW_REMOTE=1 to allow remote).
"""
    client_host = (request.client.host if request.client else "") or ""
    try:
        return reveal.reveal_project_dir(project_id, client_host)
    except reveal.RevealError as e:
        raise HTTPException(
            e.status, {"error": {"code": e.code, "message": e.message}}
        ) from e


# ── task detail (2026-10-26 Feng: tasks have no detail view either) ──
@router.get("/tasks/{task_id}")
def get_task(task_id: str, user: dict = _auth) -> dict:
    """Task detail: including x_desc (the full task description) / payload / reply semantics."""
    row = realdata.task_detail(task_id)
    if not row:
        raise HTTPException(404, {"error": {"code": "NOT_FOUND", "message": f"任务不存在：{task_id}"}})
    return row


# ── runs / executions (contract section 3.3) ────────────────────
@router.get("/runs")
def list_runs(
    response: Response,
    _page: int = Query(1, alias="_page"),
    _limit: int = Query(20, alias="_limit"),
    status: str | None = None,
    agent: str | None = None,
    q: str | None = None,
    user: dict = _auth,
) -> list[dict]:
    items = realdata.executions(caller_agent_id_of(user))
    if status:
        items = [r for r in items if r["status"] == status]
    if agent:
        items = [r for r in items if r["agentId"] == agent]
    if q:
        kw = q.lower()
        items = [r for r in items if kw in r["execName"].lower() or kw in r["run_uuid"].lower()]
    return _list_envelope(response, items, _page, _limit)


@router.get("/executions")
def list_executions(
    response: Response,
    _page: int = Query(1, alias="_page"),
    _limit: int = Query(20, alias="_limit"),
    status: str | None = None,
    agent: str | None = None,
    q: str | None = None,
    user: dict = _auth,
) -> list[dict]:
    """Alias of /runs (the frontend's page resource is called executions)."""
    return list_runs(response, _page, _limit, status, agent, q, user)


@router.get("/executions/{run_uuid}")
def get_execution(run_uuid: str, user: dict = _auth) -> dict:
    """Single execution record (includes availableActions / nudge counts).

    [2026-10-31 Feng] after manual intervention refresh only this row -- a full refresh remounts the page,
    wiping the user's filters/search/page, so one nudge bounces them to page 1 and they think they clicked wrong.
"""
    items = realdata.executions(caller_agent_id_of(user), run_uuid=run_uuid)
    if not items:
        raise HTTPException(
            404, {"error": {"code": "NOT_FOUND", "message": f"执行记录不存在：{run_uuid}"}}
        )
    return items[0]


# ── projects ────────────────────────────────────────
@router.get("/projects")
def list_projects(
    response: Response,
    _page: int = Query(1, alias="_page"),
    _limit: int = Query(20, alias="_limit"),
    q: str | None = None,
    user: dict = _auth,
) -> list[dict]:
    items = realdata.projects(caller_agent_id_of(user))
    if q:
        kw = q.lower()
        items = [p for p in items if kw in p["name"].lower() or kw in p["id"].lower()]
    return _list_envelope(response, items, _page, _limit)


# ── project detail / editing (2026-10-26 Feng: the list has no detail + editing must call MCP) ──
@router.get("/projects/{project_id}")
def get_project(project_id: str, user: dict = _auth) -> dict:
    """Project detail: including x_desc (project description) / source_dir / creation time / scale counters."""
    row = realdata.project_detail(project_id)
    if not row:
        raise HTTPException(404, {"error": {"code": "NOT_FOUND", "message": f"项目不存在：{project_id}"}})
    return row


class UpdateProjectBody(BaseModel):
    """Project edit input.

    caller_agent_id = **the acting identity** (the kernel uses it for field grading + ownership checks).
    Empty falls back to the logged-in user (the login name becomes the caller). The frontend defaults to
    the project PM, avoiding a guaranteed permission rejection.
"""

    project_name: str | None = Field(None, description="项目名称")
    project_abbr: str | None = Field(None, description="项目简称（全小写字母）")
    project_logo: str | None = Field(None, description="项目 logo（URL 或本地路径）")
    x_desc: str | None = Field(None, description="项目说明书（Markdown）")
    source_dir: str | None = Field(None, description="源码目录")
    caller_agent_id: str = Field("", description="操作身份 agent_id（缺省回退登录用户）")


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, body: UpdateProjectBody, user: dict = _auth) -> dict:
    """Project editing -- passes through to the kernel's `agx_update_project` (no direct DB write).

    Field grading and ownership checks are **entirely in the kernel**: the BFF only normalises arguments
    and does not duplicate business rules. Kernel rejection -> 403 (permission/ownership) or 400
    (arguments), never a silent success.
"""
    caller = (body.caller_agent_id or "").strip() or caller_agent_id_of(user)
    updates = {
        k: v
        for k, v in {
            "project_name": body.project_name,
            "project_abbr": body.project_abbr,
            "project_logo": body.project_logo,
            "x_desc": body.x_desc,
            "source_dir": body.source_dir,
        }.items()
        if v is not None
    }
    try:
        return realdata.update_project(caller, project_id, updates)
    except realdata.ToolRejected as e:
        status = 403 if e.code in {"PERMISSION_DENIED", "OWNERSHIP_DENIED"} else 400
        raise HTTPException(
            status,
            {"error": {"code": e.code, "message": e.message, "details": e.details}},
        ) from e
    except Exception as e:  # noqa: BLE001
        log.warning("patch_project failed: %s", e)
        raise HTTPException(
            502, {"error": {"code": "MCP_UNAVAILABLE", "message": f"内核通道不可用：{e}"}}
        ) from e


# ── project-groups ──────────────────────────────────
@router.get("/project-groups")
def list_project_groups(
    response: Response,
    _page: int = Query(1, alias="_page"),
    _limit: int = Query(20, alias="_limit"),
    q: str | None = None,
    user: dict = _auth,
) -> list[dict]:
    items = realdata.project_groups(caller_agent_id_of(user))
    if q:
        kw = q.lower()
        items = [g for g in items if kw in g["name"].lower() or kw in g["id"].lower()]
    return _list_envelope(response, items, _page, _limit)


# ── plans ───────────────────────────────────────────
@router.get("/plans")
def list_plans(
    response: Response,
    _page: int = Query(1, alias="_page"),
    _limit: int = Query(20, alias="_limit"),
    status: str | None = None,
    q: str | None = None,
    user: dict = _auth,
) -> list[dict]:
    items = realdata.plans(caller_agent_id_of(user))
    if status:
        items = [p for p in items if p["status"] == status]
    if q:
        kw = q.lower()
        items = [p for p in items if kw in p["name"].lower() or kw in p["id"].lower()]
    return _list_envelope(response, items, _page, _limit)


@router.get("/plans/{plan_id}/graph")
def get_plan_graph(plan_id: str, user: dict = _auth) -> dict:
    """Plan flow-graph data (nodes + edges, including real run_status).

    For the frontend Plan Flow component to render the plan DAG. Direct MySQL (agx_plan.steps_json
    + agx_task_execution.run_status).
"""
    g = realdata.plan_graph(plan_id, caller_agent_id_of(user))
    if not g:
        from fastapi import HTTPException
        raise HTTPException(404, {"error": {"code": "NOT_FOUND", "message": f"plan {plan_id} 不存在"}})
    return g


@router.get("/plans/{plan_id}/status")
def get_plan_status(plan_id: str, user: dict = _auth) -> dict:
    """Plan live status (polling endpoint) -- returns only run_uuid -> status + counts.

    [2026-10-27 Feng] "the flow graph state must be real-time; there is data in Redis"
    Data source = the kernel's Redis hot cache `agx:entity:execution:{run}` (written on every state
    transition), with the MySQL persisted value as the fallback for the graph's initial load
    (see realdata.plan_graph).

    ★ Deliberately carries **no** instruction / node structure: re-shipping the full text of 137 steps
      every round would turn "real-time" into a burden. The frontend only merges status, never layout.
"""
    out = realdata.plan_live_status(plan_id)
    if out["counts"]["total"] == 0:
        raise HTTPException(
            404, {"error": {"code": "NOT_FOUND", "message": f"计划不存在或无步骤：{plan_id}"}}
        )
    return out


# ── skills (shared skill library, agx_skill) ───────────────────
@router.get("/skills")
def list_skills(
    response: Response,
    _page: int = Query(1, alias="_page"),
    _limit: int = Query(20, alias="_limit"),
    category: str | None = None,
    q: str | None = None,
    user: dict = _auth,
) -> list[dict]:
    """Shared skill library list (reusable skills agents share with each other)."""
    items = realdata.skills(caller_agent_id_of(user))
    if category:
        items = [s for s in items if s["category"] == category]
    if q:
        kw = q.lower()
        items = [
            s for s in items
            if kw in s["name"].lower()
            or kw in s["description"].lower()
            or kw in s["tags"].lower()
            or kw in s["sourceAgentId"].lower()
        ]
    return _list_envelope(response, items, _page, _limit)


@router.get("/skills/{skill_id}")
def get_skill(skill_id: str, user: dict = _auth) -> dict:
    """Single skill detail (including the full body content)."""
    for s in realdata.skills(caller_agent_id_of(user)):
        if s["id"] == skill_id:
            return s
    from fastapi import HTTPException
    raise HTTPException(404, {"error": {"code": "NOT_FOUND", "message": f"skill {skill_id} 不存在"}})


# ── hosts (compute-host assets, agx_host_inventory) ───────────
@router.get("/hosts")
def list_hosts(
    response: Response,
    _page: int = Query(1, alias="_page"),
    _limit: int = Query(20, alias="_limit"),
    user: dict = _auth,
) -> list[dict]:
    """Compute-host asset list (agx_host_inventory)."""
    items = realdata.hosts(caller_agent_id_of(user))
    return _list_envelope(response, items, _page, _limit)


class UpdateHostBody(BaseModel):
    """Host edit input (only changed fields; None = unchanged).

    [2026-10-30 Feng] console editing is submitted to the kernel **as agx_core**,
    so caller_agent_id is not exposed here -- the acting identity is fixed by the BFF
    (see realdata.EDITOR_AGENT_ID). The logged-in user only serves as an audit trace (actor).
"""

    host_alias: str | None = Field(None, description="主机别名")
    endpoint_ip: str | None = Field(None, description="访问地址（IP / 主机名）")
    ssh_port: int | None = Field(None, description="SSH 端口 1-65535")
    os_platform: str | None = Field(None, description="操作系统，如 macOS / Ubuntu")
    platforms: list[dict] | None = Field(
        None,
        description="本机可用的 agent 平台及访问方式；mode=process 用 endpoint_ip+port，mode=container 用 addr",
    )
    task_slots_limit: int | None = Field(None, description="并发任务槽上限（>=0）")
    x_desc: str | None = Field(None, description="描述")


@router.patch("/hosts/{host_id}")
def patch_host(host_id: str, body: UpdateHostBody, user: dict = _auth) -> dict:
    """Host editing -- passes through to the kernel's `agx_update_host` (no direct DB write).

    The submit identity is fixed as agx_core (workshop_manager, sufficient for agx_update_host's L6);
    the logged-in user is only logged. Kernel rejection -> 403 (permission) or 400 (arguments / no valid
    fields); host not found -> 404.
"""
    updates: dict = {
        k: v
        for k, v in {
            "host_alias": body.host_alias.strip() if isinstance(body.host_alias, str) else body.host_alias,
            "endpoint_ip": body.endpoint_ip.strip() if isinstance(body.endpoint_ip, str) else body.endpoint_ip,
            "ssh_port": body.ssh_port,
            "os_platform": body.os_platform.strip() if isinstance(body.os_platform, str) else body.os_platform,
            "platforms": body.platforms,
            "task_slots_limit": body.task_slots_limit,
            "x_desc": body.x_desc,
        }.items()
        if v is not None
    }
    if "host_alias" in updates and not updates["host_alias"]:
        raise HTTPException(400, {"error": {"code": "INVALID_FIELD", "message": "主机别名不能为空"}})
    if "ssh_port" in updates and not (1 <= int(updates["ssh_port"]) <= 65535):
        raise HTTPException(400, {"error": {"code": "INVALID_FIELD", "message": "SSH 端口须在 1-65535 之间"}})
    if "task_slots_limit" in updates and int(updates["task_slots_limit"]) < 0:
        raise HTTPException(400, {"error": {"code": "INVALID_FIELD", "message": "任务槽上限不能为负"}})

    log.info("patch_host host=%s actor=%s fields=%s", host_id, user.get("name"), sorted(updates))
    try:
        return realdata.update_host(host_id, updates)
    except realdata.ToolRejected as e:
        status = 404 if e.code == "NOT_FOUND" else 403 if e.code in {"PERMISSION_DENIED", "OWNERSHIP_DENIED"} else 400
        raise HTTPException(
            status,
            {"error": {"code": e.code, "message": e.message, "details": e.details}},
        ) from e
    except Exception as e:  # noqa: BLE001
        log.warning("patch_host failed: %s", e)
        raise HTTPException(
            502, {"error": {"code": "MCP_UNAVAILABLE", "message": f"内核通道不可用：{e}"}}
        ) from e


def _write_error(e: realdata.ToolRejected):
    """Turn a kernel rejection into HTTP.

    [2026-11-01] shared by the three edit endpoints: the error codes must be distinct -- collapsing them
    into "save failed" sends people to check whether the feature is broken when in fact the timeout was
    simply set too small.
"""
    status = (
        404 if e.code == "NOT_FOUND"
        else 403 if e.code in {"PERMISSION_DENIED", "OWNERSHIP_DENIED"}
        else 409 if e.code in {"STATE_MISMATCH", "RATE_LIMITED"}
        else 400
    )
    return HTTPException(
        status, {"error": {"code": e.code, "message": e.message, "details": e.details}}
    )


# ── task editing (2026-11-01 Feng: row-level "detail" becomes "edit") ──
class UpdateTaskBody(BaseModel):
    """Editable task fields; all optional, submit only the changed ones."""

    x_desc: str | None = None          # Task description (Markdown, possibly thousands of characters)
    target_object: str | None = None
    payload: str | dict | None = None  # Payload: accepts JSON text or an object
    priority: int | str | None = None
    is_private: int | None = None
    active_status: int | str | None = None


@router.patch("/tasks/{task_id}")
def patch_task(task_id: str, body: UpdateTaskBody, user: dict = _auth) -> dict:
    """Edit a task template -- passes through to the kernel's `agx_update_task`.

    The submit identity is fixed as agx_core (a workspace-level global role, passing the kernel's
    resource-level ownership check); the logged-in user is only logged. Kernel rejection -> 403/400;
    task not found -> 404.
"""
    updates = body.model_dump(exclude_none=True)
    log.info("patch_task task=%s actor=%s fields=%s", task_id, user.get("name"), sorted(updates))
    try:
        return realdata.update_task(task_id, updates)
    except realdata.ToolRejected as e:
        raise _write_error(e) from e
    except Exception as e:  # noqa: BLE001
        log.warning("patch_task failed: %s", e)
        raise HTTPException(
            502, {"error": {"code": "MCP_UNAVAILABLE", "message": f"内核通道不可用：{e}"}}
        ) from e


# ── execution editing (2026-11-01 Feng: "you can add editing for execution records") ──
class UpdateExecutionBody(BaseModel):
    """Editable execution fields.

    ★ Deliberately excludes run_status / reply_read:
      the former is the authoritative field of the execution result (changing it forges success/failure),
      the latter goes through agx_ack_receipt (the acknowledgement loop).
"""

    exec_name: str | None = None
    timeout_seconds: int | None = None
    exec_instruction: str | None = None
    alert_note: str | None = None


@router.patch("/executions/{run_uuid}")
def patch_execution(run_uuid: str, body: UpdateExecutionBody, user: dict = _auth) -> dict:
    """Edit an execution -- passes through to the kernel's `agx_update_execution` (field permission is decided per field by the kernel)."""
    updates = body.model_dump(exclude_none=True)
    log.info("patch_execution run=%s actor=%s fields=%s", run_uuid, user.get("name"), sorted(updates))
    try:
        return realdata.update_execution(run_uuid, updates)
    except realdata.ToolRejected as e:
        raise _write_error(e) from e
    except Exception as e:  # noqa: BLE001
        log.warning("patch_execution failed: %s", e)
        raise HTTPException(
            502, {"error": {"code": "MCP_UNAVAILABLE", "message": f"内核通道不可用：{e}"}}
        ) from e


# ── skill editing (2026-11-01 Feng: "drop the detail view on the agent-sharing page and add editing") ──
class UpdateSkillBody(BaseModel):
    """Editable skill fields; all optional, submit only the changed ones."""

    skill_name: str | None = None
    category: str | None = None
    description: str | None = None
    tags: str | None = None
    content: str | None = None


@router.patch("/skills/{skill_id}")
def patch_skill(skill_id: str, body: UpdateSkillBody, user: dict = _auth) -> dict:
    """Edit a skill -- passes through to the kernel's `agx_skill_contribute` (passing skill_id updates; idempotent).

    ★ Does not write agx_skill directly: the use_count / helpful_count signals and the Redis cache are all in the kernel.
"""
    updates = body.model_dump(exclude_none=True)
    log.info("patch_skill skill=%s actor=%s fields=%s", skill_id, user.get("name"), sorted(updates))
    try:
        return realdata.update_skill(skill_id, updates)
    except realdata.ToolRejected as e:
        raise _write_error(e) from e
    except Exception as e:  # noqa: BLE001
        log.warning("patch_skill failed: %s", e)
        raise HTTPException(
            502, {"error": {"code": "MCP_UNAVAILABLE", "message": f"内核通道不可用：{e}"}}
        ) from e


# ── notices (agent announcements / work logs, agx_agent_notice) ──
@router.get("/notices")
def list_notices(
    response: Response,
    _page: int = Query(1, alias="_page"),
    _limit: int = Query(20, alias="_limit"),
    notice_type: str | None = Query(None, alias="type"),
    project_id: str | None = None,
    agent_id: str | None = None,
    q: str | None = None,
    user: dict = _auth,
) -> list[dict]:
    """Agent announcement / work-log list (agx_agent_notice)."""
    items = realdata.notices(caller_agent_id_of(user))
    if notice_type:
        items = [n for n in items if n["type"] == notice_type]
    if project_id:
        items = [n for n in items if n["projectId"] == project_id]
    if agent_id:
        items = [n for n in items if n["agentId"] == agent_id]
    if q:
        kw = q.lower()
        items = [
            n for n in items
            if kw in n["title"].lower()
            or kw in n["content"].lower()
            or kw in n["agentName"].lower()
            or kw in n["projectName"].lower()
        ]
    return _list_envelope(response, items, _page, _limit)


@router.get("/notices/{notice_id}")
def get_notice(notice_id: str, user: dict = _auth) -> dict:
    """Single announcement detail (including the full body content)."""
    for n in realdata.notices(caller_agent_id_of(user)):
        if n["id"] == notice_id:
            return n
    from fastapi import HTTPException
    raise HTTPException(404, {"error": {"code": "NOT_FOUND", "message": f"notice {notice_id} 不存在"}})


# ── dashboard (contract section 3.6) ────────────────────────────
@router.get("/dashboard/stats")
def dashboard_stats(user: dict = _auth) -> dict:
    return realdata.dashboard_stats(caller_agent_id_of(user))


# ── snapshot (for frontend hydration: one full pull) ────────────
@router.get("/snapshot")
def snapshot(user: dict = _auth) -> dict:
    """One full pull of all business data at frontend startup (read-only).

    Design intent: the page layer (providers/agentgeex.ts) keeps its **synchronous** call signatures
    unchanged, and the SPA fills the in-memory store with real data at startup, achieving "zero page-code changes".
"""
    return realdata.snapshot(caller_agent_id_of(user))


# ── reserved: products / orders placeholders (next batch) ────────────
@router.get("/products")
def list_products(
    response: Response,
    _page: int = Query(1, alias="_page"),
    _limit: int = Query(10, alias="_limit"),
    q: str | None = None,
    category: str | None = None,
    status: str | None = None,
    user: dict = _auth,
) -> list[dict]:
    rows: list[dict] = []
    response.headers["X-Total-Count"] = "0"
    _ = (user, q, category, status)
    return _paginate(rows, _page, _limit)


@router.get("/products/categories")
def product_categories(user: dict = _auth) -> list[str]:
    _ = user
    return []


@router.get("/orders")
def list_orders(
    response: Response,
    _page: int = Query(1, alias="_page"),
    _limit: int = Query(10, alias="_limit"),
    q: str | None = None,
    status: str | None = None,
    dateFrom: str | None = None,
    dateTo: str | None = None,
    user: dict = _auth,
) -> list[dict]:
    rows: list[dict] = []
    response.headers["X-Total-Count"] = "0"
    _ = (user, q, status, dateFrom, dateTo)
    return _paginate(rows, _page, _limit)


_ = kernel  # keep the reference (later handlers call kernel directly)
