"""
AgentGeeX MCP permission middleware — L0-L7 level system.

Core principles:
- Every MCP tool is assigned a minimum permission level (L0-L7)
- Every Agent derives a maximum permission level from its role
- An Agent may only call MCP tools whose level <= its own level
- An out-of-scope call returns an error directly; we no longer rely on the Agent prompt policing itself

Level definitions:
    L0 — read-only queries (available to every Agent)
    L1 — notice writes
    L2 — task CRUD
    L3 — task scheduling and execution
    L4 — Agent lifecycle management (agent_manager only)
    L5 — project management (project_manager and above)
    L6 — host management (host_manager and above)
    L7 — system management (workshop_manager only)
"""

import contextlib
import json
from functools import wraps

# ── tool -> minimum required level ──

TOOL_LEVEL = {
    # === L0: read-only ===
    "agx_check_services": 0,
    "agx_cache_stats": 0,
    "agx_cache_get": 0,
    "agx_generate_uuid": 0,
    "agx_get_project": 0,
    "agx_get_project_config": 0,
    "agx_get_global_config": 0,
    "agx_get_host": 0,
    "agx_get_agent": 0,
    "agx_get_task": 0,
    "agx_get_execution": 0,
    "agx_get_agent_group": 0,
    "agx_get_notice": 0,
    "agx_list_by_network": 0,
    "agx_list_project_agents": 0,
    "agx_quick_cmd_render": 0,
    "agx_quick_cmd_send": 5,
    "agx_query_notices": 0,
    "agx_running_tasks": 0,
    "agx_get_agent_queue_length": 0,
    "agx_list_all_agent_queues": 0,
    "agx_resolve_host_manager": 0,
    "agx_list_agent_sessions": 0,
    # === L1: notice writes ===
    "agx_write_notice": 1,
    # === L1: skill sharing (low friction: any Agent may contribute, everyone may query) ===
    "agx_skill_contribute": 1,
    "agx_skill_feedback": 1,
    "agx_skill_query": 0,
    "agx_skill_get": 0,
    # === L1: tech radar ===
    # 2026-10-23: all 8 agx_radar_* tools were deleted (Lao Feng decided: aifr_pm maintains its own
    # research process, AgentGeeX no longer carries radar duties). Tables agx_radar/agx_radar_doc/agx_radar_link
    # still exist in the DB (historical data kept) but have no MCP entry point — their permission entries were cleaned up too, to avoid diverging from the tool table.
    # === L1: session-level model read/write (2026-10-15; fine-grained check lives in _check_session_model_permission, three tiers) ===
    "agx_get_session_model": 1,
    "agx_set_session_model": 1,
    # === L2: task CRUD ===
    "agx_create_task": 2,
    "agx_update_task": 2,
    "agx_update_task_by_labs": 3,
    "agx_archive_task": 5,
    "agx_delete_task": 5,
    # === L3: scheduling and execution (dispatch/schedule raised to L5, only PMs can send) ===
    "agx_dispatch_task": 5,
    "agx_schedule_task": 5,
    "agx_finish_task": 3,
    "agx_activate_task": 3,
    "agx_continue_task": 3,
    "agx_update_execution": 3,
    "agx_note_main_session": 3,
    "agx_self_dispatch": 3,
    "agx_daily_log": 3,
    "agx_assistant_profile": 3,
    "agx_assistant_create": 3,
    "agx_assistant_list": 0,
    "agx_assistant_send": 3,
    # "agx_open_case": 3,   # removed 2026-10-22: the function was deleted entirely (zero call sites, dead code)
    "agx_update_session": 3,
    "agx_link_execution": 3,
    # "agx_dequeue_agent_message": 3,  # DEPRECATED: daemon talks to Redis directly
    # === L4: Agent lifecycle (create/clone/configure/deploy/group management) ===
    "agx_upsert_agent": 4,
    "agx_clone_agent": 4,
    "agx_update_agent": 4,
    "agx_deploy_agent": 4,
    # Temporary dispatch exemption: gate is L1; the real three tiers (super role / project admin) are decided in the function body
    "agx_set_dispatch_exempt": 1,
    "agx_create_agent_group": 4,
    "agx_add_agent_to_group": 4,
    "agx_remove_agent_from_group": 4,
    "agx_update_agent_group": 4,
    # "agx_archive_agent": 4,  # TODO: not implemented yet
    # === L5: project management (including plan management, member bind/unbind) ===
    "agx_create_project": 5,
    "agx_update_project": 5,
    "agx_bind_agent_to_project": 5,
    "agx_unbind_agent_from_project": 5,
    "agx_bind_group_to_project": 5,
    "agx_update_project_agent": 5,
    "agx_plan_deploy": 5,
    "agx_plan_query": 5,
    "agx_plan_list": 5,
    "agx_plan_cancel": 5,
    "agx_activate_plan": 5,
    # === L6: host management ===
    "agx_register_host": 6,
    "agx_update_host": 6,
    # === L7: system management ===
    "agx_cache_clear": 7,
    "agx_cache_rebuild": 7,
}


# ── Agent Role -> allowed set of permission levels ──
# Note in particular: permissions are NOT inherited! project_manager (L5) does not include L4 (Agent lifecycle)
# Each role defines its own set of allowed levels

ROLE_ALLOWED_LEVELS = {
    # Workshop supervisor: full permissions
    "workshop_manager": {0, 1, 2, 3, 4, 5, 6, 7},
    # Agent lifecycle specialist: read/write Agents + schedule and execute + basic operations; no projects/hosts/system
    "agent_manager": {0, 1, 2, 3, 4},
    # Project specialist: manage projects + schedule and execute + basic operations; cannot touch Agent lifecycle
    "project_manager": {0, 1, 2, 3, 5},
    # Host specialist: manage hosts + schedule and execute + basic operations; cannot manage Agents or projects
    "host_manager": {0, 1, 2, 3, 6},
    # Business executor: read/write + notices + tasks + scheduling; no management operations at all
    "worker": {0, 1, 2, 3},
    # Unknown role: read-only
    "unknown": {0},
}

# ── dispatch-capability exceptions ──
# After dispatch/schedule were raised to L5 (PMs only), the system-core roles agent_manager/
# host_manager (agx_agentfather, agx_host) must still be able to send commands across Agents.
# Here we allow the dispatch tools extra at the [tool level] instead of opening all L5 tools (such as creating projects),
# keeping responsibilities minimal: these roles need "dispatch command" ability, not project-management permission.
DISPATCH_TOOL_NAMES = {
    "agx_dispatch_task",
    "agx_schedule_task",
    "agx_quick_cmd_send",
}
# Note: agx_assistant_send was removed — an assistant is the Agent's own alter ego (messages to itself),
# so it should align with agx_self_dispatch (L3) and does not belong to the "cross-Agent dispatch" exception.

# System-core roles that hold dispatch-command permission but do not directly carry L5 in ROLE_ALLOWED_LEVELS
DISPATCH_EXEMPT_ROLES = {
    "agent_manager",
    "host_manager",
}

# agent_id -> role override (for exceptions where the DB role does not match the actual duty)
AGENT_ROLE_OVERRIDE = {
    "agx_agentfather": "agent_manager",   # DB role=project_manager but actually an agent_manager
}

# Hard-coded roles of core system Agents (fallback when Redis has no cache)
# * 2026-11-11 Lao Feng decided: the 4 built-in platform-management Agents get the agx_ prefix.
#   New names first (normal path), old names kept after (machines not yet reinstalled still resolve the right role).
_SYSTEM_AGENT_ROLES = {
    # ── new names (agx_ prefix) ──
    "agx_core": "workshop_manager",
    "agx_host": "host_manager",
    "agx_agentfather": "agent_manager",
    "agx_projectmother": "project_manager",
}


# ── externally injected role lookup function ──
# mcp_server.py calls set_lookup_fn() after importing permissions.
# This way the permissions module does not need to import MySQL/Redis itself.

_lookup_fn = None

# Dispatch-exemption lookup (per agent, not per role). Also injected by mcp_server.py:
# the permissions module never imports MySQL/Redis, avoiding lazy-load failures in child processes.
_exempt_lookup_fn = None


def set_lookup_fn(fn):
    """Called by mcp_server.py to inject the role lookup function.
    fn(agent_id) -> str (e.g. "workshop_manager", "unknown")
    """
    global _lookup_fn
    _lookup_fn = fn


def set_exempt_lookup_fn(fn):
    """Inject the lookup function telling whether an agent currently holds a dispatch exemption.
    fn(agent_id) -> bool
    The caller of the injection decides the criteria (agx_agent_registry.dispatch_exempt_until > NOW()).
    """
    global _exempt_lookup_fn
    _exempt_lookup_fn = fn


def _resolve_agent_role(agent_id: str) -> str:
    """Resolve an Agent's role.

    Priority: exact agent_id override > hard-coded core Agent fallback > lookup_fn (Redis+MySQL) > default unknown.
    """
    if not agent_id:
        return "unknown"

    role = None

    # 1. exact agent_id override
    if agent_id in AGENT_ROLE_OVERRIDE:
        role = AGENT_ROLE_OVERRIDE[agent_id]

    # 2. hard-coded core Agent fallback
    if role is None and agent_id in _SYSTEM_AGENT_ROLES:
        role = _SYSTEM_AGENT_ROLES[agent_id]

    # 3. externally injected lookup function (Redis + MySQL)
    if role is None and _lookup_fn is not None:
        with contextlib.suppress(Exception):
            role = _lookup_fn(agent_id)

    return role or "unknown"


def _get_agent_allowed_levels(agent_id: str) -> set:
    """Look up the set of permission levels allowed for an agent_id."""
    return ROLE_ALLOWED_LEVELS.get(_resolve_agent_role(agent_id), ROLE_ALLOWED_LEVELS["unknown"])


def _is_agent_dispatch_exempt(agent_id: str) -> bool:
    """Whether this agent holds a [temporary dispatch exemption] (agx_agent_registry.dispatch_exempt_until).

    This is a different thing from the per-role DISPATCH_EXEMPT_ROLES:
      · role exemption = this role is inherently allowed to command across Agents (agent_manager / host_manager)
      · agent exemption = an ordinary worker is [temporarily] granted dispatch ability, expiring automatically

    Fail-closed: if the lookup misses, raises, or Redis is down, treat it as no exemption.
    Letting a worker command other agents has consequences, so we would rather show the caller
    an insufficient-permission error than silently open the door because a query failed.
    """
    if not agent_id or _exempt_lookup_fn is None:
        return False
    try:
        return bool(_exempt_lookup_fn(agent_id))
    except Exception:
        return False


def _is_dispatch_exempt(func_name: str, role: str, agent_id: str = "") -> bool:
    """Whether this tool is allowed through because of the dispatch-capability exception.

    Two independent channels, both scoped only to DISPATCH_TOOL_NAMES:
    1. Role exemption: system-core roles such as agent_manager / host_manager that [must command
       across Agents] get an extra pass when calling dispatch tools (agx_dispatch_task / agx_schedule_task).
    2. Agent temporary exemption: dispatch ability explicitly granted to an ordinary worker, still valid.

    Note: both channels open up [dispatch] only, not the rest of L5 (creating projects, etc.).
    """
    if func_name not in DISPATCH_TOOL_NAMES:
        return False
    if role in DISPATCH_EXEMPT_ROLES:
        return True
    return _is_agent_dispatch_exempt(agent_id)


def require_level(level: int):
    """Decorator: verify that caller_agent_id has enough permission to call this tool.

    Usage:
        @require_level(4)
        def agx_upsert_agent(caller_agent_id: str, ...):
            ...

    An out-of-scope call returns a JSON error string instead of raising (keeps MCP protocol compatibility).
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            func_name = func.__name__
            caller_agent_id = ""

            # take the first argument as caller_agent_id
            if args:
                caller_agent_id = str(args[0]) if args[0] else ""
            elif "caller_agent_id" in kwargs:
                caller_agent_id = str(kwargs.get("caller_agent_id", ""))

            # ── main-session test-call bypass (commented out 2026-09-12, confirmed by Lao Feng) ──
            # This branch was meant as a test bypass, but it takes effect in production the same way: any caller
            # simply passing caller_agent_id="main_session" skips every L0-L7 level check.
            # Unless an agent is actually named main_session it would never match;
            # but once matched it equals bypassing the whole permission system, hence commented out.
            # if caller_agent_id == "main_session":
            #     return func(*args, **kwargs)

            allowed = _get_agent_allowed_levels(caller_agent_id)

            # Dispatch-capability exception: role exemption (agent_manager / host_manager)
            # or agent-level temporary exemption (dispatch_exempt_until not yet expired)
            if _is_dispatch_exempt(func_name, _resolve_agent_role(caller_agent_id), caller_agent_id):
                return func(*args, **kwargs)

            if level not in allowed:
                return json.dumps(
                    {
                        "error": (f"权限不足：Agent '{caller_agent_id}' 无权调用 {func_name}（需要 level {level}）。"),
                        "permission_denied": True,
                        "required_level": level,
                        "caller_allowed_levels": sorted(allowed),
                        "hint": ("请通过 agx_dispatch_task 将任务分派给有权限的 Agent 执行。"),
                    },
                    ensure_ascii=False,
                )

            return func(*args, **kwargs)

        return wrapper

    return decorator
