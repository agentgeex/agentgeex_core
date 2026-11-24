"""realdata.py - Win3 BFF real-data read layer (read-only).

[2026-10-05 Feng's decision: reads go straight to MySQL, writes go through MCP]
    - This layer is **read-only**: it talks to MySQL directly (`kernel.query`) to fetch
      data. Fields are complete and aggregatable, there are no cache-sync concerns,
      and it does not detour through MCP (fast).
    - Writes / operations (dispatch, update, cache sync) = the MCP channel
      (`kernel.call_tool`). MCP is positioned as [operation sync] -- it keeps the
      kernel cache consistent.

This layer only issues SELECTs and never writes to the DB; all writes go through MCP.
Field mapping: MySQL row -> frontend mock field (the entity shape in mock/agentgeex.ts).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import kernel, session_runtime
from .config import settings

log = logging.getLogger("win3.bff.realdata")

# Read-only fetch cap per entity (read uniformly from config; default 100000, prevents full-table dumps)
_MAX = settings.fetch_limit
_NETWORK_ID = "lan_001"  # TODO: derive from the caller once multi-tenant lands

# ══════════════════════════════════════════════════════════════════
# Field mapping: MCP tool return -> frontend mock field
# ══════════════════════════════════════════════════════════════════
_AGENT_STATUS = {1: "online", 0: "offline"}
_PROJECT_STATUS = {1: "active", 0: "archived"}
_TEMPLATE_STATUSES = {"template"}
# ★ 2026-11-03 12:47: corrected against agentgeex_agent code evidence (the previous mapping was stale).
#   Only these 7 values are ever actually written; pending / running are never produced in code
#   (pending used to be the DEFAULT, but every INSERT writes draft explicitly; "running" is actually called activated).
#   The old mapping was missing four values: activated/cancelling/archived/failed,
#   and the call site`s `.get(x, "draft")` defaulted any miss to draft --
#   so the 4 activated plans in production showed as "draft" in the list: a visible bug.
_PLAN_STATUS_MAP = {
    "draft": "draft",            # created, not yet activated
    "activated": "activated",    # activated, steps being released (= running in the old design)
    "cancelling": "cancelling",  # cancelling (some steps are still running)
    "cancelled": "cancelled",    # cancelled
    "completed": "completed",    # all steps closed out
    "archived": "archived",      # archived (legacy, no write path)
    "failed": "failed",          # reserved; no write path today
}
# ★ 2026-11-03 21:43: **archived must be in this whitelist**.
#   It was missing -> 78 archived executions were, downstream, hit by `if st not in KEEP: st = "pending"`
#   and silently rewritten to "queued", so filtering the list by status gave: queued 207 (= real 129 + archived 78),
#   archived 0. The dashboard read the 7 true values straight from SQL (129 / 78 kept distinct),
#   and the two pages disagreed -- the user can only conclude one of them is lying.
#   > This is "the fallback value decides how well a bug hides": rewriting to pending raised no error,
#   > looked plausible, and so it stayed hidden -- until someone cross-checked the two pages.
_RUN_STATUS_KEEP = {
    "draft", "pending", "running", "succeeded", "failed", "cancelled",
    # ★ 2026-11-06 Feng: added "awaiting human confirmation".
    #   **Must be in the whitelist** -- otherwise downstream `if st not in KEEP: st = "pending"`
    #   silently rewrites it to "queued": a record whose work is finished and only awaits a click
    #   would show as "still queued" and nobody would go click approve. (That is how archived got lost.)
    "awaiting_confirm",
    "pending_confirm",   # legacy alias; the kernel's agx_confirm_task still reads it
    "archived",
}


def _str(v: Any) -> str:
    return "" if v is None else str(v)


def _plan_status(v: Any) -> str:
    """agx_plan.plan_status -> frontend status value.

    ★ 2026-11-03: the fallback **no longer disguises itself as draft**.
      The old `.get(x, "draft")` turned any value absent from the mapping into "draft" --
      the result being 4 activated plans shown as draft, looking perfectly normal
      (not blank, not an error: a confidently wrong answer).
      Now: mapped values use the mapping; a miss is returned as-is so the frontend
      StatusPill can fall back to the raw text (grey background + raw string) --
      an unknown value should look unknown, not be dressed up as something known.
"""
    raw = _str(v).strip().lower()
    if not raw:
        return "draft"
    return _PLAN_STATUS_MAP.get(raw, raw)


def _dt(v: Any) -> str:
    """datetime / ISO string -> 'YYYY-MM-DD HH:MM'."""
    if v is None:
        return ""
    s = v.isoformat() if hasattr(v, "isoformat") else str(v)
    s = s.replace("T", " ")
    return s[:16]


def _secs_between(a: Any, b: Any) -> int:
    """Seconds between two timestamps (0 if either is empty).

    ★ Why not SELECT `duration_seconds` from the table: `agx_task_execution` has no
      such column (it is a derived field computed on the fly inside MCP's
      `agx_get_execution` response). The first version wrote a SELECT against the
      returned field names and got error 1054 straight away -- so it is computed here,
      matching the kernel's definition.
"""
    if not a or not b:
        return 0
    try:
        return max(0, int((b - a).total_seconds())) if hasattr(a, "__sub__") else 0
    except Exception:  # noqa: BLE001
        return 0


def _date(v: Any) -> str:
    if v is None:
        return ""
    s = v.isoformat() if hasattr(v, "isoformat") else str(v)
    return s[:10]


def _call(tool: str, caller: str, **kwargs: Any) -> Any:
    """Call an MCP tool safely: on failure only warn and return None (the page degrades to mock)."""
    try:
        return kernel.call_tool(tool, caller_agent_id=caller, **kwargs)
    except Exception as e:  # noqa: BLE001
        log.warning("realdata mcp call failed: %s | tool=%s", e, tool)
        return None


class ToolRejected(Exception):
    """Explicitly rejected by the kernel (permission / ownership / arguments), or a channel error.

    Opposite to `_call`'s "warn and degrade on read failure": writes are **never
    silently degraded** -- the caller must receive an explicit reason, otherwise the
    user believes the change succeeded when nothing changed.
"""

    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


# ══════════════════════════════════════════════════════════════════
# 1. Agents  (agx_list_by_network)
# ══════════════════════════════════════════════════════════════════
def agents(caller: str = "") -> list[dict]:
    _ = caller  # Reads do not verify identity; the parameter is kept to match the route signature
    try:
        rows = kernel.query(
            """SELECT agent_id, agent_name, agent_role, agent_type, host_id,
                      project_id, base_model, active_status, deploy_status,
                      peer_agent_id, created_at, agent_avatar,
                      dispatch_exempt_until
                 FROM agx_agent_registry
                WHERE network_id = %s
                ORDER BY pkid ASC""",
            (_NETWORK_ID,),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("realdata agents query failed: %s", e)
        return []

    # Sub-counts: sessions / tasks (aggregated by agent_id in one query)
    ses_cnt: dict[str, int] = {}
    task_cnt: dict[str, int] = {}
    exec_cnt: dict[str, int] = {}
    try:
        for r in kernel.query(
            "SELECT agent_id, COUNT(*) AS c FROM agx_session "
            "WHERE network_id = %s GROUP BY agent_id",
            (_NETWORK_ID,),
        ):
            ses_cnt[str(r.get("agent_id"))] = int(r.get("c") or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("agents session count failed: %s", e)
    try:
        for r in kernel.query(
            "SELECT agent_id, COUNT(*) AS c FROM agx_task_base "
            "WHERE network_id = %s AND active_status = 1 GROUP BY agent_id",
            (_NETWORK_ID,),
        ):
            task_cnt[str(r.get("agent_id"))] = int(r.get("c") or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("agents task count failed: %s", e)
    exec_ok: dict[str, int] = {}
    exec_fail: dict[str, int] = {}
    try:
        for r in kernel.query(
            "SELECT agent_id, COUNT(*) AS c, "
            "SUM(run_status = 'succeeded') AS ok, "
            "SUM(run_status = 'failed') AS bad "
            "FROM agx_task_execution WHERE network_id = %s GROUP BY agent_id",
            (_NETWORK_ID,),
        ):
            aid = str(r.get("agent_id"))
            exec_cnt[aid] = int(r.get("c") or 0)
            exec_ok[aid] = int(r.get("ok") or 0)
            exec_fail[aid] = int(r.get("bad") or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("agents exec count failed: %s", e)

    now = datetime.now()
    out = []
    for r in rows:
        aid = r.get("agent_id") or ""
        deploy_status = (r.get("deploy_status") or "pending").strip()
        active = int(r.get("active_status") or 0) == 1
        is_template = deploy_status in _TEMPLATE_STATUSES
        origin = (r.get("peer_agent_id") or "").strip()
        # Type (driven by real columns): template / cloned instance / native
        if is_template:
            kind, kind_label = "template", "模板"
        elif origin:
            kind, kind_label = "clone", "克隆实例"
        else:
            kind, kind_label = "native", "原生"
        out.append({
            "id": aid,
            "name": r.get("agent_name") or aid,
            "role": r.get("agent_role") or "worker",
            "status": _AGENT_STATUS.get(int(r.get("active_status") or 0), "offline"),
            "deployStatus": deploy_status,
            "kind": kind,
            "kindLabel": kind_label,
            "isTemplate": is_template,
            "inUse": (not is_template) and active,
            "originId": origin,
            "agentType": r.get("agent_type") or "openclaw",
            "avatarUrl": r.get("agent_avatar") or "",
            "model": r.get("base_model") or "—",
            "project_id": r.get("project_id") or "",
            "host_id": r.get("host_id") or "",
            "sessionCount": ses_cnt.get(aid, 0),
            "taskCount": task_cnt.get(aid, 0),
            "execCount": exec_cnt.get(aid, 0),
            "execSucceeded": exec_ok.get(aid, 0),
            "execFailed": exec_fail.get(aid, 0),
            "tokens": 0,
            "lastActiveAt": _dt(r.get("created_at")),
            # ── temporary dispatch exemption (agx_agent_registry.dispatch_exempt_until) ──────
            # The three states must be judged separately; **do not write `IS NOT NULL`**:
            #   NULL / empty string -> never opened (frontend shows "none")
            #   > now       -> currently exempt (shows "X left")
            #   <= now      -> was opened but has expired (shows "expired")
            # Treating "expired" as "none" makes the user think it was never set and keep clicking;
            # treating "expired" as "exempt" is worse -- it makes people believe they can still dispatch.
            # The decision is made server-side; the frontend no longer uses the browser clock (clock drift makes the two sides disagree).
            **exempt_state(r.get("dispatch_exempt_until"), now, r.get("agent_role")),
        })
    return out


# ──────────────────────────────────────────────────────────────────
# Agent detail (profile) -- 2026-10-31 Feng: "You don't even know what state an agent is really in"
#
# ★ Why not assemble it from agents()'s return value: agents() serves the **list** page -- 90 agents, a full
#   snapshot every time. And what a detail page needs -- x_desc / system_prompt / the 7 *_md persona files -- are all
#   longtext; stuffing them into the snapshot adds several MB to /v1/snapshot and the list page uses none of it.
#   Hence a separate fetch-by-id function, called once, only from the detail page.
# ──────────────────────────────────────────────────────────────────

# Persona files (OpenClaw's agent config files). The detail page answers exactly one question: "is this agent fully configured?",
# so the column names and display names live together here; the page no longer hardcodes them again.
_PERSONA_FILES = (
    ("agents_md", "AGENTS.md"),
    ("soul_md", "SOUL.md"),
    ("identity_md", "IDENTITY.md"),
    ("self_md", "SELF.md"),
    ("user_md", "USER.md"),
    ("tools_md", "TOOLS.md"),
    ("heartbeat_md", "HEARTBEAT.md"),
    ("notebook_md", "NOTEBOOK.md"),
)


def _json_list(raw: Any) -> list[str]:
    """A JSON-array column such as skills_allowlist -> list[str]; if it cannot be parsed, return empty honestly and leave a trace."""
    if not raw:
        return []
    val = raw
    if isinstance(raw, (str, bytes)):
        with contextlib.suppress(Exception):
            val = json.loads(raw)
    if isinstance(val, list):
        return [str(x) for x in val if str(x).strip()]
    return []


def _json_obj_list(raw: Any) -> list[dict]:
    """A JSON array-of-objects column (e.g. agx_host_inventory.platforms) -> list[dict].

    ★ Must not use _json_list: that function is meant for string arrays like
      skills_allowlist and runs `str()` on every element -- given an array of objects it
      yields ["{'platform': ...}"], so the frontend receives strings instead of objects,
      i.e. nothing was actually parsed.

    Unparsable / not an array -> [] (better to render no links than hand the frontend bad values).
"""
    if not raw:
        return []
    val = raw
    if isinstance(raw, (str, bytes)):
        with contextlib.suppress(Exception):
            val = json.loads(raw)
    if isinstance(val, list):
        return [x for x in val if isinstance(x, dict)]
    return []


def _agent_paths(
    agent_id: str,
    *,
    host_id: str,
    db_root: Any,
    db_workspace: Any,
    project_source: str = "",
) -> dict:
    """Answer "where do this agent's files actually live" -- DB values win; when absent,
derive by convention and say so.

    [2026-11-01 Feng] "You are missing something: the path to your source code."
    Reality: `agx_agent_registry.agent_root_dir / agent_workspace_dir` are **NULL for
    all 90 agents** -- deployment information only ever lands on the filesystem and is
    never written back to the DB.
    So a UI that only shows DB fields shows "—" forever, i.e. the information is absent.

    Approach: use the DB value when present (source=db); otherwise derive from the
    runtime convention:
        root      = {work_dir}/agx_agents[/host]/{agent_id}
        workspace = {root}/workspace
    ★ 2026-11-12 fix: the original derivation said openclaw_agents (an old convention
      derived from agent_type); that directory never existed on disk, and host-type
      agents actually live under agx_agents/host/. Both were wrong -> the UI's
      "where are the files" always showed a path that does not exist.
    And **label it explicitly as derived**, so nobody believes the DB really stores it.

    Existence can only be verified locally:
      the agent's host_id equals the BFF host -> measure with os.path.isdir;
      a different host -> exists=None (say you don't know rather than guess).
      Guessing "exists" across hosts makes people operate on a path that is not there.

    The project source directory (agx_project.source_dir) is returned separately: the
    code an agent works on lives in the project, not in its own workspace -- the two are
    constantly conflated.
"""
    work_dir = str(settings.work_dir or "").strip()
    if work_dir.startswith("~"):
        work_dir = str(Path(work_dir).expanduser())

    root_db = str(db_root or "").strip()
    ws_db = str(db_workspace or "").strip()

    if work_dir:
        _parts = [work_dir, "agx_agents"]
        # host-type agents have an extra host/ segment in their real path (same convention as install.py / MCP)
        if str(agent_id).startswith("agx_host"):
            _parts.append("host")
        _parts.append(str(agent_id))
        derived_root = str(Path(*_parts))
    else:
        derived_root = ""
    derived_ws = str(Path(derived_root) / "workspace") if derived_root else ""

    on_this_host = bool(host_id) and host_id == str(settings.host_id or "")

    def one(db_value: str, derived: str) -> dict:
        if db_value:
            value, source = db_value, "db"
        elif derived:
            value, source = derived, "derived"
        else:
            return {"value": "", "source": "", "exists": None}
        exists = None
        if on_this_host and value:
            with contextlib.suppress(OSError):
                exists = Path(value).is_dir()
        return {"value": value, "source": source, "exists": exists}

    src = (project_source or "").strip()
    return {
        "root": one(root_db, derived_root),
        "workspace": one(ws_db, derived_ws),
        "projectSource": {
            "value": src,
            "source": "project" if src else "",
            "exists": Path(src).is_dir() if (on_this_host and src) else None,
        },
        "hostId": host_id,
        "checkedOnThisHost": on_this_host,
        "workDir": work_dir,
    }


def agent_profile(agent_id: str) -> dict | None:
    """Full profile for one agent (detail page only).

    Returns None when the agent does not exist (the route turns that into a 404; do not
    let the frontend render "not found" as an empty detail page).
"""
    rows = kernel.query(
        """SELECT agent_id, agent_name, agent_role, agent_type, agent_labs, host_id,
                  project_id, scope, is_project_admin, base_model, active_status,
                  deploy_status, peer_agent_id, created_at, agent_avatar,
                  description, x_desc, system_prompt, default_session_id,
                  agent_root_dir, agent_workspace_dir, skills_allowlist, mcp_config,
                  dispatch_exempt_until,
                  agents_md, soul_md, identity_md, self_md, user_md, tools_md,
                  heartbeat_md, notebook_md
             FROM agx_agent_registry
            WHERE agent_id = %s
            LIMIT 1""",
        (agent_id,),
    )
    if not rows:
        return None
    r = rows[0]

    # Project name / host alias: showing a bare id on the detail page makes people hunt for the lookup table
    project_name = ""
    project_admin = ""
    project_source = ""
    with contextlib.suppress(Exception):
        pr = kernel.query(
            "SELECT project_name, admin_agent_id, source_dir FROM agx_project WHERE project_id = %s LIMIT 1",
            (r.get("project_id") or "",),
        )
        if pr:
            project_name = str(pr[0].get("project_name") or "")
            project_admin = str(pr[0].get("admin_agent_id") or "")
            project_source = str(pr[0].get("source_dir") or "")
    host_alias = ""
    with contextlib.suppress(Exception):
        hr = kernel.query(
            "SELECT host_alias, os_platform, task_slots_limit, active_task_count "
            "FROM agx_host_inventory WHERE host_id = %s LIMIT 1",
            (r.get("host_id") or "",),
        )
        if hr:
            host_alias = str(hr[0].get("host_alias") or "")

    # Counts: sessions / task templates / executions (succeeded/failed) / assistants / last active
    counts = {"sessions": 0, "tasks": 0, "executions": 0, "execOk": 0, "execFail": 0, "assistants": 0}
    last_active = ""
    with contextlib.suppress(Exception):
        for q, key in (
            ("SELECT COUNT(*) AS c FROM agx_session WHERE agent_id = %s", "sessions"),
            (
                "SELECT COUNT(*) AS c FROM agx_task_base WHERE agent_id = %s AND active_status = 1",
                "tasks",
            ),
            (
                "SELECT COUNT(*) AS c FROM agx_session WHERE agent_id = %s AND is_assistant = 1",
                "assistants",
            ),
        ):
            cr = kernel.query(q, (agent_id,))
            counts[key] = int((cr[0].get("c") if cr else 0) or 0)
        er = kernel.query(
            "SELECT COUNT(*) AS c, SUM(run_status = 'succeeded') AS ok, "
            "SUM(run_status = 'failed') AS bad FROM agx_task_execution WHERE agent_id = %s",
            (agent_id,),
        )
        if er:
            counts["executions"] = int(er[0].get("c") or 0)
            counts["execOk"] = int(er[0].get("ok") or 0)
            counts["execFail"] = int(er[0].get("bad") or 0)
        lr = kernel.query(
            "SELECT MAX(last_active_at) AS t FROM agx_session WHERE agent_id = %s", (agent_id,)
        )
        if lr:
            last_active = _dt(lr[0].get("t"))

    deploy_status = (r.get("deploy_status") or "pending").strip()
    is_template = deploy_status in _TEMPLATE_STATUSES
    origin = (r.get("peer_agent_id") or "").strip()
    if is_template:
        kind, kind_label = "template", "模板"
    elif origin:
        kind, kind_label = "clone", "克隆实例"
    else:
        kind, kind_label = "native", "原生"

    # Persona files: report "configured or not / how many chars", not all 8 long documents in the response.
    # The detail page expands one on demand and fetches its body then (the body is included here anyway; a single request is small enough:
    # most agents have NULL in these columns, and when there is real text only this page reads it).
    persona = []
    for col, label in _PERSONA_FILES:
        body = r.get(col) or ""
        persona.append(
            {
                "key": col,
                "label": label,
                "chars": len(body) if isinstance(body, str) else 0,
                "filled": bool(isinstance(body, str) and body.strip()),
                "body": body if isinstance(body, str) else "",
            }
        )

    mcp_cfg = r.get("mcp_config")
    if isinstance(mcp_cfg, str):
        with contextlib.suppress(Exception):
            mcp_cfg = json.loads(mcp_cfg)

    return {
        "id": r.get("agent_id") or "",
        "name": r.get("agent_name") or r.get("agent_id") or "",
        "role": r.get("agent_role") or "worker",
        "agentType": r.get("agent_type") or "openclaw",
        "agentLabs": r.get("agent_labs") or "",
        "kind": kind,
        "kindLabel": kind_label,
        "isTemplate": is_template,
        "originId": origin,
        "avatarUrl": r.get("agent_avatar") or "",
        "description": r.get("description") or "",
        "projectId": r.get("project_id") or "",
        "projectName": project_name,
        "projectAdminId": project_admin,
        "hostId": r.get("host_id") or "",
        "hostAlias": host_alias,
        "scope": r.get("scope") or "",
        "isProjectAdmin": int(r.get("is_project_admin") or 0) == 1,
        "model": r.get("base_model") or "",
        "status": _AGENT_STATUS.get(int(r.get("active_status") or 0), "offline"),
        "activeStatus": int(r.get("active_status") or 0) == 1,
        "deployStatus": deploy_status,
        "defaultSessionId": r.get("default_session_id") or "",
        "workspaceDir": r.get("agent_workspace_dir") or "",
        "rootDir": r.get("agent_root_dir") or "",
        # File locations: DB value first / derive by convention when absent / only check existence locally
        "paths": _agent_paths(
            agent_id,
            host_id=str(r.get("host_id") or ""),
            db_root=r.get("agent_root_dir"),
            db_workspace=r.get("agent_workspace_dir"),
            project_source=project_source,
        ),
        "createdAt": _dt(r.get("created_at")),
        "lastActiveAt": last_active,
        "counts": counts,
        "personaFiles": persona,
        "skillsAllowlist": _json_list(r.get("skills_allowlist")),
        "mcpConfig": mcp_cfg or {},
        "xDesc": r.get("x_desc") or "",
        "systemPrompt": r.get("system_prompt") or "",
        **exempt_state(r.get("dispatch_exempt_until"), None, r.get("agent_role")),
    }


# ══════════════════════════════════════════════════════════════════
# 2. Projects / ProjectGroups  (agx_list_by_network)
# ══════════════════════════════════════════════════════════════════
def projects(caller: str = "") -> list[dict]:
    """Project list -- talks to MySQL directly (`agx_project`).

    [2026-10-28 Feng] "The project id means nothing; show the project name."
    Root cause: this used to go through MCP `agx_list_by_network(entity_type="project")`.
    When the MCP SSE dropped (502) `_call` silently returned None -> the project list was
    empty -> the frontend's `getProjectName(id)` fell back to `proj_xxx` everywhere, and
    the agent page's project filter/grouping was wall-to-wall ids with no project findable.
    Hence, like skills/hosts/notices/plans, it now **talks to MySQL directly**
    (MCP keeps only the write operations).
"""
    try:
        rows = kernel.query(
            """SELECT project_id, project_name, project_abbr, platform, project_logo,
                      admin_agent_id, active_status, created_at, is_quick, quick_order
                 FROM agx_project
                WHERE network_id = %s
                ORDER BY is_quick DESC, quick_order, active_status DESC, project_name""",
            (_NETWORK_ID,),
        ) or []
    except Exception as e:  # noqa: BLE001
        log.warning("projects query failed: %s", e)
        rows = []

    logo_map: dict[str, str] = {
        str(r.get("project_id") or ""): str(r.get("project_logo") or "") for r in rows
    }

    # [2026-11-04 Feng] change "agent count" to "project members", and list the members,
    #   with the project admin first (separate colour). So what is returned is a **member breakdown**, not a number.
    #   ★ Query the whole table once and group by project_id: per-project queries become N+1 (dozens of projects on the list page = dozens of round trips).
    members_by_project: dict[str, list[dict]] = {}
    agent_by_id: dict[str, dict] = {}
    try:
        for a in kernel.query(
            """SELECT agent_id, agent_name, agent_role, project_id
                 FROM agx_agent_registry
                WHERE active_status = 1
                ORDER BY agent_name"""
        ) or []:
            aid = str(a.get("agent_id") or "")
            pid = str(a.get("project_id") or "")
            if not aid:
                continue
            row = {
                "id": aid,
                "name": str(a.get("agent_name") or aid),
                "role": str(a.get("agent_role") or "worker"),
            }
            agent_by_id[aid] = row
            if pid:
                members_by_project.setdefault(pid, []).append(row)
    except Exception as e:  # noqa: BLE001
        log.warning("projects members query failed: %s", e)

    # Real agent-count aggregation (direct MySQL, grouped by project_id, counting active_status=1)
    agent_cnt: dict[str, int] = {}
    try:
        cnt_rows = kernel.query(
            "SELECT project_id, COUNT(*) AS c FROM agx_agent_registry "
            "WHERE active_status = 1 GROUP BY project_id"
        )
        agent_cnt = {r["project_id"]: int(r["c"] or 0) for r in (cnt_rows or [])}
    except Exception as e:  # noqa: BLE001
        log.warning("projects agent-count query failed: %s", e)

    return [{
        "id": r.get("project_id") or "",
        "name": r.get("project_name") or r.get("project_id") or "",
        "abbr": r.get("project_abbr") or "",
        "platform": r.get("platform") or "openclaw",
        "status": _PROJECT_STATUS.get(int(r.get("active_status") or 0), "archived"),
        "agentCount": agent_cnt.get(r.get("project_id") or "", 0),
        "adminAgentId": r.get("admin_agent_id") or "",
        # Member breakdown (admin first). If the admin is not among the project's active members, add them anyway --
        # otherwise the "admin" slot shows a name not present in the list, which reads as a data bug.
        "members": _members_with_admin(
            members_by_project.get(r.get("project_id") or "", []),
            r.get("admin_agent_id") or "",
            agent_by_id,
        ),
        "logoUrl": logo_map.get(r.get("project_id") or "", ""),
        # [2026-11-17 15:07 Feng] "In every project dropdown, the pinned projects should be at the top...
        #   and see if a star can be added." -> the table already has is_quick / quick_order (originally for
        #   "quick projects", never exposed), so expose them now for pin-to-top ordering + star in every dropdown.
        #   ★ Sort in the SQL too: the frontend receives them already pinned first,
        #     so older pages that do not use this field still show pinned projects first.
        "isQuick": int(r.get("is_quick") or 0),
        "quickOrder": int(r.get("quick_order") or 0),
        "groupId": "",
        # [2026-11-04 Feng] "Drop the update time from the project table, use create_at instead" --
        #   `agx_project` has no updated column; the previous version stuffed in an empty string -> a whole column of fake data.
        #   Now uses the real created_at, formatted by _dt as 'YYYY-MM-DD HH:MM', same as every other entity.
        "createdAt": _dt(r.get("created_at")),
        "updatedAt": "",
    } for r in rows]


PROJECT_EDITABLE = ("project_name", "project_abbr", "project_logo", "x_desc", "source_dir")
# Editable host fields (aligned with the kernel's agx_update_host _UPDATEABLE_FIELDS['host'])
HOST_EDITABLE = (
    "host_alias", "endpoint_ip", "ssh_port", "os_platform",
    "platforms",
    "task_slots_limit", "active_task_count", "active_status",
    "presence_status", "x_desc",
)

# [2026-10-30 Feng] console edits are always submitted to the kernel as the core agent.
#   agx_core = workshop_manager (L0-L7), the only identity that covers write permission on every entity;
#   and it is not a "person", so the audit trail never renames a human into some business agent.
# ★ 2026-11-11: the core agent now carries the agx_ prefix; the old name is fully retired (no compatibility layer).
#   To use a different submit identity, just override AGX_EDITOR_AGENT_ID.
EDITOR_AGENT_ID = (os.environ.get("AGX_EDITOR_AGENT_ID") or "agx_core").strip()


def project_detail(project_id: str) -> dict | None:
    """Project detail (includes heavy fields the list page does not expose, such as x_desc / source_dir).

    [2026-10-26 Feng] the list cannot show details -> a separate detail-read endpoint.
    Direct MySQL (same as plans/skills, avoiding silent failures of high-privilege MCP tools).
"""
    try:
        rows = kernel.query(
            """SELECT project_id, project_name, project_abbr, project_logo, platform,
                      x_desc, source_dir, admin_agent_id, active_status, created_at
                 FROM agx_project
                WHERE project_id = %s""",
            (project_id,),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("realdata project_detail query failed: %s", e)
        return None
    if not rows:
        return None
    r = rows[0]
    # Member / session counts (give the detail page a sense of real scale; do not just show "—")
    counts = {"agents": 0, "tasks": 0, "sessions": 0}
    try:
        for key, sql in (
            ("agents", "SELECT COUNT(*) AS c FROM agx_agent_registry "
                       "WHERE project_id = %s AND active_status = 1"),
            ("tasks", "SELECT COUNT(*) AS c FROM agx_task_base t JOIN agx_agent_registry a "
                      "ON a.agent_id = t.agent_id WHERE a.project_id = %s AND t.active_status = 1"),
            ("sessions", "SELECT COUNT(*) AS c FROM agx_session WHERE project_id = %s"),
        ):
            got = kernel.query(sql, (project_id,))
            counts[key] = int((got or [{}])[0].get("c") or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("realdata project_detail counts failed: %s", e)

    return {
        "id": _str(r.get("project_id")),
        "name": _str(r.get("project_name")),
        "abbr": _str(r.get("project_abbr")),
        "platform": _str(r.get("platform")) or "openclaw",
        "status": _PROJECT_STATUS.get(int(r.get("active_status") or 0), "archived"),
        "activeStatus": int(r.get("active_status") or 0),
        "adminAgentId": _str(r.get("admin_agent_id")),
        "logoUrl": _str(r.get("project_logo")),
        "xDesc": _str(r.get("x_desc")),
        "sourceDir": _str(r.get("source_dir")),
        "createdAt": _dt(r.get("created_at")),
        "counts": counts,
    }


def update_project(caller: str, project_id: str, updates: dict) -> dict:
    """Update a project -- goes through MCP `agx_update_project` (★ never writes the DB directly).

    [2026-10-26 Feng] "Edits must call the MCP interface" -- the kernel is the single
    authoritative write path: its field grading (operational fields L5 / active_status L4)
    and ownership checks all live in the kernel; a second copy in the BFF would inevitably
    drift. So this only normalises arguments and parses results; it does not duplicate
    business rules.

    Raises:
        ToolRejected: explicitly rejected by the kernel (insufficient permission /
            ownership mismatch / no valid fields).
        RuntimeError: the channel is unavailable.
"""
    allowed = {k: v for k, v in (updates or {}).items() if k in PROJECT_EDITABLE}
    if not allowed:
        raise ToolRejected("NO_VALID_FIELDS", "没有可更新的字段（仅支持：项目名/简称/Logo/说明书/源码目录）")

    raw = kernel.call_tool(
        "agx_update_project",
        caller_agent_id=caller,
        project_id=project_id,
        updates=allowed,
    )

    parsed: dict = {}
    if isinstance(raw, str):
        try:
            maybe = json.loads(raw)
            parsed = maybe if isinstance(maybe, dict) else {}
        except Exception:  # noqa: BLE001
            parsed = {}
    elif isinstance(raw, dict):
        parsed = raw

    if parsed.get("error") or parsed.get("permission_denied"):
        # The kernel rejects in two shapes: {"error":..., "denied_reason":...} (ownership)
        #                      / {"error":..., "permission_denied":true} (level)
        denied = bool(parsed.get("denied_reason") or parsed.get("permission_denied"))
        code = "PERMISSION_DENIED" if denied else "MCP_REJECTED"
        msg = str(parsed.get("error") or "内核拒绝该操作")
        raise ToolRejected(code, msg, parsed)
    text = raw if isinstance(raw, str) else str(raw)
    if text.startswith("Error:"):
        raise ToolRejected("MCP_ERROR", text[len("Error:"):].strip() or text)
    return {"ok": True, "projectId": project_id, "fields": sorted(allowed), "message": text}


# An exemption only means something for roles that cannot call the dispatch tools at all.
# ★ The test is "does this role need it", not "does this role deserve it":
#   L5 roles (project_manager / host_manager / workshop_manager / agent_manager)
#   can already dispatch, so granting them an exemption is a **no-op** -- yet the UI would show "dispatch allowed",
#   telling the user "I just did something useful". A button that changes nothing is worse than no button.
_EXEMPT_ROLES = frozenset({"worker"})


def can_hold_dispatch_exempt(role: Any) -> bool:
    """Whether a role needs a "temporary dispatch exemption".

    ★ The single source of truth. The BFF's write checks, the frontend's show/hide logic
      and the script assertions all read it; no place may write a second role check --
      two checks means two chances to drift.

    worker's role levels are {0,1,2,3} while the three dispatch tools are L5 -> this gate
    is genuinely needed. Every other role is already L5 (or higher), so an exemption
    changes no permission for them.
"""
    return str(role or "").strip().lower() in _EXEMPT_ROLES


def _agent_role(agent_id: str) -> str | None:
    """Look up an agent's role; None when absent (used by pre-write guards).

    Direct MySQL rather than MCP: this is the BFF's own guard and should not cost an SSE
    round trip just to read one role.
"""
    rows = kernel.query("SELECT agent_role FROM agx_agent_registry WHERE agent_id = %s LIMIT 1", (agent_id,))
    if not rows:
        return None
    return str(rows[0].get("agent_role") or "")


def exempt_state(raw: Any, now: datetime | None = None, role: Any = None) -> dict:
    """Turn the raw `dispatch_exempt_until` -> the three-state field the frontend needs + applicability.

    **The rule is `until > now`, not `until IS NOT NULL`** (the easiest thing to get wrong here).
    Decided once, here in realdata; the frontend only renders. The frontend no longer
    compares timestamps itself, otherwise a server/browser clock mismatch produces
    "the list says exempt, the badge says expired".

    `role` is threaded in to attach `dispatchExemptApplicable`: absent means True
    (only call sites that do not care about the role may omit it; agents() always passes it).
"""
    now = now or datetime.now()
    applicable = True if role is None else can_hold_dispatch_exempt(role)
    if raw in (None, ""):
        return {
            "dispatchExemptUntil": "",
            "dispatchExemptActive": False,
            "dispatchExemptRemainingSeconds": 0,
            "dispatchExemptApplicable": applicable,
        }

    # Normally a datetime; if it is a string (some drivers / cache-miss paths) parse it MySQL-style
    until: datetime | None = raw if isinstance(raw, datetime) else None
    if until is None:
        with contextlib.suppress(Exception):
            until = datetime.strptime(str(raw)[:19], "%Y-%m-%d %H:%M:%S")
    if until is None:
        # Unparsable: treat it honestly as "has a value but not decidable", never pretend there is no exemption
        return {
            "dispatchExemptUntil": str(raw),
            "dispatchExemptActive": False,
            "dispatchExemptRemainingSeconds": 0,
            "dispatchExemptApplicable": applicable,
        }

    remaining = int((until - now).total_seconds())
    return {
        "dispatchExemptUntil": until.strftime("%Y-%m-%d %H:%M:%S"),
        "dispatchExemptActive": remaining > 0,
        # Negative when expired (the frontend uses that to tell "expired" from "never opened"); do not clamp to 0
        "dispatchExemptRemainingSeconds": remaining,
        "dispatchExemptApplicable": applicable,
    }


def _fmt_duration(seconds: int) -> str:
    """Seconds -> "2 hours" / "45 minutes" / "30 seconds". A human-readable duration; second-level precision is not wanted."""
    if seconds <= 0:
        return "已到期"
    if seconds >= 3600:
        h, m = divmod(seconds // 60, 60)
        return f"{h} 小时" + (f" {m} 分钟" if m else "")
    if seconds >= 60:
        return f"{seconds // 60} 分钟"
    return f"{seconds} 秒"


def exempt_notice(
    agent_id: str,
    *,
    until: str,
    remaining_seconds: int,
    cleared: bool = False,
    actor: str = "",
    prev_until: str = "",
) -> str:
    """Body of the notice sent to **the agent's own main session** when a dispatch exemption changes.

    [2026-11-01 Feng] "When granting it an exemption, send it a message in its own name,
    to its main session, telling it that it can now message others and for how long."
    The reason is simple: if the permission changes and it does not know, it will never use
    it -- granting is the same as not granting.

    Three things must be spelled out (omit one and it has to guess):
      1. whether it can dispatch right now;
      2. until when (an absolute instant, not "in two hours" -- delivery can be delayed);
      3. it expires automatically and it need do nothing (otherwise it will try to turn it
         off, or ask how to).

    ★ Also handle "already expired by the time it is read": messages queue up, so an
      offline agent may read this hours later. Writing only "you have 2 hours" sends it off
      to dispatch with an already-dead grant, then it gets PERMISSION_DENIED and has no idea
      why. So state explicitly: "the time in this message is authoritative".
"""
    who = f"（操作者 {actor}）" if actor else ""
    if cleared:
        # ★ Why prev_until instead of until (hit for real on 2026-11-01):
        #   when the kernel clears it, the column becomes NULL and reading it back gives nothing -- at that moment
        #   "when was it supposed to expire" has vanished from the DB, though it was a fact a millisecond ago.
        #   Saying only "not recorded" turns a known fact into an unknown, and the reader concludes it was never set.
        if prev_until:
            # Distinguish "closed early" from "closed because it had expired":
            # calling the latter "closed early" is wrong and makes it think someone revoked the grant midway.
            had_passed = False
            with contextlib.suppress(ValueError):
                had_passed = datetime.strptime(prev_until, "%Y-%m-%d %H:%M:%S") <= datetime.now()
            tail = "已到期关闭" if had_passed else "已提前关闭"
            line = f"原定到期时间是 {prev_until}（{tail}）。"
        else:
            line = "原定到期时间：没有记录（开启时就没读到，可能从来没设过）。"
        return (
            "【调度授权已关闭】\n"
            f"你现在不能再给其他 Agent 派活了{who}。\n"
            f"{line}\n"
            "这段时间里如果收到 PERMISSION_DENIED，是正常的，不是你的操作有问题。\n"
            "不需要回复这条消息。"
        )
    return (
        "【调度授权已开启】\n"
        f"你现在可以给其他 Agent 派活了{who}。\n"
        f"授权到期：{until}（约 {_fmt_duration(remaining_seconds)}）\n"
        "在此期间可以调用 agx_dispatch_task / agx_schedule_task / agx_quick_cmd_send 给其他 Agent 派任务。\n"
        "到期会自动失效，不需要你做任何操作，也不用回复这条消息。\n"
        "\n"
        "（这条消息是排队投递的：如果你读到它时已经过了上面的时间，授权已经失效，"
        "以那个时间为准，不要按「还剩多久」去估算。）"
    )


def notify_exempt(
    agent_id: str,
    *,
    until: str,
    remaining_seconds: int,
    cleared: bool = False,
    actor: str = "",
    prev_until: str = "",
) -> dict:
    """Deliver the exemption-change notice into the agent's **main session** (in its own name).

    Why in its own name (caller = that agent):
      `agx_send_text`'s guard is "you may only send to yourself", and the semantics of this
      message are "the system telling it something about itself" -- only delivery under its
      own identity lands in its main session. Sending as agx_core would be blocked by the
      guard (the session does not belong to the caller).

    ★ A failure here **does not affect** the fact that the exemption is already in force:
      the DB write already succeeded. So report the outcome faithfully (notified /
      notifyError) and let the UI show "dispatch allowed, but the notice was not
      delivered (reason)" -- never roll back the permission because the notice failed, and
      never call the whole operation a failure for that reason.
"""
    text = exempt_notice(
        agent_id,
        until=until,
        remaining_seconds=remaining_seconds,
        cleared=cleared,
        actor=actor,
        prev_until=prev_until,
    )
    session_key = f"agent:{agent_id}:main"
    try:
        raw = kernel.call_tool(
            "agx_send_text",
            caller_agent_id=agent_id,
            session_key=session_key,
            text=text,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("exempt notify failed agent=%s: %s", agent_id, e)
        return {"notified": False, "notifyError": f"发送失败：{e}", "notifyText": text}

    # The kernel may return an error string instead of raising -- still counts as not delivered, not as success
    parsed: dict = {}
    if isinstance(raw, dict):
        parsed = raw
    elif isinstance(raw, str):
        text_raw = raw.strip()
        if text_raw.startswith("Error:") or "error" in text_raw.lower()[:80]:
            log.warning("exempt notify rejected agent=%s: %s", agent_id, text_raw[:160])
            return {"notified": False, "notifyError": text_raw[:200], "notifyText": text}
        with contextlib.suppress(Exception):
            maybe = json.loads(text_raw)
            parsed = maybe if isinstance(maybe, dict) else {}

    if parsed.get("error") or parsed.get("status") in ("error", "denied"):
        msg = str(parsed.get("error") or parsed.get("message") or "内核拒绝发送")
        log.warning("exempt notify rejected agent=%s: %s", agent_id, msg)
        return {"notified": False, "notifyError": msg, "notifyText": text}

    # Trace successes too: "was the notice actually delivered" must be independently verifiable from the log,
    # not just trusted from the `true` the interface returns (written but undelivered is the classic failure here).
    log.info("exempt notify sent agent=%s session=%s len=%d", agent_id, session_key, len(text))
    return {"notified": True, "notifyError": "", "notifyText": text, "notifySession": session_key}


def set_dispatch_exempt(
    agent_id: str,
    minutes: int = 0,
    until: str = "",
    reason: str = "",
    caller: str = EDITOR_AGENT_ID,
    actor: str = "",
) -> dict:
    """Set / clear an agent's temporary dispatch exemption -- goes through MCP `agx_set_dispatch_exempt`.

    [2026-10-31 Feng] Same convention as `update_host`:
    console operations are always submitted to the kernel as `agx_core` (EDITOR_AGENT_ID),
    and the BFF does not duplicate the "who may grant" decision -- the three permission
    tiers (super role / project admin / deny self-escalation) all live in the kernel. Here
    we only normalise arguments, parse the result and hand the rejection reason back to the
    frontend verbatim.

    ★ We do not check "minutes xor until" or "until must be in the future" ourselves:
      those are the kernel's behavioural contract; a second copy in the BFF would inevitably
      drift (and which one wins when they disagree?). When the kernel returns status=error
      we raise together with its own message.

    Raises:
        ToolRejected: rejected by the kernel (denied / error); message is the kernel's own text.
        RuntimeError: the MCP channel is unavailable.
"""
    # ── applicability guard (2026-10-31 Feng: "do not give it to those who can already send messages") ──────
    # PM / host_manager / workshop_manager / agent_manager already have L5, so for them an exemption
    # is a no-op. Writing this column for them makes the UI show "dispatch allowed" while the actual permission
    # is unchanged -- a fake action that "did something and changed nothing", more misleading than no button.
    #
    # ★ Only block "set", never block "clear": invalid values already written historically must remain clearable,
    #   otherwise blocking turns them into permanently unremovable dirty data.
    wants_change = bool(until) or int(minutes or 0) > 0
    if wants_change:
        role = _agent_role(agent_id)
        if role is None:
            raise ToolRejected("NOT_FOUND", f"Agent {agent_id} 不存在")
        if not can_hold_dispatch_exempt(role):
            raise ToolRejected(
                "DISPATCH_EXEMPT_NOT_APPLICABLE",
                f"{agent_id} 的角色是 {role}，本身即可派活（L5），不需要调度豁免。"
                "豁免只对 worker 有意义。",
            )

    # ── record "the originally scheduled expiry" before clearing (2026-11-01) ────────────────────
    # Clearing in the kernel = writing NULL, so reading back gives no old value. But the notice body must state
    # "when it was supposed to expire", so it has to be read before the write. A read failure does not block
    # the main flow (a grant that should be cleared still gets cleared); the notice just says "not recorded".
    prev_until = ""
    prev_known = False          # Read succeeded (True with empty prev_until = definitely was not set)
    if not wants_change:
        with contextlib.suppress(Exception):
            rows = kernel.query(
                "SELECT dispatch_exempt_until FROM agx_agent_registry WHERE agent_id = %s",
                (agent_id,),
            )
            prev_known = True
            if rows and rows[0].get("dispatch_exempt_until"):
                prev_until = rows[0]["dispatch_exempt_until"].strftime("%Y-%m-%d %H:%M:%S")

    raw = kernel.call_tool(
        "agx_set_dispatch_exempt",
        caller_agent_id=caller,
        agent_id=agent_id,
        minutes=int(minutes or 0),
        until=str(until or ""),
        reason=str(reason or ""),
    )

    parsed: dict = {}
    if isinstance(raw, str):
        with contextlib.suppress(Exception):
            maybe = json.loads(raw)
            parsed = maybe if isinstance(maybe, dict) else {}
    elif isinstance(raw, dict):
        parsed = raw

    status = str(parsed.get("status") or "")
    if status == "denied":
        raise ToolRejected("PERMISSION_DENIED", str(parsed.get("message") or "内核拒绝该操作"), parsed)
    if status == "error":
        raise ToolRejected("MCP_REJECTED", str(parsed.get("message") or "内核拒绝该操作"), parsed)
    if status != "success":
        text = raw if isinstance(raw, str) else str(raw)
        raise ToolRejected("MCP_ERROR", text[len("Error:"):].strip() if text.startswith("Error:") else text)

    # ── notify the agent itself (2026-11-01 Feng) ──────────────────────────────
    # Only send when something actually changed:
    #   · effectiveNow  -> the grant is in force now; it must know it can dispatch
    #   · cleared       -> the grant was closed early; it must know to stop trying
    #   · neither (written but reads back as not in force) -> do not send: never announce something that did not happen
    until_s = str(parsed.get("until") or "")
    remaining = int(parsed.get("remaining_seconds") or 0)
    cleared = bool(parsed.get("cleared"))
    effective = bool(parsed.get("effective_now"))
    # ── on close: only notify when the capability was genuinely revoked (2026-11-01) ──────────
    # The kernel's `cleared` only means "this request was a clear", not "something was actually cleared":
    # clearing a column that was already NULL still returns cleared=True.
    # That would send the agent a "you can no longer dispatch" message -- which it never could.
    # Announcing something that did not happen is pure noise (hit for real: the acceptance script cleared once more at the end and produced a phantom message).
    #
    # The test uses the previous state: had a value and had not expired -> the capability was really revoked -> notify;
    #                  never set / already expired naturally -> no change -> do not send;
    #                  read failed (unknown) -> send; better one extra line than miss a real change.
    clear_notice = False
    if cleared:
        had_live = False
        if prev_until:
            with contextlib.suppress(ValueError):
                had_live = datetime.strptime(prev_until, "%Y-%m-%d %H:%M:%S") > datetime.now()
        clear_notice = had_live or (not prev_known)

    notice: dict = {"notified": False, "notifyError": "", "notifyText": ""}
    if effective or clear_notice:
        notice = notify_exempt(
            agent_id,
            until=until_s,
            remaining_seconds=remaining,
            cleared=cleared,
            actor=actor,
            prev_until=prev_until,
        )

    # Success: convert the kernel fields into the frontend shape (kernel payload kept as-is for troubleshooting)
    return {
        "ok": True,
        "agentId": str(parsed.get("agent_id") or agent_id),
        "agentRole": str(parsed.get("agent_role") or ""),
        "until": until_s,
        "remainingSeconds": remaining,
        "cleared": cleared,
        "effectiveNow": effective,
        "notified": bool(notice.get("notified")),
        "notifyError": str(notice.get("notifyError") or ""),
        "notifySession": str(notice.get("notifySession") or ""),
        "permission": str(parsed.get("permission") or ""),
        "writtenBy": str(parsed.get("written_by") or caller),
        "reason": str(parsed.get("reason") or ""),
        "scope": str(parsed.get("scope") or ""),
        "kernel": parsed,
    }


# Editable agent fields.
# ★ 2026-11-07 Feng: "Make every field editable for now; I will tell you later which ones must not be."
#   So this lists **the full set the kernel's agx_update_agent supports**, with no one-sided narrowing on the frontend --
#   which layer may change what, and for whom, is decided by the kernel's field grading (operational fields editable by the PM /
#   contract fields L4 / activation fields editable by the PM for this project's workers). A copy of those rules in the BFF would just be a second truth.
AGENT_EDITABLE = (
    "agent_name", "x_desc", "agent_role", "host_id", "system_prompt",
    "active_status", "mcp_config", "skills_allowlist",
    "agent_email", "agent_email_config", "agent_telegram_token", "agent_feishu_token",
)

_AGENT_ROLES = frozenset({
    "workshop_manager", "host_manager", "project_manager", "agent_manager", "worker",
})


def update_agent(agent_id: str, updates: dict, caller: str = EDITOR_AGENT_ID) -> dict:
    """Update an agent -- goes through MCP `agx_update_agent` (★ never writes the DB directly).

    [2026-11-07 Feng] "Edits must go through a single MCP method" -- the kernel is the single
    authoritative write path: field grading (operational / activation / contract), ownership
    checks, Redis dual-write and change events all live in the kernel. The BFF does exactly
    three things: whitelist filtering, basic shape validation, and turning the kernel's
    rejection into the matching HTTP response.

    Raises:
        ToolRejected: explicitly rejected by the kernel (permission / ownership / no valid
            fields / does not exist).
        RuntimeError: the MCP channel is unavailable.
"""
    allowed: dict = {k: v for k, v in (updates or {}).items() if k in AGENT_EDITABLE and v is not None}
    if not allowed:
        raise ToolRejected(
            "NO_VALID_FIELDS",
            "没有可更新的字段（Agent 支持：名称/说明书/角色/主机/系统提示词/启停/技能白名单/MCP 配置/邮箱与渠道令牌）",
        )

    # Shape validation lives in the BFF: given active_status="abc" the kernel would either write it or raise 500,
    # and neither should reach the user. **Validate shape only, never permission** (permission belongs to the kernel).
    if "agent_name" in allowed:
        name = str(allowed["agent_name"]).strip()
        if not name:
            raise ToolRejected("INVALID_FIELD", "Agent 名称不能为空")
        allowed["agent_name"] = name
    if "agent_role" in allowed and str(allowed["agent_role"]) not in _AGENT_ROLES:
        raise ToolRejected(
            "INVALID_FIELD",
            f"角色非法：{allowed['agent_role']}（可选：{'/'.join(sorted(_AGENT_ROLES))}）",
        )
    if "active_status" in allowed:
        v = str(allowed["active_status"]).strip()
        if v not in {"0", "1"}:
            raise ToolRejected("INVALID_FIELD", "active_status 只能是 0（停用）或 1（启用）")
        allowed["active_status"] = v
    if "host_id" in allowed:
        hid = str(allowed["host_id"]).strip()
        if not hid:
            raise ToolRejected("INVALID_FIELD", "host_id 不能为空（Agent 必须有归属主机）")
        allowed["host_id"] = hid

    # Confirm existence first: a kernel UPDATE affecting 0 rows does not raise, so without this check "edited a nonexistent id" looks like success.
    try:
        exists = kernel.query(
            "SELECT agent_id FROM agx_agent_registry WHERE agent_id = %s AND network_id = %s",
            (agent_id, _NETWORK_ID),
        )
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Agent 查询失败：{e}") from e
    if not exists:
        raise ToolRejected("NOT_FOUND", f"Agent 不存在：{agent_id}")

    raw = kernel.call_tool(
        "agx_update_agent",
        caller_agent_id=caller,
        agent_id=agent_id,
        updates=allowed,
    )

    parsed: dict = {}
    if isinstance(raw, str):
        try:
            maybe = json.loads(raw)
            parsed = maybe if isinstance(maybe, dict) else {}
        except Exception:  # noqa: BLE001
            parsed = {}
    elif isinstance(raw, dict):
        parsed = raw

    if parsed.get("error") or parsed.get("permission_denied"):
        denied = bool(parsed.get("denied_reason") or parsed.get("permission_denied"))
        code = "PERMISSION_DENIED" if denied else "MCP_REJECTED"
        raise ToolRejected(code, str(parsed.get("error") or "内核拒绝该操作"), parsed)
    text = raw if isinstance(raw, str) else str(raw)
    if text.startswith("Error:"):
        raise ToolRejected("MCP_ERROR", text[len("Error:"):].strip() or text)
    return {"ok": True, "agentId": agent_id, "fields": sorted(allowed), "message": text}


def update_host(host_id: str, updates: dict, caller: str = EDITOR_AGENT_ID) -> dict:
    """Update a host -- goes through MCP `agx_update_host` (★ never writes the DB directly).

    [2026-10-30 Feng] console edits are always submitted to the kernel as `agx_core`:
    the kernel's level check (agx_update_host needs L6), field whitelist, Redis dual-write and
    change events all live in the kernel. The BFF only normalises arguments and parses the
    result; it does not duplicate business rules.

    Raises:
        ToolRejected: explicitly rejected by the kernel (permission / no valid fields / host
            does not exist).
        RuntimeError: the MCP channel is unavailable.
"""
    allowed = {k: v for k, v in (updates or {}).items() if k in HOST_EDITABLE and v is not None}
    if not allowed:
        raise ToolRejected("NO_VALID_FIELDS", "没有可更新的字段（主机仅支持：别名/IP/SSH端口/系统/平台地址/任务槽/描述）")

    # Confirm the host exists first: a kernel UPDATE affecting 0 rows does not raise, so without this check "edited a nonexistent id" looks like success.
    try:
        exists = kernel.query(
            "SELECT host_id FROM agx_host_inventory WHERE host_id = %s AND network_id = %s",
            (host_id, _NETWORK_ID),
        )
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"主机查询失败：{e}") from e
    if not exists:
        raise ToolRejected("NOT_FOUND", f"主机不存在：{host_id}")

    raw = kernel.call_tool(
        "agx_update_host",
        caller_agent_id=caller,
        host_id=host_id,
        updates=allowed,
    )

    parsed: dict = {}
    if isinstance(raw, str):
        with contextlib.suppress(Exception):
            maybe = json.loads(raw)
            parsed = maybe if isinstance(maybe, dict) else {}
    elif isinstance(raw, dict):
        parsed = raw

    if parsed.get("error") or parsed.get("permission_denied"):
        denied = bool(parsed.get("denied_reason") or parsed.get("permission_denied"))
        code = "PERMISSION_DENIED" if denied else "MCP_REJECTED"
        raise ToolRejected(code, str(parsed.get("error") or "内核拒绝该操作"), parsed)

    text = raw if isinstance(raw, str) else str(raw)
    err = _kernel_error_text(text)
    if err is not None:
        raise ToolRejected("MCP_ERROR", err)
    return {"ok": True, "hostId": host_id, "fields": sorted(allowed), "message": text}


# ── edit capability (2026-11-01 Feng: "You must do the editing; you did none of the other edits") ──────
# One convention: the BFF only normalises arguments and returns the kernel's own words to the frontend; it does not copy field-permission rules.
# So "who may change which field" always has exactly one answer, in the kernel -- two copies would inevitably drift.

#: Editable task-template fields (= the whitelist of the kernel's agx_update_task)
TASK_EDITABLE = {"x_desc", "target_object", "payload", "priority", "is_private", "active_status"}

#: Editable execution fields (= the ones all four identity kinds may write in the kernel's _check_execution_field_policy)
EXEC_EDITABLE = {"exec_name", "timeout_seconds", "exec_instruction", "alert_note"}

#: Editable skill fields (= the inputs of agx_skill_contribute except skill_id)
SKILL_EDITABLE = {"skill_name", "category", "description", "tags", "content"}


def _kernel_error_text(text: str) -> str | None:
    """The **three wrappers** a kernel error can arrive in -- none may be missed.

    [hit for real on 2026-11-01] The MCP framework wraps an in-tool exception as
    `Error calling tool 'agx_update_execution': timeout_seconds out of the allowed range [60, 86400] seconds, received 30.`
    -- it neither starts with `Error:` nor is JSON, so it was treated as "success":
    the API returned 200, the UI popped "saved", **and not one byte in the DB changed**.

    This is the worst kind of failure: not an error, but a lie. All three wrappers are detected:
      · "Error:"                                  <- assembled by the kernel itself
      · "Error calling tool 'xxx': ..."           <- one layer added by the MCP framework
      · any plain text starting with Error
"""
    if not text:
        return None
    head = text.lstrip()
    if head[:5].lower() != "error":
        return None
    # Take the body after the colon; without a colon return the whole string
    _, sep, tail = head.partition(":")
    return (tail.strip() or head.strip()) if sep else head.strip()


def _call_and_parse(tool: str, *, code_on_reject: str = "MCP_REJECTED", **kwargs) -> dict:
    """Call a kernel write interface once, normalising "rejected" into ToolRejected and "success" into a dict.

    Why normalise: the three edit interfaces return inconsistent shapes (some JSON, some a
    plain "Error: ..." string, some natural language). The frontend should not implement a
    separate detector per entity.
"""
    raw = kernel.call_tool(tool, **kwargs)
    parsed: dict = {}
    if isinstance(raw, str):
        with contextlib.suppress(Exception):
            maybe = json.loads(raw)
            parsed = maybe if isinstance(maybe, dict) else {}
    elif isinstance(raw, dict):
        parsed = raw

    if parsed.get("error") or parsed.get("permission_denied") or parsed.get("status") == "error":
        denied = bool(parsed.get("denied_reason") or parsed.get("permission_denied"))
        raise ToolRejected(
            "PERMISSION_DENIED" if denied else code_on_reject,
            str(parsed.get("message") or parsed.get("error") or "内核拒绝该操作"),
            parsed,
        )
    text = raw if isinstance(raw, str) else str(raw)
    err = _kernel_error_text(text)
    if err is not None:
        raise ToolRejected(code_on_reject, err, parsed)
    return {"parsed": parsed, "text": text}


def update_task(task_id: str, updates: dict, caller: str = EDITOR_AGENT_ID) -> dict:
    """Edit a task template -- goes through the kernel's `agx_update_task`.

    [2026-11-01 Feng] clicking a list row opens the detail, so rows no longer need a "detail"
    button; it becomes "edit": detail by clicking the row, editing needs its own entry point.

    Editable: description / priority / payload / private / activation / target object.
    run_status is a protected field (that belongs to executions); the kernel rejects it and
    we do not intercept here.
"""
    allowed = {k: v for k, v in (updates or {}).items() if k in TASK_EDITABLE and v is not None}
    if not allowed:
        raise ToolRejected(
            "NO_VALID_FIELDS",
            "没有可更新的字段（任务支持：说明书/优先级/载荷/私有/启停/目标对象）",
        )

    # The frontend supplies payload as JSON text: parse it here so malformed JSON is not written into the DB as a plain string
    if "payload" in allowed:
        raw_payload = allowed["payload"]
        if isinstance(raw_payload, str):
            if not raw_payload.strip():
                allowed["payload"] = {}
            else:
                try:
                    parsed_payload = json.loads(raw_payload)
                except json.JSONDecodeError as e:
                    raise ToolRejected("BAD_PAYLOAD", f"载荷不是合法 JSON：{e}") from e
                if not isinstance(parsed_payload, dict):
                    raise ToolRejected("BAD_PAYLOAD", "载荷必须是 JSON 对象（{...}）")
                allowed["payload"] = parsed_payload

    rows = kernel.query("SELECT task_id FROM agx_task_base WHERE task_id = %s", (task_id,))
    if not rows:
        raise ToolRejected("NOT_FOUND", f"任务不存在：{task_id}")

    res = _call_and_parse(
        "agx_update_task", caller_agent_id=caller, task_id=task_id, updates=allowed
    )
    detail = task_detail(task_id) or {}
    return {"ok": True, "taskId": task_id, "fields": sorted(allowed), "task": detail,
            "message": res["text"]}


def update_execution(run_uuid: str, updates: dict, caller: str = EDITOR_AGENT_ID) -> dict:
    """Edit an execution -- goes through the kernel's `agx_update_execution`.

    Only fields all four identity kinds may write (title / timeout / instruction / alert note).
    **run_status is not included**: it is a protected field and changing it amounts to forging
    an execution result. reply_read is also excluded: acknowledgement has its own method
    (agx_ack_receipt) and bypassing it breaks the closed-loop statistics.
"""
    allowed = {k: v for k, v in (updates or {}).items() if k in EXEC_EDITABLE and v is not None}
    if not allowed:
        raise ToolRejected(
            "NO_VALID_FIELDS",
            "没有可更新的字段（执行记录支持：标题/超时/执行指令/告警备注）",
        )

    if "timeout_seconds" in allowed:
        try:
            allowed["timeout_seconds"] = int(allowed["timeout_seconds"])
        except (TypeError, ValueError) as e:
            raise ToolRejected("BAD_TIMEOUT", f"超时必须是整数秒：{allowed['timeout_seconds']!r}") from e

    rows = kernel.query(
        "SELECT task_run_uuid FROM agx_task_execution WHERE task_run_uuid = %s", (run_uuid,)
    )
    if not rows:
        raise ToolRejected("NOT_FOUND", f"执行记录不存在：{run_uuid}")

    res = _call_and_parse(
        "agx_update_execution", caller_agent_id=caller, task_run_uuid=run_uuid, updates=allowed
    )
    return {"ok": True, "runUuid": run_uuid, "fields": sorted(allowed), "message": res["text"]}


def update_skill(skill_id: str, updates: dict, caller: str = EDITOR_AGENT_ID) -> dict:
    """Edit a skill -- goes through the kernel's `agx_skill_contribute` (it is an upsert itself; passing skill_id updates).

    Why not raw SQL: the skill library has signal fields such as use_count / helpful_count,
    plus network ownership and cache sync, all inside the kernel. Writing directly would
    bypass them.

    ★ The kernel requires skill_name + content, so even "change only the category" must carry
      the existing values, otherwise it is treated as "create a new empty skill".
"""
    allowed = {k: v for k, v in (updates or {}).items() if k in SKILL_EDITABLE and v is not None}
    if not allowed:
        raise ToolRejected("NO_VALID_FIELDS", "没有可更新的字段（技能支持：名称/分类/说明/标签/正文）")

    rows = kernel.query(
        """SELECT skill_id, skill_name, category, description, tags, content, source_agent_id
             FROM agx_skill WHERE skill_id = %s""",
        (skill_id,),
    )
    if not rows:
        raise ToolRejected("NOT_FOUND", f"技能不存在：{skill_id}")
    cur = rows[0]
    contributor = _str(cur.get("source_agent_id"))

    merged = {
        "skill_name": _str(cur.get("skill_name")),
        "category": _str(cur.get("category")) or "other",
        "description": _str(cur.get("description")),
        "tags": _str(cur.get("tags")),
        "content": _str(cur.get("content")),
    }
    merged.update(allowed)

    if not merged["skill_name"].strip():
        raise ToolRejected("BAD_NAME", "技能名不能为空（内核按名字做幂等，空名会造出一条垃圾数据）")
    if not merged["content"].strip():
        raise ToolRejected("BAD_CONTENT", "技能正文不能为空")

    # ── the contributor must be preserved (hit for real on 2026-11-01) ────────────────────
    # The kernel's agx_skill_contribute UPDATE statement hardcodes `source_agent_id = caller`,
    # so "fix a sentence in the console" silently changes the skill's contributor from the original author to agx_core.
    # Measured: skill_ea42fd90d9e7's author changed from agx_ui_lead to agx_core after one edit.
    #
    # That falsifies a fact: A wrote the skill, and someone who fixed a typo should not become the author because of it.
    # In the console the author identity is user-facing (the list literally says "contributor"), so here we
    # **submit as the original author**, letting the kernel write the original value back unchanged; the real operator
    # is recorded by the BFF log, so the audit trail loses nothing.
    #
    # ★ This works around a kernel defect, it is not a long-term solution -- a notice has already been posted suggesting the kernel write
    #   source_agent_id only on INSERT. Once the kernel is fixed this block can be deleted (behaviour will be identical).
    submit_as = contributor or caller
    res = _call_and_parse(
        "agx_skill_contribute",
        caller_agent_id=submit_as,
        skill_id=skill_id,
        skill_name=merged["skill_name"],
        category=merged["category"],
        description=merged["description"],
        tags=merged["tags"],
        content=merged["content"],
    )
    log.info(
        "skill edit: skill=%s actor=%s submitted_as=%s (保贡献者) fields=%s",
        skill_id, caller, submit_as, sorted(allowed),
    )

    chk = kernel.query("SELECT source_agent_id FROM agx_skill WHERE skill_id = %s", (skill_id,))
    after = _str((chk or [{}])[0].get("source_agent_id"))
    return {
        "ok": True,
        "skillId": skill_id,
        "fields": sorted(allowed),
        "contributor": after,
        "message": res["text"],
    }


def task_detail(task_id: str) -> dict | None:
    """Task detail (includes the x_desc task description / payload / ownership, etc.).

    The list page only exposes description; the description itself (x_desc, possibly a
    thousand-word Markdown document) is read separately so the 24MB snapshot does not grow
    again.
"""
    try:
        rows = kernel.query(
            """SELECT task_id, task_name, task_labs, description, x_desc, agent_id,
                      payload, target_object, priority, is_private, active_status,
                      dedicated_session, task_type, reply_mode, task_logo,
                      created_at, update_dt
                 FROM agx_task_base
                WHERE task_id = %s""",
            (task_id,),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("realdata task_detail query failed: %s", e)
        return None
    if not rows:
        return None
    r = rows[0]
    agent_name = ""
    try:
        got = kernel.query("SELECT agent_name FROM agx_agent_registry WHERE agent_id = %s", (_str(r.get("agent_id")),))
        agent_name = _str((got or [{}])[0].get("agent_name"))
    except Exception as e:  # noqa: BLE001
        log.warning("realdata task_detail agent lookup failed: %s", e)

    payload = r.get("payload")
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", "replace")
    if isinstance(payload, str):
        with contextlib.suppress(Exception):
            payload = json.loads(payload)

    return {
        "id": _str(r.get("task_id")),
        "name": _str(r.get("task_name")),
        "labs": _str(r.get("task_labs")) or "general",
        "description": _str(r.get("description")),
        "xDesc": _str(r.get("x_desc")),
        "agentId": _str(r.get("agent_id")),
        "agentName": agent_name,
        "payload": payload,
        "targetObject": _str(r.get("target_object")),
        "priority": int(r.get("priority") or 0),
        "isPrivate": int(r.get("is_private") or 0),
        "activeStatus": int(r.get("active_status") or 0),
        "dedicatedSession": int(r.get("dedicated_session") or 0),
        "taskType": _str(r.get("task_type")) or "normal",
        "replyMode": _str(r.get("reply_mode")) or "result",
        "logoUrl": _str(r.get("task_logo")),
        "createdAt": _dt(r.get("created_at")),
        "updatedAt": _dt(r.get("update_dt") or r.get("created_at")),
    }


def _members_with_admin(
    members: list[dict], admin_id: str, agent_by_id: dict[str, dict]
) -> list[dict]:
    """Order the member list: admin first, the rest by name.

    Why sort on the backend:
      sorting on the frontend = sorting once per page (list / detail / dialog, three places),
      which will diverge sooner or later; and "admin first" is semantics (who is in charge),
      not a display preference of any one page.
"""
    admin_id = str(admin_id or "").strip()
    rest = [m for m in members if m.get("id") != admin_id]
    if admin_id:
        admin = dict(agent_by_id.get(admin_id) or {"id": admin_id, "name": admin_id})
        admin["isAdmin"] = True
        return [admin] + rest
    return rest


def project_groups(caller: str = "") -> list[dict]:
    """Project-group list -- talks to MySQL directly (same as projects; see the "project id" note above)."""
    try:
        rows = kernel.query(
            """SELECT group_id, group_name, x_desc, active_status
                 FROM agx_agent_group
                ORDER BY active_status DESC, group_name"""
        ) or []
    except Exception as e:  # noqa: BLE001
        log.warning("project_groups query failed: %s", e)
        rows = []

    # [2026-10-26 multi-host] A project group's "hosts" = the set of hosts its member agents live on.
    #   The frontend filter needs it (a group has no host column of its own; it can only be derived through members).
    #   While here, replace the hardcoded memberCount=0 with the real member count (available in the same SQL).
    member_cnt: dict[str, int] = {}
    group_hosts: dict[str, set[str]] = {}
    # [2026-11-04 Feng] A project group is a template; its members are template agents. List them right there
    #   so you need not open the detail to see "who is in this group". So the member breakdown (with roles) is returned too.
    #   ★ Must dedupe by (group_id, agent_id): agx_agent_group_member contains rows where the same
    #     agent joined a group more than once (measured: agx_host appears 3 times in one group) --
    #     without dedupe "6 members" while there are only 4 people, and the list prints the same person three times.
    group_members: dict[str, list[dict]] = {}
    seen_member: set[tuple[str, str]] = set()
    try:
        for r in kernel.query(
            """SELECT m.group_id, m.agent_id, a.host_id,
                      a.agent_name, a.agent_role
                 FROM agx_agent_group_member m
                 LEFT JOIN agx_agent_registry a ON a.agent_id = m.agent_id
                ORDER BY a.agent_name"""
        ) or []:
            gid = str(r.get("group_id") or "")
            aid = str(r.get("agent_id") or "")
            if not gid or not aid or (gid, aid) in seen_member:
                continue
            seen_member.add((gid, aid))
            member_cnt[gid] = member_cnt.get(gid, 0) + 1
            host = str(r.get("host_id") or "").strip()
            if host:
                group_hosts.setdefault(gid, set()).add(host)
            if aid:
                group_members.setdefault(gid, []).append({
                    "id": aid,
                    "name": str(r.get("agent_name") or aid),
                    "role": str(r.get("agent_role") or "worker"),
                })
    except Exception as e:  # noqa: BLE001
        log.warning("project_groups members query failed: %s", e)

    # ★ 2026-11-05 Feng: "there are six organisations now" -- measured, the member table has 7 group_ids
    #   while the main table agx_agent_group has only 3: `group_agentgeex_avionics / embedded / hr / ops`
    #   exist **only in the member table, with no row in the main table**.
    #   The old implementation treated the main table as the full set -> those 4 organisations were **entirely absent** from the project-group page:
    #   not visible, not clickable, and system status could not count them either.
    #   Fix: the full set is "member table ∪ main table"; when the main table lacks a row, derive a name from group_id and mark `orphan=1`
    #   (explicitly telling the frontend "this row has no master record", rather than quietly inventing something that looks normal).
    by_id: dict[str, dict] = {str(r.get("group_id") or ""): r for r in rows}
    for gid in member_cnt:
        if gid and gid not in by_id:
            by_id[gid] = {
                "group_id": gid,
                "group_name": "",
                "x_desc": "",
                "active_status": 1,
                "_orphan": True,
            }

    out = []
    for gid, r in by_id.items():
        name = str(r.get("group_name") or "").strip()
        orphan = bool(r.get("_orphan"))
        if not name:
            # From group_agentgeex_avionics -> "agentgeex avionics", at least recognisable
            name = gid.replace("group_", "").replace("_", " ").strip() or gid
        out.append({
            "id": gid,
            "name": name,
            "memberCount": member_cnt.get(gid, 0),
            "members": group_members.get(gid, []),
            "projectCount": 0,
            "status": "active" if int(r.get("active_status") or 0) == 1 else "archived",
            "xDesc": str(r.get("x_desc") or ""),
            "updatedAt": "",
            # Hosts the members live on (comma separated; the frontend filter matches on multi)
            "hostIds": ",".join(sorted(group_hosts.get(gid, set()))),
            # true = present in the member table but not the main table (incomplete data, master record needed)
            "orphan": orphan,
        })
    out.sort(key=lambda x: (x["status"] != "active", -(x["memberCount"] or 0), x["name"]))
    return out


# ══════════════════════════════════════════════════════════════════
# Host (agx_host_inventory) scale statistics -- 2026-11-05 Feng
#   "every host has agents, sessions, executed tasks and so on"
#
# Definitions (the three columns come from three different tables and must be stated, or the numbers never reconcile):
#   · Agent = agx_agent_registry.host_id (direct ownership)
#          [2026-11-05 23:40] no longer adds active_status=1 -- this number is clickable now,
#          landing on /agents?host=X, which lists **every registered agent** (including deactivated, 100 in total).
#          The two must agree, otherwise you click in and see 76 while the card says 70 and the user's first reaction is "your number is wrong".
#   · Sessions = derived from agx_session via the agent (**agx_session has no host_id column**):
#              whichever agent a session belongs to determines the host it counts against
#   · Tasks    = agx_task_execution.host_id (**the executing host**, used directly, not derived via the agent):
#              a task may be dispatched from host A to run on host B; deriving via the agent would file it under the wrong machine
#   · Online sessions = Redis agx:gw:session:by_host (what actually exists on the gateway side right now -- a different definition)
#     registered sessions 4154 vs gateway online 2406 -- the former includes historical archives; the two were never equal.
# ══════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════
# [2026-11-07 15:37 Feng] "online" has exactly one definition: **heartbeat within 90 seconds**
#
# Feng's words: "Whether or not it is online, you add that green dot. You should be able to tell whether a host is online,
#              because on the system status page you got it right."
#
# He was pointing at the root cause: the system status page uses last_heartbeat (host_stats.onlineByHeartbeat),
# while the dropdown data (hosts()) uses presence_status -- a column the daemon only ever writes 1 to, never 0,
# **so it is always 1** and every host shows a green dot. Two definitions coexisting = one fact with two answers on two pages.
#
# So this threshold exists once: host_stats and hosts both read it; nobody may write 90 themselves.
# Basis: the daemon writes a heartbeat every 30s; the watchdog (watchdog_alert.md Step H1) also uses 90s to declare a host unreachable.
# ══════════════════════════════════════════════════════════════════
HEARTBEAT_ONLINE_SECONDS = 90


def _is_online_by_heartbeat(hb_age) -> bool:
    """Heartbeat within the threshold = online. Never heartbeated (NULL) counts as **offline**, not "unknown, assume online"."""
    try:
        return hb_age is not None and int(hb_age) <= HEARTBEAT_ONLINE_SECONDS
    except (TypeError, ValueError):
        return False


def host_stats() -> dict:
    """Count agents / sessions / tasks per host."""
    try:
        hosts = kernel.query(
            """SELECT host_id, host_alias, os_platform, presence_status,
                      task_slots_limit, active_task_count, active_status,
                      last_heartbeat,
                      TIMESTAMPDIFF(SECOND, last_heartbeat, NOW()) AS hb_age
                 FROM agx_host_inventory ORDER BY pkid"""
        ) or []
    except Exception as e:  # noqa: BLE001
        log.warning("host_stats hosts query failed: %s", e)
        hosts = []

    agents: dict[str, dict] = {}
    try:
        for r in kernel.query(
            """SELECT host_id, COUNT(*) c,
                      SUM(agent_role = 'project_manager') pm,
                      SUM(deploy_status = 'pending') pending
                 FROM agx_agent_registry GROUP BY host_id"""
        ):
            agents[str(r.get("host_id") or "")] = {
                "total": int(r.get("c") or 0),
                "pm": int(r.get("pm") or 0),
                "pending": int(r.get("pending") or 0),
            }
    except Exception as e:  # noqa: BLE001
        log.warning("host_stats agents query failed: %s", e)

    sessions: dict[str, int] = {}
    try:
        # [2026-11-06 Feng] agx_session now has its own host_id column (backfilled) -> group by it directly.
        # It used to JOIN agx_agent_registry to get the agent's host, an indirect definition (sessions drift when an agent moves host).
        # ★ Do not filter active_status: the card text is "registered sessions (including historical archives)",
        #   and the list page is also unfiltered (archives included) -> the two must match, or they differ by 9 again.
        for r in kernel.query(
            """SELECT COALESCE(host_id, '') h, COUNT(*) c
                 FROM agx_session
                WHERE network_id = %s
                GROUP BY host_id""",
            (_NETWORK_ID,),
        ):
            sessions[str(r.get("h") or "")] = int(r.get("c") or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("host_stats sessions query failed: %s", e)

    tasks: dict[str, dict] = {}
    unassigned = 0
    try:
        for r in kernel.query(
            """SELECT COALESCE(host_id, '') h, COUNT(*) c,
                      SUM(run_status = 'running') running,
                      SUM(run_status = 'failed' AND finished_at >= NOW() - INTERVAL 24 HOUR) failed24
                 FROM agx_task_execution WHERE network_id = %s GROUP BY host_id""",
            (_NETWORK_ID,),
        ):
            h = str(r.get("h") or "")
            row = {
                "total": int(r.get("c") or 0),
                "running": int(r.get("running") or 0),
                "failed24h": int(r.get("failed24") or 0),
            }
            if not h:
                unassigned = row["total"]
                continue
            tasks[h] = row
    except Exception as e:  # noqa: BLE001
        log.warning("host_stats tasks query failed: %s", e)

    # Redis online sessions (reuses session_runtime's 4s cache; no second Redis read)
    live: dict[str, dict] = {}
    try:
        for row in session_runtime.stats().get("byHost", []):
            live[str(row.get("hostId") or "")] = row
    except Exception as e:  # noqa: BLE001
        log.warning("host_stats live sessions failed: %s", e)

    by_host = []
    for r in hosts:
        hid = str(r.get("host_id") or "")
        a = agents.get(hid, {"total": 0, "pm": 0, "pending": 0})
        t = tasks.get(hid, {"total": 0, "running": 0, "failed24h": 0})
        lv = live.get(hid, {})
        by_host.append({
            "hostId": hid,
            "alias": str(r.get("host_alias") or hid),
            "platform": str(r.get("os_platform") or ""),
            "online": int(r.get("presence_status") or 0) == 1,
            # [2026-11-06 10:19 Feng chose A] real "is it online" comes from the heartbeat, not presence_status:
            # the daemon writes `presence_status=1, last_heartbeat=NOW()` every 30s,
            # but **nothing ever writes it back to 0** -> the column is always 1, i.e. decorative.
            # The watchdog (watchdog_alert.md Step H1) also uses last_heartbeat to declare a host unreachable, threshold 90s.
            # heartbeatAgeSeconds=None means it has never heartbeated (NULL), completely different from "0 seconds ago".
            "lastHeartbeat": _dt(r.get("last_heartbeat")),
            "heartbeatAgeSeconds": (
                int(r["hb_age"]) if r.get("hb_age") is not None else None
            ),
            "active": int(r.get("active_status") or 0) == 1,
            "slots": int(r.get("task_slots_limit") or 0),
            "agentCount": a["total"],
            "pmCount": a["pm"],
            "pendingDeploy": a["pending"],
            # Registered sessions (MySQL, including historical archives)
            "sessionCount": sessions.get(hid, 0),
            # Gateway-side online sessions (Redis, what really exists right now)
            "liveSessions": int(lv.get("total") or 0),
            "liveRunning": int(lv.get("running") or 0),
            "liveIdle": int(lv.get("idle") or 0),
            "liveFailed": int(lv.get("failed") or 0),
            "taskCount": t["total"],
            "runningTasks": t["running"],
            "failedTasks24h": t["failed24h"],
        })

    by_host.sort(key=lambda x: (-x["agentCount"], -x["sessionCount"], x["hostId"]))

    return {
        "total": len(by_host),
        "online": sum(1 for h in by_host if h["online"]),
        # [2026-11-06] both online definitions are emitted; neither overrides the other:
        #   online            = presence_status (raw DB value, currently always 1, untrustworthy)
        #   onlineByHeartbeat = a heartbeat within 90s (genuinely online; the green dot uses this)
        "onlineByHeartbeat": sum(
            1 for h in by_host
            if _is_online_by_heartbeat(h["heartbeatAgeSeconds"])
        ),
        "byHost": by_host,
        "totals": {
            "agentCount": sum(h["agentCount"] for h in by_host),
            "sessionCount": sum(h["sessionCount"] for h in by_host),
            "liveSessions": sum(h["liveSessions"] for h in by_host),
            "taskCount": sum(h["taskCount"] for h in by_host),
            "runningTasks": sum(h["runningTasks"] for h in by_host),
            "slots": sum(h["slots"] for h in by_host),
        },
        # Historical rows with an empty host_id (unknown ownership) are listed separately -- not folded into any host, not silently dropped
        "unassignedTasks": unassigned,
        "note": "会话按「会话归属 Agent 所在主机」统计；任务按执行主机（agx_task_execution.host_id）统计",
    }


# ══════════════════════════════════════════════════════════════════
# Organisation (agx_agent_group) scale statistics -- 2026-11-05 Feng
#   "In system status you forgot the organisations... organisations, organisation sessions, executed tasks, agents,
#     all of that needs counting"
#
# Definitions (must be stated, or the numbers never reconcile):
#   · An organisation's "members" = deduped agents from agx_agent_group_member (one agent may belong to several organisations)
#   · Organisation sessions = agx_session under those members (active_status=1)
#   · Organisation tasks = agx_task_execution under those members (all of them, with running broken out)
#   ★ Because members can span organisations, **summing per-organisation numbers exceeds the global deduped count**.
#     That is the inevitable consequence of "one person appearing in two groups", not a miscalculation; the response carries sharedAgents
#     and totals so the UI can say so in words.
# ══════════════════════════════════════════════════════════════════
_BUILTIN_ORG = "group_agx_core"


def _org_display_name(group_id: str, group_name: str = "") -> str:
    """Organisation display name. When the main table has no name, derive it from group_id (group_agentgeex_hr -> "agentgeex hr"),
    so it is at least recognisable instead of a group_xxx string nobody can verify.
"""
    name = (group_name or "").strip()
    if name:
        return name
    return group_id.replace("group_", "").replace("_", " ").strip() or group_id


def org_stats() -> dict:
    """Organisation-dimension statistics: number of organisations / agents, sessions, tasks per organisation."""
    # (1) Organisation list: main table ∪ member table (organisations present only in the member table must appear too, or they do not exist)
    names: dict[str, str] = {}
    try:
        for r in kernel.query("SELECT group_id, group_name, active_status FROM agx_agent_group"):
            gid = str(r.get("group_id") or "")
            if gid and int(r.get("active_status") or 0) == 1:
                names[gid] = str(r.get("group_name") or "")
    except Exception as e:  # noqa: BLE001
        log.warning("org_stats groups query failed: %s", e)

    # (2) Members (deduped, counting only enabled agents)
    members: dict[str, list[dict]] = {}
    seen: set[tuple[str, str]] = set()
    try:
        for r in kernel.query(
            """SELECT m.group_id, m.agent_id, a.agent_name, a.agent_role, a.host_id,
                      a.active_status
                 FROM agx_agent_group_member m
                 LEFT JOIN agx_agent_registry a ON a.agent_id = m.agent_id
                ORDER BY m.group_id, a.agent_name"""
        ):
            gid = str(r.get("group_id") or "")
            aid = str(r.get("agent_id") or "")
            if not gid or not aid or (gid, aid) in seen:
                continue
            if int(r.get("active_status") or 0) != 1:  # deactivated members are not counted towards scale
                continue
            seen.add((gid, aid))
            members.setdefault(gid, []).append({
                "id": aid,
                "name": str(r.get("agent_name") or aid),
                "role": str(r.get("agent_role") or "worker"),
                "hostId": str(r.get("host_id") or ""),
            })
    except Exception as e:  # noqa: BLE001
        log.warning("org_stats members query failed: %s", e)

    # (2b) Lineage closure: organisation members are **template agents**; what actually does the work are their clones.
    #   Evidence: the book-writing squad has 5 members (book_writer / book_planner / book_reviewer /
    #   book_beta_reader / super_pm), and each has 8 clone instances attached to 8 book projects.
    #   Counting templates only -> 0 sessions, 0 tasks (templates never work), and the organisation stats become decoration.
    #   `peer_agent_id` is that lineage edge and it can be multi-level (super_pm -> super_pm_a1f729 -> ...),
    #   so compute a transitive closure rather than looking only one level deep.
    peers: dict[str, list[str]] = {}
    meta: dict[str, dict] = {}
    try:
        for r in kernel.query(
            """SELECT agent_id, peer_agent_id, agent_name, agent_role, host_id, active_status
                 FROM agx_agent_registry"""
        ):
            aid = str(r.get("agent_id") or "")
            if not aid:
                continue
            meta[aid] = {
                "name": str(r.get("agent_name") or aid),
                "role": str(r.get("agent_role") or "worker"),
                "hostId": str(r.get("host_id") or ""),
                "active": int(r.get("active_status") or 0) == 1,
            }
            peer = str(r.get("peer_agent_id") or "")
            if peer:
                peers.setdefault(peer, []).append(aid)
    except Exception as e:  # noqa: BLE001
        log.warning("org_stats lineage query failed: %s", e)

    def _closure(seed: set[str]) -> set[str]:
        """Template -> all of its derived instances (recursive, cycle-safe)."""
        out: set[str] = set(seed)
        stack = list(seed)
        while stack:
            cur = stack.pop()
            for child in peers.get(cur, []):
                if child not in out:
                    out.add(child)
                    stack.append(child)
        return out

    # (3) agent -> session count / task count (one aggregation, not repeated per group in Python)
    sess_cnt: dict[str, int] = {}
    try:
        for r in kernel.query(
            """SELECT agent_id, COUNT(*) c FROM agx_session
                WHERE network_id = %s AND active_status = 1 GROUP BY agent_id""",
            (_NETWORK_ID,),
        ):
            sess_cnt[str(r.get("agent_id") or "")] = int(r.get("c") or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("org_stats sessions query failed: %s", e)

    task_cnt: dict[str, int] = {}
    task_running: dict[str, int] = {}
    try:
        for r in kernel.query(
            """SELECT agent_id, COUNT(*) c, SUM(run_status = 'running') r
                 FROM agx_task_execution WHERE network_id = %s GROUP BY agent_id""",
            (_NETWORK_ID,),
        ):
            aid = str(r.get("agent_id") or "")
            task_cnt[aid] = int(r.get("c") or 0)
            task_running[aid] = int(r.get("r") or 0)
    except Exception as e:  # noqa: BLE001
        log.warning("org_stats tasks query failed: %s", e)

    # (4) assemble (full organisation set = active main table ∪ anything seen in the member table)
    all_ids = sorted(set(names) | set(members))
    by_org = []
    membership_count: dict[str, int] = {}
    for gid in all_ids:
        mem = members.get(gid, [])
        direct = {m["id"] for m in mem}
        # Real organisation scale = templates + all their clone instances (closure)
        roster = _closure(direct)
        for aid in roster:
            membership_count[aid] = membership_count.get(aid, 0) + 1
        by_org.append({
            "groupId": gid,
            "groupName": _org_display_name(gid, names.get(gid, "")),
            "builtin": gid == _BUILTIN_ORG,
            # No row in the main table = incomplete data (members exist, but the organisation master record is missing)
            "orphan": gid not in names,
            # agentCount = every agent that can actually work (templates + derived instances)
            "agentCount": len(roster),
            "templateCount": len(direct),
            "instanceCount": len(roster - direct),
            "sessionCount": sum(sess_cnt.get(a, 0) for a in roster),
            "taskCount": sum(task_cnt.get(a, 0) for a in roster),
            "runningTasks": sum(task_running.get(a, 0) for a in roster),
            "members": mem,
            # Derived instances (so the frontend can expand and see "which robots are really running for this organisation")
            "instances": [
                {"id": a, "name": meta.get(a, {}).get("name", a), "hostId": meta.get(a, {}).get("hostId", "")}
                for a in sorted(roster - direct)
            ],
        })

    # Ordering: business organisations first, built-in ones last; within a group by member count, then by session count
    by_org.sort(key=lambda o: (o["builtin"], -o["agentCount"], -o["sessionCount"], o["groupName"]))

    all_agents: set[str] = set()
    for mem in members.values():
        all_agents |= _closure({m["id"] for m in mem})
    return {
        "total": len(by_org),
        "builtin": sum(1 for o in by_org if o["builtin"]),
        "business": sum(1 for o in by_org if not o["builtin"]),
        "orphan": [o["groupId"] for o in by_org if o["orphan"]],
        "byOrg": by_org,
        # Global deduped count (does not equal the sum per organisation -- someone in several groups counts once)
        "totals": {
            "agentCount": len(all_agents),
            "sessionCount": sum(sess_cnt.get(a, 0) for a in all_agents),
            "taskCount": sum(task_cnt.get(a, 0) for a in all_agents),
            "runningTasks": sum(task_running.get(a, 0) for a in all_agents),
        },
        # Number of agents belonging to >1 organisation: this is why "sum per organisation > global"
        "sharedAgents": sum(1 for a, c in membership_count.items() if c > 1),
        "overlapNote": "同一 Agent 可属多个组织，各组织数字相加会大于全量去重数",
    }


# ══════════════════════════════════════════════════════════════════
# 3. Sessions  (agx_list_sessions -- includes session_type + pagination)
# ══════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════
# 3. Sessions  (direct MySQL -- reads go direct;
#    2026-10-16: used to go through MCP agx_list_sessions, whose returned fields carry nothing assistant-related
#    (is_assistant / assistant_slug / avatar / creator_agent_id) -> the assistant feature could not be implemented.
#    Switched to direct agx_session: all fields present, plus a LEFT JOIN for the creator name.
#    (consistent with tasks/plans/skills: read-only queries always go direct to MySQL)
# ══════════════════════════════════════════════════════════════════
# 3.5 Session message history (2026-11-14 11:08 Feng)
#
# "There is very useful data now: the full detail of every conversation... you can put the
#   detailed record of the conversation below the message body in the send window, and then we have history"
#
# Data source: `agx_user_message` (a full archive of question-and-answer: one row = one owner message + its reply).
# The join key to `agx_session` is **session_key = agx_session.session_id**
# (both look like `agent:agx_ui_lead:main`; the table's `session_id` is a gateway UUID, a different thing).
#
# ★ Direct MySQL, not MCP: the same lesson as skills/hosts/notices/plans --
#   BFF calls to high-privilege MCP tools fail silently, so data reads always prefer direct.
# ══════════════════════════════════════════════════════════════════

# Message-kind whitelist: only these five are allowed (the route layer uses it for input validation, closing any path for arbitrary strings into SQL)
MSG_KINDS = ("human", "envelope", "notice", "system", "command")

# The dispatch envelope carries only `task_run_uuid`; the instruction body lives in `agx_task_execution.exec_instruction`
# [2026-11-14 19:58 Feng] "I cannot see the instruction message in the UI" -> the envelope row must show the instruction body.
# ★ The single source of truth is `agx_task_execution`: **do not copy it, do not cache it, do not write it back to the message table**.
_RUN_RE = re.compile(r"run_[A-Za-z0-9_]+")
_MSG_TYPE_RE = re.compile(r'"msg_type"\s*:\s*"([a-z_]+)"')
_SEND_AGENT_RE = re.compile(r'"send_agent"\s*:\s*"([^"]+)"')


def _parse_envelope(content: str) -> dict:
    """Pull run_uuid / msg_type / send_agent out of the envelope body; leave them empty when absent.

    ★ Why not just `json.loads`: of 5,937 envelopes, 7 are not valid JSON --
      some are **two objects concatenated** (`{...}{...}`), some are Feng's plain text
      misclassified into an envelope. So: parse as JSON if possible, fall back to a regex,
      and if neither works return empty -- **never raise** (one bad row must not make a
      whole page of history unreadable).
"""
    out: dict = {"runUuid": "", "msgType": "", "sendAgent": ""}
    txt = content or ""
    try:
        obj = json.loads(txt)
        if isinstance(obj, dict):
            out["runUuid"] = str(obj.get("task_run_uuid") or "")
            out["msgType"] = str(obj.get("msg_type") or "")
            out["sendAgent"] = str(obj.get("send_agent") or "")
    except Exception:  # noqa: BLE001
        pass
    if not out["runUuid"]:
        m = _RUN_RE.search(txt)
        if m:
            out["runUuid"] = m.group(0)
    if not out["msgType"]:
        m = _MSG_TYPE_RE.search(txt)
        if m:
            out["msgType"] = m.group(1)
    if not out["sendAgent"]:
        m = _SEND_AGENT_RE.search(txt)
        if m:
            out["sendAgent"] = m.group(1)
    return out


# [2026-11-14 20:52 Feng] "show every message in the DB" -> measuring a full pull:
#   497 rows / **1.57MB** / 12.5s to render. The root cause was 49 envelopes each carrying
#   a 2,000-4,000 word `exec_instruction` while the list only shows a 4-line summary --
#   paying 95% of the cost for 95% of the content nobody sees.
# -> the list carries only a **preview** (enough to read and judge); the full text is fetched per run_uuid by the detail dialog.
#   ★ The preview is not "guessing where the body is": `INSTR_PREVIEW` is a plain fixed-length truncation,
#     and if it ever needs tuning only that constant changes; it does not affect "where the full text comes from".
INSTR_PREVIEW = 600


def _attach_dispatch(items: list[dict], with_instruction: bool = False) -> None:
    """Fill in "what this dispatch actually asked for" on this page's envelope rows. In-place mutation.

    ★ Only the run_uuids present on the current page (<=500) are queried, in one `IN (...)`
      batch: no full-table scan, no N+1.
    ★ [2026-11-14 20:52] by default only an `INSTR_PREVIEW`-length preview plus the real
      total length (`instructionLen` / `instructionTruncated`); `with_instruction=True`
      returns the full text. The single official entry point for full text is
      `execution_instruction()` (the dialog fetches on demand).
"""
    # ★ `dict.get(k, default)`'s default applies **only when the key is missing** -- here the dispatch key
    #   exists with the value None (non-envelope rows), so `.get("dispatch", {})` yields None rather than {}.
    #   Must use `or {}`. (Hit by me on 2026-11-14 19:59; the 500 error came from exactly this line.)
    uuids = sorted(
        {
            (it.get("dispatch") or {}).get("runUuid")
            for it in items
            if (it.get("dispatch") or {}).get("runUuid")
        }
    )
    if not uuids:
        return
    ph = ",".join(["%s"] * len(uuids))
    try:
        rows = kernel.query(
            f"""SELECT task_run_uuid, agent_id, send_agent_id, exec_name,
                       exec_instruction, run_status
                  FROM agx_task_execution
                 WHERE task_run_uuid IN ({ph})""",
            tuple(uuids),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("realdata attach_dispatch failed: %s", e)
        return
    by_uuid = {r.get("task_run_uuid"): r for r in (rows or [])}
    for it in items:
        d = it.get("dispatch") or {}
        rr = by_uuid.get(d.get("runUuid") or "")
        if not rr:
            continue
        d["agentId"] = rr.get("agent_id") or ""
        d["sendAgentId"] = rr.get("send_agent_id") or ""
        d["execName"] = rr.get("exec_name") or ""
        d["runStatus"] = rr.get("run_status") or ""
        full = rr.get("exec_instruction") or ""
        d["instructionLen"] = len(full)
        d["instructionTruncated"] = bool(full) and len(full) > INSTR_PREVIEW and not with_instruction
        d["instruction"] = full if with_instruction else full[:INSTR_PREVIEW]


def _display_kind(kind: str, content: str) -> str:
    """Normalise on read: pick "an address starting with /" out of `command` and return it to `human`.

    ★ 2026-11-15 15:27 Feng:
      "A piece of text I sent contained this path, and you seem to have treated it as a command too"
      Instance (fxc_math_paper main session pkid=439373): a message "~/.../research-report.md
      please turn this into a PDF too..." -- a human utterance that became a centred dashed-box
      "command" in the UI, only because **its first character was `/`**.

    Root cause is in the collector `tools/user_msg_collect.py::classify`:
      `if text.startswith("/"): return "command"`
    Intended for slash commands (`/reset` `/approve`), but absolute paths also start with `/`.
    Measured across the table: of 73 command rows, **60 are paths** (82% false positives); only 13 are real slash commands.

    Test: a slash command = the first token starts with `/` and **contains no second `/`**.
      `/reset`                  -> command
      `~/a.md`   -> path (the first token contains another slash)

    ★ Why fix here (rather than only in the collector):
      (1) `tools/` is PM territory and must not be touched without authorisation; this is webtools' own read path.
      (2) **The 60 bad rows are already in the DB** -- fixing the collection rule only governs future
          inserts; without this layer the history stays wrong forever.
      (3) This rule **cannot conflict** with a fixed collector: once the source classifies correctly
          these rows are already human and this function returns them untouched, making it a harmless
          second line of defence.
"""
    if kind != "command":
        return kind
    body = content.lstrip()
    first = body.split(None, 1)[0] if body else ""
    if first.startswith("/") and first.count("/") == 1:
        return "command"
    return "human"


# [2026-11-17 16:45] Noise specific to the DSH platform -- repeated every turn, carries no information.
# Feng's rule: store everything in the DB; **the display layer decides what to filter**.
# Measured: all 102 system rows of `agent:dshlab_pm:main` are this kind (0 with a reply):
#     · `Time sampled while preparing turn N, step M: ...` (pure timestamp, one per turn)
#     · `<system-reminder> ...` (full AGENTS.md / skill list, repeated on every new session)
#     · `Current runtime context. This snapshot supersedes ...` (runtime snapshot)
#     · `[System] …`
#
# ★ Why **only applies to platform='dsh'**: the `[System]` prefix also exists on the OpenClaw side,
#   and 62 of those 69 rows have no reply -- their content is "Your previous turn was interrupted...",
#   which is informative (it says the previous turn was interrupted). Without the platform guard they would be killed too,
#   exactly the "do not kill on prefix alone" warning in the task.
# ★ Same convention as the heartbeat: all three conditions must hold to exclude (system + platform=dsh + prefix match + no reply).
#   If any of these injections ever carries a substantive reply it is not noise and must be kept.
# ★ `%%`: kernel.query goes through pymysql's `cur.execute(sql, params)` where `%` is the format specifier.
_DSH_NOISE_SQL = (
    " AND NOT ("
    "   msg_kind = 'system'"
    "   AND platform = 'dsh'"
    "   AND ("
    "     reply_content IS NULL"
    "     OR TRIM(reply_content) = ''"
    "     OR REPLACE(REPLACE(REPLACE(TRIM(reply_content), 'NO_REPLY', ''), CHAR(10), ''), ' ', '') = ''"
    "   )"
    "   AND ("
    "     TRIM(content) LIKE 'Time sampled while preparing turn%%'"
    "     OR TRIM(content) LIKE '<system-reminder>%%'"
    "     OR TRIM(content) LIKE 'Current runtime context.%%'"
    "     OR TRIM(content) LIKE '[System]%%'"
    "   )"
    " )"
)


def session_messages(
    session_key: str,
    limit: int = 20,
    before: int | None = None,
    before_at: str | None = None,
    kinds: tuple[str, ...] = ("human",),
    with_instruction: bool = False,
) -> dict:
    """Fetch a session's message history (returned in chronological order; pagination uses a composite (msg_at, pkid) cursor).

    ★ Why not OFFSET: messages keep arriving, so OFFSET paging skips and duplicates rows.
    ★ [2026-11-14 13:05] the cursor changed from "pkid only" to a "(msg_at, pkid) composite":
      the collector rebuilt this table on 2026-11-14 (backup -> truncate -> full rescan -> restore
      from backup); `pkid` is auto-increment and was reassigned wholesale, and the restored
      historical rows ended up with larger pkids. Measured: among all human rows with a session_key there are
      **233 places** where pkid and msg_at are inverted -- ordering by pkid alone puts August messages
      ahead of November ones, and "load earlier" would skip/repeat. The primary sort key is now
      **msg_at** (business time, unaffected by rebuilds) with pkid only breaking same-millisecond ties.
    ★ By default only `human` (real conversation) rows -- across the whole table, envelopes + notices are
      over half, and mixing them in by default waters down real exchanges into a feed. The frontend can
      switch kinds to see everything.
"""
    # [2026-11-14 20:40 Feng] "show every message in the DB" --
    # several hundred messages per session is normal (this host's agx_ui_lead main session has 496), so a limit of 100 forces 5 clicks
    # on "load earlier". 500 covers the vast majority of sessions in one go; beyond that the frontend cursor still works.
    limit = max(1, min(limit, 500))
    kinds = tuple(k for k in kinds if k in MSG_KINDS) or MSG_KINDS
    ph = ",".join(["%s"] * len(kinds))

    # [2026-11-14 13:10] Show only human rows that are "not confirmed to be someone else".
    # `from_owner` is tri-state (collector 2026-11-14 `2560ca3`):
    #   1 = confirmed owner / 0 = confirmed not owner / NULL = no marker (old data, or a marker stripped by archiving)
    #
    # ★ Why not whitelist only 1: 3,171 NULL rows are things a human actually said
    #   (before 2026-10 the gateway never wrote this field), so a whitelist would delete them all.
    # ★ Why not guess on content here: the criterion trusts the column, not the text -- content matching would kill real human speech.
    # ★ Why keep this layer at all (classify was fixed at the source the same day, so this is "dead code"):
    #   it guards against **future classify misses**, not against the current data being wrong. Data gets rewritten
    #   by the next collection run; the contract does not. Upstream "patching prefixes" is an enumeration (a next one always slips through); this fallback here converges.
    #
    # ★★ Iron rule (written after hitting it twice in one round on 2026-11-14):
    #   any NOT / <> / ! on a tri-state column turns NULL into "excluded".
    #   Columns that contain NULL appear only where a branch is **explicitly enumerated**, never in a negation.
    #   For the concrete form see the WHERE below -- not copied here: a copy expires, the reason does not.
    where = (
        f"session_key = %s AND msg_kind IN ({ph})"
        " AND (msg_kind <> 'human' OR from_owner IS NULL OR from_owner = 1)"
        # ── filter "pure heartbeat": Feng's ruling of 2026-11-14 20:48
        # "Could you add a check for messages like this? Showing them has no meaning any more, right?"
        #
        # ★ Why check rather than blanket-filter by kind:
        #   of this session's 71 system rows, 66 are `[OpenClaw heartbeat poll]` (pure noise),
        #   but 5 are `[System] Your previous turn was interrupted...`
        #   -- those **are informative** (they say the previous turn was interrupted); filtering by kind would kill them too.
        #
        # ★ Why all three conditions must hold (none may be dropped):
        #   heartbeat + I answered substantively **is not noise**. That record says "the system pinged me,
        #   I found something real and handled it", which is exactly what should be kept. Only when
        #   (1) it is a heartbeat  (2) the reply is empty / pure NO_REPLY  (3) it has type system
        #   do all three hold, and only then does "it pinged, I said nothing was up" truly carry no information.
        " AND NOT ("
        "   msg_kind = 'system'"
        # ★ This must be `%%` here: kernel.query goes through pymysql's `cur.execute(sql, params)`,
        #   where `%` is the format specifier -- a single `%` raises
        #   `unsupported format character`, and the except clause swallows it into "0 rows".
        "   AND content LIKE '[OpenClaw heartbeat poll]%%'"
        "   AND ("
        "     reply_content IS NULL"
        "     OR TRIM(reply_content) = ''"
        "     OR REPLACE(REPLACE(REPLACE(TRIM(reply_content), 'NO_REPLY', ''), CHAR(10), ''), ' ', '') = ''"
        "   )"
        " )"
        # ── filter "pure opening line": only appeared in the DB after the collector backfilled on 2026-11-17
        #   252 rows of `Continue the OpenClaw runtime event.` are OpenClaw's per-turn
        #   injection: the body is always this one sentence and the reply is always empty -- same nature as the heartbeat, no information.
        #   ★ Cannot filter by LIKE prefix: 68 more rows are "opening line + a real human sentence" glued together
        #     (already classified as human/envelope by the collector's strip_runtime_prefix, so they never reach here),
        #     but to guard against other content being glued on in future -- use exact TRIM equality; do not kill one character more.
        #   ★ Same convention as the heartbeat: all three conditions must hold to exclude (system + exactly this sentence + no reply).
        " AND NOT ("
        "   msg_kind = 'system'"
        "   AND TRIM(content) = 'Continue the OpenClaw runtime event.'"
        "   AND (reply_content IS NULL OR TRIM(reply_content) = '')"
        " )"
        # ── DSH system injection (see the comment above _DSH_NOISE_SQL)
        + _DSH_NOISE_SQL
    )
    params: list[Any] = [session_key, *kinds]
    if before_at:
        # Composite cursor: earlier in time, or same timestamp with a smaller pkid (same-millisecond tiebreak)
        where += " AND (msg_at < %s OR (msg_at = %s AND pkid < %s))"
        params.extend([before_at, before_at, int(before or 0)])
    elif before:
        # Backwards compatible with old callers (pkid only): still works, but deprecated -- see ★ above
        where += " AND pkid < %s"
        params.append(int(before))

    try:
        rows = kernel.query(
            f"""SELECT pkid, session_key, session_label, agent_id, host_id,
                       msg_kind, from_owner, msg_at, content, reply_content, reply_at,
                       reply_parts, content_len, reply_len
                  FROM agx_user_message
                 WHERE {where}
                 ORDER BY msg_at DESC, pkid DESC
                 LIMIT %s""",
            tuple(params + [limit + 1]),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("realdata session_messages query failed: %s", e)
        return {"items": [], "hasMore": False, "total": 0}

    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()  # chronological order: read top to bottom in the UI

    total = 0
    try:
        tot = kernel.query(
            # ★ Must be **word-for-word identical** to the WHERE above: once the two statements drift,
            #   you get the phantom "total N, but only N-66 rendered".
            f"SELECT COUNT(*) AS n FROM agx_user_message WHERE session_key = %s AND msg_kind IN ({ph})"
            " AND (msg_kind <> 'human' OR from_owner IS NULL OR from_owner = 1)"
            " AND NOT ("
            "   msg_kind = 'system'"
            # ★ This must be `%%` here: kernel.query goes through pymysql's `cur.execute(sql, params)`,
        #   where `%` is the format specifier -- a single `%` raises
        #   `unsupported format character`, and the except clause swallows it into "0 rows".
        "   AND content LIKE '[OpenClaw heartbeat poll]%%'"
            "   AND ("
            "     reply_content IS NULL"
            "     OR TRIM(reply_content) = ''"
            "     OR REPLACE(REPLACE(REPLACE(TRIM(reply_content), 'NO_REPLY', ''), CHAR(10), ''), ' ', '') = ''"
            "   )"
            " )"
            # ★ Word-for-word identical to the list WHERE above (including the runtime-event clause),
            #   otherwise you get the phantom "total N but only N-252 rendered".
            " AND NOT ("
            "   msg_kind = 'system'"
            "   AND TRIM(content) = 'Continue the OpenClaw runtime event.'"
            "   AND (reply_content IS NULL OR TRIM(reply_content) = '')"
            " )"
            # ★ Uses **the same constant** as the list WHERE; no second copy -- copies drift.
            + _DSH_NOISE_SQL,
            (session_key, *kinds),
        )
        total = int((tot[0] or {}).get("n") or 0) if tot else 0
    except Exception as e:  # noqa: BLE001
        log.warning("realdata session_messages count failed: %s", e)

    items = [
        {
            "id": int(r.get("pkid") or 0),
            "sessionKey": r.get("session_key") or "",
            "sessionLabel": r.get("session_label") or "",
            "agentId": r.get("agent_id") or "",
            "hostId": r.get("host_id") or "",
            "kind": _display_kind(r.get("msg_kind") or "human", r.get("content") or ""),
            # Non-empty only for envelopes: which execution this dispatch maps to, and what the instruction body is (backfilled in bulk below)
            "dispatch": (
                {"runUuid": "", "msgType": "", "sendAgent": "", "agentId": "",
                 "sendAgentId": "", "execName": "", "runStatus": "", "instruction": ""}
                if (r.get("msg_kind") or "") == "envelope"
                else None
            ),
            # 1=confirmed owner / 0=confirmed not owner / None=no marker (old data / marker stripped by archiving)
            "fromOwner": None if r.get("from_owner") is None else int(r.get("from_owner")),
            "at": _dt(r.get("msg_at")),
            # Full-precision time (used by the cursor): `at` is minute-resolution and two rows in the same minute collide on the cursor
            "atIso": (r.get("msg_at").isoformat() if hasattr(r.get("msg_at"), "isoformat") else str(r.get("msg_at") or "")),
            "content": r.get("content") or "",
            "reply": r.get("reply_content") or "",
            "replyAt": _dt(r.get("reply_at")),
            "replyParts": int(r.get("reply_parts") or 0),
            "contentLen": int(r.get("content_len") or 0),
            "replyLen": int(r.get("reply_len") or 0),
        }
        for r in rows
    ]
    for it in items:
        if it.get("dispatch"):
            it["dispatch"].update(_parse_envelope(it["content"]))
    _attach_dispatch(items, with_instruction=with_instruction)
    return {"items": items, "hasMore": has_more, "total": total}


def execution_instruction(run_uuid: str) -> dict:
    """Fetch the complete record of one execution by run_uuid (used by the envelope detail dialog).

    ★ Why a separate method instead of "the list carries the full text": see the measurement above
      INSTR_PREVIEW (497 rows, 1.57MB / 12.5s). **A session may have hundreds of envelopes, but a
      person looks at one at a time.**
    ★ 404 semantics are left to the route layer: a miss here returns an empty dict, never raises.
"""
    if not run_uuid:
        return {}
    try:
        rows = kernel.query(
            # ★ Only query columns that really exist: `duration_seconds` is **derived/view-only** (present in
            #   agx_get_execution's response, absent from the table) -- the first version copied the MCP field list into
            #   a SELECT and got error 1054 straight away, with the except swallowing it into "not found" and only a 404 reaching the frontend. Elapsed time is computed from start/end.
            """SELECT task_run_uuid, agent_id, send_agent_id, exec_name,
                      exec_instruction, summary, run_status,
                      started_at, finished_at, session_id, task_id
                 FROM agx_task_execution
                WHERE task_run_uuid = %s""",
            (run_uuid,),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("realdata execution_instruction failed: %s", e)
        return {}
    if not rows:
        return {}
    r = rows[0]
    return {
        "runUuid": r.get("task_run_uuid") or "",
        "agentId": r.get("agent_id") or "",
        "sendAgentId": r.get("send_agent_id") or "",
        "execName": r.get("exec_name") or "",
        "runStatus": r.get("run_status") or "",
        "instruction": r.get("exec_instruction") or "",
        "instructionLen": len(r.get("exec_instruction") or ""),
        "summary": r.get("summary") or "",
        "startedAt": _dt(r.get("started_at")),
        "finishedAt": _dt(r.get("finished_at")),
        "durationSeconds": _secs_between(r.get("started_at"), r.get("finished_at")),
        "sessionId": r.get("session_id") or "",
        "taskId": r.get("task_id") or "",
    }


# ══════════════════════════════════════════════════════════════════
def sessions(caller: str = "", limit: int | None = None) -> list[dict]:
    limit = _MAX if limit is None else limit
    try:
        rows = kernel.query(
            """SELECT s.session_id, s.session_name, s.agent_id, s.project_id,
                      s.session_type, s.task_labs, s.is_assistant, s.assistant_slug,
                      s.avatar, s.creator_agent_id, s.run_count, s.last_active_at,
                      s.created_at, s.active_status, s.host_id,
                      oa.agent_name AS owner_name, ca.agent_name AS creator_name
                 FROM agx_session s
                 LEFT JOIN agx_agent_registry oa ON oa.agent_id = s.agent_id
                 LEFT JOIN agx_agent_registry ca ON ca.agent_id = s.creator_agent_id
                WHERE s.network_id = %s
                ORDER BY s.last_active_at DESC, s.pkid DESC
                LIMIT %s""",
            (_NETWORK_ID, limit),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("realdata sessions query failed: %s", e)
        return []
    out = []
    for r in rows:
        active = int(r.get("active_status") or 0) == 1
        aid = r.get("agent_id") or ""
        out.append({
            "id": r.get("session_id") or "",
            "name": r.get("session_name") or r.get("session_id") or "",
            "agentId": aid,
            "agentName": r.get("owner_name") or aid,
            "sessionType": r.get("session_type") or "oneshot",
            "projectId": r.get("project_id") or "",
            # [2026-11-06 Feng] agx_session gained host_id and was backfilled -- sessions have their own host,
            # no longer reverse-looked-up through the agent (when an agent moves host, historical sessions must not drift with it).
            "hostId": r.get("host_id") or "",
            "taskLabs": r.get("task_labs") or "",
            "status": "active" if active else "archived",
            "messageCount": int(r.get("run_count") or 0),
            "lastActiveAt": _dt(r.get("last_active_at")),
            # ── assistants (case sessions with is_assistant=1)
            "isAssistant": int(r.get("is_assistant") or 0) == 1,
            "assistantSlug": r.get("assistant_slug") or "",
            "avatar": r.get("avatar") or "",
            "creatorAgentId": r.get("creator_agent_id") or "",
            "creatorName": r.get("creator_name") or r.get("creator_agent_id") or "",
            "createdAt": _dt(r.get("created_at")),
        })

    # ── session live state (2026-11-05): from Redis agx:gw:*, a different system from MySQL
    #    MySQL agx_session has 4145 rows (including historical archives); Redis has only the 2405 live entities on the gateway side
    #    -> rows that do not match get runtime=null and the frontend shows "—" (not 0, not an error).
    # ★ Why look up per row instead of a batched IN: the runtime index is an in-process dict (4s cache),
    #   so these are 4145 O(1) dict lookups; the actual Redis read happens once per cache period.
    # ★ host_hint: which machine the session's agent lives on -- same-named sessions across hosts (agent:main:main)
    #   use it to fetch the data for "the machine this row actually belongs to", otherwise everything shows the Mac copy.
    agent_host: dict[str, str] = {}
    try:
        for a in kernel.query(
            "SELECT agent_id, host_id FROM agx_agent_registry WHERE active_status = 1"
        ):
            aid = a.get("agent_id") or ""
            if aid:
                agent_host[aid] = a.get("host_id") or ""
    except Exception as e:  # noqa: BLE001
        log.warning("realdata sessions agent_host map failed: %s", e)

    for row in out:
        try:
            # [2026-11-06] host_hint prefers the session's own host_id (agx_session.host_id),
            # falling back to the agent's host only when empty -- same-named cross-host sessions rely on it to get "the machine this row belongs to".
            rt = session_runtime.lookup(row["id"], row.get("hostId") or agent_host.get(row["agentId"], ""))
        except Exception as e:  # noqa: BLE001
            log.warning("realdata sessions runtime lookup failed: %s", e)
            rt = None
        row["runtime"] = rt
        # Listed side by side when the key appears on multiple hosts (cross-host duplicates; empty array when single-host, taking no slot)
        multi = session_runtime.hosts_of(row["id"])
        if len(multi) > 1:
            row["runtimeHosts"] = multi
    return out


# ══════════════════════════════════════════════════════════════════
# 4. Tasks  (direct MySQL -- reads go direct, consistent with agents())
#    2026-10-05: used to go through MCP agx_list_by_network, which filtered by caller visibility and dropped
#    every non-public task (an empty table for any logged-in user that is not a real agent); switched to direct.
# ══════════════════════════════════════════════════════════════════
def tasks(caller: str = "", limit: int | None = None) -> list[dict]:
    limit = _MAX if limit is None else limit
    try:
        rows = kernel.query(
            """SELECT task_id, task_name, task_labs, description, agent_id,
                      is_private, active_status, update_dt, created_at, reply_mode,
                      task_logo
                 FROM agx_task_base
                WHERE network_id = %s AND active_status = 1
                ORDER BY pkid ASC""",
            (_NETWORK_ID,),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("realdata tasks query failed: %s", e)
        return []
    return [{
        "id": r.get("task_id") or "",
        "name": r.get("task_name") or "",
        "description": r.get("description") or "",
        "agentId": r.get("agent_id") or "",
        "agentName": r.get("agent_id") or "",
        "labs": r.get("task_labs") or "general",
        # task logo (agx_task_base.task_logo, a column added 2026-10-20); shared per task_labs after dedupe
        "logoUrl": r.get("task_logo") or "",
        "isPrivate": int(r.get("is_private") or 0),
        "activeStatus": 1,
        # update_dt is NULL for most old rows (never updated) -> fall back to created_at so the frontend does not show "—"
        "updatedAt": _dt(r.get("update_dt") or r.get("created_at")),
        # ★ 2026-10-19 P1: the reply default is computed **on the backend** by the kernel rule; the frontend only reads it.
        #   Reason (raised by agx_ui_lead, adopted by the PM): the reply_mode -> needs_reply rule
        #   already exists twice in the kernel (dispatch_task / quick_cmd_send); a third copy in the frontend
        #   would repeat the quick_cmd string-building drift lesson.
        #   Rule matches the kernel: reply_mode in (result, confirm) -> reply required; anything else (silent) -> not.
        "replyMode": (r.get("reply_mode") or "result"),
        "defaultReply": (
            "yes" if (r.get("reply_mode") or "result") in ("result", "confirm") else "no"
        ),
    } for r in rows][:limit]


# ══════════════════════════════════════════════════════════════════
# 5. Executions  (direct MySQL -- reads go direct, all fields present)
#    2026-10-07 Feng's feedback: agx_list_by_network(execution) trims to identity fields only,
#    with no exec_instruction / summary -> the detail page's "task executions" list and detail had nothing to show.
#    So switched to direct agx_task_execution, adding exec_instruction / summary / exec_name,
#    with taskName filled in from agx_task_base's task_id->task_name mapping.
# ══════════════════════════════════════════════════════════════════
def executions(caller: str = "", limit: int | None = None, run_uuid: str = "") -> list[dict]:
    """Execution list. Passing run_uuid fetches only that one (used to refresh a row after manual intervention).

    ★ Why a single-row mode: after a successful nudge **this row's** latest state must be swapped in.
      A full snapshot refresh (refreshPageData) remounts the whole page -- the status filter the user
      just set, the search term and the page they scrolled to are all lost, so one nudge bounces them
      back to page 1 and they assume they clicked wrong. A single-row refetch replaces only that row and
      the UI stays put.
"""
    limit = _MAX if limit is None else limit
    where = "network_id = %s"
    args: tuple = (_NETWORK_ID,)
    if run_uuid:
        where += " AND task_run_uuid = %s"
        args = (_NETWORK_ID, run_uuid)
    try:
        rows = kernel.query(
            f"""SELECT task_run_uuid, exec_name, task_id, agent_id, send_agent_id, session_id,
                      project_id, host_id,
                      plan_id, prev_task, next_task, run_status,
                      exec_instruction, summary, started_at, finished_at,
                      dispatched_at, created_at, timeout_seconds, alert_count,
                      reply_sent_at, reply_read, scheduled_at
                 FROM agx_task_execution
                WHERE {where}
                ORDER BY pkid DESC""",
            args,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("realdata executions query failed: %s", e)
        return []

    # task_id -> task_name mapping (one query, for list/detail display)
    task_names: dict[str, str] = {}
    try:
        for r in kernel.query(
            "SELECT task_id, task_name FROM agx_task_base WHERE network_id = %s",
            (_NETWORK_ID,),
        ):
            tid = str(r.get("task_id") or "")
            if tid:
                task_names[tid] = str(r.get("task_name") or "")
    except Exception as e:  # noqa: BLE001
        log.warning("executions task_name map failed: %s", e)

    # "manual intervention": how many times each run has been nudged (one query, avoiding N+1)
    nudges = _nudge_counts()
    now = datetime.now()

    out = []
    for r in rows:
        st = r.get("run_status") or "pending"
        if st not in _RUN_STATUS_KEEP:
            st = "pending"
        started, finished = r.get("started_at"), r.get("finished_at")
        dur = 0
        try:
            if started and finished:
                dur = int((finished - started).total_seconds())
        except Exception:  # noqa: BLE001
            dur = 0
        tid = str(r.get("task_id") or "")
        run_uuid = r.get("task_run_uuid") or ""
        # Whether to offer the "nudge / nudge acknowledgement" buttons -- decided server-side; the frontend just renders
        jd = _nudge_judge(
            status=st,
            dispatched_at=r.get("dispatched_at"),
            created_at=r.get("created_at"),
            timeout_seconds=r.get("timeout_seconds"),
            alert_count=r.get("alert_count"),
            reply_sent_at=r.get("reply_sent_at"),
            reply_read=r.get("reply_read"),
            now=now,
        )
        ng = nudges.get(run_uuid, {})
        out.append({
            "run_uuid": run_uuid,
            "execName": r.get("exec_name") or "",
            "taskId": tid,
            "taskName": task_names.get(tid, ""),
            "agentId": r.get("agent_id") or "",
            "agentName": r.get("agent_id") or "",
            # "who dispatched it": agx_task_execution.send_agent_id (written by the kernel's agx_dispatch_task).
            # Receiver = agent_id. All 5792 rows in the table have a value; 2488 of them have send == agent (self-dispatched).
            "sendAgentId": r.get("send_agent_id") or "",
            "sessionId": r.get("session_id") or "",
            # [2026-11-03 21:38 Feng] "add project to the task-execution search filters".
            # Take agx_task_execution.project_id directly (ownership was fixed when the execution was recorded, more accurate
            # than reverse-looking it up via the agent: an agent can change project, and historical executions should not drift with it).
            "projectId": r.get("project_id") or "",
            # [2026-11-06 Feng] "agx_task_execution has a host_id field; filter on that field directly"
            # Which machine the execution ran on = host_id (fixed at dispatch time).
            # Different from "which machine the agent belongs to": an agent that moved hosts must not drag historical executions with it.
            # For empty values (54 old rows) the frontend falls back to the agent's host; see executions.tsx.
            "hostId": r.get("host_id") or "",
            "planId": r.get("plan_id") or "",
            "prevTask": r.get("prev_task") or "",
            "nextTask": r.get("next_task") or "",
            "status": st,
            # [2026-11-03 Feng] "there is a kind of scheduled task... look at what scheduled tasks exist and add a filter"
            # The DB has no "trigger method" field, but **scheduled_at is the evidence**:
            # only agx_schedule_task writes an execution time, so scheduled_at is non-empty only for those; manual dispatch always leaves it empty.
            # So "scheduled" means scheduled_at is non-empty, not the task_type you might guess
            # (task_type is only normal/plan, and plan means "this is a step in a plan", unrelated to scheduling).
            "scheduledAt": _dt(r.get("scheduled_at")),
            # [2026-11-06 11:10] created_at is returned to the frontend separately: the leaderboard card's "last 7 days" uses
            #   **created time** (SQL: e.created_at >= NOW()-7d), not started_at.
            #   queued/draft rows have no started_at at all, so filtering on started_at misses them ->
            #   clicking through shows fewer rows than the card. For the two to agree, time filtering must use the same field.
            "createdAt": _dt(r.get("created_at")),
            "startedAt": _dt(started),
            "finishedAt": _dt(finished),
            "durationSeconds": dur,
            "instruction": r.get("exec_instruction") or "",
            "summary": r.get("summary") or "",
            "log": [],
            # ── manual intervention ────────────────────────────────────────
            "availableActions": jd["availableActions"],
            "nudgeState": jd["state"],
            "nudgeReason": jd["reason"],
            "ageSeconds": jd["ageSeconds"],
            "ageBasis": jd["ageBasis"],
            "timeoutSeconds": jd["timeoutSeconds"],
            "progressRatio": jd["progressRatio"],
            "nudgeCount": int(ng.get("count") or 0),
            "nudgeLastAt": ng.get("lastAt") or "",
        })
    return out[:limit]


# ══════════════════════════════════════════════════════════════════
# 5b. Manual intervention (manual nudge) -- decision rule + write path
# ══════════════════════════════════════════════════════════════════
# [2026-10-31 Feng] The watchdog's three automatic mechanisms **all have attempt caps**:
#   wake at 70/80/90% fires once per tier; alert at alert_count crests and stops; resend send_resend_max=2.
# Once spent, the task is still running -- the UI says "still running" while nobody will ever push it again.
# A human must be able to press a button. Design: docs/agent_docs/2026-10-31-manual-intervention.md.
#
# ★ All decisions are computed **server-side**: when the frontend clock disagrees with the server you get
#   "the button should be there but isn't / is there but shouldn't be", and anyone investigating reads only frontend code.
#
# ★ Do not use started_at to judge "how long it has run": it is lazily written (empty for 191 running rows in the last 7 days),
#   so using it turns "running for ages" into "just started". The baseline is dispatched_at.

_NUDGE_URGE_RATIO = 0.7          # aligned with the watchdog's first wake tier: do not disturb before 0.7t
_NUDGE_KINDS = ("auto", "urge", "ack_remind")
_NUDGE_DEFAULT_TIMEOUT = 1800    # matches the kernel's timeout_seconds default


def _nudge_judge(
    *,
    status: str,
    dispatched_at: Any,
    created_at: Any,
    timeout_seconds: Any,
    alert_count: Any,
    reply_sent_at: Any,
    reply_read: Any,
    now: datetime,
) -> dict:
    """Return "which buttons to offer", based on state.

    Returns {state, reason, availableActions, ageSeconds, ageBasis, timeoutSeconds, progressRatio}

    state values (matching the state table in the design doc):
        a still within expectation -> no button
        b approaching timeout      -> urge (-> executor)
        c already timed out        -> urge (-> executor, stronger wording)
        d receipt sent, unsigned   -> ack_remind (-> **originator**)
        e already alerted, still stuck -> urge (-> executor)
        - undecidable (no reliable timestamp)
"""
    try:
        timeout = int(timeout_seconds or 0) or _NUDGE_DEFAULT_TIMEOUT
    except (TypeError, ValueError):
        timeout = _NUDGE_DEFAULT_TIMEOUT

    # Baseline: dispatched_at primarily; older rows (dispatched_at was not persisted before 2026-10-27)
    # fall back to created_at and state honestly in ageBasis that it is "estimated from creation time" --
    # better to explain the basis in the UI than to leave a task with no button forever because of one empty field.
    basis, base = "", None
    for name, value in (("dispatched_at", dispatched_at), ("created_at", created_at)):
        if value is not None and str(value).strip() != "":
            base, basis = value, name
            break

    age: int | None = None
    ratio: float | None = None
    if base is not None:
        with contextlib.suppress(TypeError, ValueError):
            age = int((now - base).total_seconds())
            ratio = round(age / timeout, 3) if timeout > 0 else None
        if age is not None and age < 0:
            # Timestamp in the future (clock drift / dirty data): do not guess.
            # ★ Wording must be precise: the field **does have a value**, it is just untrustworthy -- saying "no timestamp"
            #   would send people to investigate a field that is clearly non-empty.
            age, ratio, basis = None, None, "future"

    actions: list[dict] = []
    state, reason = "-", ""

    if status == "running":
        if basis == "future":
            reason = "dispatched_at 在未来（时钟不一致或数据异常），无法判断已运行多久"
        elif age is None:
            reason = "没有可用的 dispatched_at / created_at，判断不了已经跑了多久"
        elif ratio is not None and ratio < _NUDGE_URGE_RATIO:
            state = "a"
            reason = f"已运行 {age}s / 预计 {timeout}s（{ratio:.0%}），仍在预期内"
        elif age < timeout:
            state = "b"
            actions = [{"kind": "urge", "label": "催办"}]
            reason = f"已运行 {age}s / 预计 {timeout}s（{ratio or 0:.0%}），接近超时"
        else:
            state = "c"
            actions = [{"kind": "urge", "label": "催办"}]
            reason = f"已超时 {age - timeout}s（累计 {age}s / 预计 {timeout}s）"

        # E: already alerted and still running -- the watchdog will not fire again; a human can push once more
        try:
            alerts = int(alert_count or 0)
        except (TypeError, ValueError):
            alerts = 0
        if alerts >= 1 and not any(a["kind"] == "urge" for a in actions):
            state = "e"
            actions = [{"kind": "urge", "label": "催办"}]
            reason = f"已告警 {alerts} 次仍在运行（{age if age is not None else '?'}s / 预计 {timeout}s）"

    # D is **orthogonal** to the run state: a sent receipt means the executor delivered, and what is missing is the originator's acknowledgement.
    # So D judges "nobody acknowledged this receipt", not "the task is still running".
    unread = reply_sent_at is not None and str(reply_sent_at).strip() != "" and not int(reply_read or 0)
    if unread:
        if not actions:
            state = "d"
        actions = [a for a in actions if a["kind"] != "ack_remind"]
        actions.append({"kind": "ack_remind", "label": "催签收"})
        tail = "回执已发给发起方但没人签收"
        reason = f"{reason}；{tail}" if reason else tail

    return {
        "state": state,
        "reason": reason,
        "availableActions": actions,
        "ageSeconds": age,
        "ageBasis": basis,
        "timeoutSeconds": timeout,
        "progressRatio": ratio,
    }


def _nudge_counts() -> dict[str, dict]:
    """How many times each run has been nudged, and when the last one was (event table manual_nudge).

    A read failure returns an empty dict -- the counts are **auxiliary** and their absence must not
    make the whole list unopenable, but it is logged (silent degradation is allowed for reads,
    never for writes).
"""
    out: dict[str, dict] = {}
    try:
        rows = kernel.query(
            """SELECT task_run_uuid, COUNT(*) AS n, MAX(created_at) AS last_at
                 FROM agx_task_execution_event
                WHERE event_type = 'manual_nudge'
             GROUP BY task_run_uuid"""
        )
    except Exception as e:  # noqa: BLE001
        log.warning("nudge counts query failed: %s", e)
        return out
    for r in rows:
        run = str(r.get("task_run_uuid") or "")
        if run:
            out[run] = {"count": int(r.get("n") or 0), "lastAt": _dt(r.get("last_at"))}
    return out


def nudge_execution(
    run_uuid: str,
    kind: str = "auto",
    note: str = "",
    caller: str = EDITOR_AGENT_ID,
) -> dict:
    """Manual intervention -- passes through to the kernel's `agx_nudge_execution` (no direct DB write, no execution created).

    [2026-10-31 Feng] Follow the watchdog's division of labour exactly; do not invent another one:
        urge       -> executor agent_id      (same as _try_wake)
        ack_remind -> originator send_agent_id (same as _notify_owner)
    Getting it backwards means "nudging someone who already handed in their homework to do homework".

    ★ Why the error codes are separate: **rate limiting** (repeat click within 60s) and **state
      mismatch** (the task already finished) are completely different things. Shown "operation
      failed", people assume a bug and file an issue; shown "only once per 60 seconds" they wait;
      shown "the task has finished, no need to nudge" they understand.
      -> RATE_LIMITED / STATE_MISMATCH / NOT_FOUND / KERNEL_NOT_READY are distinct.

    Raises:
        ToolRejected: rejected by the kernel (including rate limit / state mismatch / not found /
            kernel not deployed); message is its own text.
        RuntimeError: the MCP channel is unavailable.
"""
    kind = (kind or "auto").strip()
    if kind not in _NUDGE_KINDS:
        raise ToolRejected(
            "BAD_KIND",
            f"未知的干预类型 '{kind}'，只能是 {' / '.join(_NUDGE_KINDS)}",
        )

    try:
        raw = kernel.call_tool(
            "agx_nudge_execution",
            caller_agent_id=caller,
            task_run_uuid=run_uuid,
            kind=kind,
            note=str(note or ""),
        )
    except Exception as e:  # noqa: BLE001
        # Before the kernel is deployed, MCP returns a plain string for an unknown tool rather than raising;
        # this also covers the other possibility (the channel raises on an unknown tool), giving a readable reason either way:
        # "the feature is not deployed yet" must never look like "server 500".
        text = str(e)
        if "unknown tool" in text.lower():
            raise ToolRejected("KERNEL_NOT_READY", _KERNEL_NOT_READY_MSG) from e
        raise

    parsed: dict = {}
    if isinstance(raw, dict):
        parsed = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if text.lower().startswith("unknown tool"):
            raise ToolRejected("KERNEL_NOT_READY", _KERNEL_NOT_READY_MSG)
        with contextlib.suppress(Exception):
            maybe = json.loads(text)
            parsed = maybe if isinstance(maybe, dict) else {}

    status = str(parsed.get("status") or "")
    if status in ("error", "denied", "failed", "rate_limited") or parsed.get("error"):
        message = str(parsed.get("message") or parsed.get("error") or "内核拒绝该操作")
        # ★ The kernel (2026-10-31 release) provides `code` straight in the error body:
        #   NOT_FOUND / STATE_MISMATCH / BAD_KIND / RATE_LIMITED / NO_TARGET / DELIVERY_FAILED
        #   copy the structured code when present; do not guess from keywords -- guessing wrong leaves people puzzling over "operation failed".
        raise ToolRejected(_nudge_error_code(message, status, str(parsed.get("code") or "")), message, parsed)
    if status != "success" and not parsed:
        text = raw if isinstance(raw, str) else str(raw)
        raise ToolRejected("MCP_ERROR", text[len("Error:"):].strip() if text.startswith("Error:") else text)

    def _pick(*keys: str, default: Any = "") -> Any:
        for k in keys:
            if parsed.get(k) not in (None, ""):
                return parsed[k]
        return default

    return {
        "ok": True,
        "kind": str(_pick("kind", default=kind)),
        "runUuid": str(_pick("task_run_uuid", "run_uuid", default=run_uuid)),
        "deliveredTo": str(_pick("delivered_to", "deliveredTo")),
        "toAgent": str(_pick("to_agent", "toAgent")),
        "eventId": str(_pick("event_id", "eventId")),
        "state": parsed.get("state_snapshot") or parsed.get("state") or {},
        "raw": parsed,
    }


def _confirm_context(run_uuid: str, code: str) -> dict:
    """For STATE_MISMATCH, append "what actually happened to this one" -- so the person knows the next step.

    [2026-11-06 ~21:5x, Feng's real feedback] He clicked approve, the UI reported
    `is 'succeeded', not 'awaiting_confirm'`, with a hint "someone may have approved/rejected first";
    after refreshing he found **he himself had just approved successfully** (at the moment of success
    he thought it had failed).

    ★ The three reasons for "no longer awaiting confirmation" are completely different to a person and
      must not be papered over with "maybe someone else approved first":
        needs_confirm=2  -> already approved (current state must be succeeded)
        needs_confirm=3  -> already rejected (current state must be failed)
        otherwise        -> this one never went through confirmation (needs_confirm=0); it closed out as soon as it finished
    The former means "it is already fine, leave it alone"; the latter means "you expected a
    confirmation it never needed" -- conflated, the person keeps investigating a problem that does
    not exist.

    The DB is queried only for STATE_MISMATCH: the other codes (NOT_FOUND / PERMISSION / BAD_ACTION)
    have causes unrelated to state, so an extra query adds no information.
"""
    if code != "STATE_MISMATCH":
        return {}
    try:
        rows = kernel.query(
            "SELECT run_status, COALESCE(needs_confirm, 0) AS needs_confirm, "
            "       finished_at, updated_at, reply_read_at "
            "FROM agx_task_execution WHERE task_run_uuid = %s",
            (run_uuid,),
        )
    except Exception:  # noqa: BLE001 -- if the lookup fails, return nothing; do not turn a 409 into a 500
        return {}
    if not rows:
        return {}
    r = rows[0]
    nc = int(r.get("needs_confirm") or 0)
    how = {2: "approved", 3: "rejected"}.get(nc, "no_confirm_needed")
    def _iso(v):
        return v.isoformat(sep=" ", timespec="seconds") if hasattr(v, "isoformat") else (str(v) if v else "")
    return {
        "currentStatus": r.get("run_status") or "",
        "needsConfirm": nc,
        "handledHow": how,
        "handledAt": _iso(r.get("updated_at")) if how != "no_confirm_needed" else "",
        "finishedAt": _iso(r.get("finished_at")),
    }


def confirm_execution(
    run_uuid: str,
    action: str = "approve",
    caller: str = EDITOR_AGENT_ID,
) -> dict:
    """Approve / reject as a human -- passes through to the kernel's `agx_confirm_task` (no direct DB write).

    [2026-11-06 Feng] For a task with needs_confirm=1 the executor does not write succeeded when done;
    it lands in `awaiting_confirm`, and only after a **human** has looked at the result and approved
    is the chain considered complete. Without this entry point the UI can display "awaiting human
    confirmation" while there is nowhere to click -- a one-way dead end (the task stops there forever
    and the chain never advances).

    action:
        approve -> run_status=succeeded, chained tasks activate automatically, plan completion is re-evaluated
        reject  -> run_status=failed, needs_reply=1 (a rejection must never be silent to the originator)

    ★ The calling identity is fixed as agx_core (workshop_manager), same as nudge:
      a **human** is clicking in the console, but the audit can only record this one agent (legacy
      issue, noted in memory). The kernel forbids an executor from approving its own work, but
      **global roles are exempt** -- otherwise when the task being executed happens to belong to
      agx_core, the human clicks and nothing happens.

    Raises:
        ToolRejected: rejected by the kernel. Error codes are split by "what the person should do":
            NOT_FOUND        -> no such execution (possibly already cleaned up)
            STATE_MISMATCH   -> no longer awaiting confirmation (someone approved already / it was rejected)
            PERMISSION_DENIED-> the current identity may not approve
            BAD_ACTION       -> action is not approve/reject
        RuntimeError: the MCP channel is unavailable.
"""
    action = (action or "").strip().lower()
    if action not in ("approve", "reject"):
        raise ToolRejected("BAD_ACTION", f"未知的确认动作 '{action}'，只能是 approve / reject")

    try:
        raw = kernel.call_tool(
            "agx_confirm_task",
            caller_agent_id=caller,
            task_run_uuid=run_uuid,
            action=action,
        )
    except Exception as e:  # noqa: BLE001
        # ★ When the kernel tool **returns an "Error: ..." string**, the MCP client raises it as an exception
        #   (McpToolError). So "business rejection" and "channel failure" must be separated here:
        #   the former goes back to the user as NOT_FOUND / STATE_MISMATCH (the person knows what to do next),
        #   only the latter is a 503. Originally only the success return value was parsed -> every rejection became a 503.
        text = str(e).strip()
        low = text.lower()
        if "unknown tool" in low:
            raise ToolRejected(
                "KERNEL_NOT_READY",
                "内核方法 agx_confirm_task 还没上线：BFF 与界面已按契约就绪，等内核提供后即可联调。",
            ) from e
        if low.startswith("error:"):
            msg = text[len("error:"):].strip()
            code = _confirm_error_code(msg)
            raise ToolRejected(code, msg, dict({"raw": text}, **_confirm_context(run_uuid, code))) from e
        if text.startswith("拒绝"):
            raise ToolRejected("PERMISSION_DENIED", text, {"raw": text}) from e
        raise

    # Kernel contract: success returns the plain text `Execution run_x approved: run_status=succeeded, needs_confirm=2.`
    #           failure returns `Error: ...`; the exception path returns _log_exception's string.
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    text = (text or "").strip()

    if text.lower().startswith("error:"):
        msg = text[len("error:"):].strip()
        code = _confirm_error_code(msg)
        raise ToolRejected(code, msg, dict({"raw": text}, **_confirm_context(run_uuid, code)))
    # ★ Cannot only look for "Error:" -- the kernel's rejected-because-executor message starts with a Chinese colon
    #   and only ever appears on rejection. Missing it turns into "it errored but the UI says success".
    if text.startswith("拒绝") or text.startswith("拒绝："):
        raise ToolRejected("PERMISSION_DENIED", text, {"raw": text})
    if not text:
        raise ToolRejected("MCP_ERROR", "内核返回空结果（不确定是否生效，请刷新确认）")

    m = re.search(r"run_status=([a-z_]+)", text)
    nc = re.search(r"needs_confirm=(\d+)", text)
    return {
        "ok": True,
        "runUuid": run_uuid,
        "action": action,
        "status": m.group(1) if m else "",
        "needsConfirm": int(nc.group(1)) if nc else None,
        "message": text,
    }


def _confirm_error_code(msg: str) -> str:
    """Map a kernel rejection reason to "what the person should do" (copy structured semantics when present; do not guess synonyms)."""
    low = msg.lower()
    if "not found" in low or "不存在" in msg:
        return "NOT_FOUND"
    if "not 'awaiting_confirm'" in low or "pending_confirm" in low:
        return "STATE_MISMATCH"
    if "不能给自己放行" in msg or "permission" in low or "权限" in msg:
        return "PERMISSION_DENIED"
    return "MCP_ERROR"


_KERNEL_NOT_READY_MSG = (
    "内核方法 agx_nudge_execution 还没上线：BFF 与界面已按契约就绪，"
    "等内核提供后即可联调（不需要改前端）。"
)


_NUDGE_KERNEL_CODES = frozenset(
    {
        "NOT_FOUND",
        "STATE_MISMATCH",
        "BAD_KIND",
        "RATE_LIMITED",
        "NO_TARGET",
        "DELIVERY_FAILED",
        "BAD_ARGS",
        "PERMISSION_DENIED",
    }
)


def _nudge_error_code(message: str, status: str, code: str = "") -> str:
    """Map the kernel's rejection reason to the "what the person should do" categories.

    [2026-10-31 integration] The kernel already provides a structured `code` in the error body -- prefer it.
    Keyword matching is only a **fallback for older kernels / exception paths**: guessing synonyms is
    never complete (measured: missed "a given task can only be nudged once per 60 seconds"), so having a code and not using it is
    gratuitous risk.
"""
    if code and code.upper() in _NUDGE_KERNEL_CODES:
        return code.upper()
    low = message.lower()
    # ★ A real bug caught in 2026-10-31 acceptance: only "rate limit / too frequent" was recognised, missing the phrase the kernel most likely uses:
    #   "a given task can only be nudged once per 60 seconds" -> classified as MCP_REJECTED, so the UI said
    #   "could not send" instead of "too frequent, try again shortly". Rate-limit wording must cover what actually appears.
    if (
        status == "rate_limited"
        or any(k in message for k in ("限流", "频繁", "冷却", "秒内", "只能催", "太多次"))
        or any(k in low for k in ("rate limit", "too many", "throttl", "cooldown"))
    ):
        return "RATE_LIMITED"
    if (
        "已结束" in message
        or "已结案" in message
        or "状态不符" in message
        or "不匹配" in message
        or "mismatch" in message.lower()
    ):
        return "STATE_MISMATCH"
    if "不存在" in message or "not found" in message.lower():
        return "NOT_FOUND"
    if "权限" in message or "denied" in message.lower():
        return "PERMISSION_DENIED"
    return "MCP_REJECTED"


# ══════════════════════════════════════════════════════════════════
# 6. Plans  (agx_plan -- direct MySQL)
# ══════════════════════════════════════════════════════════════════
def plans(caller: str = "", limit: int | None = None) -> list[dict]:
    """Plan list (agx_plan). Read-only, direct MySQL.

    Historical trap (2026-10-08, Feng reported "no plan data"):
      the original implementation went through the MCP tool `agx_plan_list`, which requires **level 5**;
      the BFF calls with the logged-in user name (level 0-3), so it was always rejected -> a dict instead
      of a list -> `isinstance(rows, list)` failed -> silently skipped -> the frontend received an empty array.
      Fix: as with skills/hosts/notices, talk to SQL directly and avoid MCP tools that need high privilege.
"""
    limit = _MAX if limit is None else limit
    try:
        rows = kernel.query(
            """SELECT plan_id, parent_plan_id, project_id, description, plan_status,
                      steps_json, created_by, created_at, completed_at
                 FROM agx_plan
                WHERE project_id IN (
                      SELECT project_id FROM agx_project WHERE network_id = %s
                )
                ORDER BY created_at DESC""",
            (_NETWORK_ID,),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("realdata plans query failed: %s", e)
        return []

    # project_id -> project_name mapping
    proj_names: dict[str, str] = {}
    try:
        for r in kernel.query(
            "SELECT project_id, project_name FROM agx_project WHERE network_id = %s",
            (_NETWORK_ID,),
        ):
            proj_names[str(r.get("project_id"))] = str(r.get("project_name") or "")
    except Exception as e:  # noqa: BLE001
        log.warning("plans project name map failed: %s", e)

    # created_by -> owner name mapping (agent display name first, raw id as fallback)
    agent_names: dict[str, str] = {}
    try:
        for r in kernel.query(
            "SELECT agent_id, agent_name FROM agx_agent_registry WHERE network_id = %s",
            (_NETWORK_ID,),
        ):
            agent_names[str(r.get("agent_id"))] = str(r.get("agent_name") or "")
    except Exception as e:  # noqa: BLE001
        log.warning("plans owner name map failed: %s", e)

    # ── split steps_json: step count + each step's run_uuid ────────────────────
    # [2026-11-03 Feng] "The completion percentage looks wrong; many are done but the data was not synced"
    #   The original `doneSteps` was **always 0** (a hardcoded placeholder), so every plan read 0/N.
    #   The data was always there (agx_task_execution has them all by plan_id, and Redis holds hot values too);
    #   what was missing was that "nobody counted".
    parsed: list[tuple[dict, int, list[str]]] = []
    for r in rows:
        obj = None
        raw = r.get("steps_json") or ""
        try:
            obj = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw
        except Exception as e:  # noqa: BLE001
            log.warning("plans parse steps failed %s: %s", r.get("plan_id"), e)
        steps: list = []
        if isinstance(obj, dict) and isinstance(obj.get("steps"), list):
            steps = obj["steps"]
        elif isinstance(obj, list):
            steps = obj
        ids = [_str(s.get("run_uuid")) for s in steps if isinstance(s, dict) and s.get("run_uuid")]
        parsed.append((r, len(steps), ids))

    # ── real completion: definition **identical** to the flow graph /plans/{id}/graph ──────
    #   denominator = the step count in steps_json (the nodes the graph can draw)
    #   numerator   = how many of those run_uuids have run_status = succeeded
    #   same source (steps_json run_uuids x agx_task_execution) -> the list's completion must equal the
    #   "succeeded" node count on the flow graph; you never get "the list says 30% while the graph is all green".
    #
    #   Fetching has two layers (same strategy as plan_graph):
    #     MySQL = the persisted truth (one query by plan_id; do not use 1400 IN placeholders)
    #     Redis = hot-value overlay (the kernel writes on every state transition; the row MySQL just wrote may lag)
    #   Overlay Redis only for plans that are "activated (still moving)" -- completed/draft plans never change again,
    #   so there is no point hitting Redis for them on every list request.
    plan_ids = [_str(r.get("plan_id")) for r, _, _ in parsed]
    exec_status: dict[str, str] = {}
    if plan_ids:
        try:
            ph = ",".join(["%s"] * len(plan_ids))
            for e in kernel.query(
                f"SELECT plan_id, task_run_uuid, run_status FROM agx_task_execution "
                f"WHERE plan_id IN ({ph})",
                tuple(plan_ids),
            ):
                exec_status[_str(e.get("task_run_uuid"))] = _str(e.get("run_status"))
        except Exception as e:  # noqa: BLE001
            log.warning("plans execution status query failed: %s", e)
    moving_ids = [
        ru
        for r, _, ids in parsed
        if _str(r.get("plan_status")) in ("activated", "running", "pending")
        for ru in ids
    ]
    for ru, st in _redis_statuses(moving_ids).items():
        if st:
            exec_status[ru] = st

    out: list[dict] = []
    for r, total_steps, ids in parsed:
        pid = _str(r.get("project_id"))
        owner = _str(r.get("created_by"))
        done_steps = sum(1 for ru in ids if exec_status.get(ru) == "succeeded")
        out.append({
            "id": _str(r.get("plan_id")),
            "name": (_str(r.get("description")) or _str(r.get("plan_id")))[:60],
            "projectId": pid,
            "projectName": proj_names.get(pid, pid),
            "owner": owner,
            "ownerName": agent_names.get(owner, owner),
            "trigger": "手动",
            "status": _plan_status(r.get("plan_status")),
            "totalSteps": total_steps,
            "doneSteps": done_steps,
            "nextRunAt": _dt(r.get("completed_at") or r.get("created_at")),
            "updatedAt": _dt(r.get("created_at")),
        })
    return out[:limit]


# ══════════════════════════════════════════════════════════════════
# 6b. Plan Graph  (agx_plan.steps_json + agx_task_execution -- plan flow DAG)
# ══════════════════════════════════════════════════════════════════
def plan_graph(plan_id: str, caller: str = "") -> dict:
    """Plan flow-graph data (nodes + edges) for the frontend Plan Flow component to render the DAG.

    Data sources (all direct MySQL):
      - agx_plan.steps_json       -> step structure (run_uuid/task_id/agent_id/instruction/...)
      - agx_task_execution        -> real run state run_status (aligned by task_run_uuid)
    Topology:
      - children[]        -> parent->child structural edges (solid)
      - prev_task/next_task -> sequence edges (dashed)
"""
    rows = kernel.query(
        """SELECT plan_id, project_id, description, plan_status, steps_json, created_at
             FROM agx_plan WHERE plan_id = %s LIMIT 1""",
        (plan_id,),
    )
    if not rows:
        return {}
    p = rows[0]

    # 1) parse steps
    steps: list[dict] = []
    raw = p.get("steps_json") or ""
    try:
        obj = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw
        if isinstance(obj, dict):
            steps = obj.get("steps") or []
        elif isinstance(obj, list):
            steps = obj
    except Exception as e:  # noqa: BLE001
        log.warning("plan_graph parse steps failed %s: %s", plan_id, e)
        steps = []

    run_uuids = [_str(s.get("run_uuid")) for s in steps if s.get("run_uuid")]

    # 2) real run state: run_uuid -> run_status
    #    ★ 2026-10-27 Feng "state must be real-time": **Redis first, MySQL as fallback**.
    #      the kernel _sync_entity-writes agx:entity:execution:{run} on every state transition,
    #      so Redis is the hot latest value; MySQL is the persisted value (may lag).
    #      Leave it empty only when neither has it (the frontend shows "pending").
    status_map: dict[str, str] = {}
    # ★ 2026-11-03 Feng: node detail must be able to "open the session and read the content", so session_id and start/end times come along.
    #   One query fetches everything (stop making extra trips for a few fields -- this table is keyed by run_uuid).
    exec_map: dict[str, dict] = {}
    if run_uuids:
        try:
            ph = ",".join(["%s"] * len(run_uuids))
            for r in kernel.query(
                f"SELECT task_run_uuid, run_status, session_id, started_at, finished_at "
                f"FROM agx_task_execution WHERE task_run_uuid IN ({ph})",
                tuple(run_uuids),
            ):
                ru = _str(r.get("task_run_uuid"))
                status_map[ru] = _str(r.get("run_status"))
                exec_map[ru] = {
                    "session_id": _str(r.get("session_id")),
                    "started_at": _dt(r.get("started_at")),
                    "finished_at": _dt(r.get("finished_at")),
                }
        except Exception as e:  # noqa: BLE001
            log.warning("plan_graph status map failed %s: %s", plan_id, e)
        live = _redis_statuses(run_uuids)
        for ru, st in live.items():
            if st:
                status_map[ru] = st

    # agent_id -> name
    agent_names: dict[str, str] = {}
    for r in kernel.query(
        "SELECT agent_id, agent_name FROM agx_agent_registry WHERE network_id = %s",
        (_NETWORK_ID,),
    ):
        agent_names[_str(r.get("agent_id"))] = _str(r.get("agent_name"))

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_edge: set[str] = set()

    def add_edge(frm: str, to: str, typ: str) -> None:
        if not frm or not to or frm == to:
            return
        k = f"{frm}->{to}:{typ}"
        if k in seen_edge:
            return
        seen_edge.add(k)
        edges.append({"from": frm, "to": to, "type": typ})

    for s in steps:
        ru = _str(s.get("run_uuid"))
        if not ru:
            continue
        children = [c for c in (s.get("children") or []) if c]
        agent = _str(s.get("agent_id"))
        nodes.append({
            "run_uuid": ru,
            "task_id": _str(s.get("task_id")),
            "agent_id": agent,
            "agent_name": agent_names.get(agent, agent),
            "instruction": _str(s.get("instruction"))[:2000],
            "exec_name": _str(s.get("exec_name")),
            "timeout_seconds": s.get("timeout_seconds"),
            "scheduled_at": _str(s.get("scheduled_at")) or None,
            "prev_task": _str(s.get("prev_task")) or None,
            "next_task": _str(s.get("next_task")) or None,
            "children": children,
            "n_children": len(children),
            "run_status": status_map.get(ru, ""),  # real status (may be empty)
            # session id = the key to open the content (empty before dispatch)
            "session_id": exec_map.get(ru, {}).get("session_id", ""),
            "started_at": exec_map.get(ru, {}).get("started_at", ""),
            "finished_at": exec_map.get(ru, {}).get("finished_at", ""),
        })
        for c in children:
            add_edge(ru, c, "children")
        if s.get("prev_task"):
            add_edge(_str(s.get("prev_task")), ru, "next")

    # task_id -> task_name (add a readable label)
    task_names: dict[str, str] = {}
    tids = [_str(n.get("task_id")) for n in nodes if n.get("task_id")]
    if tids:
        try:
            ph = ",".join(["%s"] * len(tids))
            for r in kernel.query(
                f"SELECT task_id, task_name FROM agx_task_base WHERE task_id IN ({ph})",
                tuple(tids),
            ):
                task_names[_str(r.get("task_id"))] = _str(r.get("task_name"))
        except Exception as e:  # noqa: BLE001
            log.warning("plan_graph task name map failed %s: %s", plan_id, e)
    for n in nodes:
        n["task_name"] = task_names.get(n.get("task_id") or "", "")

    return {
        "planId": plan_id,
        "projectId": _str(p.get("project_id")),
        "description": _str(p.get("description")),
        "planStatus": _plan_status(p.get("plan_status")),
        "createdAt": _dt(p.get("created_at")),
        "nNodes": len(nodes),
        "nEdges": len(edges),
        "hasRealStatus": any(n.get("run_status") for n in nodes),
        "nodes": nodes,
        "edges": edges,
    }
# ── plan live state (Redis hot cache) ──────────────────────────────────
_REDIS_RUN_PREFIX = "agx:entity:execution:"
_REDIS_PLAN_RUNS = "agx:idx:execution:by_plan:"


def _redis_statuses(run_uuids: list[str]) -> dict[str, str]:
    """Batch-fetch run_status (Redis hot values). One pipeline, not N round trips.

    Failures only warn and return empty (real-time is an enhancement; it must not make the whole graph unopenable).
"""
    if not run_uuids:
        return {}
    try:
        r = kernel.redis_client()
        pipe = r.pipeline(transaction=False)
        for ru in run_uuids:
            pipe.hget(f"{_REDIS_RUN_PREFIX}{ru}", "run_status")
        vals = pipe.execute()
    except Exception as e:  # noqa: BLE001
        log.warning("plan redis status pipeline failed: %s", e)
        return {}
    out: dict[str, str] = {}
    for ru, v in zip(run_uuids, vals, strict=False):
        if v:
            out[ru] = str(v)
    return out


def _plan_run_ids(plan_id: str) -> list[str]:
    """Run list for a plan: steps_json is authoritative, the Redis by_plan index fills gaps."""
    ids: list[str] = []
    try:
        rows = kernel.query(
            "SELECT steps_json FROM agx_plan WHERE plan_id = %s LIMIT 1", (plan_id,)
        )
        if rows:
            raw = rows[0].get("steps_json") or ""
            obj = json.loads(raw) if isinstance(raw, str) and raw.strip() else raw
            steps = (obj.get("steps") if isinstance(obj, dict) else obj) or []
            ids = [_str(x.get("run_uuid")) for x in steps if isinstance(x, dict) and x.get("run_uuid")]
    except Exception as e:  # noqa: BLE001
        log.warning("plan_run_ids steps failed %s: %s", plan_id, e)
    try:
        extra = kernel.redis_client().zrange(f"{_REDIS_PLAN_RUNS}{plan_id}", 0, -1)
        for ru in extra or []:
            if ru and ru not in ids:
                ids.append(str(ru))
    except Exception as e:  # noqa: BLE001
        log.warning("plan_run_ids redis index failed %s: %s", plan_id, e)
    return ids


def _started_at_map(plan_id: str, ids: list[str]) -> dict[str, str]:
    """run_uuid -> real start time (used to locate "the most recently started step").

    ★ Why go back to MySQL instead of Redis (2026-11-03):
      the `agx:entity:execution:*` hash has **no** started_at / dispatched_at field, only run_status.
      And Redis is exactly what cannot guarantee freshness -- locating wants "who is newest"; a few
      seconds of lag is fine, but values must not be invented.
    ★ Why fall back to dispatched_at when started_at may be empty:
      started_at is **lazily written by the kernel** (only filled in when an agent queries its own
      execution); in the morning patrol on 2026-11-03 4 of 5 shifts were empty.
      For "most recently started", dispatch time and start time differ by seconds -- good enough.
      Left empty, that step counts as "never started" and takes no part in the comparison (never treat it as 1970).
"""
    out: dict[str, str] = {}
    if not ids:
        return out
    try:
        rows = kernel.query(
            """SELECT task_run_uuid ru, started_at sa, dispatched_at da
                 FROM agx_task_execution WHERE plan_id = %s""",
            (plan_id,),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("plan started_at query failed %s: %s", plan_id, e)
        return out
    for r in rows or []:
        ru = _str(r.get("ru"))
        v = r.get("sa") or r.get("da")
        if ru and v is not None:
            out[ru] = _dt(v)
    return out


def plan_live_status(plan_id: str, run_uuids: list[str] | None = None) -> dict:
    """Plan live status (a lightweight endpoint for frontend polling).

    [2026-10-27 Feng] "The flow graph state must be real-time; there is data in Redis"
    -- return only the run_uuid -> status mapping and counts, without instruction or node structure,
       so a 5-second poll does not re-ship the full text of 137 steps every round.
"""
    ids = list(dict.fromkeys(run_uuids or _plan_run_ids(plan_id)))
    live = _redis_statuses(ids)
    counts = {"total": len(ids), "running": 0, "succeeded": 0, "failed": 0,
              "pending": 0, "cancelled": 0, "unknown": 0}
    for ru in ids:
        st = live.get(ru, "")
        if st in counts:
            counts[st] += 1
        elif st:
            counts["unknown"] += 1
        else:
            counts["pending"] += 1  # no cache = not dispatched yet
    return {
        "planId": plan_id,
        "refreshedAt": _now_ts(),
        "source": "redis",
        "statuses": live,
        # 2026-11-03: the frontend wants "put the most recently started step in the centre", which needs a time, not just a status
        "startedAt": _started_at_map(plan_id, ids),
        "counts": counts,
        "missing": [ru for ru in ids if ru not in live][:50],
    }


def _now_ts() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ══════════════════════════════════════════════════════════════════
# 7. Skills shared library  (agx_skill)
# ══════════════════════════════════════════════════════════════════
def skills(caller: str = "", limit: int | None = None) -> list[dict]:
    """Skill sharing library (reusable skills that agents share with each other).

    Direct MySQL agx_skill table; read-only. Signal ordering: helpful_count -> use_count -> updated_at.
    Field mapping: MySQL row -> frontend mock field (mock/agentgeex.ts's SkillRow).
"""
    limit = _MAX if limit is None else limit
    try:
        rows = kernel.query(
            """SELECT skill_id, skill_name, category, description, content, tags,
                      source_agent_id, use_count, helpful_count, active_status,
                      created_at, updated_at
                 FROM agx_skill
                WHERE network_id = %s AND active_status = 1
                ORDER BY helpful_count DESC, use_count DESC, updated_at DESC""",
            (_NETWORK_ID,),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("realdata skills query failed: %s", e)
        return []

    # Contributor name mapping (the skill page shows "contributed by X" instead of a bare agent_id)
    agent_names: dict[str, str] = {}
    try:
        for r in kernel.query(
            "SELECT agent_id, agent_name FROM agx_agent_registry WHERE network_id = %s",
            (_NETWORK_ID,),
        ):
            agent_names[str(r.get("agent_id"))] = str(r.get("agent_name") or "")
    except Exception as e:  # noqa: BLE001
        log.warning("skills agent name map failed: %s", e)

    out: list[dict] = []
    for r in rows:
        src = _str(r.get("source_agent_id"))
        out.append({
            "id": _str(r.get("skill_id")),
            "name": _str(r.get("skill_name")),
            "category": _str(r.get("category")) or "other",
            "description": _str(r.get("description")),
            "content": _str(r.get("content")),
            "tags": _str(r.get("tags")),
            "sourceAgentId": src,
            "sourceAgentName": agent_names.get(src, src),
            "useCount": int(r.get("use_count") or 0),
            "helpfulCount": int(r.get("helpful_count") or 0),
            "createdAt": _dt(r.get("created_at")),
            "updatedAt": _dt(r.get("updated_at")),
        })
    return out[:limit]


def hosts(caller: str = "", limit: int | None = None) -> list[dict]:
    """Compute-host assets (agx_host_inventory). Read-only, direct MySQL.

    Field mapping: MySQL row -> frontend mock field (mock/agentgeex.ts's HostRow).
"""
    limit = _MAX if limit is None else limit
    try:
        rows = kernel.query(
            """SELECT h.host_id, h.host_alias, h.endpoint_ip, h.ssh_port, h.os_platform,
                      h.platforms,
                      h.task_slots_limit, h.active_task_count, h.active_status,
                      h.presence_status, h.x_desc, h.last_heartbeat,
                      TIMESTAMPDIFF(SECOND, h.last_heartbeat, NOW()) AS hb_age,
                      (SELECT COUNT(*) FROM agx_agent_registry a
                        WHERE a.host_id = h.host_id AND a.active_status = 1) AS agent_count
                 FROM agx_host_inventory h
                WHERE h.network_id = %s
                ORDER BY h.active_status DESC, h.presence_status DESC, h.host_id""",
            (_NETWORK_ID,),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("realdata hosts query failed: %s", e)
        return []

    out: list[dict] = []
    for r in rows:
        out.append({
            "id": _str(r.get("host_id")),
            "alias": _str(r.get("host_alias")) or _str(r.get("host_id")),
            "ip": _str(r.get("endpoint_ip")),
            "sshPort": int(r.get("ssh_port") or 22),
            "os": _str(r.get("os_platform")),
            # [2026-11-04] Platforms available on this host and how to reach them. A JSON column reads back as str,
            # so if it cannot be parsed return [] (better to render no links than hand the frontend a bad value).
            "platforms": _json_obj_list(r.get("platforms")),
            "slotsLimit": int(r.get("task_slots_limit") or 0),
            "activeTasks": int(r.get("active_task_count") or 0),
            "active": int(r.get("active_status") or 0) == 1,
            # [2026-11-07] online is decided by the heartbeat only (same definition as the system status page).
            # presence_status is still emitted, but **no longer participates in the online decision** -- it is dead.
            "online": _is_online_by_heartbeat(r.get("hb_age")),
            "presence": int(r.get("presence_status") or 0) == 1,
            "lastHeartbeat": _dt(r.get("last_heartbeat")),
            "heartbeatAgeSeconds": int(r["hb_age"]) if r.get("hb_age") is not None else None,
            # [2026-10-28] Active agent count on the host (for the list; previously only task-slot usage)
            "agentCount": int(r.get("agent_count") or 0),
            "xDesc": _str(r.get("x_desc")),
        })
    return out[:limit]


def notices(caller: str = "", limit: int | None = None) -> list[dict]:
    """Agent announcements / work logs (agx_agent_notice). Read-only, direct MySQL.

    Field mapping: MySQL row -> frontend mock field (mock/agentgeex.ts's NoticeRow).
    Includes agent_name / project_name mappings so the list can display them directly.
"""
    limit = _MAX if limit is None else limit
    try:
        rows = kernel.query(
            """SELECT notice_id, project_id, agent_id, task_id, run_uuid,
                      notice_type, title, content, visibility, active_status, created_at
                 FROM agx_agent_notice
                WHERE network_id = %s
                ORDER BY created_at DESC""",
            (_NETWORK_ID,),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("realdata notices query failed: %s", e)
        return []

    # agent_id -> agent_name / project_id -> project_name mapping
    agent_names: dict[str, str] = {}
    try:
        for r in kernel.query(
            "SELECT agent_id, agent_name FROM agx_agent_registry WHERE network_id = %s",
            (_NETWORK_ID,),
        ):
            agent_names[str(r.get("agent_id"))] = str(r.get("agent_name") or "")
    except Exception as e:  # noqa: BLE001
        log.warning("notices agent name map failed: %s", e)

    proj_names: dict[str, str] = {}
    try:
        for r in kernel.query(
            "SELECT project_id, project_name FROM agx_project WHERE network_id = %s",
            (_NETWORK_ID,),
        ):
            proj_names[str(r.get("project_id"))] = str(r.get("project_name") or "")
    except Exception as e:  # noqa: BLE001
        log.warning("notices project name map failed: %s", e)

    out: list[dict] = []
    for r in rows:
        aid = _str(r.get("agent_id"))
        pid = _str(r.get("project_id"))
        out.append({
            "id": _str(r.get("notice_id")),
            "projectId": pid,
            "projectName": proj_names.get(pid, pid),
            "agentId": aid,
            "agentName": agent_names.get(aid, aid),
            "taskId": _str(r.get("task_id")),
            "runUuid": _str(r.get("run_uuid")),
            "type": _str(r.get("notice_type")) or "log",
            "title": _str(r.get("title")),
            "content": _str(r.get("content")),
            "visibility": _str(r.get("visibility")) or "project",
            "createdAt": _dt(r.get("created_at")),
        })
    return out[:limit]


# ══════════════════════════════════════════════════════════════════
# 8. Dashboard statistics (aggregated from the entities above, no SQL)
# ══════════════════════════════════════════════════════════════════
# ── system status board: aggregation SQL (rewritten 2026-10-28) ────────────────────
# Feng: "the system status data is not dynamic" -> the page polls this function every 5s.
# So the old approach -- "pull every list into memory and count" -- is **not usable** (a 24MB snapshot once is bad enough;
# once every 5s would kill the BFF). Switched to aggregation SQL: counters are queried each round, heavy metrics (trends/leaderboards)
# use a 30s in-process cache -- trends aggregate by day and cannot change within 30s.
_DASH_HEAVY_TTL = 30.0
_dash_heavy_cache: dict[str, Any] = {"at": 0.0, "data": {}}


def _day_axis(days: int = 7) -> list[str]:
    """Date axis for the last N days (including today), used to fill missing days (otherwise the chart skips days)."""
    today = __import__("datetime").date.today()
    return [
        (today - __import__("datetime").timedelta(days=i)).isoformat()
        for i in range(days - 1, -1, -1)
    ]


def _heavy_dashboard_parts() -> dict:
    """Trends / leaderboards / session curve / role distribution -- 30s cache (daily aggregation; cannot change in a short window)."""
    import time as _time

    now = _time.time()
    if now - float(_dash_heavy_cache["at"] or 0) < _DASH_HEAVY_TTL and _dash_heavy_cache["data"]:
        return _dash_heavy_cache["data"]

    axis = _day_axis(7)
    data: dict = {}

    # Execution trend for the last 7 days (by creation date)
    rows = kernel.query(
        """SELECT DATE(created_at) d, COUNT(*) n, COALESCE(SUM(total_tokens),0) tk
             FROM agx_task_execution
            WHERE network_id = %s AND created_at >= CURDATE() - INTERVAL 6 DAY
            GROUP BY DATE(created_at)""",
        (_NETWORK_ID,),
    )
    by_day = {_dt(r.get("d"))[:10]: (int(r.get("n") or 0), int(r.get("tk") or 0)) for r in rows}
    data["runsTrend"] = [
        {"date": d[5:], "runs": by_day.get(d, (0, 0))[0], "tokens": by_day.get(d, (0, 0))[1]}
        for d in axis
    ]

    # New-session curve (by creation date)
    rows = kernel.query(
        """SELECT DATE(created_at) d, COUNT(*) n
             FROM agx_session
            WHERE network_id = %s AND created_at >= CURDATE() - INTERVAL 6 DAY
            GROUP BY DATE(created_at)""",
        (_NETWORK_ID,),
    )
    s_day = {_dt(r.get("d"))[:10]: int(r.get("n") or 0) for r in rows}
    data["sessionsTrend"] = [{"date": d[5:], "count": s_day.get(d, 0)} for d in axis]

    # Top 6 call frequency over the last 7 days (aggregated by agent, with names)
    # [2026-11-05 13:39 Feng] The board became six cells in "3 columns x 2 rows" -- with only 5 rows the bottom-right is empty,
    #   exactly the "an empty gap in the middle looks bad" he mentioned. Data is padded to 6 so the grid fills.
    rows = kernel.query(
        """SELECT e.agent_id, COALESCE(a.agent_name, e.agent_id) nm, COUNT(*) c
             FROM agx_task_execution e
             LEFT JOIN agx_agent_registry a ON a.agent_id = e.agent_id
            WHERE e.network_id = %s AND e.created_at >= NOW() - INTERVAL 7 DAY
            GROUP BY e.agent_id, nm
            ORDER BY c DESC
            LIMIT 6""",
        (_NETWORK_ID,),
    )
    # [2026-11-06 11:10 Feng] "In the task-execution leaderboard, clicking a given agent's task must drill in"
    #   -> agent_id must be sent to the frontend too, otherwise drilling in can only reverse-look-up by "name" (wrong on duplicates or renames).
    #   ★ calls filters on created_at >= NOW()-7 DAY, and the execution list in the dialog
    #     must carry the same time condition so both counts match (the definition iron rule Feng keeps stressing).
    data["top"] = [
        {
            "agentId": _str(r.get("agent_id")),
            "name": _str(r.get("nm")) or _str(r.get("agent_id")),
            "calls": int(r.get("c") or 0),
        }
        for r in rows
    ]

    # Role distribution (active agents)
    rows = kernel.query(
        """SELECT agent_role r, COUNT(*) c FROM agx_agent_registry
            WHERE active_status = 1 GROUP BY agent_role""",
    )
    role_order = ["worker", "project_manager", "host_manager", "workshop_manager", "agent_manager"]
    dist = [{"name": _str(r.get("r")) or "unknown", "value": int(r.get("c") or 0)} for r in rows]
    dist.sort(key=lambda x: role_order.index(x["name"]) if x["name"] in role_order else 99)
    data["roleDistribution"] = dist

    _dash_heavy_cache["at"] = now
    _dash_heavy_cache["data"] = data
    return data


def _safe(fn, default=None):
    """One failing board block must not take down the page: return the default and log it."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log.warning("dashboard block failed: %s", e)
        return default


def dashboard_stats(caller: str = "") -> dict:
    """System status board (frontend polls every 5s).

    ★ Data sources and definitions (rewritten 2026-10-28; all reproducible):
      Projects  agx_project.active_status
      Plans     agx_plan.plan_status (7-value truth); ★ since 2026-11-03 no longer mixes in "plans with a running execution"
      Agents    agx_agent_registry (active=1) + agx_host_inventory.presence_status
                idle = host online and no running execution / busy = has a running execution / offline = host not online
                error = had a failed execution within the last hour
      Tasks     agx_task_execution.run_status; the 24h window is by finished_at
      Sessions  agx_session.active_status
      Trend     DATE(created_at) aggregation, padded per day over the last 7 days (missing days = 0)
      Ranking   execution count over the last 7 days, Top 5

    Redis: `agx:idx:execution:status:*` exists but is a **partial index** (2457 vs 5574 rows in the DB),
    so using it directly undercounts; all counts therefore come from MySQL, and Redis is used only to
    cross-check the running count (returned as sources.redisRunning for verification, not in any displayed number).
"""
    heavy = _heavy_dashboard_parts()

    # (1) projects
    pr = kernel.query(
        """SELECT SUM(active_status = 1) act, SUM(active_status = 0) arc, COUNT(*) tot
             FROM agx_project WHERE network_id = %s""",
        (_NETWORK_ID,),
    )
    p_act = int((pr[0] if pr else {}).get("act") or 0)
    p_arc = int((pr[0] if pr else {}).get("arc") or 0)
    p_tot = int((pr[0] if pr else {}).get("tot") or 0)

    # (2) plans
    # agx_plan has no network_id column (it belongs via project_id), so no network filter
    # ★ 2026-11-03 Feng: "there is no running; look at the database".
    #   The old definition crammed "number of plans with a running execution (DISTINCT plan_id)" into the `running` field,
    #   which is a different thing from plan_status: the former counts executions, the latter counts the plan's own state.
    #   Two consequences -- (1) activated(4) never appeared at all; (2) archived(5) had no field at all,
    #   so it effectively did not exist on the board. Now everything comes from the plan_status truth, field name = the value itself.
    rows = kernel.query("SELECT plan_status s, COUNT(*) c FROM agx_plan GROUP BY plan_status")
    plan_by = {_str(r.get("s")): int(r.get("c") or 0) for r in rows}
    nxt = kernel.query(
        """SELECT MIN(scheduled_at) m FROM agx_task_execution
            WHERE run_status = 'pending' AND scheduled_at IS NOT NULL""",
    )
    next_run = _dt((nxt[0] if nxt else {}).get("m"))

    # (3) agents (by host online state + whether there is a running execution / a failure in the last hour)
    rows = kernel.query(
        """SELECT a.agent_id,
                  COALESCE(h.presence_status, 0) host_online,
                  EXISTS(SELECT 1 FROM agx_task_execution e
                          WHERE e.agent_id = a.agent_id AND e.run_status = 'running') busy,
                  EXISTS(SELECT 1 FROM agx_task_execution e2
                          WHERE e2.agent_id = a.agent_id AND e2.run_status = 'failed'
                            AND e2.finished_at >= NOW() - INTERVAL 1 HOUR) err
             FROM agx_agent_registry a
             LEFT JOIN agx_host_inventory h ON h.host_id = a.host_id
            WHERE a.active_status = 1""",
    )
    a_busy = a_idle = a_off = a_err = 0
    for r in rows:
        online = int(r.get("host_online") or 0) == 1
        busy = bool(r.get("busy"))
        if int(r.get("err") or 0) == 1:
            a_err += 1
        if busy:
            a_busy += 1
        elif online:
            a_idle += 1
        else:
            a_off += 1

    # (4) tasks
    # [2026-11-03 21:33 Feng] the board's "task overview" must show **all 7 states** (laid out 3/3/1 across 3 columns),
    # adopting option A: pending is called "queued" rather than "to be handled", and split into
    #   · plan-queued = pending without scheduled_at (waiting for predecessor steps; advances automatically)
    #   · scheduled   = pending with scheduled_at (waiting for its time)
    # neither is "waiting for a human to handle it"; the old name reads as a backlog of to-dos.
    rows = kernel.query(
        """SELECT run_status s, COUNT(*) c FROM agx_task_execution WHERE network_id = %s GROUP BY run_status""",
        (_NETWORK_ID,),
    )
    t_by = {_str(r.get("s")): int(r.get("c") or 0) for r in rows}
    rows = kernel.query(
        """SELECT
             SUM(run_status = 'pending' AND scheduled_at IS NOT NULL) sched,
             SUM(run_status = 'pending' AND scheduled_at IS NULL) queued
             FROM agx_task_execution WHERE network_id = %s""",
        (_NETWORK_ID,),
    )
    t_split = rows[0] if rows else {}
    rows = kernel.query(
        """SELECT
             SUM(run_status = 'succeeded' AND finished_at >= NOW() - INTERVAL 24 HOUR) ok,
             SUM(run_status = 'failed'    AND finished_at >= NOW() - INTERVAL 24 HOUR) ng
             FROM agx_task_execution WHERE network_id = %s""",
        (_NETWORK_ID,),
    )
    t24 = rows[0] if rows else {}

    # (5) active session count
    rows = kernel.query(
        "SELECT COUNT(*) c FROM agx_session WHERE network_id = %s AND active_status = 1",
        (_NETWORK_ID,),
    )
    sess_active = int((rows[0] if rows else {}).get("c") or 0)

    # Redis running count (cross-check only, not used as a displayed value)
    redis_running: int | None
    try:
        redis_running = int(kernel.redis_client().zcard("agx:idx:execution:status:running"))
    except Exception:  # noqa: BLE001
        redis_running = None

    return {
        "projects": {
            "active": p_act,
            "paused": 0,  # no "paused" state in the DB (active_status is only 0/1); the field is kept for compatibility
            "archived": p_arc,
            "total": p_tot,
            # health = proportion of active projects (no longer a hardcoded 100/0)
            "healthScore": round(p_act * 100 / p_tot) if p_tot else 0,
        },
        "plans": {
            # 7 real values (failed is reserved with no write path today, constantly 0, still returned as-is)
            "activated": plan_by.get("activated", 0),
            "cancelling": plan_by.get("cancelling", 0),
            "draft": plan_by.get("draft", 0),
            "completed": plan_by.get("completed", 0),
            "cancelled": plan_by.get("cancelled", 0),
            "archived": plan_by.get("archived", 0),
            "failed": plan_by.get("failed", 0),
            "total": sum(plan_by.values()),
            "nextRunAt": next_run or "",
        },
        "tasks": {
            # ★ 7 real values, always from run_status (the same words as the badges in the executions list:
            #   pending=queued / draft=draft / running=running / succeeded=succeeded /
            #   failed=failed / cancelled=cancelled / archived=archived)
            "byStatus": {
                "pending": t_by.get("pending", 0),
                "running": t_by.get("running", 0),
                # Awaiting human confirmation (work done, waiting for approval) -- same name and meaning as the item in the list
                "awaiting_confirm": t_by.get("awaiting_confirm", 0),
                "succeeded": t_by.get("succeeded", 0),
                "failed": t_by.get("failed", 0),
                "cancelled": t_by.get("cancelled", 0),
                "archived": t_by.get("archived", 0),
                "draft": t_by.get("draft", 0),
            },
            # Internal breakdown of pending (option A): queued behind predecessors vs waiting for a scheduled time
            "pendingSplit": {
                "queued": int(t_split.get("queued") or 0),
                "scheduled": int(t_split.get("sched") or 0),
            },
            # Legacy field kept (other places still read it; meaning unchanged)
            "pending": t_by.get("pending", 0),
            "running": t_by.get("running", 0),
            "succeeded24h": int(t24.get("ok") or 0),
            "failed24h": int(t24.get("ng") or 0),
        },
        # ★ 2026-11-05 09:09 Feng corrected himself: "That was a slip of the tongue -- what I want counted is hosts, not organisations"
        #   The board body switched to hostStats; organizations stays in the response (costs 0,
        #   and the "4 organisations missing their master record" that org_stats exposed is a real problem), it just no longer takes board space.
        "hostStats": _safe(lambda: host_stats()),
        "organizations": _safe(lambda: org_stats()),
        "agentMetrics": {
            # online = idle + busy (a host that is online is online); idle is listed separately so the UI can distinguish
            "online": a_idle + a_busy,
            "idle": a_idle,
            "busy": a_busy,
            "offline": a_off,
            "error": a_err,
            "total": a_busy + a_idle + a_off,
            "top": heavy["top"],
        },
        "sessions": {
            "active": sess_active,
            "trend": heavy["sessionsTrend"],
            # ★ added 2026-11-05: session live-state summary (Redis agx:gw:*).
            #   `active` is the MySQL active-session count (a different definition, left unchanged);
            #   `runtime.total` is the live entity count on the gateway side (including the cross-host isolation zone); the two need not be equal --
            #   the former includes archived sessions, the latter only sessions on the 6 instances that have a collector attached.
            "runtime": _safe(lambda: session_runtime.stats()),
        },
        "runsTrend": heavy["runsTrend"],
        "roleDistribution": heavy["roleDistribution"],
        "sources": {
            "engine": "mysql",
            "redisRunning": redis_running,
            "redisIndexPartial": redis_running is not None and redis_running != t_by.get("running", 0),
        },
        # Second-level timestamp: the frontend's 5s poll uses it to tell "it really refreshed" (minute resolution shows no change)
        "generatedAt": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ══════════════════════════════════════════════════════════════════
# Snapshot: one full pull (for frontend hydration, fewer round trips)
# ══════════════════════════════════════════════════════════════════
def _quick_entries(caller: str) -> list[dict]:
    """Quick entries are shipped with the snapshot (the frontend store needs the data source in sync).

    ★ Lazy import: quick_entry depends on kernel in turn, so importing it at module level would form a
      cycle; and the snapshot is a hot path, so fewer import side effects is the steadier choice.
"""
    from . import quick_entry
    try:
        return quick_entry.list_entries(caller)
    except Exception:  # noqa: BLE001
        return []


def snapshot(caller: str = "") -> dict:
    return {
        "agents": agents(caller),
        "projects": projects(caller),
        "projectGroups": project_groups(caller),
        "sessions": sessions(caller),
        "tasks": tasks(caller),
        "executions": executions(caller),
        "plans": plans(caller),
        "skills": skills(caller),
        "hosts": hosts(caller),
        "notices": notices(caller),
        "quickEntries": _quick_entries(caller),
        "dashboard": dashboard_stats(caller),
    }
