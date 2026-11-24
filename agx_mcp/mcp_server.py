"""AgentGeeX MCP tool module.

Provides the MCP toolset for the AgentGeeX core: project, host, agent, task and execution management.

Environment variables:
    AGX_DB_HOST / AGX_DB_PORT / AGX_DB_USER / AGX_DB_PASS / AGX_DB_NAME
    AGX_REDIS_HOST / AGX_REDIS_PORT / AGX_REDIS_DB / AGX_REDIS_PASSWORD
    AGX_LOGS_DIR - log directory (default ~/agx_runs_logs/)
"""

import asyncio
import json
import os
import random
import subprocess
import sys
import threading  # use the standard threading module
import time
import traceback
import uuid
from datetime import datetime, timedelta
from typing import Union

# ── Path setup ────────────────────────────────────────────────
# The directory used to be named mcp/, the same name as the pip package mcp (the MCP SDK, a fastmcp dependency),
# so fastmcp's internal `import mcp` resolved to the local directory and raised ImportError.
# Renamed to agx_mcp/ on 2026-09-12; the conflict is gone for good.
#
# Two paths are required:
#   1) project root appended to the end of sys.path -- top-level modules such as config_loader / log_setup / common live at the project root
#   2) agx_mcp/ prepended to sys.path -- same-directory modules (permissions / agx_core).
#      !! Do not delete this line: when launched as a "script", sys.path[0] happens to be the script directory, so it looks redundant;
#         but when loaded via "import agx_mcp.mcp_server" (e.g. from test/), sys.path[0] is not that directory,
#         and removing it raises ModuleNotFoundError: No module named 'agx_core'.
#
# Why not configure PYTHONPATH in openclaw.json instead (already proven to work):
#   this script is a standalone executable entry point, and self-containment is more reliable. An environment variable would add an implicit external dependency --
#   a new machine / a different start mode / a new MCP server that forgets to set it would silently crash on import.
#   Likewise watchdog/daemon each do their own sys.path.insert instead of using env. Keep it uniform.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_agx_mcp_dir = os.path.dirname(os.path.abspath(__file__))
if _agx_mcp_dir not in sys.path:
    sys.path.insert(0, _agx_mcp_dir)
if _project_root not in sys.path:
    sys.path.append(_project_root)

from config_loader import config

# ── Logging ───────────────────────────────────────────────────
from log_setup import setup_logging

log = setup_logging("MCP", log_dir=os.path.join(_project_root, "logs"), stream=sys.stderr)
log("MCP server starting")

from fastmcp import FastMCP
from permissions import (
    ROLE_ALLOWED_LEVELS,
    _resolve_agent_role,
    require_level,
    set_exempt_lookup_fn,
    set_lookup_fn,
)
from redis import ConnectionPool, Redis

from common.mysql.mysqlconn import UsingMysql
from common.mysql.mysqlservice import executeSql


# ═══════════════════════════════════════════════════════════════
# * Decided by Lao Feng on 2026-11-11: the four built-in platform-management agents are prefixed with agx_
# ═══════════════════════════════════════════════════════════════
#   New names (the only official identity; use them for creation/dispatch/lookup):
#       agx_core / agx_host / agx_agentfather / agx_projectmother
#   Discipline: **always use the new names; resolve ownership/duty through _sys_names()**, never scatter literals.
SYS_CORE = "agx_core"
SYS_HOST = "agx_host"
SYS_AGENTFATHER = "agx_agentfather"
SYS_PROJECTMOTHER = "agx_projectmother"

SYSTEM_AGENTS = [SYS_CORE, SYS_HOST, SYS_AGENTFATHER, SYS_PROJECTMOTHER]

def _sys_names(new_name: str) -> tuple:
    """Available names of a system agent.

    All compatibility checks go through here: `caller_agent_id in _sys_names(SYS_PROJECTMOTHER)`.
    Kept as a tuple so callers can keep using `in`.
    """
    return (new_name,)


# ── Role lookup injected into the permissions module ──
# permissions.py deliberately does not import MySQL/Redis (lazy loading fails in child processes),
# so mcp_server.py injects the lookup function after import.
def _lookup_agent_role(agent_id: str) -> str:
    """Look up an agent's role name. Redis cache first, fall back to MySQL on miss."""
    try:
        r = get_redis_client()
        data_prefix = "agx:entity:agent:"
        data = r.hgetall(f"{data_prefix}{agent_id}")
        if data and data.get("agent_role"):
            return data["agent_role"]
    except Exception as exc:
        log(f"_lookup_agent_role Redis failed for {agent_id}: {exc}", "WARN")
    try:
        rows = executeSql("SELECT agent_role FROM agx_agent_registry WHERE agent_id=%s", (agent_id,))
        if rows:
            return rows[0]["agent_role"]
    except Exception as exc:
        log(f"_lookup_agent_role MySQL failed for {agent_id}: {exc}", "WARN")
    return "unknown"


# ── Resource-level ownership check ────────────────────────────
# The level system (permissions.py L0-L7) only answers "may I use this tool";
# it does not answer "may I touch this record". This helper adds record-level ownership.
# Rules (finalized by Lao Feng on 2026-09-12):
#   a) own resource      -> caller == resource.agent_id
#   b) project admin     -> caller == agx_project.admin_agent_id
#                        (the admin of system_project is agx_core)
#   c) global role       -> role in {host_manager, workshop_manager}
# Note: the check lives at the MCP tool layer, not in agx_core -- the watchdog imports
# agx_core internals directly (acting on behalf of agx_core), bypassing the MCP layer by design.


def _check_resource_ownership(caller_agent_id: str, resource_agent_id: str, resource_project_id: str) -> str | None:
    """Check whether the caller may operate on this resource.

    Rules (defined by Lao Feng on 2026-09-12):
        (a) own resource           -> caller == resource.agent_id
        (b) project admin          -> caller == project.admin_agent_id (incl. system_project -> agx_core)
        (c) global role            -> role in {host_manager, workshop_manager}

    Returns:
        None -> allowed
        str  -> reason for denial
    """
    if not caller_agent_id:
        return None

    # (a) own resource
    if resource_agent_id and caller_agent_id == resource_agent_id:
        return None

    # (c) global role
    role = _resolve_agent_role(caller_agent_id)
    if role in ("host_manager", "workshop_manager"):
        return None

    # (b) project admin
    if resource_project_id:
        rows = execute_sql(
            "SELECT admin_agent_id FROM agx_project WHERE project_id = %s AND active_status = 1",
            (resource_project_id,),
        )
        if rows and caller_agent_id == rows[0].get("admin_agent_id"):
            return None

    return f"权限不足:Agent '{caller_agent_id}' 无权操作该资源(归属 {resource_agent_id or '?'} / 项目 {resource_project_id or '?'})。仅资源归属 Agent、所属项目管理员(admin_agent_id)、host_manager、workshop_manager 可操作。"


def _check_pipeline_caller(caller_agent_id: str, caller_role: str, mode: str) -> str | None:
    """Check the caller's permission for the pipeline methods (Lao Feng, 2026-10-13: the method itself enforces permission = entrance locked down).

    The "ownership gate" rule for the two declarative pipeline methods -- not a documentation
    convention, but enforced by code:
        mode="project" -> creating a project is exclusive to projectmother. Only agx_projectmother
            + global roles (workshop_manager / host_manager) may call it.
        mode="agent"   -> creating an agent is exclusive to agentfather; but it is externally visible --
            a PM (project_manager) may request creation/cloning of a worker for [its own project].
            Only agx_agentfather + project_manager + global roles
            (workshop_manager / host_manager) may call it.

    Returns:
        None -> allowed
        str  -> reason for denial
    """
    if not caller_agent_id:
        return None

    # Global roles (dispatch hub / host management) can always call it (needed for operations/orchestration)
    if caller_role in ("workshop_manager", "host_manager"):
        return None

    if mode == "project":
        if caller_agent_id in _sys_names(SYS_PROJECTMOTHER):
            return None
        return (
            f"权限不足:'{caller_agent_id}' 无权调用建项目管线。"
            "建项目是 projectmother 的专属职责 —— 请 dispatch 给 agx_projectmother 执行。"
        )

    if mode == "agent":
        if caller_agent_id in _sys_names(SYS_AGENTFATHER):
            return None
        # Externally visible: a PM (project admin) may request creation/cloning of a worker in its own project
        if caller_role == "project_manager":
            return None
        return (
            f"权限不足:'{caller_agent_id}' 无权调用建 Agent 管线。"
            "建 Agent 是 agentfather 的专属职责; PM 可请求建/克隆本项目的 worker。"
            "请 dispatch 给 agx_agentfather 执行。"
        )

    return f"权限不足:未知的 pipeline 模式 '{mode}'"


# ── Read-side visibility (read scope) ─────────────────────
# The level system (permissions.py) only answers "may this role touch this class of tools"; the read side must also answer "which record may it read".
# Principles (set by Lao Feng on 2026-10-10):
#   system group (workshop_manager/host_manager/agent_manager) -> everything
#   project_manager (project admin)                            -> members of its own project
#   worker                                                     -> itself (+ public)
# * Shadow mode read_scope_mode: off|log|enforce (default off -> zero behavior change on release)
# Design doc: docs/agent_docs/2026-10-10-read-side-permission-scope-design.md

def _read_scope_mode() -> str:
    """Read-side scope mode: off (no check/no filter) | log (check + log but do not block) | enforce (really block)."""
    try:
        m = (config.get("read_scope_mode", "off") or "off").strip().lower()
        return m if m in ("off", "log", "enforce") else "off"
    except Exception:
        return "off"


def _read_scope_of(caller_agent_id: str) -> str:
    """Return the caller's read scope: 'all' | 'project' | 'self'."""
    try:
        role = _lookup_agent_role(caller_agent_id)
    except Exception:
        role = "unknown"
    if role in ("workshop_manager", "host_manager", "agent_manager"):
        return "all"
    if role == "project_manager":
        return "project"
    return "self"


def _caller_admins_project(caller_agent_id: str, project_id: str) -> bool:
    """Whether the caller is the admin of this project (agx_project.admin_agent_id)."""
    if not project_id:
        return False
    try:
        rows = execute_sql(
            "SELECT 1 FROM agx_project WHERE project_id = %s AND admin_agent_id = %s AND active_status = 1",
            (project_id, caller_agent_id),
        )
        return bool(rows)
    except Exception:
        return False


def _can_read_scope(caller_agent_id: str, owner_agent_id: str, project_id: str = "", tool: str = "") -> bool:
    """Decide whether the caller may read a record owned by owner_agent_id / project_id.

    * Shadow mode: off -> always True (no filtering); log -> decide but still return True;
      enforce -> return the decision.
    """
    mode = _read_scope_mode()
    if mode == "off":
        return True

    allowed = True
    if caller_agent_id:
        if owner_agent_id and owner_agent_id == caller_agent_id:
            allowed = True
        else:
            scope = _read_scope_of(caller_agent_id)
            if scope == "all":
                allowed = True
            elif scope == "project":
                allowed = _caller_admins_project(caller_agent_id, project_id)
            else:
                allowed = False

    if not allowed and mode == "log":
        log(f"[READ-SCOPE][log] {tool} caller={caller_agent_id} blocked-from owner={owner_agent_id} project={project_id}", "WARN")
        return True  # shadow: do not actually block
    return allowed


def _check_plan_ownership(caller_agent_id: str, plan_id: str) -> str | None:
    """Check whether the caller may operate on this plan (resource-level ownership).

    Plan ownership resolution (* revised 2026-10-26):
        0. [Preferred] agx_plan.created_by -- a plan is the creator's resource; the agent in a
           step is merely "the one asked to do the work". When planning and execution are split,
           the executor must not be treated as the owner.
           (The old implementation only looked at step ownership, so "a cross-agent plan created
            by A could not even be activated by A", or cancelled -- producing orphan draft plans
            that could never be reclaimed.)
        1. Fall back to the ownership of the execution pointed to by
           agx_plan.start_task_run_uuid / task_run_uuid
        2. Then fall back to (agent_id, project_id) of any execution under this plan
        3. Last resort: agx_plan.project_id (only judgeable at project level)

    Returns:
        None -> allowed; str -> reason for denial
    """
    if not caller_agent_id:
        return None

    rows = execute_sql(
        "SELECT project_id, created_by, start_task_run_uuid, task_run_uuid FROM agx_plan WHERE plan_id = %s",
        (plan_id,),
    )
    if not rows:
        return f"Error: Plan '{plan_id}' not found"

    # ── (d) plan creator: naturally holds disposal rights over its own plan (activate/cancel) ──
    created_by = rows[0].get("created_by") or ""
    if created_by and created_by == caller_agent_id:
        return None

    project_id = rows[0].get("project_id") or ""
    seed_run = rows[0].get("start_task_run_uuid") or rows[0].get("task_run_uuid") or ""

    resource_agent_id = ""
    if seed_run:
        exec_rows = execute_sql(
            "SELECT agent_id, project_id FROM agx_task_execution WHERE task_run_uuid = %s", (seed_run,)
        )
        if exec_rows:
            resource_agent_id = exec_rows[0].get("agent_id", "") or ""
            project_id = exec_rows[0].get("project_id") or project_id
    if not resource_agent_id:
        exec_rows = execute_sql(
            "SELECT agent_id, project_id FROM agx_task_execution WHERE plan_id = %s LIMIT 1", (plan_id,)
        )
        if exec_rows:
            resource_agent_id = exec_rows[0].get("agent_id", "") or ""
            project_id = exec_rows[0].get("project_id") or project_id

    return _check_resource_ownership(caller_agent_id, resource_agent_id, project_id)


import re

_AGENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_AGENT_ID_RULES = "命名规范:仅限小写英文字母 [a-z]、数字 [0-9]、下划线 [_]。必须以小写字母开头。禁止大写、空格、中文、特殊字符。原因:agent_id 用作 Redis key、文件系统路径、OpenClaw CLI --agent 参数。"


def _validate_agent_id(agent_id: str) -> tuple:
    """
    Validate the agent_id naming convention.

    Returns:
        (is_valid: bool, error_message: str)
        valid   -> (True, "")
        invalid -> (False, error message describing the rule)
    """
    if not agent_id or not agent_id.strip():
        return (False, "agent_id 不能为空")
    if not _AGENT_ID_PATTERN.match(agent_id.strip()):
        return (False, f"agent_id '{agent_id}' 不合法。{_AGENT_ID_RULES}")
    return (True, "")


_PROJECT_ID_RULES = (
    "命名规范:必须带 'proj_' 前缀, 其后仅限小写英文字母 [a-z]、数字 [0-9]、下划线 [_]。"
    "禁止大写、空格、中文、特殊字符。"
    "原因:project_id 用作 Redis key、文件系统路径、Agent 归属标识。"
)

# project suffix rule: digits may lead (aligned with the hex suffix produced by agx_generate_uuid, e.g. proj_03ced907489c)
_PROJECT_SUFFIX_PATTERN = re.compile(r"^[a-z0-9_]+$")


def _validate_project_id(project_id: str) -> tuple:
    """
    Validate a custom project_id naming convention (* added 2026-10-13).

    Rule = mandatory 'proj_' prefix followed by ^[a-z0-9_]+$ (digits may lead -- aligned with
    the hex suffix that the system's own agx_generate_uuid produces, e.g. proj_03ced907489c).

    Returns:
        (is_valid: bool, error_message: str)
    """
    if not project_id or not project_id.strip():
        return (False, "project_id 不能为空")
    pid = project_id.strip()
    if not pid.startswith("proj_"):
        return (False, f"project_id '{pid}' 不合法。{_PROJECT_ID_RULES}")
    suffix = pid[len("proj_") :]
    if not _PROJECT_SUFFIX_PATTERN.match(suffix):
        return (False, f"project_id '{pid}' 不合法。{_PROJECT_ID_RULES}")
    return (True, "")


set_lookup_fn(_lookup_agent_role)


# ── Dispatch-exemption lookup injected into the permissions module ──
# Criterion: agx_agent_registry.dispatch_exempt_until > NOW()
# Applies only to DISPATCH_TOOL_NAMES, and any failure returns False (fail-closed).
def _lookup_dispatch_exempt(agent_id: str) -> bool:
    """Whether this agent currently holds a live dispatch exemption (per agent only, not per role).

    Cache first: if the Redis hash carries dispatch_exempt_until, decide directly -- otherwise every
    rejected dispatch by a worker would hit MySQL again (workers calling agx_dispatch_task get
    rejected quite often).

    The field being [absent] from the cache has two possible causes, and only a DB round-trip can tell:
      a) the field was never synced (old cache)
      b) the exemption was cleared and _do_update used hdel (removed the field rather than writing NULL)
    One fallback read resolves both; the result is then written back to the cache (an empty string
    marks "no exemption"), so case b is not repeatedly treated as case a.
    """
    try:
        r = get_redis_client()
        data = r.hgetall(f"agx:entity:agent:{agent_id}")
        if data and "dispatch_exempt_until" in data:
            return _exempt_is_live(data.get("dispatch_exempt_until"))
    except Exception as exc:
        log(f"_lookup_dispatch_exempt Redis failed for {agent_id}: {exc}", "WARN")

    # Fall back to MySQL (Redis unavailable also lands here, so there is always a single source of truth)
    try:
        rows = executeSql(
            "SELECT dispatch_exempt_until FROM agx_agent_registry WHERE agent_id=%s",
            (agent_id,),
        )
        if not rows:
            return False
        raw = rows[0].get("dispatch_exempt_until")
        live = _exempt_is_live(raw)
        # Write the result back to the cache as a side effect: an empty string still gets written, as an explicit "already decided" marker
        try:
            _sync_entity(
                "agent",
                agent_id,
                {"dispatch_exempt_until": "" if raw is None else str(raw)},
            )
        except Exception:
            pass  # a failed cache write does not affect the decision
        return live
    except Exception as exc:
        log(f"_lookup_dispatch_exempt MySQL failed for {agent_id}: {exc}", "WARN")
        return False


def _exempt_is_live(value) -> bool:
    """Whether dispatch_exempt_until is still live (not expired).

    Tolerates three sources: a MySQL datetime object, a string from the cache,
    and the empty string/None/"None" that means "no exemption".
    Anything unparsable is False -- a malformed timestamp must never accidentally allow dispatch.
    """
    if value is None:
        return False
    if hasattr(value, "year"):  # datetime
        return value > datetime.now()
    text = str(value).strip()
    if not text or text in ("None", "null", "NULL", "0"):
        return False
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text[:19] if len(text) > 19 else text, fmt) > datetime.now()
        except ValueError:
            continue
    return False


set_exempt_lookup_fn(_lookup_dispatch_exempt)


# --- Environment configuration ---

REDIS_HOST = config.redis.get("host", "127.0.0.1")
REDIS_PORT = int(config.redis.get("port", 6379))
REDIS_PASSWORD = config.redis.get("password", None)
REDIS_DB = int(config.redis.get("db", 0))
REDIS_USERNAME = config.redis.get("username", "default")


# --- MCP instance & Redis connection pool ---

mcp = FastMCP("AgentGeeX_Kernel")

# * Single connection pool (multiple/dual pools are forbidden). db is fixed inside the pool kwargs.
# Fix record (2026-09-12): the original implementation passed db to Redis() but not into the pool kwargs,
# and redis-py 7.x ignores it -> every connection landed on db=0 (which once made a test silently write to production DB0).
# Now db lives in kwargs; to switch to a test DB, change redis.db in the config file (do not open another pool).
pool = ConnectionPool(
    host=REDIS_HOST,
    port=REDIS_PORT,
    username=REDIS_USERNAME,
    password=REDIS_PASSWORD,
    db=REDIS_DB,
    decode_responses=True,
    max_connections=int(config.redis.get("pool_size", 20)),
)


# ============================================================

TMUX_TIMEOUT = int(config.get("tmux_timeout", 10))  # timeout for the tmux subprocess (seconds)

# Redis cache key prefix constants

# ============================================================

CACHE_PREFIX = {
    "project": "agx:entity:project:",
    "host": "agx:entity:host:",
    "agent": "agx:entity:agent:",
    "task": "agx:entity:task:",
    "execution": "agx:entity:execution:",
    "agent_group": "agx:entity:agent_group:",
    "agent_group_member": "agx:entity:agent_group_member:",
    "notice": "agx:entity:notice:",
    "plan": "agx:entity:plan:",
    "skill": "agx:entity:skill:",
}


IDX_PREFIX = {
    "project_all": "agx:idx:project:all",
    "host_all": "agx:idx:host:all",
    "agent_all": "agx:idx:agent:all",
    "task_all": "agx:idx:task:all",
    "execution_all": "agx:idx:execution:all",
    "agent_group_all": "agx:idx:agent_group:all",
    "agent_group_member_all": "agx:idx:agent_group_member:all",
    "notice_all": "agx:idx:notice:all",
    "plan_all": "agx:idx:plan:all",
    "skill_all": "agx:idx:skill:all",
    "agent_active": "agx:idx:agent:active",
    "task_active": "agx:idx:task:active",
}


EXEC_STATUS_IDX = "agx:idx:execution:status:"
EXEC_BY_PARENT = "agx:idx:execution:by_parent:"
EXEC_BY_TASK = "agx:idx:execution:by_task:"

# * Decided by Lao Feng on 2026-10-30: the receipt-pending index (hot path for resend decisions) shares its criterion with agx_core.
EXEC_RECEIPT_PENDING_IDX = "agx:idx:execution:receipt:pending"


def _receipt_pending_wanted(run_status, needs_reply, reply_read) -> bool:
    """Whether this execution should stay in the "receipt pending acknowledgement" index (same SQL criterion as the watchdog)."""
    if str(run_status or "").strip() not in ("succeeded", "failed"):
        return False
    try:
        nr = int(needs_reply or 0)
    except Exception:
        nr = 0
    if nr not in (1, 2):
        return False
    try:
        rr = int(reply_read or 0)
    except Exception:
        rr = 0
    return rr != 1


def _receipt_pending_score(reply_sent_at, finished_at) -> float:
    """Index score: the moment the pending receipt clock started (epoch seconds). Falls back to now when missing."""
    import datetime as _dt
    import time as _time

    for v in (reply_sent_at, finished_at):
        if not v:
            continue
        try:
            if isinstance(v, _dt.datetime):
                return v.timestamp()
            return _time.mktime(_time.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S"))
        except Exception:
            continue
    return _time.time()

# ═══════════════════════════════════════════════════════════════
# * Agent directory layout as of 2026-10-25 (set by Lao Feng, same criterion as install.py)
# ═══════════════════════════════════════════════════════════════
#   old: {work_dir}/openclaw_agents/{agent_id}/
#   new: {work_dir}/agx_agents/{agent_id}/            <- flat layout, the only one for agents
#        {work_dir}/agx_agents/host/{agent_id}/       <- agx_host (one per host)
#
#   * Why host-type agents go into host/ separately: core/agentfather/projectmother and the other production agents
#     are globally unique, while agx_host is one per host; keeping them apart avoids namespace confusion.
#   * No per-host layering at the root: a single directory across the version repo -> makes agent drift easy (moving hosts only changes the DB host_id).
#
#   * Compatibility: this machine (test_host_mac) keeps the old layout (legacy). The decision is based on [what exists on disk],
#     not on remembered config: use agx_agents if present, otherwise openclaw_agents.

AGX_DIR_NEW = "agx_agents"
AGX_HOST_SUBDIR = "host"


def _resolve_agents_dir_name(work_dir: str) -> str:
    """Resolve the agent root directory name: agx_agents (new) / openclaw_agents (old)."""
    base = os.path.expanduser(work_dir or "")
    return AGX_DIR_NEW


def _agent_dir(work_dir: str, agent_id: str, *sub: str) -> str:
    """Build the agent directory path (including the host subdirectory).

    Host-type agents always land in host/agx_host_{hostname}/.
    """
    _dirname = _resolve_agents_dir_name(work_dir)
    parts = [os.path.expanduser(work_dir or "~"), _dirname]
    if agent_id.startswith("agx_host"):
        parts.append(AGX_HOST_SUBDIR)
    parts.append(agent_id)
    parts.extend(sub)
    return os.path.join(*parts)


# ============================================================

# Helper functions

# ============================================================


def get_redis_client(db: int = None) -> Redis:
    """Get the Redis client.

    * Single connection pool (multiple/dual pools are forbidden). db is fixed in the pool kwargs;
      the db parameter is obsolete (signature kept for compatibility) and passing a different value
      will not open another pool. To switch databases, change redis.db in the config file.
    """
    return Redis(connection_pool=pool)


def execute_sql(query, params=None, commit=False):
    """Execute SQL and return the result."""

    return executeSql(query, params, commit)


# ============================================================

# Hard-coded allowlist


# ============================================================

# Redis cache helper functions

# ============================================================


def _sync_entity(entity_type: str, entity_id: str, data: dict):
    """Write the entity into a Redis hash and build its indexes. Silently degrades when Redis is unavailable."""
    try:
        r = get_redis_client()

        key = f"{CACHE_PREFIX[entity_type]}{entity_id}"

        # Filter out None values, so Redis does not store the string "None"

        clean = {k: v for k, v in data.items() if v is not None}

        # Skip oversized content fields (store them in files, not in the cache)

        _NO_CACHE_MD = {"agents_md", "heartbeat_md", "identity_md", "soul_md", "tools_md", "user_md"}
        if entity_type == "agent":
            clean = {k: v for k, v in clean.items() if k not in _NO_CACHE_MD}

        # Make sure every value can be serialized to a string
        #
        # * 2026-11-04: dict/list must go through json.dumps, never str().
        #   Reason: str([{"a":1}]) = "[{'a': 1}]" -- single quotes, not valid JSON.
        #   Values read from a MySQL json column are already str and never reach here; but entities built
        #   by hand in code (e.g. agx_register_host) do pass lists, and what lands in Redis is broken JSON,
        #   so the front end's json.parse throws.
        def _to_str(_v):
            if isinstance(_v, str):
                return _v
            if isinstance(_v, (dict, list)):
                return json.dumps(_v, ensure_ascii=False)
            return str(_v)

        str_data = {k: _to_str(v) for k, v in clean.items()}
        pipe = r.pipeline()

        # 1. Write the hash (no expiry; refreshed automatically when data changes)

        if str_data:
            pipe.hset(key, mapping=str_data)

        # 2. Update the owning indexes

        idx_all_key = IDX_PREFIX.get(f"{entity_type}_all")

        if idx_all_key:
            score = data.get("pkid", 0) or 0
            # * FIX (2026-10-25, DH-063): some callers (e.g. agx_list_agent_tasks / agx_list_by_network)
            #   do not select pkid in their SELECT cols and wrote score 0 directly ->
            #   a zset is ordered by score (zrevrange takes the most recent N), so score=0 sinks to the bottom forever.
            #   Evidence: the first 6 tasks had normal scores in task:all before the call, and all became 0 after it.
            #   Fix: when data has no pkid, read the existing score first; write 0 only if it is still 0/missing.
            if "pkid" not in data or data.get("pkid") is None:
                try:
                    _cur = r.zscore(idx_all_key, entity_id)
                    score = int(_cur) if _cur is not None else 0
                except Exception:
                    score = 0
            pipe.zadd(idx_all_key, {entity_id: int(score)})

        # 3. Extra per-type indexes

        _build_extra_index(pipe, entity_type, entity_id, data)

        pipe.execute()

    except Exception as exc:
        log(f"_sync_entity({entity_type}, {entity_id}) Redis failed: {exc}", "WARN")


def _flag_is_on(v) -> bool:
    """Normalize a DB/parameter "switch field" into True/False.

    * Fixed 2026-10-21 (a real defect):
      The original code wrote `data.get("active_status") == "1"` (string comparison),
      but execute_sql returns MySQL tinyint as **int**, not str ->
      for anything passed straight from a DB row the test was always False, and the index was silently lost.

      Evidence:
        _build_extra_index(task, id, {"active_status": 1})   -> not added to task:active
        _build_extra_index(task, id, {"active_status": "1"}) -> added (correct)

    With a single entry point, int / str / bool / bytes are all handled correctly.
    """
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v == 1
    s = str(v).strip().lower()
    return s in ("1", "true", "yes", "on")


def _build_extra_index(pipe: Redis, entity_type: str, entity_id: str, data: dict):
    """Build the extra indexes for a specific entity type."""
    if entity_type == "project":
        # Index by network

        if data.get("network_id"):
            pipe.sadd(f"agx:idx:project:by_network:{data['network_id']}", entity_id)

    elif entity_type == "host":
        if data.get("network_id"):
            pipe.sadd(f"agx:idx:host:by_network:{data['network_id']}", entity_id)

        if _flag_is_on(data.get("presence_status")):
            pipe.sadd("agx:idx:host:online", entity_id)
        else:
            pipe.srem("agx:idx:host:online", entity_id)

    elif entity_type == "agent":
        # * Fixed 2026-10-21 (defect 3): it used to sadd unconditionally -> disabled agents stayed in the active index.
        #   Evidence: _build_extra_index(agent, id, {"active_status": 0}) -> still added.
        #   When data has no active_status key (partial update), leave the index alone to avoid removing it by mistake.
        if "active_status" in data:
            if _flag_is_on(data.get("active_status")):
                pipe.sadd(IDX_PREFIX["agent_active"], entity_id)
            else:
                pipe.srem(IDX_PREFIX["agent_active"], entity_id)

        if data.get("network_id"):
            pipe.sadd(f"agx:idx:agent:by_network:{data['network_id']}", entity_id)

    elif entity_type == "task":
        # * 2026-10-21: same _flag_is_on change, plus "leave it alone when not passed" (partial updates must not remove it by mistake)
        if "active_status" in data:
            if _flag_is_on(data.get("active_status")):
                pipe.sadd(IDX_PREFIX["task_active"], entity_id)
            else:
                pipe.srem(IDX_PREFIX["task_active"], entity_id)

        if data.get("agent_id"):
            pipe.sadd(f"agx:idx:task:by_agent:{data['agent_id']}", entity_id)

    elif entity_type == "execution":
        if data.get("task_id"):
            pipe.sadd(f"{EXEC_BY_TASK}{data['task_id']}", entity_id)

        # Build the parent-child execution relation index

        if data.get("parent_run_uuid"):
            pipe.sadd(f"{EXEC_BY_PARENT}{data['parent_run_uuid']}", entity_id)

        # * Fixed 2026-10-28, "losing an index means going blind":
        #   The old code did status = data.get("run_status", "unknown"). Whenever a caller performed a
        #   "partial field refresh" (agx_update_execution only changes alert_note / x_desc,
        #   agx_ack_receipt only changes reply_read+ack_state ...), data carried no
        #   run_status -> it defaulted to unknown: first zrem from buckets such as running,
        #   then zadd into status:unknown.
        #   But the watchdog finds work with zrange(agx:idx:execution:status:running) ->
        #   a running task was moved out of sight by its own cache refresh, never to be woken or alerted again.
        #   Evidence: Redis status:unknown = 643 entries, MySQL run_status='unknown' = 0 rows
        #   (that state does not exist in the database; it was purely a cache phantom).
        #   Collateral defect: with status="unknown" the by_plan index used _PLAN_SCORE.get(...,0)
        #   and landed on 0 = pending, mis-recording completed plan steps as pending.
        #   Fix: touch the status index / plan index only when data really carries run_status.
        status = (data.get("run_status") or "").strip()
        if status:
            score = data.get("pkid", 0) or 0

            # Remove from all status indexes, then add only the current one

            for s in _EXEC_STATUSES:
                pipe.zrem(f"{EXEC_STATUS_IDX}{s}", entity_id)

            pipe.zadd(f"{EXEC_STATUS_IDX}{status}", {entity_id: int(score)})

            # plan dimension index (score encodes status: 0=pending 1=running 2=succeeded 3=failed 4=cancelled)
            if data.get("plan_id"):
                _PLAN_SCORE = {"draft": -1, "pending": 0, "running": 1, "succeeded": 2, "failed": 3, "cancelled": 4}
                pipe.zadd(f"agx:idx:execution:by_plan:{data['plan_id']}", {entity_id: _PLAN_SCORE.get(status, 0)})

        # * Decided by Lao Feng on 2026-10-30: receipt-pending index -- same discipline as above:
        #   **touch the index only when all three decision fields are present**. A partial refresh (agx_ack_receipt without
        #   run_status, agx_plan_cancel without needs_reply) has no full picture, and deciding ownership from partial data
        #   inevitably puts the row in the wrong place; the 2026-10-28 "losing an index means going blind" incident was exactly this.
        #   Acknowledgement is a deterministic removal event, and agx_ack_receipt does its own explicit ZREM.
        if all(k in data for k in ("run_status", "needs_reply", "reply_read")):
            if _receipt_pending_wanted(
                data.get("run_status"), data.get("needs_reply"), data.get("reply_read")
            ):
                pipe.zadd(
                    EXEC_RECEIPT_PENDING_IDX,
                    {entity_id: _receipt_pending_score(
                        data.get("reply_sent_at"), data.get("finished_at"))},
                )
            else:
                pipe.zrem(EXEC_RECEIPT_PENDING_IDX, entity_id)

    elif entity_type == "agent_group":
        if data.get("network_id"):
            pipe.sadd(f"agx:idx:agent_group:by_network:{data['network_id']}", entity_id)

    elif entity_type == "agent_group_member":
        if data.get("network_id"):
            pipe.sadd(f"agx:idx:agent_group_member:by_network:{data['network_id']}", entity_id)

        if data.get("group_id"):
            pipe.sadd(f"agx:idx:agent_group_member:by_group:{data['group_id']}", entity_id)

        if data.get("agent_id"):
            pipe.sadd(f"agx:idx:agent_group_member:by_agent:{data['agent_id']}", entity_id)

    elif entity_type == "notice":
        if data.get("agent_id"):
            pipe.sadd(f"agx:idx:notice:agent:{data['agent_id']}", entity_id)
        if data.get("project_id"):
            pipe.sadd(f"agx:idx:notice:project:{data['project_id']}", entity_id)
        if data.get("task_id"):
            pipe.sadd(f"agx:idx:notice:task:{data['task_id']}", entity_id)
        if data.get("run_uuid"):
            pipe.sadd(f"agx:idx:notice:run:{data['run_uuid']}", entity_id)

    elif entity_type == "plan":
        score = data.get("pkid", 0) or 0
        pipe.zadd(IDX_PREFIX["plan_all"], {entity_id: int(score)})
        if data.get("project_id"):
            pipe.sadd(f"agx:idx:plan:by_project:{data['project_id']}", entity_id)
        if data.get("created_by"):
            pipe.sadd(f"agx:idx:plan:by_agent:{data['created_by']}", entity_id)

    elif entity_type == "skill":
        # * Added 2026-10-21 (defect 1): skill previously had [no branch] in _build_extra_index,
        #   so its dimension indexes relied entirely on manual sadd in agx_skill_contribute (L10698-10699),
        #   which meant: (1) any path that bypasses contribute (cache_rebuild / cache refill) never had the dimensions;
        #                (2) deleting a skill left every index behind (real ghosts).
        #   Evidence: DB had 48 rows / 24 cached, skill_all only 9, and category and source were each missing 14.
        if data.get("category"):
            pipe.sadd(f"agx:idx:skill:category:{data['category']}", entity_id)
        if data.get("source_agent_id"):
            pipe.sadd(f"agx:idx:skill:source:{data['source_agent_id']}", entity_id)



def _get_entity(entity_type: str, entity_id: str) -> dict | None:
    """Read an entity from Redis; return None on miss. Also returns None when Redis is unavailable."""
    try:
        r = get_redis_client()
        key = f"{CACHE_PREFIX[entity_type]}{entity_id}"
        data = r.hgetall(key)
        if data:
            return data
        return None

    except Exception as exc:
        log(f"_get_entity({entity_type}, {entity_id}) Redis failed: {exc}", "WARN")
        return None


# * Added 2026-10-21: the full status index set for executions (must match the _sync_entity write side)
_EXEC_STATUSES = ("running", "succeeded", "failed", "pending", "cancelled",
                  "draft", "awaiting_confirm", "unknown")


def _remove_from_indexes(entity_type: str, entity_id: str, row: dict | None = None):
    """Remove an entity's membership from [all] indexes.

    * Why this function exists (real defect measured 2026-10-21):
      The old _invalidate_entity only did `r.delete(agx:entity:{type}:{id})` and touched no index
      -> ghost ids pointing at entities that no longer exist were left in the indexes (dirty indexes).
      Measured consequences:
        1) _check_plan_completion read hget=None (entity missing), None is not in
           ("pending","running","draft") -> counted as "done" -> **the plan was wrongly marked completed**
        2) agx_get_task returned a pile of non-existent execution ids
        3) anything iterating an index: got a ghost id -> every lookup missed -> spun on nothing

    * Maintenance discipline: whenever a new index dimension is added in _sync_entity(), it must be
      added here too, otherwise that dimension is silently left uncleaned. The two sides are mirrors.

    Args:
        entity_type: entity type (project/host/agent/task/execution/plan/notice/
                     skill/agent_group/agent_group_member)
        entity_id:   entity ID. For agent_group_member it is the composite key "group_id:agent_id"
        row:         optional, the raw DB row. Used to locate foreign-key dimensions of reverse
                     indexes (e.g. plan_id / task_id / parent_run_uuid of an execution).
                     Without it the generic "all/status" indexes are still cleaned, but reverse
                     indexes (by_plan / by_agent ...) cannot be located -> pass it whenever possible.
    """
    if entity_type not in CACHE_PREFIX:
        return

    row = row or {}
    eid = entity_id

    try:
        r = get_redis_client()
        pipe = r.pipeline()

        # ── Generic: all-entity indexes ──
        idx_all = IDX_PREFIX.get(f"{entity_type}_all")
        if idx_all:
            pipe.zrem(idx_all, eid)

        # ── Clear per-type dedicated indexes ──
        if entity_type == "project":
            if row.get("network_id"):
                pipe.srem(f"agx:idx:project:by_network:{row['network_id']}", eid)

        elif entity_type == "host":
            pipe.srem("agx:idx:host:online", eid)
            if row.get("network_id"):
                pipe.srem(f"agx:idx:host:by_network:{row['network_id']}", eid)

        elif entity_type == "agent":
            pipe.srem(IDX_PREFIX["agent_active"], eid)
            if row.get("network_id"):
                pipe.srem(f"agx:idx:agent:by_network:{row['network_id']}", eid)
            # Reverse: other indexes where this agent appears as a foreign key
            pipe.srem(f"agx:idx:task:by_agent:{eid}", eid)
            pipe.srem(f"agx:idx:plan:by_agent:{eid}", eid)
            pipe.srem(f"agx:idx:notice:agent:{eid}", eid)
            pipe.srem(f"agx:idx:agent_group_member:by_agent:{eid}", eid)

        elif entity_type == "task":
            pipe.srem(IDX_PREFIX["task_active"], eid)
            if row.get("agent_id"):
                pipe.srem(f"agx:idx:task:by_agent:{row['agent_id']}", eid)
            # * Reverse: members of agx:idx:execution:by_task:{task_id} are [execution_id]s,
            #   not task_ids -- so srem(eid) is wrong; the whole key must be deleted
            #   (the task is gone, so the index is meaningless). Verified the hard way on 2026-10-21.
            pipe.delete(f"{EXEC_BY_TASK}{eid}")

        elif entity_type == "execution":
            # * Clear all 7 status indexes
            for s in _EXEC_STATUSES:
                pipe.zrem(f"{EXEC_STATUS_IDX}{s}", eid)
            if row.get("task_id"):
                pipe.srem(f"{EXEC_BY_TASK}{row['task_id']}", eid)
            if row.get("parent_run_uuid"):
                pipe.srem(f"{EXEC_BY_PARENT}{row['parent_run_uuid']}", eid)
            # * This is exactly the class that was missed on 2026-10-21
            if row.get("plan_id"):
                pipe.zrem(f"agx:idx:execution:by_plan:{row['plan_id']}", eid)

        elif entity_type == "plan":
            if row.get("project_id"):
                pipe.srem(f"agx:idx:plan:by_project:{row['project_id']}", eid)
            if row.get("created_by"):
                pipe.srem(f"agx:idx:plan:by_agent:{row['created_by']}", eid)
            # Reverse: execution indexes of this plan
            pipe.delete(f"agx:idx:execution:by_plan:{eid}")

        elif entity_type == "notice":
            if row.get("agent_id"):
                pipe.srem(f"agx:idx:notice:agent:{row['agent_id']}", eid)
            if row.get("project_id"):
                pipe.srem(f"agx:idx:notice:project:{row['project_id']}", eid)
            if row.get("task_id"):
                pipe.srem(f"agx:idx:notice:task:{row['task_id']}", eid)
            if row.get("run_uuid"):
                pipe.srem(f"agx:idx:notice:run:{row['run_uuid']}", eid)

        elif entity_type == "agent_group":
            if row.get("network_id"):
                pipe.srem(f"agx:idx:agent_group:by_network:{row['network_id']}", eid)

        elif entity_type == "agent_group_member":
            if row.get("network_id"):
                pipe.srem(f"agx:idx:agent_group_member:by_network:{row['network_id']}", eid)
            if row.get("group_id"):
                pipe.srem(f"agx:idx:agent_group_member:by_group:{row['group_id']}", eid)
            if row.get("agent_id"):
                pipe.srem(f"agx:idx:agent_group_member:by_agent:{row['agent_id']}", eid)

        elif entity_type == "skill":
            # * Added 2026-10-21 (defect 1): strictly mirrors the skill branch of _build_extra_index.
            #   It was missing before -> deleting a skill left the category/source indexes behind.
            if row.get("category"):
                pipe.srem(f"agx:idx:skill:category:{row['category']}", eid)
            if row.get("source_agent_id"):
                pipe.srem(f"agx:idx:skill:source:{row['source_agent_id']}", eid)

        pipe.execute()

    except Exception as exc:
        log(f"_remove_from_indexes({entity_type}, {entity_id}) Redis failed: {exc}", "WARN")


def _invalidate_entity(entity_type: str, entity_id: str, row: dict | None = None):
    """Invalidate the entity cache and drop its membership from every index. Silently degrades when Redis is unavailable.

    * Revised 2026-10-21: the original implementation deleted only the entity hash and left the indexes
      in place -> dirty indexes. It now also calls _remove_from_indexes(). Passing row is recommended
      (it locates the reverse-index dimensions).
    """
    try:
        r = get_redis_client()
        key = f"{CACHE_PREFIX[entity_type]}{entity_id}"
        r.delete(key)
        _remove_from_indexes(entity_type, entity_id, row)

    except Exception as exc:
        log(f"_invalidate_entity({entity_type}, {entity_id}) Redis failed: {exc}", "WARN")


def _load_from_mysql_and_cache(sql: str, params: tuple, entity_type: str, entity_id_col: str) -> dict | None:
    """Read through to MySQL and fill the Redis cache."""
    rows = execute_sql(sql, params)

    if not rows:
        return None

    row = rows[0]
    entity_id = row[entity_id_col]

    _sync_entity(entity_type, entity_id, row)

    return row


def _publish_event(entity_type: str, event: str, entity_id: str, payload: dict = None):
    """Publish a Redis Pub/Sub event for external monitoring systems to consume."""
    try:
        r = get_redis_client()

        message = {
            "type": entity_type,
            "event": event,
            "entity_id": entity_id,
            "ts": __import__("datetime").datetime.now().isoformat(),
        }

        if payload:
            message["payload"] = payload

        r.publish("agx:events", json.dumps(message, ensure_ascii=False))

    except Exception as exc:
        log(f"_publish_event({entity_type}, {entity_id}) failed: {exc}", "WARN")


def _check_plan_completion(plan_id: str):
    """Check whether the plan is fully complete (via Redis cache).

    Logic: read run_status of every step from Redis agx:idx:execution:by_plan:{plan_id};
    no pending/running/draft -> all done -> set plan_status=completed.
    No MySQL access, no extra index cost.
    The caller supplies plan_id from agx_task_execution, so parallel plans do not interfere.
    """
    try:
        r = get_redis_client()
        step_ids = r.zrange(f"agx:idx:execution:by_plan:{plan_id}", 0, -1)
        if not step_ids:
            return

        pipe = r.pipeline()
        for sid in step_ids:
            pipe.hget(f"agx:entity:execution:{sid}", "run_status")
        statuses = pipe.execute()
        # * Key correction 2026-10-21: hget returning None means this execution entity [does not exist]
        #   (cache lost / already deleted / dirty index pointing at a ghost id).
        #   The old criterion only checked ("pending","running","draft"), and None is not among them
        #   -> counted as "done" -> the plan was [wrongly marked completed].
        #   Now: None always means [not done]; wait for the entity to recover or for --orphans to clean it up.
        #   Better to finish late than to finish wrongly.
        # * Added 2026-11-06: awaiting_confirm -- that state means the executor has delivered and is waiting for human release,
        #   which semantically is [not done]. The old criterion had only pending/running/draft -> it counted as done,
        #   and the measured consequence: in a multi-step plan, when an earlier step stops at awaiting_confirm and a later
        #   step succeeds, this check concludes "nothing unfinished" -> the plan is marked completed before the release happens.
        unfinished = [
            s for s in statuses
            if s is None or s in ("pending", "running", "draft", "awaiting_confirm")
        ]
        if unfinished:
            log(f"_check_plan_completion({plan_id}): {len(unfinished)} 个 step 状态不可判定"
                f"(entity 缺失、未完成或待放行), 暂不判定完成", "WARN")
            return

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        execute_sql(
            "UPDATE agx_plan SET plan_status='completed', completed_at=%s WHERE plan_id=%s",
            (now_str, plan_id),
            commit=True,
        )
        _sync_entity("plan", plan_id, {"plan_status": "completed", "completed_at": now_str})
        _publish_event("plan", "completed", plan_id)
    except Exception as exc:
        log(f"_check_plan_completion({plan_id}) failed: {exc}", "WARN")


def _invalidate_worker_queues_cache(host_id: str):
    """Actively invalidate the worker_queues cache so the daemon immediately sees agent changes."""
    try:
        r = get_redis_client()
        cache_key = f"agx:worker_queues:{host_id}"
        r.delete(cache_key)
    except Exception as exc:
        log(f"_invalidate_worker_queues_cache({host_id}) failed: {exc}", "WARN")


# ============================================================
# Build fingerprint -- so agx_check_services can answer "which build is running in production"
# ============================================================
# Background (decided by Lao Feng on 2026-10-29): mcp_version / mcp_sha used to be hand-written literals,
#   so changing four thousand lines would not move them -> the live build could not be audited, which once caused a dispute
#   over "is the new code or the old code running". Hence this automatic fingerprint.
#
# * Three hard constraints (stated by Lao Feng on 2026-10-29; read them before changing anything):
#   1) Without git it must degrade without raising -- someone else may have installed a copy (no .git), and a machine
#      may have no git executable at all. In that case fall back to the "file mtime" fingerprint,
#      never raise. agx_check_services is the "system self-check"; the self-check tool crashing
#      is the worst possible outcome.
#   2) With git but a dirty work tree, dirty must be reported explicitly -- "code is running but not committed" is the
#      easiest scenario to misjudge, and the HEAD hash must not masquerade as "the code that is running".
#   3) Do not change a single character of the hand-written mcp_version / mcp_sha -- that is the human release note
#      ("what was deliberately released last time"); the automatic fingerprint is another axis ("what is on disk right now").
#      Both coexist, each says its own thing, and they must not overwrite each other.
#
# Output examples:
#   with git and clean  : f5ba715           git clean
#   with git but dirty  : f5ba715+dirty(3)  git dirty (3 = number of changed files)
#   no .git / no git    : copy@<mtime>      nogit, fingerprint is the mtime
# ============================================================
_BUILD_STAMP_CACHE = None

# Only the code paths that make up the runtime matter. A repo-wide `git status` is deliberately avoided:
#   local changes to agx_config.json / docs should not dirty the build (118/188 always have
#   local changes in agx_config.json), otherwise it would raise a false alarm every day.
_BUILD_CODE_PATHS = ["agx_mcp", "scripts", "daemon.sh"]


def _git(args, cwd):
    """Run one git command; any failure (no git / not a repo / timeout) returns None, never raises."""
    try:
        out = subprocess.check_output(
            ["git"] + args, cwd=cwd,
            stderr=subprocess.DEVNULL, timeout=3,
        )
        return out.decode("utf-8", "replace").strip()
    except Exception:
        return None


def _build_stamp() -> dict:
    """Compute the build fingerprint of the current MCP code (computed once per process lifetime).

    Never raises: any failing step degrades to the file-mtime fingerprint.
    """
    global _BUILD_STAMP_CACHE
    if _BUILD_STAMP_CACHE is not None:
        return _BUILD_STAMP_CACHE

    stamp = {
        "build_sha": "unknown",
        "build_dirty": None,        # None = unknowable (not False)
        "build_source": "nogit",    # git | nogit
        "build_at": None,           # mtime of this file
        "build_detail": "unknown",
    }

    # * 2026-11-11: when the install directory is a symlink to the repo (~/.agx/agx_mcp -> repo/agx_mcp),
    #   abspath(__file__) lands in the install dir while realpath lands in the repo. The build fingerprint asks
    #   "which commit is this code on disk", so it must follow the symlink to the real thing, otherwise it always reports nogit.
    _stamp_root = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    if not os.path.isdir(_stamp_root):
        _stamp_root = _project_root

    try:
        stamp["build_at"] = datetime.fromtimestamp(
            os.path.getmtime(__file__)
        ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass

    # ── Gate 1: is there a git executable on this machine? (copy installs may not have one) ──
    git_available = _git(["--version"], _stamp_root) is not None

    head = None
    if git_available:
        # ── Gate 2: is this directory itself a git work tree?
        #   --show-toplevel must be compared: git searches parent directories for a repo, and without comparing,
        #   a copy installed inside someone else's repo would report "the parent repo's hash",
        #   which is worse than reporting unknown -- a wrong answer posing as a right one.
        toplevel = _git(["rev-parse", "--show-toplevel"], _stamp_root)
        if toplevel and os.path.realpath(toplevel) == os.path.realpath(_stamp_root):
            head = _git(["rev-parse", "--short", "HEAD"], _stamp_root)

    if head:
        stamp["build_source"] = "git"
        stamp["build_sha"] = head

        # ── Gate 3: code running but not committed? (the easiest scenario to misjudge; must be explicit) ──
        #   --untracked-files=no: logs/temp files do not count as dirty.
        #   Path limited: only runtime code, so config/doc changes do not raise false alarms.
        try:
            status = subprocess.check_output(
                ["git", "status", "--porcelain", "--untracked-files=no",
                 "--"] + _BUILD_CODE_PATHS,
                cwd=_stamp_root, stderr=subprocess.DEVNULL, timeout=5,
            ).decode("utf-8", "replace").strip()
            changed = [ln for ln in status.splitlines() if ln.strip()]
            dirty = len(changed) > 0
        except Exception:
            dirty, changed = None, []

        stamp["build_dirty"] = dirty
        if dirty is True:
            stamp["build_sha"] = f"{head}+dirty({len(changed)})"
            stamp["build_detail"] = (
                f"{head} 有 {len(changed)} 个代码文件未提交（运行中的未必是 HEAD）"
            )
        elif dirty is False:
            stamp["build_detail"] = f"{head}，代码与 HEAD 一致"
        else:
            stamp["build_detail"] = f"{head}，但工作区状态不可知（git status 失败）"
    else:
        # No git or not a git repo -- fall back to the mtime fingerprint and explain why
        reason = "非 git 工作树（copy 安装）" if git_available else "本机无 git 可执行文件"
        at = stamp["build_at"] or "unknown"
        stamp["build_source"] = "nogit"
        stamp["build_sha"] = f"copy@{at}"
        stamp["build_detail"] = f"copy@{at}（{reason}，只能以文件 mtime 为指纹）"

    _BUILD_STAMP_CACHE = stamp
    return stamp


# ============================================================

# Tools: system self-check

# ============================================================


@mcp.tool()
@require_level(0)
def agx_check_services(caller_agent_id: str) -> dict:
    """
    [System self-check] Verify AgentGeeX kernel connectivity and environment integrity.

    Logic:

    1. Check that PyMySQL can handshake with the database.
    2. Verify the current DB user may run a query (SELECT 1).
    3. Check that the AgentGeeX core tables (e.g. agx_project) have been created by the DDL.
    4. Check Redis service status.
    5. Verify the consistency of the Redis cache-layer indexes.

    When to use:

    - Before any bulk database operation.
    - As the "boot self-check" when an agent starts.
    - For troubleshooting when a connection error appears.

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity and enables an audit trail. Read-only tools are no exception -- every call chain must be traceable.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).

    """
    # * Build marker: verifies whether the code took effect when "only the MCP service is restarted, not the gateway".
    result = {"service_build": "MCP-BUILD-20261104-0746-T1"}

    # --- MySQL check ---

    try:
        row = executeSql("SELECT 1 AS alive, VERSION() AS ver")[0]
        version_info = row["ver"]
        table_exists = executeSql("SHOW TABLES LIKE 'agx_project'")

        db_name = config.mysql.get("database", "agentgeex")
        db_host = config.mysql.get("host", "localhost")
        db_port = str(config.mysql.get("port", 3306))

        result["mysql_check_info"] = {
            "status": "healthy",
            "message": "AgentGeeX Kernel MySQL (PyMySQL) 服务正常",
            "database": db_name,
            "server_version": str(version_info),
            "table_check": "passed" if table_exists else "tables_missing",
            "endpoint": f"{db_host}:{db_port}",
        }

    except Exception as e:
        log(f"MySQL health check FAILED: {e}", "ERROR")
        result["mysql_check_info"] = {
            "status": "unhealthy",
            "message": str(e),
        }

    # --- Redis check ---

    try:
        r = get_redis_client()

        if r.ping():
            info = r.info(section="server")

            # Verify cache index consistency

            cache_stats = {}

            for entity_type, prefix in CACHE_PREFIX.items():
                try:
                    keys = list(r.scan_iter(match=f"{prefix}*", count=500))

                    cache_stats[f"{entity_type}_count"] = len(keys)

                except Exception as exc:
                    log(f"agx_check_services scan_iter({entity_type}) failed: {exc}", "WARN")
                    cache_stats[f"{entity_type}_count"] = -1

            result["redis_check_info"] = {
                "status": "healthy",
                "message": "Redis 缓存层连接正常",
                "redis_version": info.get("redis_version"),
                "endpoint": f"{REDIS_HOST}:{REDIS_PORT}",
                "db_index": REDIS_DB,
                "cache_stats": cache_stats,
            }

    except Exception as e:
        log(f"Redis health check FAILED: {e}", "ERROR")
        result["redis_check_info"] = {
            "status": "unhealthy",
            "message": str(e),
        }

    # * 2026-10-22: the version fields shrank -- mcp_version + mcp_sha used to carry a whole
    #   commit message (measured 1,380 + 1,912 = 3,292 characters / about 4.8KB), poured into the caller's context on every
    #   agx_check_services call; and "anyone can write anything" made it a
    #   structural context-pollution channel (found by aifr_pm, 2026-10-21).
    #   Now it is "timestamp + one-line subject", with details left in git log; the mcp_sha field name is kept
    #   (nothing reads it in code, no front-end depends on it) but holds only a short digest, so existing callers do not break.
    result["redis_check_info"]["mcp_version"] = (
        "2026-10-22 09:47:00 | REFACTOR(remove-agx_open_case): "
        "删除零调用点死代码 agx_open_case（214 行）、create 直调 ensure_session"
    )
    result["redis_check_info"]["mcp_sha"] = (
        "remove-ao-open-case-dead-code"
    )
    # * 2026-10-29: automatic build fingerprint, **coexisting with, not overwriting**, the hand-written release note above.
    #   The hand-written one answers "what was deliberately released"; the automatic one answers "what is on disk now".
    #   * To judge "is production running the new or the old code", use build_sha / build_dirty, not mcp_version.
    #   * Without git / on a copy install it degrades to copy@<mtime>; the self-check must never crash just because version lookup failed.
    _bs = _build_stamp()
    result["redis_check_info"]["build_sha"] = _bs["build_sha"]
    result["redis_check_info"]["build_dirty"] = _bs["build_dirty"]
    result["redis_check_info"]["build_source"] = _bs["build_source"]
    result["redis_check_info"]["build_at"] = _bs["build_at"]
    result["redis_check_info"]["build_detail"] = _bs["build_detail"]
    result["redis_check_info"]["permissions_sha"] = (
        "dispatch-exempt: role-level (agent_manager/host_manager) "
        "+ agent-level (dispatch_exempt_until, self-expiring)"
    )
    result["redis_check_info"]["permissions_updated_at"] = "2026-10-31 08:55:00"
    result["redis_check_info"]["runtime_timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return result


# ============================================================

# Tools: core tools

# ============================================================


@mcp.tool()
@require_level(0)
def agx_generate_uuid(caller_agent_id: str = "", prefix: str = "agx", infix: str = "", count: int = 1) -> str:
    """
    [Core tool] Generate a globally unique, prefixed identifier (UUID) that follows the AgentGeeX naming rules.

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity and enables an audit trail. Pure utility functions are no exception -- every call chain must be traceable.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).

        prefix (str): resource-type prefix. The following conventions are strongly recommended:

            * 'proj': project ID
            * 'task': task base definition ID (Task Base ID)
            * 'run':  concrete task execution instance ID (Run UUID)
            * 'sess': sub-agent or conversation-fragment session ID (Session UUID)
            * 'agt':  agent registry ID (Agent ID)
            * 'host': physical host ID (Host ID)

        infix (str, optional): middle segment marker, e.g. task_labs.
            Produces the run_{task_lab}_{uuid} form, which is easier to recognize.

        count (int, optional): how many to generate, default 1. Batches are deduplicated internally.

    Output examples: 'proj_a1b2c3d4e5f6', 'run_project_create_a1b2c3d4e5f6'

    Batch output: when count>1 returns a JSON array, e.g. ["run_a_xxx","run_b_xxx","run_c_xxx"]

    """
    clean_prefix = prefix.lower().strip().rstrip("_") + "_"

    # batch generation (deduplicated)
    if count > 1:
        seen = set()
        result = []
        while len(result) < count:
            unique_id = uuid.uuid4().hex[:12]
            if unique_id in seen:
                continue
            seen.add(unique_id)
            if infix and infix.strip():
                clean_infix = infix.lower().strip().strip("_")
                result.append(f"{clean_prefix}{clean_infix}_{unique_id}")
            else:
                result.append(f"{clean_prefix}{unique_id}")
        return json.dumps(result, ensure_ascii=False)

    # single generation
    unique_id = uuid.uuid4().hex[:12]
    if infix and infix.strip():
        clean_infix = infix.lower().strip().strip("_")
        return f"{clean_prefix}{clean_infix}_{unique_id}"
    return f"{clean_prefix}{unique_id}"


# ============================================================

# Tools: project creation -- dual write MySQL + Redis cache

# ============================================================


# built-in system project ID, used for generic/internal tasks without an explicit project_id

SYSTEM_PROJECT_ID = "system_project"


def _resolve_project_id(project_id: str, network_id: str = "") -> str:
    """Resolve project_id; return the built-in system project ID when empty."""
    return project_id if project_id else SYSTEM_PROJECT_ID


def _resolve_project_id_from_agent(agent_id: str) -> str:
    """Get the owning project from the agent's project_id field."""
    if not agent_id:
        return SYSTEM_PROJECT_ID

    rows = execute_sql(
        "SELECT project_id FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1 LIMIT 1",
        (agent_id,),
    )

    if rows and rows[0].get("project_id"):
        return rows[0]["project_id"]

    return SYSTEM_PROJECT_ID


@mcp.tool()
@require_level(5)
def agx_create_project(
    caller_agent_id: str,
    project_name: str,
    description: str,
    project_abbr: str = "",
    platform: str = "openclaw",
    source_dir: str = "",
    admin_agent_id: str = "",
    project_id: str = "",
) -> str:
    """
    [Project creation] Initialize a new business project inside the network of the calling agent.

    Why it matters:
    A project is the top-level container for all tasks, agents and compute. For example "dealer analysis"
    and "automated backtesting" belong to different projects.
    network_id is derived from the calling agent; it no longer has to be passed in (same-network is enforced).

    Parameters:

    - caller_agent_id: caller agent ID (for permission checks, and to derive network_id)
    - project_name: human-readable project name, e.g. 'Dealer_Indicator_V4'.
    - description: project background; state the core goal and the owner.
    - project_abbr: short project abbreviation, e.g. 'div4'. Must be all lowercase letters; used as the agent naming prefix so members are easy to attribute. May be empty.
    - platform: target platform type of the project, e.g. "openclaw" | "claude_code" | "custom".
        Determines the default runtime and the shared task set available to agents in this project.
        Defaults to "openclaw".
    - source_dir: project resource directory holding source code etc. May be empty.
    - admin_agent_id: project admin agent ID (optional). If the caller already knows the project admin
        (usually a project_manager), pass it and it is written straight into agx_project.admin_agent_id;
        leave it empty when unsure, and it can be filled later by cloning the PM or by the integrity check.
    - project_id: * added 2026-10-13. Custom project ID (optional). Semantics mirror agx_clone_agent.new_agent_id:
        * passed  -> validate the format (mandatory 'proj_' prefix + ^[a-z][a-z0-9_]*$) + explicit duplicate check
                     (reject if it exists, returning "project_id 'xxx' already exists, choose a different project_id");
        * omitted -> keep the default: auto-generate proj_<random_hex>.
        Motivation: the project id is meaningful inside the system (PMs/members recognize ownership from it);
        a random hex is unclear (feedback after 4 months of use).

    Returns:
        str: success message including project_id (custom or auto-generated).

    """
    try:
        # Project creation allowlist: only specific agents

        # Derive network_id from the calling agent (same network enforced, cannot be passed in)
        caller_agent = execute_sql(
            "SELECT network_id FROM agx_agent_registry WHERE agent_id = %s",
            (caller_agent_id,),
        )
        if not caller_agent:
            return json.dumps({"error": f"调用方 Agent '{caller_agent_id}' 不存在"})
        network_id = caller_agent[0]["network_id"]

        # project_id: use the caller's explicit value if given (validate + explicit duplicate check), otherwise auto-generate
        # ── * 2026-10-13: align with the semantics of agx_clone_agent.new_agent_id ──
        if project_id and project_id.strip():
            pid = project_id.strip()
            valid, err_msg = _validate_project_id(pid)
            if not valid:
                return json.dumps({"error": err_msg}, ensure_ascii=False)
            dup = execute_sql(
                "SELECT COUNT(*) AS cnt FROM agx_project WHERE project_id = %s",
                (pid,),
            )
            if dup and dup[0].get("cnt", 0) > 0:
                return json.dumps(
                    {"error": f"project_id '{pid}' already exists, choose a different project_id"},
                    ensure_ascii=False,
                )
            project_id = pid
        else:
            project_id = agx_generate_uuid(prefix="proj")

        abbr = project_abbr.strip().lower() if project_abbr else None

        sd = source_dir.strip() if source_dir else None

        # optional admin_agent_id: validate its existence when passed
        admin = admin_agent_id.strip() if admin_agent_id else None
        if admin:
            admin_row = execute_sql(
                "SELECT agent_id FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
                (admin,),
            )
            if not admin_row:
                return json.dumps({"error": f"admin_agent_id '{admin}' 不存在或已停用"})

        sql = "INSERT INTO agx_project (network_id, project_id, project_name, project_abbr, x_desc, source_dir, admin_agent_id) VALUES (%s, %s, %s, %s, %s, %s, %s)"

        pkid = execute_sql(sql, (network_id, project_id, project_name, abbr, description, sd, admin), commit=True)

        # --- sync to the Redis cache ---

        _sync_entity(
            "project",
            project_id,
            {
                "pkid": pkid,
                "project_id": project_id,
                "network_id": network_id,
                "project_name": project_name,
                "project_abbr": abbr or "",
                "x_desc": description,
                "source_dir": sd or "",
                "admin_agent_id": admin or "",
                "active_status": "1",
            },
        )

        _publish_event("project", "created", project_id)

        return f"Project created: {project_name} ID: {project_id}" + (f" Admin: {admin}" if admin else "")

    except Exception as e:
        return _log_exception("agx_create_project", e)


# ============================================================

# Tools: host registration -- dual write MySQL + Redis cache

# ============================================================


@mcp.tool()
@require_level(6)
def agx_register_host(
    caller_agent_id: str,
    network_id: str,
    host_alias: str,
    ip: str,
    platform: str,
    slots: int = 16,
    host_id: str = "",
    platforms: list | None = None,
) -> str:
    """
    [Host registration] Register a physical or virtual host into the AgentGeeX asset pool.
    When to use:

    - Onboarding a new Ubuntu server, macOS workstation or local WSL2 environment.

    Args:

        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        network_id (str): logical network ID this host belongs to.
        host_alias (str): host alias, e.g. "Ubuntu-Server-01" or "Test-MacBook-Pro".
        ip (str): access address of the host. May be a LAN IP, a public IP or a Tailscale virtual IP.
        platform (str): OS type. Suggested enum: ['macOS', 'Ubuntu', 'WSL2', 'Windows'].
        slots (int): maximum concurrent tasks, default 16. How many agent instances this host can carry at once.
        host_id (str, optional): custom host ID. If passed and the host exists, creation is skipped;

        empty means auto-generate.
        platforms (list, optional): agent platforms available on this host and how to reach them (JSON array,
        pass a list directly; it is serialized on insert). Format spec (same as the column comment, do not invent keys):
          required {platform, name, mode, port, base_path}
            platform  platform id, e.g. "openclaw"
            name      display name, e.g. "OpenClaw #2"
            mode      "process" | "container"
            port      integer port, e.g. 19789
            base_path HTTP path, e.g. "/chat"
          conditional keys {addr, profile, note}
            addr      required only when mode=container (the container owns its address);
                      **forbidden** when mode=process (the address is built from endpoint_ip+port)
            profile   OpenClaw profile name, decides which ~/.openclaw-{profile}/ is read/written;
                      required for multiple instances, empty string "" for the primary instance
            note      free-form comment; **must not carry information the machine needs to read**
                      (promote anything machine-readable to its own key)
        Example: [{"platform":"openclaw","name":"OpenClaw #2","mode":"process",
                "port":19789,"base_path":"/chat","profile":"openclaw2"}]
        When omitted, a new host gets platforms = NULL (fill it later with agx_update_host).

    Returns:
        str: registration confirmation including host_id.

    """
    try:
        # auto-generate host_id when empty

        if not host_id:
            host_id = agx_generate_uuid(prefix="host")

        else:
            # check whether it already exists

            exists = execute_sql(
                "SELECT 1 FROM agx_host_inventory WHERE host_id=%s",
                (host_id,),
            )

            if exists:
                return f"Host already exists: {host_alias} ID: {host_id}"

        sql = """
        INSERT INTO agx_host_inventory (network_id, host_id, host_alias, endpoint_ip, os_platform, platforms, task_slots_limit, presence_status)

        VALUES (%s, %s, %s, %s, %s, %s, %s, 1)

        """
        pkid = execute_sql(
            sql,
            (
                network_id,
                host_id,
                host_alias,
                ip,
                platform,
                json.dumps(platforms, ensure_ascii=False) if platforms else None,
                slots,
            ),
            commit=True,
        )

        # --- sync to the Redis cache ---

        _sync_entity(
            "host",
            host_id,
            {
                "pkid": pkid,
                "host_id": host_id,
                "network_id": network_id,
                "host_alias": host_alias,
                "endpoint_ip": ip,
                "os_platform": platform,
                "platforms": json.dumps(platforms, ensure_ascii=False) if platforms else None,
                "task_slots_limit": str(slots),
                "active_task_count": "0",
                "active_status": "1",
                "presence_status": "1",
            },
        )

        _publish_event("host", "registered", host_id)

        return f"Host registered: {host_alias} ID: {host_id}"

    except Exception as e:
        return _log_exception("agx_register_host", e)


# ============================================================

# Tools: team management -- dual write MySQL + Redis cache

# ============================================================


# @mcp.tool() - not exposed to MCP, internal to sync_struct only
@require_level(4)
def agx_create_agent_group(
    caller_agent_id: str, network_id: str, group_name: str, description: str = "", group_id: str = ""
) -> str:
    """
    [Create team] Create a new agent team group and sync it to the Redis cache.
    A team is a logical organizational unit of agents. For example "social media operations team" may contain
    a trend-collection agent, a feedback-watch agent, a story-writing agent, and so on.
    An agent belongs to at most one team; a team may hold many agents.

    Args:

        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        network_id (str): owning network ID.
        group_name (str): team name, e.g. "social media operations team".
        description (str): team description, e.g. "runs the day-to-day operations of the social media matrix".
        group_id (str, optional): custom team ID prefix. Auto-generated when empty.
    Returns:

        str: success message including the generated group_id.

    """
    try:
        group_id = group_id if group_id else agx_generate_uuid(prefix="group")

        sql = "INSERT INTO agx_agent_group (network_id, group_id, group_name, x_desc) VALUES (%s, %s, %s, %s)"

        pkid = execute_sql(sql, (network_id, group_id, group_name, description), commit=True)

        _sync_entity(
            "agent_group",
            group_id,
            {
                "pkid": pkid,
                "group_id": group_id,
                "network_id": network_id,
                "group_name": group_name,
                "x_desc": description,
                "active_status": "1",
            },
        )

        _publish_event("agent_group", "created", group_id, {"group_name": group_name})

        return f"Agent group created: {group_name} ID: {group_id}"

    except Exception as e:
        return _log_exception("agx_create_agent_group", e)


@mcp.tool()
@require_level(4)
def agx_update_agent_group(caller_agent_id: str, group_id: str, updates: dict) -> str:
    """
    [Update team] Dynamically update selected fields of agx_agent_group, dual-writing MySQL + Redis.

    Args:

        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        group_id (str): team ID.
        updates (dict): field dictionary to update.
            Updatable fields: group_name, x_desc, active_status.
            Example: {"group_name": "new team name"}
                     {"active_status": "0"}

    Returns:

        str: update result message.

    """
    return _do_update("agent_group", "group_id", group_id, updates)


# @mcp.tool() - not exposed to MCP, internal to sync_struct only
@require_level(4)
def agx_add_agent_to_group(caller_agent_id: str, group_id: str, agent_id: str, network_id: str = "") -> str:
    """
    [Add agent to team] Add the given agent to the given team (many-to-many).
    An agent may belong to several teams at once, and a team may contain several agents.

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        group_id (str): team ID.
        agent_id (str): agent ID.
        network_id (str, optional): owning network ID. Looked up from the agent when omitted.

    Returns:

        str: success message.

    """
    try:
        if not network_id:
            agent = execute_sql(
                "SELECT network_id FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
                (agent_id,),
            )

            if not agent:
                return f"Error: agent_id '{agent_id}' not found or inactive"

            network_id = agent[0]["network_id"]

        sql = "INSERT IGNORE INTO agx_agent_group_member (network_id, group_id, agent_id) VALUES (%s, %s, %s)"

        pkid = execute_sql(sql, (network_id, group_id, agent_id), commit=True)

        if not pkid:
            return f"Agent {agent_id} is already in group {group_id}"

        _sync_entity(
            "agent_group_member",
            f"{group_id}:{agent_id}",
            {
                "pkid": pkid,
                "network_id": network_id,
                "group_id": group_id,
                "agent_id": agent_id,
                "active_status": "1",
            },
        )

        _publish_event(
            "agent_group_member",
            "added",
            f"{group_id}:{agent_id}",
            {
                "group_id": group_id,
                "agent_id": agent_id,
            },
        )

        # inject the group's host-conditional tasks (owner_group not empty); they arrive with the group binding
        _inject_group_tasks_for_agent(group_id, agent_id, network_id)

        return f"Agent {agent_id} added to group {group_id}"

    except Exception as e:
        return _log_exception("agx_add_agent_to_group", e)


# @mcp.tool() - not exposed to MCP, internal to sync_struct only
@require_level(4)
def agx_remove_agent_from_group(caller_agent_id: str, group_id: str, agent_id: str) -> str:
    """
    [Remove agent from team] Remove the given agent from the given team.

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        group_id (str): team ID.
        agent_id (str): agent ID.

    Returns:

        str: success message.

    """
    try:
        # check whether it already exists

        exists = execute_sql(
            "SELECT 1 FROM agx_agent_group_member WHERE group_id = %s AND agent_id = %s",
            (group_id, agent_id),
        )

        if not exists:
            return f"Agent {agent_id} is not in group {group_id}"

        execute_sql(
            "DELETE FROM agx_agent_group_member WHERE group_id = %s AND agent_id = %s",
            (group_id, agent_id),
            commit=True,
        )

        # * 2026-10-21: pass row as well (group_id/agent_id are both known),
        #   otherwise the by_group / by_agent / by_network reverse indexes cannot be cleared
        _invalidate_entity("agent_group_member", f"{group_id}:{agent_id}",
                           {"group_id": group_id, "agent_id": agent_id})

        _publish_event("agent_group_member", "removed", f"{group_id}:{agent_id}")

        return f"Agent {agent_id} removed from group {group_id}"

    except Exception as e:
        return _log_exception("agx_remove_agent_from_group", e)


# ============================================================

# Tools: project-agent relation management -- dual write MySQL + Redis cache

# ============================================================


@mcp.tool()
@require_level(5)
def agx_bind_agent_to_project(caller_agent_id: str, project_id: str, agent_id: str, network_id: str = "") -> str:
    """
    [Bind agent to project] Set the agent's project_id to the given project.

    An agent must be bound to a project before it can serve execution instances of that project.

    project_id lives in the agx_agent_registry table.

    Args:

        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        project_id (str): project ID.
        agent_id (str): agent ID.
        network_id (str): owning network ID (optional; recorded when provided).

    Returns:

        str: success message.

    """
    try:
        # query the project network, or get it from the agent

        if not network_id:
            proj = execute_sql(
                "SELECT network_id FROM agx_project WHERE project_id = %s",
                (project_id,),
            )

            if proj:
                network_id = proj[0]["network_id"]

            else:
                agent = execute_sql(
                    "SELECT network_id FROM agx_agent_registry WHERE agent_id = %s",
                    (agent_id,),
                )

                if agent:
                    network_id = agent[0]["network_id"]

        sql = "UPDATE agx_agent_registry SET project_id = %s WHERE agent_id = %s"

        execute_sql(sql, (project_id, agent_id), commit=True)

        # sync the Redis cache

        _sync_entity(
            "agent",
            agent_id,
            {
                "project_id": project_id,
            },
        )

        _publish_event(
            "agent",
            "bound",
            agent_id,
            {
                "project_id": project_id,
                "agent_id": agent_id,
            },
        )

        return f"Agent {agent_id} bound to project {project_id}"

    except Exception as e:
        return _log_exception("agx_bind_agent_to_project", e)


# ══════════════════════════════════════════════════════════════════════════
# clone identity adaptation & deployment memory isolation (2026-09-28)
# --------------------------------------------------------------------------
# Background:
#   1) clone is a pure DB operation (INSERT..SELECT); agents_md and the other *_md fields are inherited verbatim from the template ->
#      the title/name hard-coded in an old template spreads with every clone (super_pm -> aifr_pm); the new templates'
#      reserved {{AGENT_NAME}} placeholder never had a renderer either.
#      -> _render_agent_md_for_identity() renders the new instance identity in place at clone time.
#   2) MEMORY.md / memory/*.md in the workspace are runtime memory; a new agent must not carry over the
#      template/parent instance's memory when it lands; deploy uses makedirs(exist_ok=True) + "x" and does not clean existing dirs.
#      -> _quarantine_workspace_memory() moves the memory into a quarantine directory on the "first deploy" (move, never delete).
# ══════════════════════════════════════════════════════════════════════════

# identity placeholders: {{KEY}} -> instance value
_AGENT_MD_PLACEHOLDER_KEYS = (
    "AGENT_ID",
    "AGENT_NAME",
    "AGENT_ROLE",
    "AGENT_MODEL",
    "HOST_ID",
    "PROJECT_ID",
)

# first-line title: # AGENTS.md - <name> [workspace|work manual|...]
# * Fixed a BUG on 2026-11-20: the old character class `[-\--]` reduced to a single LITERAL '-' (45) after re parsing,
#   while the actual template titles use an em dash '-' (U+2014) (e.g. super_pm: "# AGENTS.md - super_pm...")
#   -> the first-line title was never rewritten, so every clone kept the template's original name.
#   Now it covers hyphen / non-breaking hyphen / figure dash / en dash / em dash / horizontal bar / full-width minus.
_AGENTS_TITLE_RE = re.compile(r"^(#\s*AGENTS\.md\s*[-\u2010-\u2015\uFF0D]\s*).*$")

# identity block name line: - **name: xxx** / - **name:** xxx / - **name: xxx
_AGENTS_NAME_LINE_RE = re.compile(
    r"^(\s*-\s*\*\*\s*名称\s*[::]\s*\*\*\s*|\s*-\s*\*\*\s*名称\s*[::]\s*).*$"
)


def _render_agent_md_for_identity(text: str, mapping: dict, title_block: bool = True) -> str:
    """Render the template *_md fields into the identity of a new instance.

    Two stages (placeholders first, identity block second), idempotent:
      1. Placeholder substitution: {{AGENT_ID}}/{{AGENT_NAME}}/{{AGENT_ROLE}}/
         {{AGENT_MODEL}}/{{HOST_ID}}/{{PROJECT_ID}} -> instance values
      2. Conservative identity-block rewrite (for old hard-coded templates): only the "first-line title"
         and the "first name line" are touched; all other body text is left alone to avoid collateral damage.

    Args:
        text: the raw template field (may be empty).
        mapping: identity mapping; keys are listed in _AGENT_MD_PLACEHOLDER_KEYS.
        title_block: whether to run stage 2 (the identity-block rewrite).
            * 2026-11-20: defaults to True only for backward compatibility with existing callers; when cloning,
            **only agents_md passes True**; SOUL/IDENTITY/USER/TOOLS pass False -- they are narrative text, and
            forcing in a `- **name: ...**` line stretches the "who am I" section and reads like template residue.
            Those four files express identity well enough through the {{AGENT_ID}} placeholder or plain language.
    """
    if not text:
        return text

    out = text
    if "{{" in out:
        for k in _AGENT_MD_PLACEHOLDER_KEYS:
            out = out.replace("{{" + k + "}}", str(mapping.get(k, "")))

    if not out:
        return out

    # * 2026-11-20: for anything other than AGENTS.md it stops here (placeholder substitution only, the title/name lines are untouched)
    if not title_block:
        return out

    name = str(mapping.get("AGENT_NAME", ""))
    aid = str(mapping.get("AGENT_ID", ""))

    lines = out.split("\n")
    # 1) first-line title: rewritten only when it really is "# AGENTS.md - ..."
    # * the title uses agent_id -- agent_name may be a Chinese display name (e.g. "book reader dry-run reviewer"),
    #   and using it as the title leaves the clone's title still without its own agent_id.
    title_identity = aid or name
    if lines and _AGENTS_TITLE_RE.match(lines[0]):
        lines[0] = _AGENTS_TITLE_RE.sub(
            lambda m: m.group(1) + title_identity + " 工作空间", lines[0], count=1
        )
    name_cell = f"{aid} ({name})**" if (aid and name and aid != name) else f"{aid or name}**"

    # 2) the identity block name line
    for i, ln in enumerate(lines[:25]):
        mm = _AGENTS_NAME_LINE_RE.match(ln)
        if mm:
            lines[i] = mm.group(1) + name_cell
            return "\n".join(lines)

    # 3) old templates have no name line: insert one before the first H2, or after the first-line title
    #    (headless old templates, e.g. the book_* series)
    insert_at = None
    for i, ln in enumerate(lines[:25]):
        if i > 0 and ln.lstrip().startswith("#"):
            insert_at = i
            break
    if insert_at is None and len(lines) > 1 and lines[1].strip() == "":
        insert_at = 2
    if insert_at is not None:
        lines.insert(insert_at, f"- **名称: {name_cell}")
        if insert_at + 1 < len(lines) and lines[insert_at + 1].strip() != "":
            lines.insert(insert_at + 1, "")

    return "\n".join(lines)


_MEMORY_QUARANTINE_PREFIX = ".memory_quarantine_"


def _quarantine_workspace_memory(workspace_dir: str) -> dict:
    """First-deploy memory isolation: move MEMORY.md and memory/ under the workspace into a quarantine directory.

    Principle (approved by Lao Feng on 2026-09-28):
      - **Move, never delete**: physical move, never physical deletion; the directory can be restored wholesale.
      - Idempotent: reuse an existing quarantine directory; append a timestamp suffix on a name clash under the same base.
      - If there are no memory files, do nothing (no side effects).

    Returns:
        {"quarantined": [basename...], "dir": "<quarantine dir>", "skipped": ""}
    """
    import shutil

    result = {"quarantined": [], "dir": "", "skipped": ""}
    mem_file = os.path.join(workspace_dir, "MEMORY.md")
    mem_dir = os.path.join(workspace_dir, "memory")
    targets = [p for p in (mem_file, mem_dir) if os.path.exists(p)]
    if not targets:
        result["skipped"] = "no-memory-files"
        return result

    # reuse the isolation directory this agent already has, to avoid fragments
    try:
        existing = sorted(
            d for d in os.listdir(workspace_dir) if d.startswith(_MEMORY_QUARANTINE_PREFIX)
        )
    except OSError:
        existing = []
    if existing:
        q_dir = os.path.join(workspace_dir, existing[-1])
    else:
        q_dir = os.path.join(
            workspace_dir, f"{_MEMORY_QUARANTINE_PREFIX}{time.strftime('%Y%m%d_%H%M%S')}"
        )
    os.makedirs(q_dir, exist_ok=True)

    for p in targets:
        base = os.path.basename(p)
        dst = os.path.join(q_dir, base)
        if os.path.exists(dst):
            dst = os.path.join(q_dir, f"{base}.{time.strftime('%H%M%S')}")
        shutil.move(p, dst)
        result["quarantined"].append(base)

    result["dir"] = q_dir
    return result


def _clone_agent_from_template(
    template_agent_id: str,
    project_id: str,
    network_id: str,
    host_id: str = "",
    agent_name: str = "",
    new_agent_id: str = "",
) -> tuple:
    """
    [Internal function] Clone a new agent instance from a template agent.

    Template lookup priority:
    1. agx_agent_registry where agent_id = template_agent_id
    2. agx_agent_registry where agent_labs = template_agent_id (first active record)

    Clone result:
    - agent_id: uses new_agent_id when provided (after an explicit duplicate check), otherwise auto-generates
      {template_agent_id}_{random_hex(6)}
    - agent_name: uses agent_name when provided (display name), otherwise falls back to agent_id
    - project_id = target project
    - peer_agent_id = the template's agent_id
    - shared tasks are created automatically (driven by agx_struct.json)
    - Redis cache is synced automatically

    Args:
        template_agent_id: template agent ID (exact agent_id match first, agent_labs match as fallback)
        project_id: target project ID
        network_id: owning network ID
        host_id: target host ID
        agent_name: custom display name (optional, defaults to agent_id)
        new_agent_id: custom agent ID (optional, auto-generated when omitted)
            Naming rule: only lowercase letters [a-z], digits [0-9] and underscore [_].
            No uppercase, spaces, CJK or special characters (used as Redis key / path / CLI arg)

    Returns:
        (new_agent_id, error_message) - an empty error_message means success.

    """
    import os

    template = execute_sql(
        "SELECT * FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
        (template_agent_id,),
    )

    if not template:
        template = execute_sql(
            "SELECT * FROM agx_agent_registry WHERE agent_labs = %s AND active_status = 1 LIMIT 1",
            (template_agent_id,),
        )

    if not template:
        return ("", f"Template agent '{template_agent_id}' not found in registry")

    t = template[0]

    # agent_id: use new_agent_id when the caller passed one explicitly, otherwise auto-generate
    if new_agent_id and new_agent_id.strip():
        generated_id = new_agent_id.strip()
        # naming-convention validation
        valid, err_msg = _validate_agent_id(generated_id)
        if not valid:
            return (generated_id, err_msg)
        # explicit duplicate check: reject when agent_id already exists, instead of relying on a garbled PK-conflict error
        dup = execute_sql(
            "SELECT COUNT(*) AS cnt FROM agx_agent_registry WHERE agent_id = %s",
            (generated_id,),
        )
        if dup and dup[0].get("cnt", 0) > 0:
            return (generated_id, f"agent_id '{generated_id}' already exists, choose a different new_agent_id")
    else:
        suffix = os.urandom(3).hex()  # 3 bytes -> 6 hex, plenty for the agent cloning scale
        generated_id = f"{template_agent_id}_{suffix}"
    # agent_name: use the passed value when present (display name), otherwise fall back to agent_id
    generated_name = agent_name.strip() if (agent_name and agent_name.strip()) else generated_id

    # -- identity adaptation (2026-09-28): render the inherited *_md in place at clone time --
    # single template: {AGENT_ID, AGENT_NAME, AGENT_ROLE, AGENT_MODEL, HOST_ID, PROJECT_ID}
    _id_map = {
        "AGENT_ID": generated_id,
        "AGENT_NAME": generated_name,
        "AGENT_ROLE": t.get("agent_role", "") or "",
        "AGENT_MODEL": t.get("base_model", "") or "",
        "HOST_ID": host_id or t.get("host_id", "") or "",
        "PROJECT_ID": project_id,
    }
    # * Fixed 2026-11-20: the identity-block rewrite (first-line title + name line) should only apply to AGENTS.md,
    #   otherwise a `- **name: ...**` line gets forced into the body of SOUL/IDENTITY/USER/TOOLS,
    #   polluting the already written "who am I" section. Those files only need placeholder substitution.
    r_agents_md = _render_agent_md_for_identity(t.get("agents_md"), _id_map)
    r_identity_md = _render_agent_md_for_identity(t.get("identity_md"), _id_map, title_block=False)
    r_soul_md = _render_agent_md_for_identity(t.get("soul_md"), _id_map, title_block=False)
    r_tools_md = _render_agent_md_for_identity(t.get("tools_md"), _id_map, title_block=False)
    r_user_md = _render_agent_md_for_identity(t.get("user_md"), _id_map, title_block=False)

    # OK INSERT INTO ... SELECT - a pure database operation, inherits every field of the template
    # only 6 key fields change: agent_id, agent_name, project_id, peer_agent_id, host_id, deploy_status
    # * 2026-09-28: the 5 content fields (agents/identity/soul/tools/user_md) are no longer SELECTed as-is,
    #   they are rendered above and written as parameters, so hard-coded template identity cannot spread through cloning.
    sql = """INSERT INTO agx_agent_registry (network_id, agent_id, agent_name, description, agent_role, host_id, base_model, system_prompt,
        agent_labs, agent_type, project_id, peer_agent_id, active_status, deploy_status, x_desc,
        notebook_md, self_md, heartbeat_md, agents_md, identity_md, soul_md, tools_md, user_md,
        default_session_id, mcp_config, agent_email, agent_email_config,
        agent_telegram_token, agent_feishu_token
    ) SELECT network_id, %s, %s, description, agent_role, %s, base_model, system_prompt,
        agent_labs, agent_type, %s, %s, '1', 'pending', x_desc,
        NULL, NULL, NULL, %s, %s, %s, %s, %s,
        default_session_id, mcp_config, agent_email, agent_email_config,
        agent_telegram_token, agent_feishu_token
    FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1"""

    clone_agent_type = t.get("agent_type", "openclaw")

    pkid = execute_sql(
        sql,
        (
            # SELECT replacement values
            generated_id,  # agent_id -> new
            generated_name,  # agent_name -> new
            host_id,  # host_id -> target host
            project_id,  # project_id -> target project
            t.get("agent_id", ""),  # peer_agent_id -> the template's agent_id
            # * 2026-09-28: the 5 rendered content fields (order matches the %s placeholders in SELECT)
            r_agents_md,
            r_identity_md,
            r_soul_md,
            r_tools_md,
            r_user_md,
            template_agent_id,  # WHERE agent_id = template
        ),
        commit=True,
    )

    # fix default_session_id (the template has agent:old_id:main -> change it to the new id)
    execute_sql(
        "UPDATE agx_agent_registry SET default_session_id=%s WHERE agent_id=%s",
        (f"agent:{generated_id}:main", generated_id),
        commit=True,
    )

    # fully copy every task from the template database (including is_private=0/1), deduplicated automatically
    # the database is dynamic -- the template may gain tasks at any time after initialization, so a static struct json cannot be relied on
    _clone_all_tasks_from_template(template_agent_id, generated_id, network_id)

    _sync_entity(
        "agent",
        generated_id,
        {
            "pkid": pkid,
            "agent_id": generated_id,
            "agent_name": generated_name,
            "description": t.get("description", ""),
            "agent_role": t.get("agent_role", "worker"),
            "host_id": host_id or t.get("host_id", ""),
            "base_model": t.get("base_model", ""),
            "agent_labs": t.get("agent_labs", ""),
            "agent_type": clone_agent_type,
            "project_id": project_id,
            "peer_agent_id": t.get("agent_id", ""),
            "deploy_status": "pending",
            "active_status": "1",
            "default_session_id": f"agent:{generated_id}:main",
        },
    )

    return (generated_id, "")


@mcp.tool()
@require_level(4)
def agx_clone_agent(
    caller_agent_id: str,
    host_id: str,
    template_agent_id: str,
    target_project_id: str,
    network_id: str = "",
    agent_name: str = "",
    new_agent_id: str = "",
) -> str:
    """
    [Agent clone] Clone a brand-new agent instance from a template agent.

    After cloning, deploy_status = 'pending'; the caller must dispatch agent_deploy to
    host_manager to materialize the filesystem.

    Args:
        caller_agent_id: caller agent ID (for permission checks)
        host_id: target host ID (required. An agent must have an owning host, it cannot be empty)
        template_agent_id: agent_id of the template agent (e.g. "agx_core" or "memofam_admin")
        target_project_id: target project ID (the project the cloned agent belongs to)
        network_id: owning network ID (optional; derived from the template when omitted)
        agent_name: custom display name (optional, defaults to new_agent_id)
        new_agent_id: custom new agent ID (optional; auto-generates {template}_{random_hex} when omitted)
            Naming rule: only [a-z0-9_]; no uppercase/spaces/CJK/special characters (used as Redis key/path/CLI).
            Duplicates are explicitly rejected with an error message.

    Returns:
        str: success message including the new agent's agent_id.
    """
    # read the template agent to get its network_id
    template = execute_sql(
        "SELECT * FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
        (template_agent_id,),
    )
    if not template:
        return f"Error: Template agent '{template_agent_id}' not found or inactive"

    t = template[0]
    derived_network = network_id or t.get("network_id", "")
    # host_id is required, it cannot be empty
    if not host_id:
        return "Error: host_id 是必填参数,Agent 必须归属于一台主机,不能为空"

    new_aid, err = _clone_agent_from_template(
        template_agent_id, target_project_id, derived_network, host_id, agent_name, new_agent_id
    )
    if err:
        return f"Error: {err}"

    # -- project admin auto-fill: the clone is a project_manager and the target project has no admin -> make it the admin --
    # case: when a project is created first and members are cloned one by one later (not via bind_group_to_project), the project may stay without an admin.
    # after cloning the PM it is filled in automatically, so the project admin keeps management abilities such as seeing members' private tasks.
    try:
        clone_role = execute_sql(
            "SELECT agent_role FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
            (new_aid,),
        )
        log(f"[admin-auto] clone={new_aid} role_rows={clone_role}", "INFO")
        if clone_role and clone_role[0].get("agent_role") == "project_manager":
            proj_admin = execute_sql(
                "SELECT admin_agent_id FROM agx_project WHERE project_id = %s",
                (target_project_id,),
            )
            log(f"[admin-auto] pm={new_aid} proj={target_project_id} admin_rows={proj_admin}", "INFO")
            if proj_admin and not proj_admin[0].get("admin_agent_id"):
                execute_sql(
                    "UPDATE agx_project SET admin_agent_id = %s WHERE project_id = %s",
                    (new_aid, target_project_id),
                    commit=True,
                )
                log(f"[admin-auto] UPDATE done for {target_project_id} -> {new_aid}", "INFO")
                try:
                    _sync_entity("project", target_project_id, {"admin_agent_id": new_aid, "network_id": "lan_001"})
                except Exception as se:
                    log(f"agx_clone_agent _sync_entity failed: {se}", "WARN")
                try:
                    _publish_event("project", "admin_set", target_project_id)
                except Exception as pe:
                    log(f"agx_clone_agent _publish_event failed: {pe}", "WARN")
    except Exception as exc:
        log(f"agx_clone_agent auto-set project admin failed: {exc}\n{traceback.format_exc()}", "WARN")

    return json.dumps(
        {
            "status": "cloned",
            "agent_id": new_aid,
            "template_agent_id": template_agent_id,
            "project_id": target_project_id,
            "network_id": derived_network,
            "host_id": host_id,
            "deploy_status": "pending",
            "next_step": "dispatch agent_deploy to host_manager on " + host_id,
        },
        ensure_ascii=False,
    )


@mcp.tool()
@require_level(5)
def agx_bind_group_to_project(
    caller_agent_id: str, project_id: str, group_id: str, host_id: str, network_id: str = ""
) -> str:
    """
    [Bulk clone a team into a project] Clone every member template of a team into new agent instances and bind them to the target project.

    This tool will:
    1. Query the members with group_id={group_id} in agx_agent_group_member
    2. Clone a new agent instance from each member template
    3. The new agent's agent_id = {template name}_{random suffix} (e.g. agx_core_a3f2b1)
    4. Old and new agents do not affect each other -- one group can be bound to N projects

    Cloned agents have deploy_status='pending'; host_id is already written at creation, so agent_deploy does not need to assign a host again.

    Args:

        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        project_id (str): project ID.
        group_id (str): team ID.
        host_id (str): target host ID (required; which host the new agents bind to).
        network_id (str): owning network ID (optional).

    Returns:

        str: clone summary including the new agent ID list.

    """
    try:
        # -- validate host_id: it cannot be empty and must exist in agx_host_inventory --
        if not host_id or not host_id.strip():
            return json.dumps({"status": "failed", "error": "host_id 不能为空"}, ensure_ascii=False)
        host_check = execute_sql(
            "SELECT COUNT(*) AS cnt FROM agx_host_inventory WHERE host_id = %s AND active_status = 1",
            (host_id,),
        )
        if not host_check or host_check[0].get("cnt", 0) == 0:
            return json.dumps(
                {"status": "failed", "error": f"host_id '{host_id}' 在 agx_host_inventory 中不存在或已停用"},
                ensure_ascii=False,
            )

        if not network_id:
            proj = execute_sql(
                "SELECT network_id FROM agx_project WHERE project_id = %s",
                (project_id,),
            )

            if proj:
                network_id = proj[0]["network_id"]

        members = execute_sql(
            "SELECT agent_id FROM agx_agent_group_member WHERE group_id = %s AND active_status = 1",
            (group_id,),
        )

        if not members:
            return f"Error: no active members found in group {group_id}"

        success = 0

        errors = 0

        new_ids = []

        for m in members:
            try:
                member_agent_id = m["agent_id"]

                new_id, err = _clone_agent_from_template(member_agent_id, project_id, network_id, host_id)

                if new_id:
                    success += 1

                    new_ids.append(new_id)

                    # inject this group's host-conditional tasks (owner_group not empty) into the clone
                    # the clone logic skipped those tasks; they are injected here along with the group binding
                    _inject_group_tasks_for_agent(group_id, new_id, network_id)

                else:
                    errors += 1

            except Exception as exc:
                log(f"agx_bind_group_to_project clone member failed: {exc}", "WARN")
                errors += 1

        result = f"Bound group {group_id} to project {project_id}: cloned {success} agents"

        if new_ids:
            result += "\nNew agent IDs:\n" + "\n".join(f"  - {aid}" for aid in new_ids)

        if errors:
            result += f"\n({errors} failed)"

        # -- auto-assign the project admin: find the clone corresponding to the role='project_manager' member template --
        current_admin = execute_sql(
            "SELECT admin_agent_id FROM agx_project WHERE project_id = %s",
            (project_id,),
        )
        if current_admin and not current_admin[0].get("admin_agent_id"):
            for m in members:
                member_agent_id = m["agent_id"]
                member_role = execute_sql(
                    "SELECT agent_role FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
                    (member_agent_id,),
                )
                if member_role and member_role[0].get("agent_role") == "project_manager":
                    # find the clone agent for that template (agent_id starts with the template name)
                    admin_candidate = None
                    for aid in new_ids:
                        if aid.startswith(member_agent_id + "_"):
                            admin_candidate = aid
                            break
                    if admin_candidate:
                        execute_sql(
                            "UPDATE agx_project SET admin_agent_id = %s WHERE project_id = %s",
                            (admin_candidate, project_id),
                            commit=True,
                        )
                        _sync_entity(
                            "project", project_id, {"admin_agent_id": admin_candidate, "network_id": network_id}
                        )
                        result += f"\n  Project admin set to: {admin_candidate}"
                    break

        return result

    except Exception as e:
        return _log_exception("agx_bind_group_to_project", e)


@mcp.tool()
@require_level(5)
def agx_unbind_agent_from_project(caller_agent_id: str, project_id: str, agent_id: str) -> str:
    """
    [Unbind agent from project] Reset the agent's project_id to system_project.

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        project_id (str): project ID.
        agent_id (str): agent ID.

    Returns:

        str: success message.

    """
    try:
        execute_sql(
            "UPDATE agx_agent_registry SET project_id = 'system_project' WHERE agent_id = %s",
            (agent_id,),
            commit=True,
        )

        _sync_entity(
            "agent",
            agent_id,
            {
                "project_id": "system_project",
            },
        )

        _publish_event(
            "agent",
            "unbound",
            agent_id,
            {
                "project_id": "system_project",
                "agent_id": agent_id,
            },
        )

        return f"Agent {agent_id} unbound from project {project_id}, reset to system_project"

    except Exception as e:
        return _log_exception("agx_unbind_agent_from_project", e)


@mcp.tool()
@require_level(5)
def agx_update_project_agent(caller_agent_id: str, project_id: str, agent_id: str, updates: dict) -> str:
    """
    [Update project-agent relation] Update the project-level attributes of an agent.

    project_id now lives in the agx_agent_registry table.

    Supported field: active_status (only the agent's active state).

    Args:

        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        project_id (str): project ID.
        agent_id (str): agent ID.
        updates (dict): the field dictionary to update.
            Updatable field: active_status.
            Example: {"active_status": "0"}

    Returns:

        str: update result message.

    """
    try:
        updateable = _UPDATEABLE_FIELDS.get("agent", [])

        filtered = {k: v for k, v in updates.items() if k in updateable}

        if not filtered:
            return "Error: no valid fields to update for agent"

        if "active_status" not in filtered:
            return "Error: only active_status is supported via this tool; use agx_update_agent for other fields"

        sql = "UPDATE agx_agent_registry SET active_status = %s WHERE agent_id = %s"

        execute_sql(sql, (filtered["active_status"], agent_id), commit=True)

        try:
            _sync_entity("agent", agent_id, {"active_status": filtered["active_status"]})

        except Exception as exc:
            log(f"agx_update_project_agent _sync_entity failed for {agent_id}: {exc}", "WARN")

        # refresh the worker_queues cache (an active_status change affects the daemon's polling list)
        try:
            host_row = execute_sql(
                "SELECT host_id FROM agx_agent_registry WHERE agent_id = %s", (agent_id,), commit=False
            )
            if host_row:
                _invalidate_worker_queues_cache(host_row[0]["host_id"])
        except Exception as exc:
            log(f"agx_update_project_agent _invalidate_worker_queues_cache failed for {agent_id}: {exc}", "WARN")

        try:
            _publish_event("agent", "updated", agent_id, {"fields": list(filtered.keys())})

        except Exception as exc:
            log(f"agx_update_project_agent _publish_event failed for {agent_id}: {exc}", "WARN")

        return f"Agent {agent_id} updated: [{', '.join(filtered.keys())}]"

    except Exception as e:
        return _log_exception("agx_update_project_agent", e)


@mcp.tool()
@require_level(0)
def agx_list_project_agents(caller_agent_id: str, project_id: str) -> str:
    """
    [List agents in a project] List all active agents bound to the given project.

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity and enables an audit trail. Read-only tools are no exception -- every call chain must be traceable.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).

        project_id (str): project ID.

    Returns:

        str: agent list as a JSON string.

    """
    try:
        rows = execute_sql(
            """
            SELECT agent_id, agent_name, agent_role, base_model

            FROM agx_agent_registry

            WHERE project_id = %s AND active_status = 1

        """,
            (project_id,),
        )

        # look up the teams each agent belongs to

        if rows:
            for row in rows:
                teams = execute_sql(
                    """
                    SELECT ag.group_id, ag.group_name
                    FROM agx_agent_group_member gm
                    LEFT JOIN agx_agent_group ag ON gm.group_id = ag.group_id
                    WHERE gm.agent_id = %s AND gm.active_status = 1 AND ag.active_status = 1
                    """,
                    (row["agent_id"],),
                )

                row["teams"] = teams if teams else []

        return json.dumps(rows, ensure_ascii=False, default=str) if rows else "[]"

    except Exception as e:
        return _log_exception("agx_list_project_agents", e)


def _render_quick_cmd(cmd_id: str, params: str = "{}") -> dict:
    """[Internal, no decorator] The authoritative implementation of quick-command string rendering (reused by agx_quick_cmd_render / agx_quick_cmd_send).

    Renders "cmd_id + parameter values" against agx_quick_cmd.template into the final instruction text, plus metadata.
    * Single implementation: this function is the only source of truth for rendering; both MCP methods call it
      instead of keeping their own copy.

    Returns:
        dict: success = {"ok": True, "instruction":..., "cmd_id":..., "cmd_name":...,
                         "is_public":..., "target_agent_id":..., "task_labs":..., "run_session_id":...}
              failure = {"ok": False, "error": "CMD_NOT_FOUND|PARAM_INVALID|PARAM_MISSING", ...}
    """
    # 1. read the table
    rows = execute_sql(
        """
        SELECT cmd_id, cmd_name, is_public, target_agent_id, task_labs, run_session_id, template, params_schema
        FROM agx_quick_cmd
        WHERE cmd_id = %s AND active_status = 1
        LIMIT 1
        """,
        (cmd_id,),
    )
    if not rows:
        return {"ok": False, "error": "CMD_NOT_FOUND", "cmd_id": cmd_id}
    row = rows[0]

    # 2. parse params
    try:
        params_dict = json.loads(params) if params else {}
        if not isinstance(params_dict, dict):
            return {"ok": False, "error": "PARAM_INVALID", "detail": "params must be a JSON object"}
    except Exception:
        return {"ok": False, "error": "PARAM_INVALID", "detail": "params is not valid JSON"}

    # 3. parse params_schema + required check
    schema = []
    raw_schema = row.get("params_schema")
    if raw_schema:
        try:
            schema = json.loads(raw_schema) if isinstance(raw_schema, str) else raw_schema
        except Exception:
            schema = []
    if not isinstance(schema, list):
        schema = []
    schema_by_name = {}
    missing = []
    for item in schema:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        schema_by_name[name] = item
        if item.get("required"):
            val = params_dict.get(name, item.get("default"))
            if val is None or (isinstance(val, str) and val.strip() == ""):
                missing.append(name)
    if missing:
        return {"ok": False, "error": "PARAM_MISSING", "missing": missing, "cmd_id": cmd_id}

    # 3.5 enum domain check (2026-10-14): for a type=enum parameter, a non-empty value must be inside the options' value set,
    #     otherwise an illegal value is rendered into the message verbatim (silent pollution). Empty/omitted values are not checked (left to the default / empty-string rule).
    invalid_enum = []
    for name, item in schema_by_name.items():
        if item.get("type") != "enum":
            continue
        val = params_dict.get(name)
        if val is None or (isinstance(val, str) and val.strip() == ""):
            continue
        opts = item.get("options") or []
        allowed = set()
        for o in opts:
            if isinstance(o, dict) and "value" in o:
                allowed.add(str(o["value"]))
            else:
                allowed.add(str(o))
        if allowed and str(val) not in allowed:
            invalid_enum.append({"name": name, "value": val, "allowed": sorted(allowed)})
    if invalid_enum:
        return {"ok": False, "error": "PARAM_INVALID", "detail": "enum value not in allowed options", "invalid": invalid_enum, "cmd_id": cmd_id}

    # 4. authoritative rendering
    template = row.get("template") or ""

    def _sub(match):
        name = match.group(1)
        if name in params_dict:
            val = params_dict[name]
        elif name in schema_by_name:
            val = schema_by_name[name].get("default", "")
        else:
            # slots not present in the schema are kept as-is
            return match.group(0)
        if isinstance(val, bool):
            return "true" if val else "false"
        # * date fallback (2026-10-14): type=date with an empty value -> use today's YYYY-MM-DD
        # rationale: a date parameter's default is a static string and cannot express "today"; an empty value means today
        # (consistent with agx_daily_log defaulting to today), so an empty slot is never rendered.
        if val is None or (isinstance(val, str) and val.strip() == ""):
            _item = schema_by_name.get(name) or {}
            if _item.get("type") == "date":
                return datetime.now().strftime("%Y-%m-%d")
        if val is None:
            return ""
        return str(val)

    instruction = re.sub(r"\{([a-zA-Z0-9_]+)\}", _sub, template)

    # 4.5 completed parameters (2026-10-14): for each schema parameter item take params_dict[name] -> item.default -> empty string as the final value,
    #     consistent with the _sub resolution rule during rendering (including the date fallback value). Used by agx_quick_cmd_send for the persisted record.
    params_resolved = {}
    for _name, _item in schema_by_name.items():
        if _name in params_dict:
            _val = params_dict[_name]
        else:
            _val = _item.get("default", "")
        # date fallback: empty value and type=date -> today's YYYY-MM-DD (consistent with _sub)
        if _val is None or (isinstance(_val, str) and _val.strip() == ""):
            if _item.get("type") == "date":
                _val = datetime.now().strftime("%Y-%m-%d")
        # bool normalized to the "true"/"false" string (consistent with _sub, so the persisted value matches the sent message)
        if isinstance(_val, bool):
            _val = "true" if _val else "false"
        params_resolved[_name] = "" if _val is None else _val

    # 5. return
    return {
        "ok": True,
        "instruction": instruction,
        "cmd_id": row.get("cmd_id"),
        "cmd_name": row.get("cmd_name"),
        "is_public": int(row.get("is_public") or 0),
        "target_agent_id": row.get("target_agent_id"),
        "task_labs": row.get("task_labs"),
        "run_session_id": row.get("run_session_id"),
        "params_resolved": params_resolved,
    }


@mcp.tool()
@require_level(0)
def agx_quick_cmd_render(caller_agent_id: str, cmd_id: str, params: str = "{}") -> str:
    """
    [Quick command rendering] Render "cmd_id + parameter values" against agx_quick_cmd.template into the final instruction text.

    This method is the [single authoritative rendering source] for quick commands (L0 read-only: read the table and render; no writes, no dispatch).
    Contract (finalized by the PM):
      - template column = plain-text instruction template (with {placeholders}), not JSON.
      - business fields live in their own columns: cmd_name/is_public/target_agent_id/task_labs/run_session_id/params_schema.
      - parameter structure = the params_schema column (JSON list).

    Rendering rules:
      - substitute the template using the regex \\{([a-zA-Z0-9_]+)\\};
      - value priority for each slot = params[name] passed in -> that parameter's default in schema -> empty string;
      - bools become "true"/"false"; slots not present in the schema are left untouched.

    MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity and enables an audit trail. Read-only tools are no exception -- every call chain must be traceable.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).

        cmd_id (str): quick command logical ID (required).

        params (str): parameter JSON string (name -> value), default "{}".

    Returns:

        str: a JSON string {"instruction":..., "cmd_id":..., "cmd_name":...,
             "target_agent_id":..., "task_labs":..., "is_public":..., "run_session_id":...},
             or an error {"error":"CMD_NOT_FOUND|PARAM_INVALID|PARAM_MISSING", ...}.
             PARAM_INVALID covers: params not JSON / invalid JSON / not an object / enum value outside options.

    """
    try:
        res = _render_quick_cmd(cmd_id, params)
        res.pop("ok", None)
        return json.dumps(res, ensure_ascii=False, default=str)

    except Exception as e:
        return _log_exception("agx_quick_cmd_render", e)


@mcp.tool()
@require_level(5)
def agx_quick_cmd_send(
    caller_agent_id: str,
    cmd_id: str,
    params: str = "{}",
    self_session_id: str = "",
    target_session_key: str = "",
    timeout_seconds: int = None,
    exec_name: str = "",
    needs_reply: int = None,
    force_no_reply: bool = False,
    instruction_suffix: str = "",
    client_request_id: str = "",
) -> str:
    """
    [Quick command send] One step: look up -> render -> dispatch, composing one execution and delivering it.

    This method collapses the BFF's original three calls (lookup -> render -> dispatch) into [one kernel method].
    Rendering reuses the single implementation in agx_quick_cmd_render (_render_quick_cmd), it is not copied.
    It also records "which quick command dispatched this task" in agx_task_execution.quick_cmd_id.

    Flow:
      1) Read agx_quick_cmd (cmd_id + active_status=1) -> is_public / target_agent_id / task_labs;
           * is_public=0 (private): send to target_agent_id;
             is_public=1 (public): not bound to an agent, target = caller_agent_id itself;
      2) Reuse _render_quick_cmd to validate and render (CMD_NOT_FOUND / PARAM_MISSING / PARAM_INVALID);
      3) Look up agx_task_base by (agent_id=target, task_labs) to get the real task_id;
           * in public mode, "the caller has no such task_labs" returns TASK_NOT_FOUND outright -- permission narrows naturally.
      4) Call _ao_dispatch_task(..., quick_cmd_id=cmd_id) to dispatch; self_session_id is passed through as-is (receipt routing).

    Permission (L5 baseline + a high-risk fence in the method body):
      - tool level @require_level(5) = the same "regular dispatch permission" as agx_dispatch_task;
      - extra fence in the method body: high-risk commands (pointing at the system owners agx_agentfather / agx_projectmother)
        may only be sent by [the responsible party itself + global roles (workshop_manager)]; other L5 roles (e.g. a PM) cannot send on their behalf.

    MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity and enables an audit trail.

    Args:
        caller_agent_id (str): caller agent ID (required).
        cmd_id (str): quick command logical ID (required).
        params (str): parameter JSON string (name -> value), default "{}".
        self_session_id (str): [sender session] used to route the receipt; defaults to agent:{caller}:main.
        target_session_key (str): * added 2026-10-17. [Delivery target session] -- which session of the
            target agent runs this execution. Empty keeps the existing auto-derivation (oneshot / dedicated_session),
            identical to the behavior before this change (backward compatible).
            * Orthogonal to self_session_id, do not mix them up:
               self_session_id    = which [sender] session it came from (where the receipt returns)
               target_session_key = which [receiver] session it runs in (where the work lives)
            When passed it must pass the character-safety check and must belong to the [target agent] (agent:{target_agent_id}: prefix).
        timeout_seconds (int): watchdog timeout (seconds), default when omitted.
        exec_name (str): short execution title (Chinese, optional).
        needs_reply (int): * added 2026-10-17. [Receipt requirement] 0-not needed 1-needed.
            Omitted (None) -> keep the existing derivation (from the target task's reply_mode: result/confirm -> 1).
            Explicit 0/1 -> the caller decides (same semantics as agx_dispatch_task).
        force_no_reply (bool): explicitly disable the receipt, highest priority (int default 0 cannot distinguish
            "explicitly passed 0" from "not passed", hence the boolean decoupling -- same as agx_dispatch_task).
        instruction_suffix (str): * added 2026-10-18. Supplementary text appended to the end of the instruction after
            rendering (typical use: the "reference skill" pointer block selected in the unified input bar, rendered by the BFF).
            Append only, it does not participate in template validation; empty string = nothing appended (same as before).
        client_request_id (str): * added 2026-11-19. Idempotency key. Reuse the same value when retrying the same
            intent -> hits the original execution and returns it directly, no duplicate dispatch, and **no duplicate
            usage counting**. Semantics identical to agx_dispatch_task / agx_universal_send (passed through to
            _ao_dispatch_task).
            * Why it had to be added: the quickcmd branch of agx_universal_send has long read
              `_res.get("idempotent")` and written it into its return value, but this method neither accepted nor
              forwarded client_request_id -> that field was always False, so the "network retries produce no
              duplicate side effects" promise in the universal_send docs **did not actually hold** for quickcmd.

    Returns:
        str: JSON {"run_uuid":..., "msg_id":..., "cmd_id":..., "target_agent_id":...,
             "task_labs":..., "is_public":..., "session_key":...} or {"error":...}.
             * session_key = the receiver session key actually persisted (returned by the kernel; use it directly,
             do not compute it yourself).
    """
    try:
        # -- 1) reuse the single rendering implementation --
        res = _render_quick_cmd(cmd_id, params)
        if not res.get("ok"):
            res.pop("ok", None)
            return json.dumps(res, ensure_ascii=False, default=str)

        instruction = res["instruction"]
        # -- 5.0) append the supplementary note (skill references etc.; nothing appended when empty, backward compatible) --
        if instruction_suffix:
            instruction = f"{instruction}\n\n{instruction_suffix}"
        target_agent_id = res.get("target_agent_id") or ""
        cmd_task_labs = res.get("task_labs") or ""
        is_public = int(res.get("is_public") or 0)

        # -- 1.5) public/private split (decided by Lao Feng on 2026-10-26) --
        #   private (is_public=0): send to target_agent_id (which must already be bound)
        #   public (is_public=1): not bound to an agent, target = the caller itself, and task_id is looked up by its own task_labs
        #   * public permission narrows naturally: if the caller has no such task_labs -> lookup fails -> it simply cannot be used.
        if is_public:
            if not caller_agent_id:
                return json.dumps(
                    {"error": "CMD_CALLER_MISSING", "cmd_id": cmd_id,
                     "detail": "public quick_cmd requires caller_agent_id"},
                    ensure_ascii=False,
                )
            target_agent_id = caller_agent_id
        elif not target_agent_id:
            return json.dumps(
                {"error": "CMD_TARGET_MISSING", "cmd_id": cmd_id,
                 "detail": "private quick_cmd requires target_agent_id"},
                ensure_ascii=False,
            )

        # -- 2) the old high-risk fence has been removed; the L5 baseline is enough --
        # Note: commands pointing at the system owners (agx_agentfather/agx_projectmother) used to be
        # blocked by the _HIGH_RISK_TARGETS fence, which allowed only the owner itself + workshop_manager to send them.
        # But the tasks for a PM to create projects/agents live only under the system owners, and a PM has no task of its own --
        # the fence blocked the PM's legitimate path. The L5 baseline (only PMs/system core roles may call this tool) is enough,
        # so the whole block was removed.

        # -- 3) resolve the real task_id from (target_agent_id, task_labs) --
        #   in public mode target_agent_id was already replaced by the caller above -> the same join works, no second path needed.
        task_rows = execute_sql(
            "SELECT task_id, task_labs, task_name, network_id, dedicated_session, reply_mode, task_type "
            "FROM agx_task_base WHERE agent_id = %s AND task_labs = %s AND active_status = 1 LIMIT 1",
            (target_agent_id, cmd_task_labs),
        )
        if not task_rows:
            return json.dumps(
                {"error": "TASK_NOT_FOUND", "cmd_id": cmd_id, "target_agent_id": target_agent_id,
                 "task_labs": cmd_task_labs, "is_public": is_public,
                 "detail": (f"public quick_cmd: caller '{target_agent_id}' has no task_labs='{cmd_task_labs}'"
                            if is_public else
                            f"no agx_task_base for agent='{target_agent_id}' task_labs='{cmd_task_labs}'")},
                ensure_ascii=False,
            )
        task_row = task_rows[0]
        task_id = task_row["task_id"]

        # -- 4) resolve host_id / project_id / network_id --
        agent_row = execute_sql(
            "SELECT host_id, deploy_status FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
            (target_agent_id,),
        )
        if not agent_row:
            return json.dumps(
                {"error": f"agent '{target_agent_id}' 不存在或已停用"}, ensure_ascii=False
            )
        if agent_row[0].get("deploy_status") != "deployed":
            return json.dumps(
                _deploy_gate_msg(target_agent_id, agent_row[0].get("deploy_status")),
                ensure_ascii=False,
            )
        host_id = agent_row[0].get("host_id", "") or ""
        if not host_id:
            return json.dumps({"error": f"cannot determine host_id for agent '{target_agent_id}'"}, ensure_ascii=False)
        network_id = task_row.get("network_id") or config.network_id
        project_id = _resolve_project_id_from_agent(target_agent_id)

        # -- 5) assemble the final instruction (same rule as agx_dispatch_task: universal may use the rendered text directly) --
        task_labs = (task_row.get("task_labs") or "").strip()

        # needs_reply resolution (2026-10-17: the caller may pass it explicitly; otherwise keep the template derivation)
        #   force_no_reply has the highest priority -> 0;
        #   needs_reply passed explicitly (anything but None) -> the caller decides;
        #   not passed -> derive from reply_mode (same rule as agx_dispatch_task: result/confirm -> 1).
        reply_mode_val = task_row.get("reply_mode", "result")
        if force_no_reply:
            needs_reply_val = 0
        elif needs_reply is not None:
            needs_reply_val = 1 if int(needs_reply) else 0
        else:
            needs_reply_val = 1 if reply_mode_val in ("result", "confirm") else 0

        # send_session_id: use the explicit value when passed (after the safety check), otherwise the main session
        send_session_id = ""
        if self_session_id:
            if _is_safe_session_key(self_session_id):
                send_session_id = self_session_id
            else:
                return json.dumps(
                    {"error": "invalid self_session_id: contains unsafe characters", "got": self_session_id},
                    ensure_ascii=False,
                )
        elif caller_agent_id:
            send_session_id = f"agent:{caller_agent_id}:main"

        # -- 5.5) target_session_key: the delivery target session (added 2026-10-17) --
        #   * orthogonal to send_session_id: that one is "from whose session it is sent" (receipt routing),
        #     this one is "which session it runs in" (task landing point). Three directions, three names, no collisions.
        #   empty -> keep the existing derivation entirely (oneshot / dedicated_session), identical to before the change.
        #   non-empty -> (1) character-safety check (same rule as dispatch) (2) ownership check (must belong to the target agent).
        target_session_key = (target_session_key or "").strip()
        if target_session_key:
            if not _is_safe_session_key(target_session_key):
                return json.dumps(
                    {"error": "invalid target_session_key: contains unsafe characters",
                     "got": target_session_key},
                    ensure_ascii=False,
                )
            if not (
                target_session_key == f"agent:{target_agent_id}:main"
                or target_session_key.startswith(f"agent:{target_agent_id}:")
            ):
                return json.dumps(
                    {"error": "SESSION_NOT_OWNED",
                     "detail": f"target_session_key 不属于目标 Agent {target_agent_id}",
                     "target_session_key": target_session_key,
                     "target_agent_id": target_agent_id},
                    ensure_ascii=False,
                )

        # -- 6) dispatch (the normal _ao_dispatch_task plus quick_cmd_id + quick_cmd_params) --
        quick_cmd_params = json.dumps(res.get("params_resolved") or {}, ensure_ascii=False)
        result = _ao_dispatch_task(
            caller_agent_id=caller_agent_id,
            instruction=instruction,
            agent_id=target_agent_id,
            task_id=task_id,
            network_id=network_id,
            project_id=project_id,
            host_id=host_id,
            timeout_seconds=timeout_seconds,
            session_key=target_session_key,
            send_session_id=send_session_id,
            needs_reply=needs_reply_val,
            exec_name=exec_name,
            quick_cmd_id=cmd_id,
            quick_cmd_params=quick_cmd_params,
            client_request_id=client_request_id,
        )
        if "error" in result:
            return json.dumps(result, ensure_ascii=False)

        # -- 6.5) usage statistics (* approved by Lao Feng on 2026-11-19) --
        #   use_count / last_used_at used to be read by the BFF and the kernel SELECTs but never written ->
        #   all 52 rows were 0/NULL (measured 2026-11-19). Fixing it in one kernel place covers both BFF paths:
        #     (1) routers/quick_cmd.py:run()            calls this method directly
        #     (2) the quickcmd branch of agx_universal_send   also calls this method internally (around :14567)
        #   so do not change the BFF -- two write sites would inevitably drift.
        #
        #   * count only a [genuinely new dispatch]: on an idempotent hit (result.idempotent=True)
        #     _ao_dispatch_task created no execution and delivered no message, so statistics must not be +1 again,
        #     otherwise fake data for "one retry = one use" would drown the real usage heat.
        #   * silent degradation on failure: statistics are an auxiliary signal and must never make the dispatch fail (same as agx_skill_get).
        #   * no Redis write: agx_quick_cmd is not in CACHE_PREFIX (a BFF-managed table, no cache copy).
        if not result.get("idempotent"):
            try:
                execute_sql(
                    "UPDATE agx_quick_cmd SET use_count = use_count + 1, last_used_at = NOW() "
                    "WHERE cmd_id = %s",
                    (cmd_id,),
                    commit=True,
                )
            except Exception as exc:
                log(f"agx_quick_cmd_send 统计更新失败 cmd={cmd_id}: {exc}", "WARN")

        return json.dumps(
            {
                "run_uuid": result.get("run_uuid"),
                "msg_id": result.get("msg_id"),
                "cmd_id": cmd_id,
                "target_agent_id": target_agent_id,
                "task_labs": cmd_task_labs,
                "is_public": is_public,
                # * 2026-11-19: expose the idempotent-hit flag. The quickcmd branch of agx_universal_send
                #   has long read `_res.get("idempotent")`, but this method never returned it -> always False.
                "idempotent": bool(result.get("idempotent")),
                # -- 2026-10-14 extended return (item 5 (2), plan A): extra fields needed once BFF run() collapsed to a single call --
                "title": res.get("cmd_name"),
                "queue": result.get("queue"),
                "queue_length": result.get("queue_length"),
                "task_ref": {"task_id": task_id, "task_name": task_row.get("task_name")},
                "instruction": instruction,
                # * 2026-10-17: the kernel returns [the receiver session key actually persisted].
                #   The BFF must not compute it itself (f"agent:{target}:main"), otherwise "shown != actual".
                "session_key": result.get("session_key") or "",
            },
            ensure_ascii=False,
        )

    except Exception as e:
        return _log_exception("agx_quick_cmd_send", e)


# Tools: agent onboarding -- dual write MySQL + Redis cache
# ============================================================


# -- shared task management (driven by agx_struct.json) --


def _load_struct_json() -> dict:
    """Load the agx_struct.json configuration."""
    import json

    struct_path = config.get("struct_path") or os.path.join(_project_root, "agx_struct.json")
    if not os.path.exists(struct_path):
        log(f"agx_struct.json not found at {struct_path}", "WARN")
        return {}
    with open(struct_path) as f:
        return json.load(f)


def _get_shared_task_defs(agent_platform: str = None) -> list:
    """Read all shared tasks from agx_struct.json that have is_private=0 and match the current platform.

    Filter rules:
    - is_private=0 (shared task)
    - platforms contains "all" or the current agent_platform
    - owner_agent is null -> available to every agent
    """
    if not agent_platform:
        agent_platform = config.get("agent_type", "openclaw")

    data = _load_struct_json()
    shared = []
    for task in data.get("tasks", []):
        if task.get("is_private", 1) != 0:
            continue
        task_platforms = task.get("platforms", [])
        if task_platforms and "all" not in task_platforms and agent_platform not in task_platforms:
            continue
        shared.append(task)
    return shared


def _clone_all_tasks_from_template(template_agent_id: str, new_agent_id: str, network_id: str) -> list:
    """Fully copy every task from the template agent's database records to the clone.

    Queries all tasks of the template straight from agx_task_base and INSERTs them one by one into the clone.
    Automatic deduplication: skip tasks that already exist by (agent_id, task_labs).

    !! Host-conditional tasks (owner_group not empty) are not inherited by cloning:
    - such tasks belong to a specific group and are only injected dynamically once the host agent joins that group
      (see _inject_group_tasks_for_agent / agx_add_agent_to_group).
    - cloning copies only host tasks with an empty owner_group, so the clone does not inherit proprietary tasks of
      unrelated groups.

    Why the database and not agx_struct.json:
    - the database is dynamic -- the template can gain tasks at any time after initialization
    - agx_struct.json is static -- it never changes after initialization
    - relying on the static file would make clone lose is_private=1 business tasks added to the template later

    Returns:
        the list of copied task_labs.
    """
    template_tasks = execute_sql(
        """SELECT task_name, task_labs, parent_task_id, x_desc, target_object, payload,
                  priority, reply_mode, active_status, is_private, dedicated_session, task_type, description,
                  session_done_wake, owner_group
           FROM agx_task_base
           WHERE agent_id = %s AND (owner_group IS NULL OR owner_group = '')""",
        (template_agent_id,),
    )
    if not template_tasks:
        return []

    created = []
    task_sql = """INSERT INTO agx_task_base
        (network_id, task_name, task_labs, parent_task_id, agent_id, task_id,
         description, x_desc, target_object, payload, priority, reply_mode, active_status,
         is_private, dedicated_session, task_type, session_done_wake, owner_group)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""

    for t in template_tasks:
        task_labs = t["task_labs"]

        # dedupe: skip existing ones by (agent_id, task_labs)
        existing = execute_sql(
            "SELECT 1 FROM agx_task_base WHERE agent_id=%s AND task_labs=%s LIMIT 1",
            (new_agent_id, task_labs),
        )
        if existing:
            continue

        task_id = agx_generate_uuid(prefix="task")

        # payload is a MySQL JSON column; pymysql may return a dict or a str
        payload_val = t.get("payload")
        if payload_val is not None and not isinstance(payload_val, str):
            payload_val = json.dumps(payload_val, ensure_ascii=False)

        execute_sql(
            task_sql,
            (
                network_id,
                t["task_name"],
                task_labs,
                t.get("parent_task_id"),
                new_agent_id,
                task_id,
                t.get("description", ""),
                t.get("x_desc", ""),
                t.get("target_object"),
                payload_val,
                t.get("priority", 1),
                t.get("reply_mode", "result"),
                t.get("active_status", 1),
                t.get("is_private", 0),
                t.get("dedicated_session", 0),
                t.get("task_type", "normal"),
                t.get("session_done_wake", 1),
                None,  # clone artifacts always have an empty owner_group (host-conditional tasks are not cloned)
            ),
            commit=True,
        )

        _sync_entity(
            "task",
            task_id,
            {
                "task_id": task_id,
                "network_id": network_id,
                "task_name": t["task_name"] or "",
                "task_labs": task_labs,
                "agent_id": new_agent_id,
                "owner_group": "",
                "x_desc": t.get("x_desc", ""),
                "description": t.get("description", ""),
                "is_private": str(t.get("is_private", 0)),
                "active_status": str(t.get("active_status", 1)),
                "dedicated_session": str(t.get("dedicated_session", 0)),
                "task_type": t.get("task_type", "normal") or "normal",
                "reply_mode": t.get("reply_mode", "result"),
                "priority": str(t.get("priority", 1)),
                "session_done_wake": str(t.get("session_done_wake", 1)),
            },
        )
        created.append(task_labs)

    return created


def _inject_group_tasks_for_agent(group_id: str, agent_id: str, network_id: str) -> list:
    """Inject the host-conditional tasks (owner_group not empty) of the groups an agent belongs to.

    Semantics: a task definition carries both agent_id (host) and owner_group (host condition).
    When an agent joins a group, every host-conditional task of that group is injected into that agent
    (agent_id=target, owner_group preserved), so the capability "arrives with the group binding".

    Injection source: any row in agx_task_base with owner_group=group_id is used as the template
    (host-conditional tasks are installed under the template host, e.g. super_pm).

    Automatic deduplication: skip what already exists by (agent_id, task_labs).

    Returns:
        the list of injected task_labs.
    """
    if not group_id or not agent_id:
        return []

    # host-conditional task source: records whose owner_group matches this group.
    # each host may have several rows with the same task_labs (multi-host case); the outer loop dedupes by (agent_id, task_labs);
    # if several sources exist for one task_labs, only the first is taken (stable ordering, no duplicate injection).
    sources = execute_sql(
        """SELECT task_name, task_labs, parent_task_id, x_desc, target_object, payload,
                  priority, reply_mode, active_status, is_private, dedicated_session, task_type,
                  description, session_done_wake, agent_id AS src_agent
           FROM agx_task_base
           WHERE owner_group = %s AND active_status = 1
           ORDER BY pkid ASC""",
        (group_id,),
    )
    if not sources:
        return []

    created = []
    seen_labs = set()
    for s in sources:
        task_labs = s["task_labs"]
        # with several hosts sharing a task_labs, inject only the first
        if task_labs in seen_labs:
            continue
        seen_labs.add(task_labs)
        # dedupe: skip existing ones by (agent_id, task_labs)
        existing = execute_sql(
            "SELECT 1 FROM agx_task_base WHERE agent_id=%s AND task_labs=%s LIMIT 1",
            (agent_id, task_labs),
        )
        if existing:
            continue

        task_id = agx_generate_uuid(prefix="task")
        payload_val = s.get("payload")
        if payload_val is not None and not isinstance(payload_val, str):
            payload_val = json.dumps(payload_val, ensure_ascii=False)

        execute_sql(
            """INSERT INTO agx_task_base
                (network_id, task_name, task_labs, parent_task_id, agent_id, owner_group, task_id,
                 description, x_desc, target_object, payload, priority, reply_mode, active_status,
                 is_private, dedicated_session, task_type, session_done_wake)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                network_id,
                s["task_name"],
                task_labs,
                s.get("parent_task_id"),
                agent_id,
                group_id,
                task_id,
                s.get("description", ""),
                s.get("x_desc", ""),
                s.get("target_object"),
                payload_val,
                s.get("priority", 1),
                s.get("reply_mode", "result"),
                s.get("active_status", 1),
                s.get("is_private", 0),
                s.get("dedicated_session", 0),
                s.get("task_type", "normal"),
                s.get("session_done_wake", 1),
            ),
            commit=True,
        )

        _sync_entity(
            "task",
            task_id,
            {
                "task_id": task_id,
                "network_id": network_id,
                "task_name": s["task_name"] or "",
                "task_labs": task_labs,
                "agent_id": agent_id,
                "owner_group": group_id,
                "x_desc": s.get("x_desc", ""),
                "description": s.get("description", ""),
                "is_private": str(s.get("is_private", 0)),
                "active_status": str(s.get("active_status", 1)),
                "dedicated_session": str(s.get("dedicated_session", 0)),
                "task_type": s.get("task_type", "normal") or "normal",
                "reply_mode": s.get("reply_mode", "result"),
                "priority": str(s.get("priority", 1)),
                "session_done_wake": str(s.get("session_done_wake", 1)),
            },
        )
        created.append(task_labs)

    return created


def _create_shared_tasks_for_agent(agent_id: str, network_id: str, agent_platform: str = None) -> list:
    """Create every is_private=0 shared task for a new agent (deduplicated).

    Reads task definitions from agx_struct.json and creates them into agx_task_base + Redis.
    Replaces the previously hard-coded general/watchdog_alert creation logic.

    Returns:
        the list of created task_labs.
    """
    task_defs = _get_shared_task_defs(agent_platform)
    created = []
    task_sql = "INSERT INTO agx_task_base (network_id, task_name, task_labs, agent_id, task_id, description, x_desc, reply_mode, dedicated_session, task_type) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"

    for task_def in task_defs:
        task_labs = task_def["task_labs"]
        reply_mode = task_def.get("reply_mode", "result")

        # dedupe: check whether it already exists
        existing = execute_sql(
            "SELECT 1 FROM agx_task_base WHERE agent_id=%s AND task_labs=%s AND active_status=1 LIMIT 1",
            (agent_id, task_labs),
        )
        if existing:
            continue

        task_id = agx_generate_uuid(prefix="task")
        task_name = task_def["task_name"]

        # prefer the .md file for the full task manual; fall back to the short description in struct json when absent
        # install.py does the same at line 1149: _load_md(md_path) or t_def.get("description", "")
        md_file = task_def.get("md_file", "")
        if md_file:
            md_path = os.path.join(_project_root, md_file)
            if os.path.isfile(md_path):
                with open(md_path, encoding="utf-8") as f:
                    x_desc = f.read().strip()
            else:
                x_desc = task_def.get("description", "")
        else:
            x_desc = task_def.get("description", "")

        execute_sql(
            task_sql,
            (
                network_id,
                task_name,
                task_labs,
                agent_id,
                task_id,
                task_def.get("description", ""),
                x_desc,
                reply_mode,
                1 if task_def.get("dedicated_session") else 0,
                task_def.get("task_type", "normal"),
            ),
            commit=True,
        )

        _sync_entity(
            "task",
            task_id,
            {
                "task_id": task_id,
                "network_id": network_id,
                "task_name": task_name,
                "task_labs": task_labs,
                "agent_id": agent_id,
                "x_desc": x_desc,
                "description": task_def.get("description", ""),
                "reply_mode": reply_mode,
                "priority": str(task_def.get("priority", 1)),
                "active_status": "1",
                "dedicated_session": "1" if task_def.get("dedicated_session") else "0",
                "task_type": task_def.get("task_type", "normal"),
            },
        )
        created.append(task_labs)

    return created


def _create_pm_inherited_tasks_for_agent(agent_id: str, network_id: str, agent_platform: str = None) -> list:
    """Have a new project_manager inherit the task-management templates of projectmother (active_status=0).

    Reads the tasks with active_status=0 from agx_projectmother's agx_task_base (old name as fallback)
    (plan_execute, plan_monitor, task_manage, etc.) and copies them to the target project_manager
    with is_private=0, active_status=0.
    """
    # * prefer the new name, fall back to the old one (during migration projectmother's tasks may still sit under the old agent_id)
    source_tasks = []
    for _src in _sys_names(SYS_PROJECTMOTHER):
        source_tasks = execute_sql(
            "SELECT task_name, task_labs, description, x_desc, dedicated_session, task_type FROM agx_task_base WHERE agent_id=%s AND active_status=0",
            (_src,),
        )
        if source_tasks:
            break
    if not source_tasks:
        return []

    created = []
    task_sql = "INSERT INTO agx_task_base (network_id, task_name, task_labs, agent_id, task_id, description, x_desc,  is_private, active_status, dedicated_session, task_type) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"

    for t in source_tasks:
        task_labs = t["task_labs"]

        # dedupe
        existing = execute_sql(
            "SELECT 1 FROM agx_task_base WHERE agent_id=%s AND task_labs=%s LIMIT 1",
            (agent_id, task_labs),
        )
        if existing:
            continue

        task_id = agx_generate_uuid(prefix="task")
        task_name = t["task_name"]
        x_desc = t.get("x_desc", "")
        dedicated = t.get("dedicated_session", 0)

        execute_sql(
            task_sql,
            (
                network_id,
                task_name,
                task_labs,
                agent_id,
                task_id,
                t.get("description", ""),
                x_desc,
                0,  # is_private=0
                1,  # active_status=1 (a PM can use it right away)
                dedicated,
                t.get("task_type", "normal") or "normal",
            ),
            commit=True,
        )

        _sync_entity(
            "task",
            task_id,
            {
                "task_id": task_id,
                "network_id": network_id,
                "task_name": task_name,
                "task_labs": task_labs,
                "agent_id": agent_id,
                "x_desc": x_desc,
                "description": t.get("description", ""),
                "active_status": "1",
                "is_private": "0",
                "dedicated_session": str(dedicated),
                "task_type": t.get("task_type", "normal") or "normal",
            },
        )
        created.append(task_labs)

    return created


def _create_domain_task_for_agent(
    agent_id: str, network_id: str, agent_labs: str, system_prompt: str = "", agent_name: str = ""
) -> dict:
    """Create a domain task for an agent.

    Uses agent_labs as task_labs and describes the agent's professional duty.
    Reads the template from tasks/<agent_labs>.md first; when absent, generates it from system_prompt.

    Design goal: keep every agent's work from flowing through the generic general task;
    instead each agent gets a domain task that reflects its professional capability.

    Returns:
        {"task_id": "task_xxx", "task_labs": "xxx"} or None (when skipped)
    """
    import os

    # skip when agent_labs is empty, or when it collides with a builtin shared task name
    if not agent_labs or not agent_labs.strip():
        return None

    BUILTIN_TASK_LABS = {
        "general",
        "watchdog_alert",
        "system_check",
        "host_info",
        "host_communication",
        "network_analysis",
        "project_create",
        "project_audit",
        "project_configure",
        "agent_clone",
        "agent_configure",
        "agent_deploy",
        "system_init",
        "sync_struct",
        "notice_summarize",
        SYS_CORE,
        SYS_HOST,
        SYS_AGENTFATHER,
        SYS_PROJECTMOTHER,
    }
    if agent_labs in BUILTIN_TASK_LABS:
        return None  # builtin tasks already have their labs, no domain task needed

    # dedupe
    existing = execute_sql(
        "SELECT 1 FROM agx_task_base WHERE agent_id=%s AND task_labs=%s AND active_status=1 LIMIT 1",
        (agent_id, agent_labs),
    )
    if existing:
        return None

    # -- build the task content --
    task_name = agent_name or agent_id
    x_desc = ""

    # prefer the template file
    tasks_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tasks")
    template_path = os.path.join(tasks_dir, f"{agent_labs}.md")
    if os.path.isfile(template_path):
        with open(template_path) as f:
            x_desc = f.read().strip()
    elif system_prompt:
        # no template -> generate from system_prompt
        x_desc = (
            f"# {task_name} - 领域任务\n\n"
            f"## 角色定位\n{system_prompt}\n\n"
            f"## 核心能力\n根据以上角色定义执行专业工作,具体指令由调度方通过 `instruction` 传入。\n\n"
            f"## 协作关系\n通过 `agx_dispatch_task` 与其他 Agent 协作,通过 `agx_write_notice` 记录工作成果。"
        )
    else:
        # neither template nor prompt -> minimal information
        x_desc = f"# {task_name} - 领域任务\n\nAgent 专属任务,对应 agent_labs=`{agent_labs}`。"

    # -- write DB + Redis --
    task_id = agx_generate_uuid(prefix="task")
    task_sql = "INSERT INTO agx_task_base (network_id, task_name, task_labs, agent_id, task_id, description, x_desc, is_private, dedicated_session, reply_mode, task_type) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"

    execute_sql(
        task_sql,
        (network_id, task_name, agent_labs, agent_id, task_id, "", x_desc, 0, 0, "result", "normal"),
        commit=True,
    )

    _sync_entity(
        "task",
        task_id,
        {
            "task_id": task_id,
            "network_id": network_id,
            "task_name": task_name,
            "task_labs": agent_labs,
            "agent_id": agent_id,
            "x_desc": x_desc,
            "description": "",
            "priority": "1",
            "active_status": "1",
            "is_private": "0",
            "dedicated_session": "0",
            "task_type": "normal",
            "reply_mode": "result",
        },
    )

    return {"task_id": task_id, "task_labs": agent_labs}


# -- agent onboarding / update ---------------------------------


@mcp.tool()
@require_level(4)
def agx_upsert_agent(
    caller_agent_id: str,
    network_id: str,
    agent_id: str,
    agent_name: str,
    role: str,
    host_id: str = None,
    model: str = "deepseek",
    prompt: str = "",
    agent_labs: str = "",
    agent_type: str = "openclaw",
    project_id: str = "system_project",
    peer_agent_id: str = None,
    description: str = "",
    x_desc: str = "处理发给当前Agent(代理)的通用系统消息和通知,未指定具体任务时使用此默认任务",
    notebook_md: str = None,
    self_md: str = None,
    agents_md: str = None,
    heartbeat_md: str = None,
    identity_md: str = None,
    soul_md: str = None,
    tools_md: str = None,
    user_md: str = None,
    agent_avatar: str = None,
) -> str:
    """
    [Agent onboarding] Register or update an agent's identity information.

    Logic:

    Upsert semantics: update when agent_id exists, create otherwise.
    Args:
        network_id (str): owning network ID.
        agent_id (str): unique agent identifier (required).
            Naming rule: only lowercase letters [a-z], digits [0-9] and underscore [_].
            No uppercase, spaces, CJK or special characters.
            Reason: agent_id is used as a Redis key, a filesystem path and the OpenClaw CLI --agent argument.
            e.g. "trend_scout".
        agent_name (str): display name (required), e.g. "trend and channel hunter"; multilingual is allowed.
        role (str): agent role. Enum:

            - 'workshop_manager': top-level dispatch.
            - 'host_manager': host management.
            - 'project_manager': project management.
            - 'agent_manager': agent lifecycle management (create, clone, configure, retire).
            - 'worker': concrete execution unit.

        host_id (str, optional): default physical host ID.
        model (str): base model name used by this agent, e.g. "deepseek-reasoner", "gpt-4o".
        prompt (str): core content of the agent's system prompt, kept for later audit.
        agent_labs (str): agent label, matching the folder name under agents/, e.g. "agx_core".
        agent_type (str): agent platform type, e.g. "openclaw" | "claude_code" | "custom".
            Determines the agent's runtime and shared task set (e.g. watchdog_alert exists only on the openclaw platform).
            Defaults to "openclaw".
        project_id (str): owning project ID; the default "system_project" means system level.
        peer_agent_id (str, optional): source agent ID (peer); NULL means a native agent.
        description (str, optional): short description (the public identity card shown to other agents,
            as opposed to the full semantic description in x_desc). e.g. "content generation expert who turns
            intelligence into technical articles". Read from the description field of agx_struct.json.
        x_desc (str): description of the agent's general task, used when creating the default task. May be empty; there is a default.
        notebook_md (str, optional): NOTEBOOK.md note index and rules.
        self_md (str, optional): private notes in notices/self.md.
        agents_md (str, optional): AGENTS.md content.
        heartbeat_md (str, optional): HEARTBEAT.md content.
        identity_md (str, optional): IDENTITY.md content.
        soul_md (str, optional): SOUL.md content.
        tools_md (str, optional): TOOLS.md content.
        user_md (str, optional): USER.md content.
        agent_avatar (str, optional): agent avatar (URL or local path, no binary stored).

    Role definitions (role):

    - 'workshop_manager': workshop supervisor, responsible for global dispatch.
    - 'host_manager': host management.
    - 'project_manager': project lead, responsible for the logic within a single project.
    - 'agent_manager': agent factory, responsible for creating, cloning, configuring and retiring agents.
    - 'worker': the laborer/executor, responsible for concrete code or metric computation.

    """
    try:
        # agent_id naming-convention validation (empty value + format)
        valid, err_msg = _validate_agent_id(agent_id)
        if not valid:
            return json.dumps({"error": err_msg})
        if not agent_name or not agent_name.strip():
            return json.dumps({"error": "agent_name 不能为空"})

        target_id = agent_id.strip()

        # decide whether this is a create or an update (does agent_id already exist)
        existing = execute_sql(
            "SELECT agent_id FROM agx_agent_registry WHERE agent_id = %s",
            (target_id,),
        )
        is_update = bool(existing)

        sql = """
        INSERT INTO agx_agent_registry (
            network_id, agent_id, agent_name, description, agent_role, host_id, base_model, system_prompt,
            agent_labs, agent_type, project_id, peer_agent_id, active_status,
            notebook_md, self_md, agents_md, heartbeat_md, identity_md, soul_md, tools_md, user_md,
            default_session_id, agent_avatar
        )

        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)

        ON DUPLICATE KEY UPDATE

            agent_name=VALUES(agent_name),
            description=VALUES(description),
            host_id=COALESCE(VALUES(host_id), host_id),
            base_model=VALUES(base_model), system_prompt=VALUES(system_prompt),
            agent_labs=VALUES(agent_labs), agent_type=VALUES(agent_type),
            project_id=VALUES(project_id),
            peer_agent_id=VALUES(peer_agent_id), active_status=1,
            notebook_md=VALUES(notebook_md), self_md=VALUES(self_md),
            agents_md=VALUES(agents_md), heartbeat_md=VALUES(heartbeat_md),
            identity_md=VALUES(identity_md), soul_md=VALUES(soul_md),
            tools_md=VALUES(tools_md), user_md=VALUES(user_md),
            default_session_id=VALUES(default_session_id),
            agent_avatar=COALESCE(VALUES(agent_avatar), agent_avatar)

        """
        pkid = execute_sql(
            sql,
            (
                network_id,
                target_id,
                agent_name,
                description or "",
                role,
                host_id,
                model,
                prompt,
                agent_labs or None,
                agent_type,
                project_id,
                peer_agent_id,
                notebook_md,
                self_md,
                agents_md,
                heartbeat_md,
                identity_md,
                soul_md,
                tools_md,
                user_md,
                f"agent:{target_id}:main",
                agent_avatar,
            ),
            commit=True,
        )

        # --- create shared tasks (read the is_private=0 tasks from agx_struct.json) ---

        if not is_update:
            _create_shared_tasks_for_agent(target_id, network_id, agent_type)
            # domain task: uses agent_labs as task_labs and describes the agent's professional duty
            _create_domain_task_for_agent(target_id, network_id, agent_labs or "", prompt, agent_name)
            # special case: the project_manager role also inherits the task-management templates (plan_execute etc.)
            if role == "project_manager":
                _create_pm_inherited_tasks_for_agent(target_id, network_id, agent_type)

        # --- sync to the Redis cache ---

        default_session_id = f"agent:{target_id}:main"

        _sync_entity(
            "agent",
            target_id,
            {
                "pkid": pkid if not is_update else 0,
                "agent_id": target_id,
                "network_id": network_id,
                "host_id": host_id or "",
                "agent_name": agent_name,
                "description": description or "",
                "agent_role": role,
                "base_model": model,
                "agent_labs": agent_labs or "",
                "project_id": project_id,
                "peer_agent_id": peer_agent_id or "",
                "agent_avatar": agent_avatar or "",
                "active_status": "1",
                "agent_root_dir": "",
                "agent_workspace_dir": "",
                "agents_md": agents_md or "",
                "heartbeat_md": heartbeat_md or "",
                "identity_md": identity_md or "",
                "soul_md": soul_md or "",
                "tools_md": tools_md or "",
                "user_md": user_md or "",
                "notebook_md": notebook_md or "",
                "self_md": self_md or "",
                "default_session_id": default_session_id,
            },
        )

        _publish_event("agent", "updated" if is_update else "created", target_id)

        # -- refresh the worker_queues cache so the daemon sees the new agent immediately --
        agent_host = host_id or ""
        if agent_host:
            _invalidate_worker_queues_cache(agent_host)

        return f"Agent processed: {agent_name} ID: {target_id}"

    except Exception as e:
        return _log_exception("agx_upsert_agent", e)


# ============================================================

# Tools: task definition -- dual write MySQL + Redis cache

# ============================================================


@mcp.tool()
@require_level(2)
def agx_create_task(
    caller_agent_id: str,
    agent_id: str,
    description: str,
    x_desc: str = "",
    payload: dict = None,
    task_name: str = "",
    task_labs: str = "",
    dedicated_session: int = 0,
    reply_mode: str = "result",
    task_type: str = "normal",
    is_private: int = 0,
) -> str:
    """
    [Task definition] Create a static task base record (Task Base).

    Why it matters:

    agx_task_base stores the capabilities / executable task templates an agent has.

    It only defines "what to do", not "who does it", and it is not bound to a project.

    Actual execution (agx_task_execution) is what binds to a project.

    Args:

        agent_id (str): associated agent ID.
            - Indicates which agent is responsible for executing this task.
        description (str): short task description (the public identity card shown to other agents).
            e.g. "dealer indicator L1 analysis task". Written to the description column.
        x_desc (str, optional): full task manual (Markdown), written to the x_desc column.
            Usually loaded automatically by install.py from a .md file. May be empty for tasks created at runtime.
        payload (dict, optional): structured parameter package. Must be a JSON object.
            Example: {"stock_list": ["sh600519"], "threshold": 0.05}.
            These parameters are the hard input when the agent executes.
        task_name (str, optional): task name, matching the file name under tasks/ (without .md).
        task_labs (str, optional): task label, matching the subdirectory name under tasks/.
        dedicated_session (int, optional): whether a dedicated session is needed. 1=yes, 0=no. Default 0.
        reply_mode (str, optional): reply mode. result=reply with the result when done, confirm=confirm before executing, silent=no reply. Default result.
        task_type (str, optional): task type. normal=ordinary task, plan=plan task (default timeout 3600s). Default normal.
        is_private (int, optional): whether the task is private. 1=visible only to the owning agent (and the project admin/global roles);
            0=visible to everyone (shared task). Default 0.
            Note: is_private only controls "query visibility", not "execution permission";
            if the task itself has a caller-role requirement (e.g. PM only), the task manual must check it itself.

    * Permission (added 2026-10-21, resource-level ownership check):
        A task may only be created for yourself, or for a member of a project you administer.
            Resource-owning agent  = agent_id (this parameter)
            Resource-owning project = that agent's agx_agent_registry.project_id
        Allowed when any of:
            (a) caller_agent_id == agent_id               -- creating for yourself
            (b) caller_agent_id == that project's admin_agent_id -- a PM creating for a member of its project
            (c) caller is a global role (host_manager / workshop_manager)
        Everything else is rejected. **Creating a task across projects is not allowed.**

    Returns:

        str: task creation success message and task_id.

    """
    try:
        agent = execute_sql(
            "SELECT network_id, project_id FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
            (agent_id,),
        )

        if not agent:
            return f"Error: agent_id '{agent_id}' not found or inactive"

        network_id = agent[0]["network_id"]

        # -- resource-level ownership check: a task belongs to the target agent --
        # * added 2026-10-21. Previously agx_create_task had no ownership check at all,
        #   so even a worker (L2) could create a task for any agent of any project (measured: 3/3 all passed).
        #   Its siblings agx_update_task / agx_archive_task / agx_delete_task all had one,
        #   only the "create" step was missing -- create unchecked, update checked, exactly backwards.
        #
        # * [the target agent's project] must be passed as resource_project_id:
        #   branch (b) of _check_resource_ownership tests
        #   "caller == project.admin_agent_id" and does not look at the caller's own project_id.
        #   Not passing it -> a PM could not even create tasks for members of its own project (collateral damage).
        _deny = _check_resource_ownership(
            caller_agent_id,
            agent_id,                          # resource-owning agent
            agent[0].get("project_id") or "",  # the project this agent belongs to
        )
        if _deny:
            return (
                f"Error: {_deny} "
                f"(agx_create_task 只能为自己、或自己管理项目内的成员创建 task)"
            )

        # --- duplicate check (2026-09-14): reject when a same-named task_labs already exists for this agent, so no dirty rows are added silently ---
        if task_labs:
            dup = execute_sql(
                "SELECT task_id FROM agx_task_base WHERE agent_id = %s AND task_labs = %s AND active_status = 1",
                (agent_id, task_labs),
            )
            if dup:
                return (
                    f"Error: task already exists for agent '{agent_id}' with task_labs='{task_labs}' "
                    f"(task_id={dup[0]['task_id']}). 请改用 agx_update_task 修改内容,或先删除/归档原 task。"
                )

        task_id = agx_generate_uuid(prefix="task")

        sql = "INSERT INTO agx_task_base (network_id, task_name, task_labs, agent_id, task_id, description, x_desc, payload, dedicated_session, reply_mode, task_type, is_private) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"

        pkid = execute_sql(
            sql,
            (
                network_id,
                task_name or None,
                task_labs or None,
                agent_id,
                task_id,
                description or None,
                x_desc or None,
                json.dumps(payload),
                dedicated_session,
                reply_mode,
                task_type,
                is_private,
            ),
            commit=True,
        )

        # --- sync to the Redis cache ---

        _sync_entity(
            "task",
            task_id,
            {
                "pkid": pkid,
                "task_id": task_id,
                "network_id": network_id,
                "task_name": task_name or None,
                "task_labs": task_labs or None,
                "agent_id": agent_id,
                "x_desc": x_desc or "",
                "description": description or "",
                "priority": "1",
                "active_status": "1",
                "dedicated_session": str(dedicated_session),
                "task_type": task_type,
                "reply_mode": reply_mode,
                "is_private": str(is_private),
            },
        )

        _publish_event("task", "created", task_id)

        return f"Task base created: {task_id}"

    except Exception as e:
        return _log_exception("agx_create_task", e)


# ============================================================

# Tools: task ignition -- create the execution instance + update state, dual-writing the cache

# ============================================================


def _build_exec_instruction(
    instruction: str,
    task_id: str,
    needs_subagent: bool,
    agent_type: str = "",
    extra_instruction: str = "",
) -> str:
    """Build the final exec_instruction by merging instruction with the task's x_desc.

    Sub-agent instructions only apply to non-openclaw platforms. openclaw goes through the Gateway
    Session and does not need the tmux sub-agent creation flow.

    extra_instruction: supplementary body text passed in through the exec_instruction parameter.
        For non-universal tasks it used to be silently dropped (2026-10-24 incident: two handoffs both
        came out empty and the receiver only saw the title). It is now merged in as an [supplementary
        instruction] section, placed right after [this instruction] and before [task manual] -- keeping
        both the template manual and the caller's body text.
        Defaults to an empty string, zero behavior change for existing callers.
    """
    # read the task's x_desc

    task_row = execute_sql(
        "SELECT x_desc FROM agx_task_base WHERE task_id = %s AND active_status = 1",
        (task_id,),
    )

    task_desc = ""

    if task_row and task_row[0].get("x_desc"):
        task_desc = task_row[0]["x_desc"]

    # build the sub-agent section (non-openclaw platforms only)

    subagent_prompt = ""
    at = (agent_type or "").lower()

    if needs_subagent and at not in ("openclaw", ""):
        subagent_prompt = (
            "【 子代理要求 - 必须遵守,不得自行处理】\n"
            "你必须创建子代理来执行本任务。禁止在当前会话中直接执行--你的唯一职责是\n"
            "解析 task_run_uuid、启动子代理、绑定 session_id,然后立即结束。\n"
            "具体流程请严格参考 docs/internal/ws-direct-execution.md。\n"
            "创建子代理后,立即调用名为 agentgeex 的 MCP 工具 agx_update_execution,\n"
            "将 session_id 写入 agx_task_execution 记录。不要等待子代理完成。\n"
        )
    elif needs_subagent and at == "openclaw":
        log("needs_subagent=True 对 openclaw agent 无效(openclaw 走 Gateway Session),已忽略", "WARN")

    # merge: sub-agent requirements + interface instruction + task manual

    parts = [
        p
        for p in [
            subagent_prompt if needs_subagent else "",
            f"【本次指令】\n{instruction}" if instruction else "",
            f"【补充指令】\n{extra_instruction}" if extra_instruction else "",
            f"【任务说明书】\n{task_desc}" if task_desc else "",
        ]
        if p
    ]

    return "\n\n".join(parts)


# ============================================================
# internal dispatch function (no MCP decorator, imported from agx_core)
# ============================================================

from agx_core import (  # noqa: E402
    _ao_activate_task,
    _ao_continue_task,
    _ao_dispatch_task,
    _ao_schedule_task,
    _derive_session_key,
    _inject_assistant_hint,
    _is_safe_session_key,
    _resolve_host_manager,
    _resolve_send_host_id,
    _sanitize_name_field,
    build_assistant_profile,
    derive_exec_name,
    ensure_session,
    assistant_files_root_for,
    assistant_files_root_for_slug,
    ASSISTANT_FILES_ROOT,
)


# -- timeout floor for tasks that need human release (decided by Lao Feng on 2026-11-06) --
# needs_confirm=1 means "done is not done; a human must review the result first". Human response time is unpredictable,
# while the default timeout is config.task_timeout (3600s ~ 1h) -- if the human has not looked within an hour,
# the task is judged timed out and gets redelivered/woken, bouncing back and forth. Hence:
#   - when timeout_seconds is not passed explicitly, a needs_confirm=1 task gets at least 1 day;
#   - an explicit value is always respected (the caller knows what it is doing; do not decide for it).
# can be overridden with config.confirm_wait_timeout (86400 when unset).
_CONFIRM_WAIT_TIMEOUT_DEFAULT = 86400


def _confirm_wait_timeout() -> int:
    try:
        return int(getattr(config, "confirm_wait_timeout", _CONFIRM_WAIT_TIMEOUT_DEFAULT))
    except Exception:
        return _CONFIRM_WAIT_TIMEOUT_DEFAULT


def _timeout_for_confirm(timeout_seconds, needs_confirm: int, default: int) -> int:
    """Resolve the final timeout: needs_confirm=1 and no explicit timeout -> raise to at least 1 day."""
    t = int(timeout_seconds or default)
    try:
        nc = int(needs_confirm or 0)
    except Exception:
        nc = 0
    if nc == 1 and timeout_seconds is None:
        t = max(t, _confirm_wait_timeout())
    return t


@mcp.tool()
@require_level(5)
def agx_dispatch_task(
    caller_agent_id: str,
    instruction: str,
    agent_id: str = "",
    task_id: str = "",
    sender_id: str = "",
    needs_subagent: bool = False,
    timeout_seconds: int = None,
    needs_reply: int = 0,
    force_no_reply: bool = False,
    prev_task: str = "",
    next_task: str = "",
    plan_id: str = "",
    exec_instruction: str = "",
    session_key: str = "",
    self_session_id: str = "",
    session_name: str = "",
    exec_name: str = "",
    client_request_id: str = "",
    needs_confirm: int = 0,
) -> str:
    """
    [Dispatch task] Create the execution record and send the protocol command message, in one step.

    This is the standard entry point for a dispatcher to hand out work. Internally it:

    1. Creates the agx_task_execution record (pins the task)
    2. Assembles the command message per the protocol
    3. LPUSHes to the target agent's Redis queue
    4. Syncs the Redis cache and publishes an event

    agx_dispatch_task provides:

    - atomicity: the execution record and the message delivery are one call
    - smart derivation: host_id and project_id come from the agent record automatically
    - fewer parameters: the dispatcher only cares about whom to send to and what to do

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it:
        1. records the caller identity and enables an audit trail
        2. works with the agent allowlist (AGENT_ALLOWLIST etc.) for permission checks
        3. prevents unauthorized agents from calling write interfaces

        sender_id is a protocol-layer field written into the message header, distinct from caller_agent_id.
        The message also carries a send_agent field, derived automatically from caller_agent_id, which
        tracks the actual originating agent of the message.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).
        instruction (str): the dynamic instruction for this execution.
        agent_id (str, optional): target agent ID. Read from the task record when omitted.
        task_id (str, optional): associated static task ID. Uses the agent's default task when omitted.
        sender_id (str, optional): sender agent_id (the dispatcher itself, protocol-layer field).
        needs_subagent (bool, optional): whether to create a sub-agent to execute, default False.
            When True, a sub-agent creation instruction is appended (needed only by old tmux-mode agents).
        timeout_seconds (int, optional): watchdog timeout (seconds). Set by the task initiator according to
            task complexity; the watchdog marks the task failed when no finish arrives within this time.
            Default 1800 (15 minutes). Quick tasks: 1200-1800; complex tasks: 1800+.
        needs_reply (int, optional): receipt requirement. 0-not needed 1-reply needed (watchdog: 1->2->3).
            When 1, the watchdog automatically creates a receipt notification for the sender once the task is done.
        force_no_reply (bool, optional): explicitly disable the receipt (default False). When True it outranks
            the task.reply_mode template derivation -- this solves "needs_reply=0 forced back to 1 by the template":
            pass True when the sender explicitly wants no receipt (rather than only needs_reply=0).
        prev_task (str, optional): preceding task execution instance ID.
        next_task (str, optional): following task execution instance ID.
        plan_id (str, optional): owning plan ID.
        exec_instruction (str, optional): the full execution instruction.
            For task_labs='universal' tasks: takes over the whole thing and is written directly into
            agx_task_execution.exec_instruction, skipping the merge with _build_exec_instruction and the template x_desc.
            For **other task types**: it is no longer silently dropped (fixed 2026-10-24); it is merged as the
            [supplementary instruction] section of the final exec_instruction, placed after [this instruction]
            and before [task manual].
            When not passed: the original path runs, merging instruction with the task template x_desc.
            Typical case: dynamic scheduling of universal tasks, where each dispatch has different instruction
            content, passed in here with full background, steps and output requirements.
        exec_name (str, optional): short execution title (Chinese). One line saying what "this run" is doing.
            When omitted it is derived: the first heading line of the instruction (or a semantic field for JSON)
            -> falling back to the task description.
            Recommended to pass explicitly for universal / plan tasks so lists stay readable.
            Distinct from task_base.description (the task-type template description).
        session_key (str, optional): * session addressing key (e.g. "agent:agentgeex_agent:log_organize").
            When passed it is written directly as this execution's session_id (used to [pin/reuse a session] --
            several tasks land in the same session and their context accumulates continuously; reuse the same key
            to reuse the session). When omitted it is derived from task attributes (backward compatible,
            unchanged behavior):
              - dedicated_session=1 and task_labs=universal -> agent:{aid}:universal:{new uuid} (a new session each time)
              - dedicated_session=1 and task_labs!=universal -> agent:{aid}:{task_labs} (stably reused)
              - task_labs=general -> agent.default_session_id (the main session)
            Recommended together with session_name (name it on first use, then reuse by the same name).
        self_session_id (str, optional): * [the sender's own session id] (added 2026-10-09).
            Semantics = "the session you (the caller) are currently in" -- used to route the receipt.
            When dispatching across agents, the other side's receipt is delivered to [this session]; when omitted
            it falls back to your main session (agent:{caller}:main).
            * How to get it: the top of every exec_instruction has a [session context] block stating "the session
            this execution runs in" -- pass it through as-is.
            !! Old behavior (deprecated): the sender session used to be guessed via SQL by looking at the caller's
            running executions; that guessed wrong in multi-session (assistant / multi-task) setups -> now it is
            [passed explicitly, never guessed].
            (The flip side of the alias receive_session_id: session_key is the [receiver] session,
             self_session_id is the [sender] session -- orthogonal, no more ambiguity.)
        needs_confirm (int, optional): * task-level property: 0=not needed (default) 1=needs human release.
            Semantics = "when this task runs to completion it is not done; a human must look at the result and release it".
            When 1: agx_finish_task(status=succeeded) does not write succeeded but lands on
            run_status='awaiting_confirm' (chaining does not advance, timeouts are not counted); a human calling
            agx_confirm_task(approve) turns it into succeeded and releases it; reject turns it into failed.
            * This is a [task property], decided at dispatch/plan time; it is not decided at finish time.

    Returns:

        str: JSON containing run_uuid, msg_id, queue and queue_length.

    """
    DEFAULT_TIMEOUT = int(config.task_timeout)
    # <> 2026-11-06: needs_confirm=1 with no timeout -> at least 1 day (see _timeout_for_confirm)
    timeout_seconds = _timeout_for_confirm(timeout_seconds, needs_confirm, DEFAULT_TIMEOUT)
    try:
        # -- 1. determine task_id --

        if not task_id and not agent_id:
            return json.dumps({"error": "must provide task_id or agent_id"}, indent=2)

        if not task_id:
            # look up the default task by agent_id

            tasks = execute_sql(
                "SELECT task_id, network_id FROM agx_task_base WHERE agent_id = %s AND task_labs = 'general' AND active_status = 1 LIMIT 1",
                (agent_id,),
            )

            if not tasks:
                return json.dumps(
                    {"error": f"no default task (task_labs='general') found for agent '{agent_id}'"}, indent=2
                )

            task_id = tasks[0]["task_id"]

            network_id = tasks[0]["network_id"]

        else:
            task = execute_sql(
                "SELECT network_id, agent_id FROM agx_task_base WHERE task_id = %s AND active_status = 1",
                (task_id,),
            )

            if not task:
                return json.dumps({"error": f"task_id '{task_id}' not found or inactive"}, indent=2)

            network_id = task[0]["network_id"]

            if not agent_id:
                agent_id = task[0]["agent_id"]

        # -- 2. determine host_id + validate the agent's deploy state --

        agent_row = execute_sql(
            "SELECT host_id, deploy_status FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
            (agent_id,),
        )

        if not agent_row:
            return json.dumps({"error": f"agent '{agent_id}' 不存在或已停用"}, indent=2)
        if agent_row[0].get("deploy_status") != "deployed":
            return json.dumps(
                _deploy_gate_msg(agent_id, agent_row[0].get("deploy_status")), indent=2
            )
        host_id = agent_row[0].get("host_id", "")
        if not host_id:
            return json.dumps({"error": f"cannot determine host_id for agent '{agent_id}'"}, indent=2)

        # -- 3. resolve session_key and send_session_id --

        task_row = execute_sql(
            "SELECT task_labs, dedicated_session, reply_mode, task_type FROM agx_task_base WHERE task_id = %s",
            (task_id,),
        )
        task_labs = ""
        dedicated_session = 0
        if task_row:
            task_labs = task_row[0].get("task_labs", "")
            dedicated_session = task_row[0].get("dedicated_session", 0)
            reply_mode_val = task_row[0].get("reply_mode", "result")
            task_type = task_row[0].get("task_type", "normal")
            # ── auto-derive needs_reply from task.reply_mode ──
            #   force_no_reply=True means the sender [explicitly disables the receipt], outranking the template derivation.
            #   (needs_reply is an int defaulting to 0, so it cannot distinguish "explicitly passed 0" from "not passed" -> the boolean handles that)
            if force_no_reply:
                needs_reply = 0
            elif not needs_reply and reply_mode_val in ("result", "confirm"):
                needs_reply = 1
            # -- plan-type tasks read their timeout from config automatically --
            if task_type == "plan" and timeout_seconds is None:
                timeout_seconds = int(getattr(config, "plan_timeout", 3600))

        # -- universal task privacy check (bug fix 2026-09-30) --
        #   universal is each agent's private standing channel (internal assistant / dedicated session),
        #   and by design only the owner may dispatch into it (including the owner turning an external message received on
        #   general into a universal self-handled task). External agents must not dispatch into it directly.
        #   Note: is_private only governs query visibility, not execution reachability, hence this extra check.
        if task_labs == "universal" and caller_agent_id and caller_agent_id != agent_id:
            return json.dumps(
                {
                    "error": "universal task is private to its owner agent; "
                    f"caller '{caller_agent_id}' cannot dispatch universal task of '{agent_id}'"
                },
                indent=2,
            )

        # * session_key: when the caller passes it explicitly it wins (for pinning/reusing a session)
        #   otherwise derive it from the task attributes (backward compatible, behavior unchanged)
        # * 2026-10-05 security fix: an explicit session_key must pass the "character safety" check
        #   (only illegal characters are blocked, not platform structure -- multi-platform compatible). Reject when illegal, never fall back silently.
        if session_key:
            if not _is_safe_session_key(session_key):
                return json.dumps(
                    {
                        "error": "invalid session_key: not a usable session key "
                        f"(control/space/ellipsis/percent/placeholder). got={session_key!r}"
                    },
                    indent=2,
                )
            # explicitly given and safe -> use it as-is
        else:
            # * 2026-10-25: the derivation ladder moved down into agx_core._derive_session_key (single source of truth).
            #   Previously this logic lived only in this facade, so the inner _ao_dispatch_task did not know general ->
            #   every path that bypassed the facade and called the inner layer directly (watchdog dispatch/receipt, quick_cmd_send)
            #   landed on a one-off session when using general, the opposite of the "general lands in the main session" design intent.
            #   Now it is a single implementation, so the facade and the inner layer are necessarily consistent.
            session_key = _derive_session_key(agent_id, task_labs, dedicated_session)

        # * send_session_id = [the sender's own session id] (decided by Lao Feng on 2026-10-09: make it explicit, stop guessing)
        #   Old implementation: SQL-queried the caller's running execution and "guessed afterwards" -> guessed wrong with multiple sessions (assistant/multi-task).
        #   New implementation: (1) the caller passes self_session_id explicitly -> use it directly (after the character-safety check)
        #            (2) not passed -> the main session agent:{caller}:main (a guaranteed fallback, no more guessing)
        #   * 2026-10-09 FIX (self-dispatch empty-value bug): removed the `caller != agent_id` guard.
        #     Self-dispatch (caller==agent) needs send_session_id too: the execution's message still has to carry
        #     self_session_id (the downstream daemon direct-delivery and the watchdog receipt both read it), otherwise the receipt lands in the wrong session
        #     (measured run_universal_fbc56b6d9713: aifr_pm self-dispatched -> send_session_id empty ->
        #      the receipt was fallback-delivered to aifr_pm:main instead of the actual working session).
        send_session_id = ""
        if self_session_id:
            if _is_safe_session_key(self_session_id):
                send_session_id = self_session_id
            else:
                return json.dumps(
                    {
                        "error": "invalid self_session_id: contains unsafe characters "
                        f"(control/space/ellipsis/percent). got={self_session_id!r}"
                    },
                    indent=2,
                )
        elif caller_agent_id:
            # not passed -> the main session (no more SQL guessing). Self-dispatch and cross-agent are treated alike.
            send_session_id = f"agent:{caller_agent_id}:main"

        # -- 4. query agent_type for sub-agent control --

        agent_type = ""
        try:
            at_row = execute_sql(
                "SELECT agent_type FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
                (agent_id,),
            )
            if at_row:
                agent_type = at_row[0].get("agent_type", "")
        except Exception as exc:
            log(f"agx_dispatch_task agent_type lookup failed for {agent_id}: {exc}", "WARN")

        resolved_project_id = _resolve_project_id_from_agent(agent_id)

        # * 2026-10-24: universal tasks take over the whole text (the template merge is skipped);
        #   for non-universal tasks exec_instruction is no longer silently dropped -- it is merged in as a [supplementary instruction].
        if exec_instruction and task_labs == "universal":
            final_instruction = exec_instruction
        else:
            final_instruction = _build_exec_instruction(
                instruction,
                task_id,
                needs_subagent,
                agent_type,
                extra_instruction=exec_instruction or "",
            )
            if exec_instruction:
                log(
                    f"exec_instruction 对非 universal task(task_labs={task_labs}) 以【补充指令】并入"
                    f" | len={len(exec_instruction)}",
                    "INFO",
                )

        result = _ao_dispatch_task(
            caller_agent_id=caller_agent_id,
            instruction=final_instruction,
            agent_id=agent_id,
            task_id=task_id,
            network_id=network_id,
            project_id=resolved_project_id,
            host_id=host_id,
            sender_id=sender_id,
            timeout_seconds=timeout_seconds,
            session_key=session_key,
            send_session_id=send_session_id,
            needs_reply=needs_reply,
            prev_task=prev_task,
            next_task=next_task,
            plan_id=plan_id,
            session_name=session_name,
            exec_name=exec_name,
            client_request_id=client_request_id,
            needs_confirm=needs_confirm,
        )

        if "error" in result:
            return json.dumps(result, indent=2)

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_dispatch_task", e)}, indent=2)


@mcp.tool()
@require_level(3)
def agx_schedule_task(
    caller_agent_id: str,
    instruction: str,
    agent_id: str = "",
    task_id: str = "",
    sender_id: str = "",
    needs_subagent: bool = False,
    timeout_seconds: int = None,
    needs_reply: int = 0,
    force_no_reply: bool = False,
    parent_task_id: str = "",
    parent_run_uuid: str = "",
    prev_task: str = "",
    next_task: str = "",
    scheduled_at: str = "",
    plan_id: str = "",
    exec_instruction: str = "",
    session_key: str = "",
    self_session_id: str = "",
    session_name: str = "",
    exec_name: str = "",
    needs_confirm: int = 0,
) -> str:
    """
    [Schedule task] Create the execution record without delivering it; status stays pending.

    Differences from agx_dispatch_task:
    - writes only the database record, does not push to the Redis queue
    - run_status = 'pending', the watchdog does not scan it
    - used while building multi-step plans; each step is later activated with agx_activate_task
    - supports parent_task_id / parent_run_uuid to record the task hierarchy
    - supports prev_task / next_task to record the ordering of a task chain
    - supports scheduled_at to set the expected run time (MySQL DATETIME format); the watchdog activates it then

    Args:
        caller_agent_id (str): caller agent ID.
        instruction (str): execution instruction.
        agent_id (str, optional): target agent ID.
        task_id (str, optional): task ID.
        sender_id (str, optional): sender agent_id.
        needs_subagent (bool, optional): whether a sub-agent is needed.
        timeout_seconds (int, optional): timeout in seconds, default 900.
        needs_reply (int, optional): receipt requirement.
        force_no_reply (bool, optional): explicitly disable the receipt (default False), outranks template derivation.
        parent_task_id (str, optional): parent task ID, referencing agx_task_base.task_id.
        parent_run_uuid (str, optional): parent execution instance ID.
        prev_task (str, optional): preceding task execution instance ID.
        next_task (str, optional): following task execution instance ID.
        scheduled_at (str, optional): expected run time (MySQL DATETIME format, e.g. '2026-06-01 09:00:00').
        plan_id (str, optional): owning plan ID.
        exec_instruction (str, optional): the full execution instruction.
            For task_labs='universal' tasks: takes over the whole thing and is written directly into
            agx_task_execution.exec_instruction, skipping the merge with _build_exec_instruction and the template x_desc.
            For **other task types**: it is no longer silently dropped (fixed 2026-10-24); it is merged as the
            [supplementary instruction] section of the final exec_instruction, placed after [this instruction]
            and before [task manual].
            When not passed: the original path runs, merging instruction with the task template x_desc.
            Typical case: scheduled runs of universal tasks, where each schedule has different instruction content,
            passed in here with full background, steps and output requirements.
        exec_name (str, optional): short execution title (Chinese). Derived automatically when omitted, same as agx_dispatch_task.
        session_key (str, optional): * session addressing key. Takes effect with highest priority when passed
            (pin/reuse a session); otherwise derived from task attributes. See the same parameter in agx_dispatch_task.
        needs_confirm (int, optional): task-level property: 0=not needed (default) 1=needs human release.
            Semantics identical to the same parameter in agx_dispatch_task; in a plan, prefer putting it on each
            step's dispatch parameters (the confirmation is only needed after the step succeeds).

    Returns:
        str: JSON containing run_uuid and status="pending".
    """
    DEFAULT_TIMEOUT = int(config.task_timeout)
    # <> 2026-11-06: needs_confirm=1 with no timeout -> at least 1 day (see _timeout_for_confirm)
    timeout_seconds = _timeout_for_confirm(timeout_seconds, needs_confirm, DEFAULT_TIMEOUT)
    try:
        if not task_id and not agent_id:
            return json.dumps({"error": "must provide task_id or agent_id"}, indent=2)

        if not task_id:
            tasks = execute_sql(
                "SELECT task_id, network_id FROM agx_task_base WHERE agent_id = %s AND task_labs = 'general' AND active_status = 1 LIMIT 1",
                (agent_id,),
            )
            if not tasks:
                return json.dumps({"error": f"no default task found for agent '{agent_id}'"}, indent=2)
            task_id = tasks[0]["task_id"]
            network_id = tasks[0]["network_id"]
        else:
            task = execute_sql(
                "SELECT network_id, agent_id FROM agx_task_base WHERE task_id = %s AND active_status = 1",
                (task_id,),
            )
            if not task:
                return json.dumps({"error": f"task_id '{task_id}' not found or inactive"}, indent=2)
            network_id = task[0]["network_id"]
            if not agent_id:
                agent_id = task[0]["agent_id"]

        # host_id is not resolved here - it is fetched dynamically from the agent registry on activation

        task_row = execute_sql(
            "SELECT task_labs, dedicated_session, reply_mode, task_type FROM agx_task_base WHERE task_id = %s",
            (task_id,),
        )
        task_labs = ""
        dedicated_session = 0
        if task_row:
            task_labs = task_row[0].get("task_labs", "")
            dedicated_session = task_row[0].get("dedicated_session", 0)
            reply_mode_val = task_row[0].get("reply_mode", "result")
            task_type = task_row[0].get("task_type", "normal")
            # ── auto-derive needs_reply from task.reply_mode ──
            #   force_no_reply=True means the sender [explicitly disables the receipt], outranking the template derivation.
            #   (needs_reply is an int defaulting to 0, so it cannot distinguish "explicitly passed 0" from "not passed" -> the boolean handles that)
            if force_no_reply:
                needs_reply = 0
            elif not needs_reply and reply_mode_val in ("result", "confirm"):
                needs_reply = 1
            # -- plan-type tasks read their timeout from config automatically --
            if task_type == "plan" and timeout_seconds is None:
                timeout_seconds = int(getattr(config, "plan_timeout", 3600))

        # -- universal task privacy check (bug fix 2026-09-30) --
        #   universal is each agent's private standing channel (internal assistant / dedicated session),
        #   and by design only the owner may dispatch into it (including the owner turning an external message received on
        #   general into a universal self-handled task). External agents must not dispatch into it directly.
        #   Note: is_private only governs query visibility, not execution reachability, hence this extra check.
        if task_labs == "universal" and caller_agent_id and caller_agent_id != agent_id:
            return json.dumps(
                {
                    "error": "universal task is private to its owner agent; "
                    f"caller '{caller_agent_id}' cannot schedule universal task of '{agent_id}'"
                },
                indent=2,
            )

        # * session_key: when the caller passes it explicitly it wins (for pinning/reusing a session)
        #   otherwise derive it from the task attributes (backward compatible, behavior unchanged)
        # * 2026-10-05 security fix: an explicit session_key must pass the "character safety" check (same as agx_dispatch_task)
        if session_key:
            if not _is_safe_session_key(session_key):
                return json.dumps(
                    {
                        "error": "invalid session_key: not a usable session key "
                        f"(control/space/ellipsis/percent/placeholder). got={session_key!r}"
                    },
                    indent=2,
                )
            # explicitly given and safe -> use it as-is
        else:
            # * 2026-10-25: same as agx_dispatch_task -- the derivation ladder moved down into
            #   agx_core._derive_session_key (single source of truth); it is no longer reimplemented here.
            session_key = _derive_session_key(agent_id, task_labs, dedicated_session)

        # send_session_id logic is the same as agx_dispatch_task (2026-10-09: made explicit, no more guessing)
        # * 2026-10-09 FIX (self-dispatch empty-value bug): removed the `caller != agent_id` guard (same as agx_dispatch_task)
        send_session_id = ""
        if self_session_id:
            if _is_safe_session_key(self_session_id):
                send_session_id = self_session_id
            else:
                return json.dumps(
                    {
                        "error": "invalid self_session_id: contains unsafe characters "
                        f"(control/space/ellipsis/percent). got={self_session_id!r}"
                    },
                    indent=2,
                )
        elif caller_agent_id:
            send_session_id = f"agent:{caller_agent_id}:main"

        # -- query agent_type for sub-agent control --

        agent_type = ""
        try:
            at_row = execute_sql(
                "SELECT agent_type FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
                (agent_id,),
            )
            if at_row:
                agent_type = at_row[0].get("agent_type", "")
        except Exception as exc:
            log(f"agx_schedule_task agent_type lookup failed for {agent_id}: {exc}", "WARN")

        # * 2026-10-24: universal tasks take over the whole text (the template merge is skipped);
        #   for non-universal tasks exec_instruction is no longer silently dropped -- it is merged in as a [supplementary instruction].
        if exec_instruction and task_labs == "universal":
            final_instruction = exec_instruction
        else:
            final_instruction = _build_exec_instruction(
                instruction,
                task_id,
                needs_subagent,
                agent_type,
                extra_instruction=exec_instruction or "",
            )
            if exec_instruction:
                log(
                    f"exec_instruction 对非 universal task(task_labs={task_labs}) 以【补充指令】并入"
                    f" | len={len(exec_instruction)}",
                    "INFO",
                )
        resolved_project_id = _resolve_project_id_from_agent(agent_id)

        result = _ao_schedule_task(
            caller_agent_id=caller_agent_id,
            instruction=final_instruction,
            agent_id=agent_id,
            task_id=task_id,
            network_id=network_id,
            project_id=resolved_project_id,
            host_id="",
            sender_id=sender_id,
            timeout_seconds=timeout_seconds,
            session_key=session_key,
            send_session_id=send_session_id,
            needs_reply=needs_reply,
            parent_task_id=parent_task_id,
            parent_run_uuid=parent_run_uuid,
            prev_task=prev_task,
            next_task=next_task,
            scheduled_at=scheduled_at,
            plan_id=plan_id,
            session_name=session_name,
            exec_name=exec_name,
            needs_confirm=needs_confirm,
        )

        if "error" in result:
            return json.dumps(result, indent=2)

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_schedule_task", e)}, indent=2)


@mcp.tool()
@require_level(3)
def agx_activate_task(
    caller_agent_id: str,
    task_run_uuid: str,
) -> str:
    """
    [Activate task] Activate a pending execution record and deliver it to the agent queue.

    Only valid for records with run_status='pending'; other states return an error.
    After activation:
    - run_status -> 'running'
    - started_at -> current time
    - assembles the command message and delivers it to the agent's Redis queue
    - syncs the Redis cache and publishes an event

    Args:
        caller_agent_id (str): caller agent ID.
        task_run_uuid (str): execution instance ID to activate (e.g. run_xxx).

    Returns:
        str: JSON containing run_uuid, msg_id, queue, queue_length.

    Example:
        agx_activate_task(task_run_uuid="run_a1b2c3d4e5f6")
        -> {"run_uuid": "run_a1b2c3d4e5f6", "msg_id": "msg_...", "queue": "...", ...}
    """
    try:
        # -- resource-level ownership check: only the owning agent / project admin / global roles may activate --
        _own_rows = execute_sql(
            "SELECT agent_id, project_id FROM agx_task_execution WHERE task_run_uuid = %s",
            (task_run_uuid,),
        )
        if not _own_rows:
            return json.dumps({"error": f"execution '{task_run_uuid}' not found"}, indent=2, ensure_ascii=False)
        _deny = _check_resource_ownership(
            caller_agent_id, _own_rows[0].get("agent_id", ""), _own_rows[0].get("project_id", "")
        )
        if _deny:
            return json.dumps({"error": _deny, "permission_denied": True}, indent=2, ensure_ascii=False)

        result = _ao_activate_task(
            caller_agent_id=caller_agent_id,
            task_run_uuid=task_run_uuid,
        )

        if "error" in result:
            return json.dumps(result, indent=2)

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_activate_task", e)}, indent=2)


@mcp.tool()
@require_level(3)
def agx_continue_task(
    caller_agent_id: str,
    original_run_uuid: str,
    instruction: str,
    sender_id: str = "",
    timeout_seconds: int = None,
    prev_task: str = "",
    next_task: str = "",
    session_name: str = "",
    exec_name: str = "",
    needs_reply: int = 0,
    force_no_reply: bool = False,
    self_session_id: str = "",
    needs_confirm: int = -1,
) -> str:
    """
    [Continue task] Keep a multi-turn conversation going on top of an existing session.

    When to use: a task one exchange cannot settle (e.g. a complex analysis needing follow-up clarification);
    there is no need to create a new session, continue inside the same one.

    Core flow:
    1. Look up the original session_key from original_run_uuid (agent:xxx:run_xxx)
    2. Generate a new run_uuid (parent=original) reusing the session_id
    3. Send sessions.send (not sessions.create) via Redis -> daemon -> ws_client
    4. Listen for chat events and sessions.changed within the same session to get the token

    Permission (* approved by Lao Feng on 2026-11-19; four kinds may continue):
        (a) sender -- caller == send_agent_id of the original execution
                     The tool's very semantics are "the sender follows up with the executor"; the sender obviously
                     may continue a conversation it started itself.
                     This rule is allowed inside this method only, not added to _check_resource_ownership --
                     that generic function also guards update_agent / update_project / archive_task, and adding a
                     "sender" branch there would let a sender modify someone else's agent config (privilege escalation).
        (b) executor -- caller == agent_id of the original execution
        (c) project admin -- caller == admin_agent_id of the project the original execution belongs to
        (d) global role -- host_manager / workshop_manager
        (b)(c)(d) reuse the existing three rules of _check_resource_ownership; behavior unchanged.

    Args:
        caller_agent_id (str): caller agent ID.
        original_run_uuid (str): execution ID of the original conversation (the run_uuid returned by the first agx_dispatch_task).
        instruction (str): the new instruction for the continuation.
        sender_id (str, optional): sender agent_id (protocol-layer field).
        timeout_seconds (int, optional): timeout, default 1800 seconds (follows config.task_timeout).
        prev_task (str, optional): preceding task execution instance ID.
        next_task (str, optional): following task execution instance ID.
        exec_name (str, optional): short execution title (Chinese). When omitted it reuses the original name and appends "(continued)".
        needs_reply (int, optional): receipt requirement. 0-not needed (default) 1-reply needed (watchdog: 1->2->3).
            * Aligned with agx_dispatch_task: decided by the sender; when not passed it is derived from the original
            task's reply_mode (result/confirm -> 1).
        force_no_reply (bool, optional): explicitly disable the receipt (default False), outranks the derivation above.
        self_session_id (str, optional): * [the sender's own session id] (added 2026-10-09).
            Semantics = "the session you (the caller) are currently in" -- used to route the receipt.
            When omitted it falls back to your main session (agent:{caller}:main).
            * How to get it: the top of every exec_instruction has a [session context] block stating your session
            id -- pass it through as-is.
        needs_confirm (int, optional): whether the continued task needs human release.
            -1 (default) = inherit the original task's needs_confirm (continuing the same task, the confirmation
            requirement should not be dropped); 0/1 = explicit override.

    Returns:
        str: JSON containing run_uuid, msg_id, session_key, queue, etc.

    Example:
        agx_continue_task(
            original_run_uuid="run_a1b2c3d4e5f6",
            instruction="please explain the third point you made in more detail"
        )
        -> {"run_uuid": "run_newxxx", "session_key": "agent:agx_core:run_a1b2c3d4e5f6", ...}
    """
    # <> 2026-11-06: a continuation inherits the original task's needs_confirm (-1) by default, so the timeout can only be
    #   decided once the original task is read -- it must not be pinned to a default here, otherwise an inherited "needs release" task still gets only 1 hour.
    _timeout_explicit = timeout_seconds is not None
    try:
        # -- resource-level ownership check: continuing equals reusing that execution's session --
        _own_rows = execute_sql(
            "SELECT agent_id, send_agent_id, project_id, needs_confirm "
            "FROM agx_task_execution WHERE task_run_uuid = %s",
            (original_run_uuid,),
        )
        if not _own_rows:
            return json.dumps({"error": f"execution '{original_run_uuid}' not found"}, indent=2, ensure_ascii=False)
        _nc_req = -1 if needs_confirm is None else int(needs_confirm)
        _eff_nc = (
            int(_own_rows[0].get("needs_confirm") or 0)
            if _nc_req < 0
            else _nc_req
        )
        timeout_seconds = _timeout_for_confirm(
            timeout_seconds if _timeout_explicit else None,
            _eff_nc,
            int(config.task_timeout),
        )
        # * approved by Lao Feng on 2026-11-19: the sender may continue a conversation it started itself.
        #   Why allow it here instead of changing _check_resource_ownership:
        #   that generic function also guards update_agent / update_project / archive_task,
        #   and adding a "sender" branch there means a sender could also modify someone else's agent config (privilege escalation).
        #   But the semantics of continue_task are precisely "the sender follows up with the executor" (see the docstring),
        #   and authorizing only the executor makes it structurally unusable (evidence: agx_ui_lead was rejected with PERMISSION_DENIED).
        _is_sender = (caller_agent_id == (_own_rows[0].get("send_agent_id") or ""))
        if not _is_sender:
            _deny = _check_resource_ownership(
                caller_agent_id, _own_rows[0].get("agent_id", ""), _own_rows[0].get("project_id", "")
            )
            if _deny:
                return json.dumps({"error": _deny, "permission_denied": True}, indent=2, ensure_ascii=False)

        # ── auto-derive needs_reply from original task.reply_mode ──
        #   Same rule as agx_dispatch_task: the sender decides; when not passed, fall back to the task's reply_mode.
        #   force_no_reply=True -> explicitly disable the receipt, outranking the template derivation.
        if force_no_reply:
            needs_reply = 0
        elif not needs_reply:
            _trow = execute_sql(
                "SELECT b.reply_mode AS reply_mode FROM agx_task_base b "
                "JOIN agx_task_execution e ON e.task_id = b.task_id "
                "WHERE e.task_run_uuid = %s LIMIT 1",
                (original_run_uuid,),
            )
            _rm = (_trow[0].get("reply_mode") if _trow else "") or "result"
            if _rm in ("result", "confirm"):
                needs_reply = 1

        result = _ao_continue_task(
            caller_agent_id=caller_agent_id,
            original_run_uuid=original_run_uuid,
            instruction=instruction,
            sender_id=sender_id,
            timeout_seconds=timeout_seconds,
            prev_task=prev_task,
            next_task=next_task,
            session_name=session_name,
            exec_name=exec_name,
            needs_reply=needs_reply,
            needs_confirm=_nc_req,
            self_session_id=(
                self_session_id
                if (self_session_id and _is_safe_session_key(self_session_id))
                else f"agent:{caller_agent_id}:main"
            ),
        )
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_continue_task", e)}, indent=2)


# ============================================================
# DEPRECATED: agx_dequeue_agent_message
#
# Reason: message consumption is already handled by agx_daemon.py via redis-py BLPOP directly,
# so no MCP tool is needed to pull queue messages. The comment is kept in case it is restored later.
# (2026-06-09)
# ============================================================
# @mcp.tool()
# @require_level(3)
# def agx_dequeue_agent_message(caller_agent_id: str, agent_id: str, timeout: int = 0, host_id: str = "") -> str:
#     """
#     [Pop a message from the agent message queue] Pop one message from the given agent's queue.
#     ...
#     """
#     try:
#         r = get_redis_client()
#         if host_id:
#             queue_key = f"agx:queue:{host_id}:agent:{agent_id}"
#         else:
#             queue_key = f"agx:queue:agent:{agent_id}"
#         if timeout > 0:
#             result = r.blpop(queue_key, timeout=timeout)
#             if result:
#                 return result[1]
#             return "null"
#         else:
#             result = r.lpop(queue_key)
#             if result:
#                 return result
#             return "null"
#     except Exception as e:
#         return f"Error: {str(e)}"


@mcp.tool()
@require_level(0)
def agx_get_agent_queue_length(caller_agent_id: str, agent_id: str) -> str:
    """
    [Query agent queue length] How many messages are still waiting in the given agent's queue.

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity and enables an audit trail. Read-only tools are no exception -- every call chain must be traceable.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).

        agent_id (str): target agent ID.

    Returns:

        str: queue length information.

    """
    try:
        r = get_redis_client()

        # host-scoped queue: derive host_id from caller_agent_id automatically
        host_id_val = _resolve_send_host_id(caller_agent_id)
        queue_key = f"agx:queue:{host_id_val}:agent:{agent_id}" if host_id_val else f"agx:queue:agent:{agent_id}"

        length = r.llen(queue_key)

        return f"Queue {queue_key} length: {length}"

    except Exception as e:
        return _log_exception("agx_get_agent_queue_length", e)


@mcp.tool()
@require_level(0)
def agx_list_all_agent_queues(caller_agent_id: str) -> str:
    """
    [List all agent queues] List every active agent message queue in the system and its length.

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity and enables an audit trail. Read-only tools are no exception -- every call chain must be traceable.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).

    Returns:

        str: queue list as JSON.

    """
    try:
        r = get_redis_client()

        # scan the new format agx:queue:{host_id}:agent:{agent_id} plus the old format agx:queue:agent:{agent_id}
        keys_new = list(r.scan_iter(match="agx:queue:*:agent:*", count=500))
        keys_legacy = list(r.scan_iter(match="agx:queue:agent:*", count=500))
        keys = keys_new + keys_legacy

        # read-side visibility scope (shadow mode defaults to off -> behavior unchanged)
        _mode = _read_scope_mode()
        if _mode != "off" and caller_agent_id:
            def _aid_of(k):
                kk = k.decode() if isinstance(k, bytes) else k
                return kk.rsplit("agent:", 1)[-1]
            _scope = _read_scope_of(caller_agent_id)
            if _scope == "all":
                pass
            elif _mode == "log":
                log(f"[READ-SCOPE][log] agx_list_all_agent_queues caller={caller_agent_id} scope={_scope} would-filter non-visible queues", "WARN")
            else:
                _aid_list = list({_aid_of(k) for k in keys})
                proj_map = {}
                if _aid_list:
                    _ph = ",".join(["%s"] * len(_aid_list))
                    for _row in execute_sql(
                        f"SELECT agent_id, project_id FROM agx_agent_registry WHERE agent_id IN ({_ph})",
                        tuple(_aid_list),
                    ):
                        proj_map[_row["agent_id"]] = _row.get("project_id") or ""
                keys = [
                    k for k in keys
                    if _can_read_scope(caller_agent_id, _aid_of(k), proj_map.get(_aid_of(k), ""), tool="agx_list_all_agent_queues")
                ]

        result = {}

        for key in sorted(set(keys)):
            result[key] = r.llen(key)

        if not result:
            return json.dumps({"message": "No agent queues found"}, indent=2)

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        return _log_exception("agx_list_all_agent_queues", e)


@mcp.tool()
@require_level(0)
def agx_list_agent_sessions(caller_agent_id: str) -> str:
    """
    [List agent sessions] List every running tmux session.

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity and enables an audit trail. Read-only tools are no exception -- every call chain must be traceable.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).

    Returns:

        str: session list as JSON, including session_name, created, attached_clients, etc.

    """
    try:
        proc = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}|#{session_created}|#{session_attached}"],
            capture_output=True,
            text=True,
            timeout=TMUX_TIMEOUT,
        )

        if proc.returncode != 0:
            return json.dumps({"sessions": [], "note": proc.stderr.strip() or "no sessions"}, indent=2)

        sessions = []

        for line in proc.stdout.strip().split("\n"):
            if not line.strip():
                continue

            parts = line.split("|")

            sessions.append(
                {
                    "session_name": parts[0] if len(parts) > 0 else "",
                    "created": parts[1] if len(parts) > 1 else "",
                    "attached_clients": int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0,
                }
            )

        return json.dumps({"sessions": sessions}, indent=2, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_list_agent_sessions", e)}, indent=2)


# ============================================================

# Tools: session sharding -- dual write MySQL + Redis cache

# ============================================================

# Tools: archive and finish -- update state, dual-writing the cache

# ============================================================


@mcp.tool()
@require_level(3)
def agx_finish_task(caller_agent_id: str, task_run_uuid: str, status: str, result: str = "", summary: str = "") -> str:
    """
    [Archive and finish] Record the final result of a task execution and release resources.

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it:
        1. records the caller identity and enables an audit trail
        2. works with the agent allowlist for permission checks
        3. prevents unauthorized agents from calling write interfaces

     Forced reply on failure:
        When status='failed', regardless of the original agx_task_execution.needs_reply value,
        it is set to 1 so the watchdog is guaranteed to notify the initiator.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).
        task_run_uuid (str): run instance ID.
        status (str): execution result status. Only 'succeeded' or 'failed'.
        result (str, optional): raw data produced by the task (usually a JSON string).
        summary (str, optional): human-readable summary of this execution.

    Returns:

        str: confirmation of the finish status.

    """
    if status not in ["succeeded", "failed"]:
        return "Error: status must be 'succeeded' or 'failed'"

    # -- resource-level ownership check: only the owning agent / project admin / global roles may finish --
    _own_rows = execute_sql(
        "SELECT agent_id, project_id, alert_level, alert_count, send_agent_id, send_session_id, "
        "needs_confirm, run_status "
        "FROM agx_task_execution WHERE task_run_uuid = %s",
        (task_run_uuid,),
    )
    if not _own_rows:
        return f"Error: execution '{task_run_uuid}' not found"
    # * 2026-10-28: record the alert level before finishing -- used to decide whether this is "completed by itself after an alert" (revived).
    #   The notification text promises "if the task later completes by itself, a revived event will be recorded automatically", which must be honoured.
    _prev_alert_level = int(_own_rows[0].get("alert_level") or 0)
    _prev_alert_count = int(_own_rows[0].get("alert_count") or 0)
    _deny = _check_resource_ownership(
        caller_agent_id, _own_rows[0].get("agent_id", ""), _own_rows[0].get("project_id", "")
    )
    if _deny:
        return _deny

    # -- * 2026-11-06 human release (decided by Lao Feng): a needs_confirm=1 task is not complete even when it succeeds --
    #   Semantics: the task property decides that "after execution a human must review the result and release it".
    #   So here it does [not write succeeded] but writes awaiting_confirm:
    #     - not running -> timeout/wakeup mechanisms stop shouting at it
    #     - not succeeded -> chained steps do not advance (the watchdog chain only recognizes succeeded)
    #   A human calling agx_confirm_task(approve) -> succeeded and release; reject -> failed.
    _cur_status = (_own_rows[0].get("run_status") or "").strip()
    _needs_confirm = int(_own_rows[0].get("needs_confirm") or 0)
    _eff_status = status
    if status == "succeeded" and _needs_confirm == 1:
        _eff_status = "awaiting_confirm"
    if _cur_status == "awaiting_confirm":
        return (
            f"Execution {task_run_uuid} is already 'awaiting_confirm' (needs_confirm=1). "
            "It stays locked until a human calls agx_confirm_task(approve|reject); "
            "re-submitting is unnecessary and has no effect."
        )

    # -- forced reply on failure: whatever needs_reply was, a failure must notify --
    # * 2026-10-19 lazy-write fallback for started_at (the second one):
    #   the body of receipt/notification tasks is already inside instruction, and the agent never calls agx_get_execution
    #   to read itself -> started_at stays empty forever -> duration cannot be computed and statistics lack a start time.
    #   If it is still empty when finishing, fill it from created_at (not NOW, to avoid started>finished out-of-order).
    #   COALESCE guarantees a real start value is never overwritten.
    # * decided by Lao Feng on 2026-10-27: started_at must NOT be backfilled from created_at.
    #   created_at is when the plan was inserted (47 steps in the same second); treating it as the start time
    #   fabricates a "it started long ago" illusion (measured: w_ch06_s01~s04)
    #   started_at was all written as 11:54:54 = created_at, while they actually started after 11:55).
    #   Now only real values are kept: if the agent never read it -> stays NULL (honest).
    _started_backfill = "started_at = started_at"
    if status == "failed":
        sql = f"""
        UPDATE agx_task_execution
        SET run_status = %s, result_data = %s, summary = %s,
            needs_reply = 1, {_started_backfill}, finished_at = NOW(),
            alert_level = IF(alert_level IN (1, 2, 3), 9, alert_level)
        WHERE task_run_uuid = %s
        """
    else:
        sql = f"""
        UPDATE agx_task_execution
        SET run_status = %s, result_data = %s, summary = %s,
            {_started_backfill}, finished_at = NOW(),
            alert_level = IF(alert_level IN (1, 2, 3), 9, alert_level)
        WHERE task_run_uuid = %s
        """
    try:
        execute_sql(sql, (_eff_status, result, summary, task_run_uuid), commit=True)

        # --- also query task_id for updating the Redis cache ---

        # * 2026-10-30: read back needs_reply / reply_read as well --
        #   finishing sets needs_reply to 1, so the task enters the "awaiting receipt" queue;
        #   the maintenance condition of the receipt-pending index requires all three fields to be present; missing one means waiting for reconciliation,
        #   i.e. "a task that just finished cannot be chased". Reading the true values both fixes the stale cache and aligns the index on the spot.
        _exec_record = execute_sql(
            "SELECT task_id, alert_level, alert_count, needs_reply, "
            "COALESCE(reply_read, 0) AS reply_read FROM agx_task_execution "
            "WHERE task_run_uuid = %s",
            (task_run_uuid,),
        )

        # --- sync the execution instance state to the Redis cache ---
        # * fixed 2026-10-28: alert_level/alert_count must be included.
        #   The finishing SQL collapses alert_level 1/2/3 to 9, but the original sync_data only wrote
        #   run_status/result_data/summary/finished_at -> alert_level in the cache was still
        #   the pre-finish value. Evidence run_general_cd663bddb29a: MySQL alert_level=9,
        #   Redis still 2, and agx_get_execution reads the cache -> reporting "already succeeded but the alert was not closed".
        #   When the cache receives a partial field update, every field modified by the same SQL must be included.
        sync_data = {
            "run_status": _eff_status,
            "result_data": result,
            "summary": summary,
            "finished_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if _exec_record:
            sync_data["alert_level"] = str(_exec_record[0].get("alert_level") or "0")
            sync_data["alert_count"] = str(_exec_record[0].get("alert_count") or "0")
            # * 2026-10-30: two of the three decision fields of the receipt-pending index (run_status is above)
            sync_data["needs_reply"] = str(_exec_record[0].get("needs_reply") or "0")
            sync_data["reply_read"] = str(_exec_record[0].get("reply_read") or "0")

        _sync_entity("execution", task_run_uuid, sync_data)

        _publish_event("execution", "finished", task_run_uuid, {"status": _eff_status})

        # -- completed by itself after an alert -> record a revived event (honouring the promise in the notification text) --
        if _prev_alert_level in (1, 2, 3) and _eff_status == "succeeded":
            try:
                from agx_mcp.agx_core import _ao_record_event as _rec
                from agx_mcp.agx_core import _ao_send_notice as _notice
                from agx_mcp.agx_core import _ao_mark_event_delivery as _mark

                # * fixed 2026-10-29: revived used to be written to the DB only and never actually delivered,
                #   while delivery_state was hard-coded to 'sent' -> a fake value (measured: in the same batch of events
                #   wake/alert/receipt were all delivered, only revived was always sent).
                #   More importantly: at that moment the initiator is still holding a "please handle this" alert,
                #   and if it is not told the task completed by itself, it wastes time investigating.
                #   So one real delivery is added, with a delivery_key so the daemon writes back delivered.
                _send_agent = (_own_rows[0].get("send_agent_id") or "").strip()
                _send_session = (_own_rows[0].get("send_session_id") or "").strip()
                _evt = _rec(
                    task_run_uuid=task_run_uuid,
                    event_type="revived",
                    level=1,
                    from_agent=caller_agent_id,
                    to_agent=_send_agent or (_own_rows[0].get("agent_id") or ""),
                    to_session=_send_session,
                    channel="queue" if _send_session else "",
                    reason=f"告警后自行完成（曾告警 {_prev_alert_count} 次，alert_level={_prev_alert_level}）",
                    payload={"alert_count": _prev_alert_count, "alert_level": _prev_alert_level},
                    delivery_state="pending",
                )
                if _evt and _send_agent and _send_session:
                    # the delivery-confirmation marker must start with run_ (the daemon only scans agx:ws:result:run_*)
                    _dk = ("run_notice_" + _evt[len("evt_") :]) if _evt.startswith("evt_") else ""
                    _txt = (
                        "【AgentGeeX 任务恢复】\n"
                        "\n"
                        f"原任务: {task_run_uuid}\n"
                        f"执行方: {_own_rows[0].get('agent_id', '')}\n"
                        "状态: succeeded（此前已告警，现自行完成）\n"
                        f"事件: {_evt}\n"
                        "\n"
                        "结论摘要:\n"
                        f"{summary}\n"
                        "\n"
                        "此前的超时告警无需处置，无需重投。\n"
                    )
                    _r = _notice(_send_agent, _send_session, _txt, event_id=_evt, delivery_key=_dk)
                    _mark(_evt, "sent" if _r.get("ok") else "failed")
                # no initiator session -> nowhere to deliver. Keep pending (honestly expressing "not delivered"),
                # do not fake sent; receipt redelivery only scans event_type='receipt', so it will not be re-sent by mistake.
            except Exception as _e:
                __import__("sys").stderr.write(f"[revived] record failed: {_e}\n")

        # -- wake the watchdog to scan immediately (instead of waiting for the 30s poll) --
        try:
            r = get_redis_client()
            r.setex("agx:watchdog:wake", 5, "1")
        except Exception as exc:
            log(f"agx_finish_task watchdog wake setex failed: {exc}", "WARN")

        # -- clean up the wakeup state (the task is over, no longer needed) --
        try:
            r = get_redis_client()
            r.delete(f"agx:watchdog:wake:{task_run_uuid}")
        except Exception as exc:
            log(f"agx_finish_task watchdog wake delete failed for {task_run_uuid}: {exc}", "WARN")

        # -- check whether the owning plan is fully complete --
        plan_rows = execute_sql(
            "SELECT plan_id FROM agx_task_execution WHERE task_run_uuid = %s",
            (task_run_uuid,),
        )
        if plan_rows and plan_rows[0].get("plan_id") and _eff_status in ("succeeded", "failed"):
            _check_plan_completion(plan_rows[0]["plan_id"])

        if _eff_status == "awaiting_confirm":
            return (
                f"Execution {task_run_uuid} submitted and LOCKED as 'awaiting_confirm' "
                f"(needs_confirm=1). It is NOT succeeded: downstream steps will not advance "
                f"and timeout/wake alerts stop. A human must call "
                f"agx_confirm_task(task_run_uuid='{task_run_uuid}', action='approve') to release, "
                f"or action='reject' to fail it."
            )
        return f"Execution {task_run_uuid} marked as {_eff_status}."

    except Exception as e:
        return _log_exception("agx_finish_task", e)


# ============================================================

# Tools: human confirmation - confirm a task in the awaiting_confirm state

# ============================================================


@mcp.tool()
@require_level(0)
def agx_confirm_task(caller_agent_id: str, task_run_uuid: str, action: str = "approve") -> str:
    """
    [Human confirmation] Approve or reject a task in the awaiting_confirm state.

    When to use:
    - task property needs_confirm=1: agx_finish_task does [not write succeeded] on success,
      it lands on run_status='awaiting_confirm' (chaining does not advance; timeouts/wakeups no longer fire).
    - After a human has reviewed the result, call this tool to approve or reject.
    - The watchdog only [reports]; it performs no state transition (the old design had the watchdog scan and
      transition, abolished on 2026-11-06).

    action:
        - "approve" (default): release, run_status -> succeeded, chained tasks are activated automatically
        - "reject": reject, run_status -> failed

    Args:
        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).
        task_run_uuid (str): execution instance ID awaiting confirmation.
        action (str): "approve" or "reject", default "approve".

    Returns:
        str: confirmation result.
    """
    if action not in ("approve", "reject"):
        return "Error: action must be 'approve' or 'reject'"

    try:
        # 1. the current state must be awaiting_confirm (tolerating the old name pending_confirm)
        #   * 2026-11-06: the [full field set needed for finishing] must be read in one go here.
        #   Why: finish only wrote half of it (it could not write succeeded), and this method now takes over the rest;
        #   and because _sync_entity does not merge with the existing hash -- whatever is passed is the whole truth,
        #   omitting needs_reply makes _receipt_pending_wanted read None -> judged as "no receipt needed"
        #   -> the task is zrem'd out of the receipt-pending index -> the initiator never gets the receipt.
        rows = execute_sql(
            "SELECT run_status, needs_confirm, agent_id, project_id, needs_reply, "
            "COALESCE(reply_read, 0) AS reply_read, alert_level, alert_count, "
            "finished_at, reply_sent_at, plan_id "
            "FROM agx_task_execution WHERE task_run_uuid = %s",
            (task_run_uuid,),
        )
        if not rows:
            return f"Error: execution {task_run_uuid} not found"

        # -- resource-level ownership check: only the owning agent / project admin / global roles may confirm --
        _deny = _check_resource_ownership(caller_agent_id, rows[0].get("agent_id", ""), rows[0].get("project_id", ""))
        if _deny:
            return _deny

        current_status = rows[0].get("run_status", "")

        if current_status not in ("awaiting_confirm", "pending_confirm"):
            return (
                f"Error: execution {task_run_uuid} is '{current_status}', not 'awaiting_confirm' "
                f"(legacy 'pending_confirm' is also accepted)"
            )

        # * decided by Lao Feng on 2026-11-06: the executor must not release its own task.
        #   Why it is mandatory: the whole point of needs_confirm is "a human has reviewed the result";
        #   if the executor could call approve = approving itself, the whole chain would be pointless.
        #   Why global roles are the exception: the "release" button in the external UI calls as agx_core
        #   (workshop_manager); when the task being released happens to belong to agx_core,
        #   a blanket rule would make the button do nothing (the same caller may be a human or itself,
        #   indistinguishable). So only "self-approval by a non-global role" is blocked.
        #   Known gap: the audit can only record agx_core; it cannot tell which human clicked.
        _exec_agent = (rows[0].get("agent_id") or "").strip()
        if caller_agent_id and caller_agent_id == _exec_agent:
            if _resolve_agent_role(caller_agent_id) not in ("host_manager", "workshop_manager"):
                return (
                    f"拒绝:{caller_agent_id} 是本任务的执行方，不能给自己放行。"
                    "需由其他人调 agx_confirm_task(action='approve'|'reject')。"
                )

        # 2. confirm -> succeeded; reject -> failed
        #   * both outcomes must fill in the half that finish did not do (because finish could not write a terminal state):
        #     - reject = failure, must match the failed branch of agx_finish_task: needs_reply=1
        #       (otherwise a rejection is silent to the initiator -- nobody knows the task was sent back)
        #     - finished_at was already written at finish; COALESCE fills only empty values, never overwriting the real delivery moment
        #       (finished_at semantics = when the executor delivered, not when the human released)
        if action == "approve":
            new_status = "succeeded"
            new_needs_confirm = 2  # confirmed
            confirm_note = "人类已确认(放行)"
            execute_sql(
                "UPDATE agx_task_execution SET run_status = %s, needs_confirm = %s, "
                "summary = CONCAT(COALESCE(summary,''), ' | ', %s), "
                "finished_at = COALESCE(finished_at, NOW()) "
                "WHERE task_run_uuid = %s",
                (new_status, new_needs_confirm, confirm_note, task_run_uuid),
                commit=True,
            )
        else:
            new_status = "failed"
            new_needs_confirm = 3  # rejected
            confirm_note = "人类已驳回"
            execute_sql(
                "UPDATE agx_task_execution SET run_status = %s, needs_confirm = %s, "
                "summary = CONCAT(COALESCE(summary,''), ' | ', %s), "
                "needs_reply = 1, finished_at = COALESCE(finished_at, NOW()) "
                "WHERE task_run_uuid = %s",
                (new_status, new_needs_confirm, confirm_note, task_run_uuid),
                commit=True,
            )

        # 3. sync the Redis cache (* all fields must be included, see 1. above)
        _after = execute_sql(
            "SELECT needs_reply, COALESCE(reply_read, 0) AS reply_read, "
            "alert_level, alert_count, finished_at, reply_sent_at "
            "FROM agx_task_execution WHERE task_run_uuid = %s",
            (task_run_uuid,),
        )
        _sync_data = {
            "run_status": new_status,
            "needs_confirm": str(new_needs_confirm),
        }
        if _after:
            _r0 = _after[0]
            for _f in ("needs_reply", "reply_read", "alert_level", "alert_count"):
                _sync_data[_f] = str(_r0.get(_f) or "0")
            for _f in ("finished_at", "reply_sent_at"):
                if _r0.get(_f):
                    _sync_data[_f] = str(_r0.get(_f))
        _sync_entity("execution", task_run_uuid, _sync_data)

        _publish_event("execution", f"{action}d", task_run_uuid, {"status": new_status})

        # 4. plan completion check: finish only calls _check_plan_completion on succeeded/failed,
        #    skipping it for awaiting_confirm. The state only truly becomes terminal after release/rejection -> it must be added here,
        #    otherwise if the last step of a multi-step plan needs release, the plan never reaches completed.
        _pid = (rows[0].get("plan_id") or "").strip()
        if _pid:
            _check_plan_completion(_pid)

        # 4. wake the watchdog (chained tasks need an immediate scan)
        try:
            r = get_redis_client()
            r.setex("agx:watchdog:wake", 5, "1")
        except Exception as exc:
            log(f"agx_confirm_task watchdog wake setex failed: {exc}", "WARN")

        # * fixed 2026-11-06: the original success path had no return -> returned None ->
        #   the MCP output schema requires a string, the caller received -32602 while the DB had already been changed
        #   (the symptom is "it errored but actually took effect", the worst kind of failure).
        _verb = "approved" if action == "approve" else "rejected"
        return (
            f"Execution {task_run_uuid} {_verb}: "
            f"run_status={new_status}, needs_confirm={new_needs_confirm}."
        )

    except Exception as e:
        return _log_exception("agx_confirm_task", e)

# ============================================================

# Tools: receipt acknowledgement - acknowledge after reading (reply_read / reply_read_at)

# ============================================================


@mcp.tool()
@require_level(0)
def agx_ack_receipt(caller_agent_id: str, task_run_uuid: str) -> str:
    """
    [Acknowledge receipt] Acknowledge after reading a receipt, meaning "I really did read it".

    * What is acknowledged is the [task], not that notification record (decided by Lao Feng on 2026-10-29).
        Receipt messages reuse the dispatch envelope: task_run_uuid / send_agent /
        self_session_id / msg_type=receipt. The receiver reads the summary itself by task_run_uuid,
        then knows from msg_type=receipt that "this one has to be signed". So signing also uses only
        task_run_uuid -- no event_id is needed; an event is merely the history of "how many times and when
        it was sent".

    * State lands in agx_task_execution, not in the event table:
        reply_sent_at       when I sent it (a resend overwrites with the latest)
        reply_delivered_at  when it was delivered
        reply_read_at       first acknowledgement time (written only once; a repeat acknowledgement does not overwrite)
        reply_read_last_at  most recent acknowledgement time (refreshed on every acknowledgement)  <- this method writes here
        ack_state           sent / delivered / read / unacked

    * Closing point = acknowledgement (not sending):
        This method also pushes needs_reply to 3 (replied). The watchdog takes that as "this matter is settled"
        and stops resending. Previously "set to 3 the moment it was sent" meant an unsigned receipt counted as
        done, so both resends and alerts were blind to it.

    * All three steps are mandatory (when msg_type=receipt arrives):
        1. read     read the summary by task_run_uuid (someone else's output, not yours)
        2. judge    does the conclusion hold / does it require follow-up action from you
        3. sign     call this method -- without this step the system has no idea you read it

    * Idempotent: a repeated acknowledgement does not error and only refreshes reply_read_last_at --
        reply_read_at keeps the first acknowledgement time, otherwise the "how long until the receipt was read"
        statistic would be erased by a re-acknowledgement
        (evidence 2026-11-02: after two acknowledgements only the last timestamp was left, the first was lost).
    * Ownership: you may only acknowledge receipts [sent to yourself] -- the caller must be the initiator of the
        original task (send_agent_id), or the resource owner / project admin / a global role (via the unified ownership check).

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and ownership check).
        task_run_uuid (str): the original task's ID (the run uuid in the message you received).

    Returns:
        str: acknowledgement result.
    """
    try:
        _arg = (task_run_uuid or "").strip()

        # * compatibility layer (2026-10-29): old habits may pass the message's event_id as the parameter here.
        #   It is mapped once and never treated as the acknowledgement target -- the real write path is always the task row.
        if _arg.startswith("evt_"):
            rows_evt = execute_sql(
                "SELECT task_run_uuid FROM agx_task_execution_event WHERE event_id = %s",
                (_arg,),
            )
            if not rows_evt:
                return f"Error: event {_arg} not found"
            _arg = (rows_evt[0].get("task_run_uuid") or "").strip()
            if not _arg:
                return f"Error: event {_arg} has no task_run_uuid"

        rows = execute_sql(
            "SELECT task_run_uuid, agent_id, project_id, send_agent_id, "
            "reply_read, reply_read_at, run_status "
            "FROM agx_task_execution WHERE task_run_uuid = %s",
            (_arg,),
        )
        if not rows:
            return f"Error: execution {_arg} not found"

        row = rows[0]
        _is_sender = (caller_agent_id or "").strip() == (row.get("send_agent_id") or "").strip()

        # -- ownership: a receipt is sent to [the initiator], and the initiator itself may of course acknowledge it --
        if not _is_sender:
            _deny = _check_resource_ownership(
                caller_agent_id, row.get("agent_id", ""), row.get("project_id", "")
            )
            if _deny:
                return _deny

        already = str(row.get("reply_read") or "0") == "1"

        # -- acknowledge: write the task row (single source of truth) --
        execute_sql(
            "UPDATE agx_task_execution "
            "SET reply_read = 1, "
            "reply_read_at = IFNULL(reply_read_at, NOW()), "
            "reply_read_last_at = NOW(), "
            "ack_state = 'read', needs_reply = 3 "
            "WHERE task_run_uuid = %s",
            (_arg,),
            commit=True,
        )

        # -- sync the Redis cache --
        try:
            fresh = execute_sql(
                "SELECT reply_read, reply_read_at, reply_read_last_at, ack_state, needs_reply "
                "FROM agx_task_execution "
                "WHERE task_run_uuid = %s",
                (_arg,),
            )
            if fresh:
                _sync_entity(
                    "execution",
                    _arg,
                    {
                        "reply_read": str(fresh[0].get("reply_read") or "0"),
                        "reply_read_at": str(fresh[0].get("reply_read_at") or ""),
                        "reply_read_last_at": str(fresh[0].get("reply_read_last_at") or ""),
                        "ack_state": "read",
                        "needs_reply": str(fresh[0].get("needs_reply") or "3"),
                    },
                )
                # * 2026-10-30: acknowledgement is a deterministic "remove from the pending index" event --
                #   it does not rely on the three-field guard of _build_extra_index (this data carries no run_status,
                #   so the guard would skip it); ZREM is called directly here. Otherwise a zombie is left in the index:
                #   the read side could self-heal from the hash, but only on the next scan, which wastes a full round.
                get_redis_client().zrem(EXEC_RECEIPT_PENDING_IDX, _arg)
        except Exception as exc:
            log(f"agx_ack_receipt cache sync failed: {exc}", "WARN")

        _publish_event("execution", "receipt_acked", _arg,
                       {"is_receipt": True, "repeat": already})

        if already:
            return (
                f"Execution {_arg} already acknowledged; "
                f"reply_read_last_at refreshed (first read time kept)."
            )
        return f"Execution {_arg} acknowledged (reply_read=1)."

    except Exception as e:
        return _log_exception("agx_ack_receipt", e)




# ============================================================

# Tools: plan management -- plan_deploy / plan_query / plan_list / plan_cancel

# ============================================================


def _validate_and_expand_plan_steps(steps: list) -> tuple:
    """Validate plan step topology integrity and expand children -> parent_run_uuid.
    Returns (step_map, validated_steps, entry_tasks) or an error message.

    Split into its own function so agx_plan_create and agx_plan_deploy can share it.
    """
    if not isinstance(steps, list) or len(steps) == 0:
        return json.dumps({"error": "steps must be a non-empty list"}, indent=2), None, None, None

    # -- expand children -> fill in parent_run_uuid --
    for step in steps:
        run_uuid = step.get("run_uuid")
        if not run_uuid:
            return json.dumps({"error": f"step missing run_uuid: {step}"}, indent=2), None, None, None
        children = step.get("children", [])
        for child_uuid in children:
            for child_step in steps:
                if child_step.get("run_uuid") == child_uuid:
                    if not child_step.get("parent_run_uuid"):
                        child_step["parent_run_uuid"] = run_uuid
                    break

    # -- validate topology integrity --
    step_map = {s["run_uuid"]: s for s in steps}
    first_step = steps[0]
    if first_step.get("parent_run_uuid"):
        return (
            json.dumps({"error": f"First step {first_step['run_uuid']} must not have parent_run_uuid"}, indent=2),
            None,
            None,
            None,
        )

    for step in steps:
        pu = step.get("parent_run_uuid")
        if pu and pu not in step_map:
            return (
                json.dumps({"error": f"Step {step['run_uuid']} has unknown parent_run_uuid: {pu}"}, indent=2),
                None,
                None,
                None,
            )
        # -- mutual-exclusion check: prev_task and parent_run_uuid cannot both be present --
        pt = step.get("prev_task", "")
        if pt and pu:
            return (
                json.dumps(
                    {
                        "error": f"Step {step['run_uuid']}: prev_task='{pt}' 和 parent_run_uuid='{pu}' 互斥,不能同时设置。用 parent_run_uuid(子任务属于父的分支)或 prev_task(串行链),二选一。"
                    },
                    indent=2,
                ),
                None,
                None,
                None,
            )
        for cu in step.get("children") or []:
            if cu not in step_map:
                return (
                    json.dumps({"error": f"Step {step['run_uuid']} has unknown child: {cu}"}, indent=2),
                    None,
                    None,
                    None,
                )

    # -- needs_confirm normalization + domain validation (* 2026-11-06) --
    #   a plan step supports needs_confirm=1 (a human must release it when done; run_status lands on awaiting_confirm).
    #   It is normalized to an int 0/1 at [validation] time and dirty values are rejected: create and deploy share this function,
    #   so bad data is caught in agx_plan_create and never blows up later at deploy/insert time.
    #   not passed -> 0 (no release needed by default, consistent with historical behavior).
    for step in steps:
        _nc_raw = step.get("needs_confirm", 0)
        try:
            _nc = int(_nc_raw or 0)
        except (TypeError, ValueError):
            return (
                json.dumps(
                    {"error": f"Step {step['run_uuid']}: needs_confirm 必须是整数 0/1，收到 {_nc_raw!r}"},
                    indent=2,
                ),
                None,
                None,
                None,
            )
        if _nc not in (0, 1):
            return (
                json.dumps(
                    {"error": f"Step {step['run_uuid']}: needs_confirm 只接受 0(不需要)/1(需人类放行)，收到 {_nc}"},
                    indent=2,
                ),
                None,
                None,
                None,
            )
        step["needs_confirm"] = _nc

    # find the entry tasks (those without parent_run_uuid)
    entry_tasks = [s["run_uuid"] for s in steps if not s.get("parent_run_uuid")]

    return None, step_map, steps, entry_tasks


def _deploy_executions(
    caller_agent_id: str,
    plan_id: str,
    project_id: str,
    steps: list,
    step_map: dict,
    task_run_uuid: str = "",
    send_session_id_override: str = "",
) -> str:
    """Write steps into agx_task_execution + the Redis cache.
    Called by agx_plan_deploy, and also used by the agx_plan_create+deploy chain.

    Args:
        task_run_uuid (str): the current execution's task_run_uuid -> its execution.session_id is written into the
            steps' send_session_id. Empty -> falls back to agent:{caller_agent_id}:main
        send_session_id_override (str): * added 2026-10-12. Explicitly sets the steps' send_session_id (the sender session).
            Highest priority (used by internal orchestration such as agx_daily_log, which passes the initiator session
            straight through instead of going via task_run_uuid).
            When empty it falls back to task_run_uuid derivation, then to agent:{caller}:main.
    """
    # send_session_id (sender session) resolution priority:
    #   (1) explicit override (agx_daily_log etc. pass it straight through) -> (2) task_run_uuid to look up execution.session_id
    #   -> (3) fall back to agent:{caller_agent_id}:main (honouring the docstring promise "without task_run_uuid, fall back to the PM main session")
    send_session_id = (send_session_id_override or "").strip()
    if not send_session_id and task_run_uuid and task_run_uuid.startswith("run_"):
        exec_row = execute_sql(
            "SELECT session_id FROM agx_task_execution WHERE task_run_uuid = %s",
            (task_run_uuid,),
        )
        if exec_row and exec_row[0].get("session_id"):
            send_session_id = exec_row[0]["session_id"]
    if not send_session_id:
        send_session_id = f"agent:{caller_agent_id}:main"
    created = []
    run_uuids = []

    with UsingMysql(commit=True) as um:
        for step in steps:
            run_uuid = step["run_uuid"]
            agent_id = step.get("agent_id")
            instruction_text = step.get("instruction", "")
            children_list = step.get("children", [])
            # has child tasks -> inject the child task information into instruction
            if children_list:
                child_lines = []
                for i, cu in enumerate(children_list, 1):
                    cs = step_map.get(cu)
                    if cs:
                        ca = cs.get("agent_id", "unknown")
                        ci = cs.get("instruction", "(无指令)")
                        child_lines.append(f"  {i}. `{cu}` - {ci} (agent: {ca})")
                child_block = (
                    f"\n\n---\n"
                    f"##  系统信息:本任务有 {len(children_list)} 个子任务\n\n"
                    f"子任务全部 **succeeded** 后,看门狗才会激活本任务。\n\n"
                    f"**子任务列表:**\n" + "\n".join(child_lines) + f"\n\n**查询子任务结果:**\n"
                    f'- 单个查询: 调用 `agx_get_execution("{{子任务run_uuid}}")` 获取详情(含 result/summary 字段)\n'
                    f"- 批量查询: `SELECT * FROM agx_task_execution WHERE parent_run_uuid='{run_uuid}' AND run_status='succeeded'`\n"
                )
                instruction_text = instruction_text + child_block
            task_id = step.get("task_id", "")
            parent_run_uuid = step.get("parent_run_uuid", "")
            activate_plan_id = step.get("activate_plan", "")  # sub-plan ID, only present for plan_execute-type tasks
            # -- human release + timeout (* 2026-11-06) --
            #   The old implementation hard-coded needs_confirm to 0 -> no step in a plan could require "a human must release it when done".
            #   With step-level needs_confirm=1: finish_task does not write succeeded even on success, it lands on awaiting_confirm;
            #   and since human response time is unpredictable, when timeout_seconds is not given explicitly _timeout_for_confirm
            #   raises it to at least 1 day (the same criterion as agx_dispatch_task, no separate rule).
            #   * _timeout_for_confirm only raises when timeout_seconds is None,
            #     so the [original value] must be passed here (None when not passed); do not fill in a default first.
            needs_confirm = int(step.get("needs_confirm", 0) or 0)
            timeout_sec = _timeout_for_confirm(
                step.get("timeout_seconds", None), needs_confirm, int(config.task_timeout)
            )
            prev_task = step.get("prev_task", "")
            next_task = step.get("next_task", "")
            scheduled_at = step.get("scheduled_at", "")

            # -- short execution title (plan A) --
            _exec_name_raw = step.get("exec_name", "")
            if (_exec_name_raw or "").strip():
                exec_name = derive_exec_name(_exec_name_raw, instruction_text, "", "")
            else:
                _td = execute_sql(
                    "SELECT task_labs, description FROM agx_task_base WHERE task_id = %s",
                    (task_id,),
                )
                _tl = (_td[0].get("task_labs") or "").strip() if _td else ""
                _tdesc = (_td[0].get("description") or "").strip() if _td else ""
                exec_name = derive_exec_name("", instruction_text, _tl, _tdesc)

            if not agent_id:
                return json.dumps({"error": f"step {run_uuid} missing agent_id"}, indent=2)

            # determine host_id + validate the agent's deploy state
            host_id = ""
            agent_row = execute_sql(
                "SELECT host_id, deploy_status FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
                (agent_id,),
            )
            if not agent_row:
                return json.dumps({"error": f"step {run_uuid}: agent '{agent_id}' 不存在或已停用"}, indent=2)
            if agent_row[0].get("deploy_status") != "deployed":
                return json.dumps(
                    _deploy_gate_msg(agent_id, agent_row[0].get("deploy_status"), prefix=f"step {run_uuid}: "),
                    indent=2,
                )
            host_id = agent_row[0].get("host_id", "")

            # determine task_id (use the agent's general when not passed)
            if not task_id:
                tasks = execute_sql(
                    "SELECT task_id, network_id FROM agx_task_base WHERE agent_id = %s AND task_labs = 'general' AND active_status = 1 LIMIT 1",
                    (agent_id,),
                )
                if tasks:
                    task_id = tasks[0]["task_id"]
                    network_id = tasks[0]["network_id"]
                else:
                    return json.dumps({"error": f"no default task for agent '{agent_id}' at step {run_uuid}"}, indent=2)
            else:
                task_info = execute_sql(
                    "SELECT network_id, task_labs FROM agx_task_base WHERE task_id = %s AND active_status = 1",
                    (task_id,),
                )
                if task_info:
                    network_id = task_info[0]["network_id"]
                else:
                    return json.dumps({"error": f"task_id '{task_id}' not found for step {run_uuid}"}, indent=2)

            # -- 2026-10-13 FIX: plan step deployment merges the task template (aligned with agx_dispatch_task) --
            # the old implementation wrote instruction_text directly -> the task template x_desc was lost -> the executor only saw
            # "call the MCP script" instead of "the task manual" (violating the design: template = what to do, instruction = parameters).
            # aligned with agx_dispatch_task: universal tasks skip the merge (they already carry a full instruction); other tasks merge
            # template x_desc -> exec_instruction = [this instruction] + [task manual].
            _step_labs = ""
            if task_id:
                _labs_row = execute_sql(
                    "SELECT task_labs FROM agx_task_base WHERE task_id = %s AND active_status = 1",
                    (task_id,),
                )
                if _labs_row:
                    _step_labs = (_labs_row[0].get("task_labs") or "").strip()
            if _step_labs != "universal" and instruction_text:
                _agent_type = ""
                try:
                    _at_row = execute_sql(
                        "SELECT agent_type FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
                        (agent_id,),
                    )
                    if _at_row:
                        _agent_type = _at_row[0].get("agent_type", "") or ""
                except Exception as exc:
                    log(f"_deploy_executions agent_type lookup failed for {agent_id}: {exc}", "WARN")
                instruction_text = _build_exec_instruction(
                    instruction_text, task_id, False, _agent_type
                )

            # -- session addressing: a step may specify session_key explicitly (e.g. a main-session snapshot must land on agent:{aid}:main) --
            # default (not specified): each plan step lands on its own session agent:{agent_id}:{run_uuid}
            step_session_key = (step.get("session_key") or "").strip()
            session_id = step_session_key or f"agent:{agent_id}:{run_uuid}"
            send_host_id = _resolve_send_host_id(caller_agent_id)

            um.cursor.execute(
                """INSERT INTO agx_task_execution
                   (network_id, project_id, plan_id, activate_plan_id, task_id, agent_id, task_run_uuid, host_id,
                    exec_instruction, send_agent_id, send_host_id, send_session_id, session_id, run_status,
                    timeout_seconds, needs_confirm, parent_run_uuid, prev_task, next_task, scheduled_at, exec_name,
                    created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s, %s, %s, %s,
                           NULL)""",
                (
                    network_id,
                    project_id,
                    plan_id,
                    activate_plan_id or "",
                    task_id,
                    agent_id,
                    run_uuid,
                    host_id,
                    instruction_text,
                    caller_agent_id,
                    send_host_id or None,
                    send_session_id or None,
                    session_id,
                    timeout_sec,
                    needs_confirm,
                    parent_run_uuid or None,
                    prev_task or None,
                    next_task or None,
                    scheduled_at or None,
                    exec_name or None,
                ),
            )
            pkid = um.cursor.lastrowid
            created.append(
                (
                    run_uuid,
                    pkid,
                    network_id,
                    task_id,
                    agent_id,
                    send_host_id,
                    host_id,
                    instruction_text,
                    timeout_sec,
                    send_session_id,
                    parent_run_uuid,
                    prev_task,
                    next_task,
                    scheduled_at,
                    activate_plan_id,
                    session_id,
                    exec_name,
                    needs_confirm,
                )
            )

    # -- Redis cache sync + session registration --
    for run_uuid, pkid, net_id, t_id, a_id, sh_id, h_id, instr, tmo, ssi, pru, pt, nt, sat, api, sess_id, en, nc in created:
        _sync_entity(
            "execution",
            run_uuid,
            {
                "pkid": pkid,
                "task_run_uuid": run_uuid,
                "network_id": net_id,
                "project_id": project_id,
                "plan_id": plan_id,
                "activate_plan_id": api or "",
                "task_id": t_id,
                "agent_id": a_id,
                "send_agent_id": caller_agent_id,
                "send_host_id": sh_id,
                "send_session_id": ssi or "",
                "host_id": h_id,
                "run_status": "draft",
                "session_id": sess_id or f"agent:{a_id}:{run_uuid}",
                "exec_instruction": instr,
                "exec_name": en or "",
                "timeout_seconds": str(tmo),
                # * needs_confirm must go into the cache: the watchdog's _activate_next uses the Redis hash to decide
                #   "whether the preceding step is awaiting release" (prev_needs_confirm == "1"),
                #   and writing only MySQL without the cache -> downstream is treated as activatable, making the release semantics hollow.
                "needs_confirm": str(nc),
                "parent_run_uuid": pru or "",
                "prev_task": pt or "",
                "next_task": nt or "",
                "scheduled_at": sat or "",
            },
        )
        # plan step sessions are registered too (a row is created at draft time, activation only counts)
        try:
            _tl_rows = execute_sql(
                "SELECT task_labs FROM agx_task_base WHERE task_id = %s",
                (t_id,),
            )
            _tl = (_tl_rows[0].get("task_labs") or "").strip() if _tl_rows else ""
            ensure_session(
                session_id=sess_id or f"agent:{a_id}:{run_uuid}",
                name_hint="",
                agent_id=a_id,
                project_id=project_id,
                task_labs=_tl,
                run_uuid=run_uuid,
                creator_agent_id=caller_agent_id,
                network_id=net_id,
            )
        except Exception as _e:
            log(f"ensure_session failed for plan step {run_uuid}: {_e}", "WARN")
        run_uuids.append(run_uuid)

    return json.dumps(
        {"ok": True, "plan_id": plan_id, "steps_deployed": len(run_uuids), "run_uuids": run_uuids},
        indent=2,
        ensure_ascii=False,
    )


# * 2026-10-21 split: a private implementation (no decorator) for internal reuse, a public facade guards the outside.
#   Reason: the pipeline methods (agx_create_agent_pipeline / agx_create_project_pipeline / agx_daily_log)
#   need to reuse this logic internally, but they are public entry points with @require_level(0|3). Calling the public
#   agx_plan_create (L5) directly would let the decorator block based on the [caller's] permission -> agent_manager (agentfather,
#   L0-4) would be wrongly rejected when calling a L0 pipeline ("plan_create permission denied level 5").
#   Exactly the same pattern as the existing _ao_dispatch_task / _ao_quick_cmd_send.
#   Security unchanged: _check_pipeline_caller at the pipeline entry does a per-role check inside the function body,
#   it does not rely on this function's decorator.
def _ao_plan_create(
    caller_agent_id: str, plan_path: str, parent_plan_id: str = "", host_id: str = ""
) -> str:
    """
    [Create plan] Read and validate a JSON file, write it into agx_plan, without creating executions.

    Separated from agx_plan_deploy: first produce a plan draft, then deploy after human review.
    Chained use without review is also supported: agx_plan_create -> agx_plan_deploy.

    Internal flow:
    1. Read the JSON file and validate its schema
    2. Expand children -> fill in the child steps' parent_run_uuid
    3. Validate topology integrity (mutual exclusion, orphan tasks, etc.)
    4. INSERT INTO agx_plan (store the raw JSON + metadata, status=draft)
    5. Sync the Redis cache
    6. Return plan_id + a step summary

    Args:
        caller_agent_id (str): caller agent ID.
        plan_path (str): path of the JSON plan file; see docs/internal/project-management.md section 2 for the format.
        parent_plan_id (str): parent plan ID, used to relate a child plan to a master plan (optional).
        host_id (str): the host the plan belongs to (optional). * Decided by Lao Feng on 2026-10-29:
            semantics = "the host responsible for dispatching this plan + receiving plan_completed",
            **not** "the host that created the plan". Default empty -> falls back to the caller's host (old behavior).
            Why it is needed: pipeline methods hard-code the activator as agx_core, and both the activation and the
            plan_completed notification happen on the activator's host (_send_plan_completed talks straight to the
            local gateway). If host_id followed the caller, then as soon as the caller is not on core's host ->
            the creating dog wins the notification right but cannot reach core's session (after 3 retries ~ 6s it
            gives up forever) -> core never finishes the task -> times out after 7200s, and the initiator receives a
            bogus alert 2 hours later.
            Therefore pipelines must pass host_id=_resolve_send_host_id(SYS_CORE).

    * Optional step fields (excerpt; they map one-to-one to the keys read by _deploy_executions):
        run_uuid / agent_id / instruction / task_id / exec_name / session_key /
        timeout_seconds / prev_task / next_task / children / parent_run_uuid /
        scheduled_at / activate_plan /
        needs_confirm (int, 0|1, default 0): * added 2026-11-06.
            1 = this step is not done when it completes: agx_finish_task does not write succeeded even on success,
            but lands run_status='awaiting_confirm' and waits for a human to call agx_confirm_task;
            when timeout_seconds is not given explicitly, the timeout is raised to at least 1 day
            (see _timeout_for_confirm), so it is not marked timed out before a human looks at it.
            Illegal values (other than 0/1) fail at [validation] time (_validate_and_expand_plan_steps), so they
            never blow up later at deploy/insert time.

    Returns:
        str: JSON containing plan_id, steps_count, entry_tasks[], plan_status.
    """
    import pathlib

    try:
        plan_path_obj = pathlib.Path(plan_path)
        if not plan_path_obj.is_file():
            return json.dumps({"error": f"Plan file not found: {plan_path}"}, indent=2)
        plan = json.loads(plan_path_obj.read_text(encoding="utf-8"))

        # -- validate top-level fields --
        for f in ("plan_id", "project_id", "description", "steps"):
            if f not in plan:
                return json.dumps({"error": f"Missing required field: {f}"}, indent=2)

        plan_id = plan["plan_id"]
        project_id = plan["project_id"]

        # -- duplicate check --
        existing = execute_sql("SELECT 1 FROM agx_plan WHERE plan_id = %s", (plan_id,))
        if existing:
            return json.dumps({"error": f"plan_id '{plan_id}' already exists. Cancel or delete it first."}, indent=2)

        # -- validate step topology --
        err, step_map, validated_steps, entry_tasks = _validate_and_expand_plan_steps(plan["steps"])
        if err:
            return err

        # -- write agx_plan --
        git_branch = plan.get("git_branch", "") or ""
        parent_plan_id_val = parent_plan_id if parent_plan_id else None
        activate_mode = plan.get("activate_mode", "watchdog") or "watchdog"
        start_task_run_uuid = plan.get("start_task_run_uuid", "") or None
        # the host the plan belongs to (decides which machine's watchdog schedules it, and who receives plan_completed).
        #   The watchdog isolates plans by it: a machine only schedules plans registered to itself,
        #   avoiding several watchdogs scanning the same plan -> duplicate activation, log spam, and a notification right that cannot be delivered.
        # * 2026-10-29 semantics fixed: host_id should equal the [activator] host, not the [creator] host.
        #   - whoever creates also activates (agx_daily_log etc.): the two are the same, the default fallback is correct
        #   - pipeline methods (the activator is hard-coded to agx_core): pass host_id explicitly, see each call site
        plan_host_id = (host_id or "").strip() or _resolve_send_host_id(caller_agent_id)
        execute_sql(
            """INSERT INTO agx_plan (plan_id, parent_plan_id, project_id, created_by, host_id, description, git_branch, plan_status, steps_json, activate_mode, start_task_run_uuid)
               VALUES (%s, %s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s)""",
            (
                plan_id,
                parent_plan_id_val,
                project_id,
                caller_agent_id,
                plan_host_id or None,
                plan.get("description", ""),
                git_branch,
                json.dumps(plan, ensure_ascii=False),
                activate_mode,
                start_task_run_uuid,
            ),
            commit=True,
        )

        # Redis
        redis_data = {
            "plan_id": plan_id,
            "project_id": project_id,
            "created_by": caller_agent_id,
            "host_id": plan_host_id,
            "description": plan.get("description", ""),
            "git_branch": git_branch,
            "plan_status": "draft",
            "activate_mode": activate_mode,
        }
        if parent_plan_id_val:
            redis_data["parent_plan_id"] = parent_plan_id_val
        if start_task_run_uuid:
            redis_data["start_task_run_uuid"] = start_task_run_uuid
        _sync_entity("plan", plan_id, redis_data)

        return json.dumps(
            {
                "plan_id": plan_id,
                "steps_count": len(validated_steps),
                "entry_tasks": entry_tasks,
                "plan_status": "draft",
                "hint": "计划已创建为 draft。调用 agx_plan_deploy 部署 execution,或 agx_activate_plan 激活。",
            },
            indent=2,
            ensure_ascii=False,
        )

    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON: {e}"}, indent=2)
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_plan_create", e)}, indent=2)


@mcp.tool()
@require_level(0)
def agx_ack_notice(caller_agent_id: str, event_id: str) -> str:
    """[Acknowledge notice] Acknowledge after reading a wakeup/alert message, proving "I really did read it".

    * Division of labour with agx_ack_receipt (decided by Lao Feng on 2026-10-28):
        agx_ack_receipt -> signs a [receipt execution]; axis is agx_task_execution.reply_read/reply_read_at
        agx_ack_notice  -> signs a [notice event];      axis is agx_task_execution_event.ack_state/ack_at

    Why they are split: wakeups/alerts [do not create an execution] (to avoid alerts begetting alerts and to keep
    the task table clean), so "was it read" can only be recorded on the event table.

    Ownership: you may only acknowledge notices sent to you (event.to_agent == caller_agent_id).
    Idempotent: a repeated acknowledgement only refreshes ack_at.

    Args:
        caller_agent_id: caller agent ID
        event_id: event ID (evt_xxx, given in the notice body)

    Returns:
        JSON string (including event_type / task_run_uuid / ack_at)
    """
    import json as _json
    from datetime import datetime as _dt

    ev = (event_id or "").strip()
    if not ev.startswith("evt_"):
        return _json.dumps({"error": "event_id must look like 'evt_xxx'"}, ensure_ascii=False)

    rows = execute_sql(
        "SELECT to_agent, event_type, task_run_uuid, ack_state, created_at "
        "FROM agx_task_execution_event WHERE event_id = %s",
        (ev,),
        commit=False,
    )
    if not rows:
        return _json.dumps({"error": f"event '{ev}' not found"}, ensure_ascii=False)

    row = rows[0]
    to_agent = (row.get("to_agent") or "").strip()
    if to_agent and caller_agent_id != to_agent:
        return _json.dumps(
            {"error": f"权限不足: 该通知发给 '{to_agent}'，不是 '{caller_agent_id}'"},
            ensure_ascii=False,
        )

    # -- receipt events must go through agx_ack_receipt (2026-10-31) --
    #   event_type='receipt' is only the log line for "the receipt was sent" (see the one recorded in agx_core
    #   when is_reply_to is non-empty); what actually decides "whether the watchdog resends" is
    #   agx_task_execution.reply_read / reply_read_at / ack_state, not this log line.
    #   Sign it with this method: the log line becomes read while the execution axis stays 0 -> the watchdog decides "not read" and keeps resending,
    #   while the caller sees ack_state='read' and thinks it is signed -- the two axes diverge and nobody reports an error.
    #   Measured: 15 cases already (event=read while reply_read=0). So it is rejected outright here with a pointer to the right method.
    if (row.get("event_type") or "").strip() == "receipt":
        return _json.dumps(
            {
                "error": "这是回执事件(event_type=receipt)，不能用 agx_ack_notice 签收",
                "use_instead": "agx_ack_receipt",
                "task_run_uuid": row.get("task_run_uuid") or "",
                "hint": (
                    "回执的签收轴在 agx_task_execution.reply_read；"
                    "请调用 agx_ack_receipt(caller_agent_id=..., "
                    "task_run_uuid=<上面的 task_run_uuid>)"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )

    execute_sql(
        "UPDATE agx_task_execution_event SET ack_state='read', ack_at=NOW() "
        "WHERE event_id = %s AND ack_state <> 'read'",
        (ev,),
        commit=True,
    )
    _ack_at = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    return _json.dumps(
        {
            "event_id": ev,
            "event_type": row.get("event_type"),
            "task_run_uuid": row.get("task_run_uuid"),
            "ack_state": "read",
            "ack_at": _ack_at,
            "already_read": (row.get("ack_state") or "") == "read",
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
@require_level(0)
def agx_task_events(caller_agent_id: str, task_run_uuid: str, limit: int = 50) -> str:
    """[Task timeline] List the wakeup/alert/receipt/acknowledgement events of a task in chronological order.

    * This is the only authoritative query entry for "how many times I nudged it, when, and whether it read it".
      Wakeups/alerts do not create executions, so their history is not on agx_task_execution -- it is all in this event table.

    Typical use: after a task times out, look at the timeline first, then decide whether to redeliver or terminate.

    Args:
        caller_agent_id: caller agent ID
        task_run_uuid: the task ID being watched
        limit: maximum number of entries to return (default 50, ascending)

    Returns:
        JSON: {task_run_uuid, count, events: [...]}
    """
    import json as _json

    run = (task_run_uuid or "").strip()
    if not run:
        return _json.dumps({"error": "task_run_uuid required"}, ensure_ascii=False)

    try:
        n = max(1, min(int(limit or 50), 200))
    except Exception:
        n = 50

    rows = execute_sql(
        "SELECT event_id, event_type, level, from_agent, to_agent, to_session, channel, "
        "reason, delivery_state, delivered_at, ack_state, ack_at, created_at "
        "FROM agx_task_execution_event WHERE task_run_uuid = %s "
        "ORDER BY created_at ASC, event_id ASC LIMIT %s",
        (run, n),
        commit=False,
    )
    # * fixed 2026-11-05 (XHOST measurement): this tool's description claims to be "the only authoritative entry for whether it read it",
    #   but "read or not" only lands in agx_task_execution; the event table never records it (this is the deliberate
    #   design of 2026-10-29: alerts create no execution so only events can record them; receipts have an execution, so it is recorded on the row).
    #   Result: the timeline always showed ack_state=none, making people chase a task that had already been acknowledged.
    #
    #   Fix: change only the **read** side, not the **write** side. Bring the execution's three acknowledgement columns along,
    #   so one view shows both "how many times it was nudged" and "whether it was actually read".
    _exec_ack = {}
    try:
        _er = execute_sql(
            "SELECT reply_read, reply_read_at, reply_read_last_at, ack_state, needs_reply "
            "FROM agx_task_execution WHERE task_run_uuid = %s",
            (run,),
            commit=False,
        )
        if _er:
            _exec_ack = {
                "reply_read": _er[0].get("reply_read"),
                "reply_read_at": _er[0].get("reply_read_at"),
                "reply_read_last_at": _er[0].get("reply_read_last_at"),
                "ack_state": _er[0].get("ack_state"),
                "needs_reply": _er[0].get("needs_reply"),
            }
    except Exception as _e:
        _exec_ack = {"error": f"execution ack lookup failed: {_e}"}

    return _json.dumps(
        {
            "task_run_uuid": run,
            "count": len(rows or []),
            # * source of truth for acknowledgement: read this, not events[].ack_state (that column only serves
            #   alerts/wakeups that create no execution; a receipt's acknowledgement lands on the execution row)
            "execution_ack": _exec_ack,
            "events": rows or [],
        },
        ensure_ascii=False,
        indent=2,
        default=str,
    )


@mcp.tool()
@require_level(5)
def agx_plan_create(caller_agent_id: str, plan_path: str, parent_plan_id: str = "") -> str:
    """[Create plan] Public facade (L5) -- a thin wrapper over _ao_plan_create.

    External permission remains L5 (the boundary of global orchestration capability is unchanged).
    For internal pipeline reuse, call _ao_plan_create, so this decorator does not block on the caller's permission.

    * A step supports the optional field needs_confirm (0|1, default 0): =1 means that after the step succeeds it
      does not land on succeeded but on awaiting_confirm, and needs a human to call agx_confirm_task to release it.
      See the docstrings of _ao_plan_create / _ao_plan_deploy for the full step field table.
    """
    return _ao_plan_create(caller_agent_id, plan_path, parent_plan_id)


# * split 2026-10-21, for the same reason as _ao_plan_create.
def _ao_plan_deploy(
    caller_agent_id: str,
    plan_id: str = "",
    plan_path: str = "",
    task_run_uuid: str = "",
    send_session_id: str = "",
) -> str:
    """
    [Deploy plan] Write the plan's steps into agx_task_execution.

    Two ways to call:
    1. Chained: call agx_plan_create first, take the plan_id, then agx_plan_deploy(plan_id=...).
       -> reads steps from agx_plan.steps_json and deploys them.
    2. Direct file: agx_plan_deploy(plan_path="/path/to/plan.json"); it creates and deploys internally.

    Internal flow:
    1. Read the plan (from the DB or a JSON file)
    2. Validate step topology (fill in parent_run_uuid, check mutual exclusion)
    3. Bulk INSERT agx_task_execution (status=draft)
    4. Sync the Redis cache
    5. Return a summary

    Args:
        caller_agent_id (str): caller agent ID.
        plan_id (str, optional): an already-created plan_id (chained mode).
        plan_path (str, optional): path of the JSON plan file (direct file mode).
        task_run_uuid (str, optional): task_run_uuid of the current execution.
            The receipt of every step in the plan is delivered to this execution's session.
            - when handling a task message, extract task_run_uuid from the message body and pass it
            - when starting from a conversation (no task_run_uuid), do not pass it and fall back to the PM main session
        send_session_id (str, optional): * added 2026-10-12. Explicitly sets each step's send_session_id (the sender session).
            Takes priority over the task_run_uuid derivation (e.g. agx_daily_log passes the initiator session straight through).
            When empty it falls back to the task_run_uuid derivation, then to agent:{caller}:main.

    * Optional step fields (read from steps_json; this method consumes them key by key):
        run_uuid / agent_id / instruction / task_id / exec_name / session_key /
        timeout_seconds / prev_task / next_task / children / parent_run_uuid /
        scheduled_at / activate_plan /
        needs_confirm (int, 0|1, default 0): * added 2026-11-06.
            1 = after this step succeeds it does not land on succeeded but on awaiting_confirm, and needs a human to
            call agx_confirm_task to release it; that intermediate state does not count as plan completion
            (_check_plan_completion / the watchdog's _is_plan_completed both treat awaiting_confirm as unfinished),
            and only after release can it become completed.
            When timeout_seconds is not given explicitly the timeout is raised to at least 1 day (_timeout_for_confirm).

    Returns:
        str: JSON containing plan_id, steps_deployed, run_uuids[].
    """
    import pathlib

    try:
        # -- mode detection --
        if plan_id and not plan_path:
            # chained mode: read the already-created plan from the DB
            plan_rows = execute_sql(
                "SELECT * FROM agx_plan WHERE plan_id = %s",
                (plan_id,),
            )
            if not plan_rows:
                return json.dumps({"error": f"plan '{plan_id}' not found. Call agx_plan_create first."}, indent=2)

            plan_row = plan_rows[0]
            plan_status = plan_row.get("plan_status", "")
            if plan_status == "active":
                return json.dumps(
                    {"error": f"plan '{plan_id}' is already active. Cancel first to re-deploy."}, indent=2
                )

            # check whether executions have already been deployed
            existing_exec = execute_sql(
                "SELECT COUNT(*) AS cnt FROM agx_task_execution WHERE plan_id = %s",
                (plan_id,),
            )
            if existing_exec and existing_exec[0]["cnt"] > 0:
                return json.dumps(
                    {
                        "error": f"plan '{plan_id}' already has {existing_exec[0]['cnt']} executions. Cancel plan first to re-deploy."
                    },
                    indent=2,
                )

            steps_json = plan_row.get("steps_json", "")
            if not steps_json:
                return json.dumps({"error": f"plan '{plan_id}' has no steps_json"}, indent=2)

            plan = json.loads(steps_json)
            project_id = plan_row.get("project_id", "")
        elif plan_path:
            # direct-file mode: read from a JSON file
            plan_path_obj = pathlib.Path(plan_path)
            if not plan_path_obj.is_file():
                return json.dumps({"error": f"Plan file not found: {plan_path}"}, indent=2)
            plan = json.loads(plan_path_obj.read_text(encoding="utf-8"))
            plan_id = plan.get("plan_id", "")
            project_id = plan.get("project_id", "")

            if not plan_id or not project_id:
                return json.dumps({"error": f"Plan file {plan_path} must include plan_id and project_id"}, indent=2)

            # duplicate check
            existing = execute_sql("SELECT 1 FROM agx_plan WHERE plan_id = %s", (plan_id,))
            if not existing:
                # the plan has not been created yet; create it first
                create_result = json.loads(_ao_plan_create(caller_agent_id, plan_path))
                if "error" in create_result:
                    return json.dumps(create_result, indent=2)
        else:
            return json.dumps({"error": "Must provide plan_id (chain mode) or plan_path (file mode)"}, indent=2)

        steps = plan.get("steps", [])
        if not steps:
            return json.dumps({"error": "plan has no steps"}, indent=2)

        # -- validate step topology --
        err, step_map, _, _ = _validate_and_expand_plan_steps(steps)
        if err:
            return err

        # -- deploy executions --
        return _deploy_executions(
            caller_agent_id, plan_id, project_id, steps, step_map, task_run_uuid, send_session_id
        )

    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid JSON: {e}"}, indent=2)
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_plan_deploy", e)}, indent=2)


@mcp.tool()
@require_level(5)
def agx_plan_deploy(
    caller_agent_id: str,
    plan_id: str = "",
    plan_path: str = "",
    task_run_uuid: str = "",
    send_session_id: str = "",
) -> str:
    """[Deploy plan] Public facade (L5) -- a thin wrapper over _ao_plan_deploy.

    External permission remains L5. For internal pipeline reuse, call _ao_plan_deploy.

    * A step supports the optional field needs_confirm (0|1, default 0): =1 means that after the step succeeds it
      does not land on succeeded but on awaiting_confirm, and needs a human to call agx_confirm_task to release it;
      that intermediate state does not count as plan completion. See the docstring of _ao_plan_deploy for the full
      step field table.
    """
    return _ao_plan_deploy(caller_agent_id, plan_id, plan_path, task_run_uuid, send_session_id)


@mcp.tool()
@require_level(5)
def agx_book_orchestrate_chapter(
    caller_agent_id: str,
    book_root: str,
    chapter_path: str,
    project_id: str,
    stage: str = "write",
    round_num: int = 1,
) -> str:
    """
    [Single chapter orchestration] Given a chapter draft path, automatically do Git -> split -> plan JSON generation -> workspace creation.

    Pure logic, zero LLM involvement. Calls ChapterOrchestrator.run().

    Flow:
    1. Validate input (file exists, directory writable, project_id valid)
    2. Git branch management (optional, use_git=False)
    3. Call split_by_h2.py to physically split the chapter
    4. Read manifest.json -> generate the plan JSON per stage
    5. Create the drafts directory + status.json
    6. Return plan_json (for the caller to agx_plan_create + agx_plan_deploy)

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        book_root (str): absolute path of the manuscript root.
        chapter_path (str): absolute path of the chapter .md source file.
        project_id (str): project ID.
        stage (str): pipeline stage, default write.
            - split: split into H2 sections only, produce manifest.json + TOC.txt, no plan
            - write: produce a pure writing plan (quality gate + N parallel writers), no review
            - review: produce a review plan; round_num controls the review round (default 1)
            - merge: produce a merge plan that combines all writer output into the final chapter
            - full: the complete pipeline (write -> three layered review rounds/revise/dry read -> merge) in one go.
              * The three rounds are **not the same thing run three times**; they layer up as "structure -> facts -> language":
                 round 1 structure and logic (argument chain, paragraph duty, drift, transitions)
                 round 2 facts and terminology (verifiable data, consistent terms, contradictions)
                 round 3 language and style (punctuation, consistent wording, repetition, readability)
              Each round also produces a different artifact (diagnosis table / checklist / unification list); issues
              belonging to other layers go into a "handed to later rounds" section. See tools/book_orchestrate.py: _ROUND_FOCUS.
        round_num (int): review round (only effective when stage=review), default 1.


    Returns:
        JSON string: {"success": bool, "plan_id": "...", "stage": "...", ...}
    """
    # clear the Python module cache to make sure the latest version is loaded
    # presumably unnecessary
    # for km in list(sys.modules.keys()):
    #     if "book_orchestrate" in km:
    #         del sys.modules[km]
    from tools.book_orchestrate import ChapterOrchestrator

    try:
        orch = ChapterOrchestrator(
            book_root=book_root,
            chapter_path=chapter_path,
            project_id=project_id,
            stage=stage,
            round_num=round_num,
            use_git=False,
        )
        file_result = orch.run()
        if not file_result.get("success"):
            return json.dumps(file_result, ensure_ascii=False, indent=2)

        # -- stage=split: split only, generate no plan, return the split result directly (fixed 2026-10-25) --
        # Background: the split branch produces no plan_json_path, and the old code read that field unconditionally ->
        # it reported "plan JSON file missing: " (empty after the colon) even though the split had actually succeeded.
        if stage == "split":
            return json.dumps(file_result, ensure_ascii=False, indent=2)

        plan_path = file_result.get("plan_json_path", "")
        if not plan_path or not os.path.isfile(plan_path):
            return json.dumps(
                {"success": False, "errors": [f"plan JSON 文件缺失: {plan_path}"]},
                ensure_ascii=False,
                indent=2,
            )

        # -- read the plan JSON file --
        with open(plan_path, encoding="utf-8") as f:
            plan = json.load(f)

        plan_id = plan.get("plan_id", "")

        # -- duplicate check --
        existing = executeSql("SELECT 1 FROM agx_plan WHERE plan_id = %s", (plan_id,))
        if existing:
            return json.dumps(
                {"error": f"plan_id '{plan_id}' already exists. 请先清理旧计划。"},
                ensure_ascii=False,
                indent=2,
            )

        # -- validate step topology --
        err, step_map, validated_steps, entry_tasks = _validate_and_expand_plan_steps(plan["steps"])
        if err:
            return err

        # -- write agx_plan --
        git_branch = file_result.get("git_branch", "") or ""
        activate_mode = plan.get("activate_mode", "watchdog") or "watchdog"
        start_task_run_uuid = plan.get("start_task_run_uuid", "") or None
        _plan_host = _resolve_send_host_id(caller_agent_id)
        executeSql(
            """INSERT INTO agx_plan (plan_id, project_id, created_by, host_id, description, git_branch, plan_status, steps_json, activate_mode, start_task_run_uuid)
               VALUES (%s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s)""",
            (
                plan_id,
                project_id,
                caller_agent_id,
                _plan_host or None,
                plan.get("description", ""),
                git_branch if git_branch != "(none)" else "",
                json.dumps(plan, ensure_ascii=False),
                activate_mode,
                start_task_run_uuid,
            ),
            commit=True,
        )

        # Redis sync
        redis_data = {
            "plan_id": plan_id,
            "project_id": project_id,
            "created_by": caller_agent_id,
            "host_id": _plan_host,
            "description": plan.get("description", ""),
            "git_branch": git_branch if git_branch != "(none)" else "",
            "plan_status": "draft",
            "activate_mode": activate_mode,
        }
        if start_task_run_uuid:
            redis_data["start_task_run_uuid"] = start_task_run_uuid
        _sync_entity("plan", plan_id, redis_data)

        # -- one step: deploy the executions (draft) --
        deploy_result = _deploy_executions(caller_agent_id, plan_id, project_id, validated_steps, step_map)
        deploy_obj = json.loads(deploy_result) if isinstance(deploy_result, str) else deploy_result
        if not deploy_obj.get("ok"):
            return (
                deploy_result
                if isinstance(deploy_result, str)
                else json.dumps(deploy_result, ensure_ascii=False, indent=2)
            )

        return json.dumps(
            {
                "success": True,
                "plan_id": plan_id,
                "chapter": orch.chapter,
                "git_branch": orch.branch_name or "",
                "split_dir": orch.split_dir,
                "drafts_dir": orch.drafts_dir,
                "plan_status": "draft",
                "steps_count": len(validated_steps),
                "executions_deployed": deploy_obj.get("steps_deployed", 0),
                "entry_tasks": entry_tasks,
                "hint": "计划 + execution 已创建为 draft。调用 agx_activate_plan 激活。",
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        import traceback

        return json.dumps(
            {
                "success": False,
                "errors": [f"{type(e).__name__}: {e}"],
                "traceback": traceback.format_exc(),
            },
            ensure_ascii=False,
            indent=2,
        )


@mcp.tool()
@require_level(5)
def agx_book_orchestrate_multi_chapter(
    caller_agent_id: str,
    book_root: str,
    chapters: str,
    parallel: bool = False,
) -> str:
    """
    [Multi-chapter orchestration] N chapters -> N sub-plans + 1 master plan (a plan of plans).

    Wraps agx_book_orchestrate_chapter:
    1. Call agx_book_orchestrate_chapter for each chapter to generate a sub-plan JSON
    2. Build the master plan JSON, where each step uses the plan_execute task to activate a sub-plan
    3. Insert the master plan and create the execution

    Serial/parallel:
    - parallel=False (serial by default): steps form a chain via prev_task/next_task
    - parallel=True: all steps are independent and the watchdog scans them at once

    Args:
        caller_agent_id (str): caller agent ID.
        book_root (str): absolute path of the manuscript root.
        chapters (str): JSON string, the chapter list.
            Format: [{"chapter_path": "...", "project_id": "...",
                    "stage": "full", "round_num": 1}, ...]
            stage defaults to "full" (the complete pipeline); allowed values:
                - "split": split into H2 sections only, produce manifest.json + TOC.txt
                - "write": writing only (quality gate + N parallel writers)
                - "review": review only; round_num controls the round
                - "merge": merge all writer output only
                - "full": the complete pipeline (write -> three layered review rounds/revise/dry read -> merge)
                  * the three rounds layer as "structure -> facts -> language"; each round does something different (not a repeat)
            round_num is only effective when stage="review", default 1.
            See the stage parameter of agx_book_orchestrate_chapter.
        parallel (bool): whether to run in parallel. Default False (serial).

    Returns:
        JSON string: {
            "success": true,
            "master_plan_id": "plan_multi_Nchapters",
            "sub_plan_ids": ["plan_ch03_xxx", "plan_ch04_xxx"],
            "steps_count": N,
            "parallel": false,
            "hint": "the master plan has been created as a draft."
        }
    """
    from tools.book_orchestrate import MultiChapterOrchestrator

    try:
        # -- 1. parse the chapters JSON --
        chapters_list = json.loads(chapters)
        if not isinstance(chapters_list, list) or not chapters_list:
            return json.dumps(
                {"success": False, "errors": ["chapters 必须是非空 JSON 数组"]},
                ensure_ascii=False,
                indent=2,
            )

        # -- 2. generate a sub-plan for each chapter (call agx_book_orchestrate_chapter) --
        sub_plan_ids = []
        sub_errors = []
        for i, ch in enumerate(chapters_list):
            chapter_path = ch.get("chapter_path", "")
            project_id = ch.get("project_id", "")
            stage = ch.get("stage", "full")
            round_num = ch.get("round_num", 1)

            chapter_name = os.path.splitext(os.path.basename(chapter_path))[0]
            log(f"[MULTI_CHAPTER] [{i + 1}/{len(chapters_list)}] 编排 {chapter_name}(stage={stage})")

            sub_result_str = agx_book_orchestrate_chapter(
                caller_agent_id=caller_agent_id,
                book_root=book_root,
                chapter_path=chapter_path,
                project_id=project_id,
                stage=stage,
                round_num=round_num,
            )
            sub_result = json.loads(sub_result_str)

            if sub_result.get("success"):
                sub_plan_id = sub_result.get("plan_id", "")
                sub_plan_ids.append(sub_plan_id)
                chapters_list[i]["sub_plan_id"] = sub_plan_id
                log(f"[MULTI_CHAPTER]    子 plan: {sub_plan_id}")
            else:
                sub_err = sub_result.get("error") or sub_result.get("errors")
                sub_errors.append(
                    {
                        "chapter": chapter_name,
                        "error": sub_err,
                    }
                )

        if sub_errors:
            return json.dumps(
                {"success": False, "sub_plan_ids": sub_plan_ids, "errors": [{"sub_plan_errors": sub_errors}]},
                ensure_ascii=False,
                indent=2,
            )

        # -- 3. build the master plan JSON --
        orch = MultiChapterOrchestrator(
            book_root=book_root,
            chapters=chapters_list,
            parallel=parallel,
        )
        master_plan = orch.build_master_plan()
        master_plan_id = master_plan["plan_id"]
        project_id = chapters_list[0].get("project_id", "")

        # -- 4. duplicate check --
        existing = executeSql("SELECT 1 FROM agx_plan WHERE plan_id = %s", (master_plan_id,))
        if existing:
            return json.dumps(
                {"error": f"总计划 '{master_plan_id}' already exists. 请先清理。"},
                ensure_ascii=False,
                indent=2,
            )

        # -- 5. validate step topology --
        err, step_map, validated_steps, entry_tasks = _validate_and_expand_plan_steps(master_plan["steps"])
        if err:
            return err

        # -- 6. write agx_plan --
        _master_host = _resolve_send_host_id(caller_agent_id)
        executeSql(
            """INSERT INTO agx_plan (plan_id, project_id, created_by, host_id, description, plan_status, steps_json, activate_mode)
               VALUES (%s, %s, %s, %s, %s, 'draft', %s, 'watchdog')""",
            (
                master_plan_id,
                project_id,
                caller_agent_id,
                _master_host or None,
                master_plan.get("description", ""),
                json.dumps(master_plan, ensure_ascii=False),
            ),
            commit=True,
        )

        # Redis sync
        _sync_entity(
            "plan",
            master_plan_id,
            {
                "plan_id": master_plan_id,
                "project_id": project_id,
                "created_by": caller_agent_id,
                "host_id": _master_host,
                "description": master_plan.get("description", ""),
                "plan_status": "draft",
                "activate_mode": "watchdog",
            },
        )

        # -- 7. deploy the executions --
        deploy_result = _deploy_executions(caller_agent_id, master_plan_id, project_id, validated_steps, step_map)
        deploy_obj = json.loads(deploy_result) if isinstance(deploy_result, str) else deploy_result
        if not deploy_obj.get("ok"):
            return (
                deploy_result
                if isinstance(deploy_result, str)
                else json.dumps(deploy_result, ensure_ascii=False, indent=2)
            )

        return json.dumps(
            {
                "success": True,
                "master_plan_id": master_plan_id,
                "sub_plan_ids": sub_plan_ids,
                "steps_count": len(validated_steps),
                "chapters_count": len(chapters_list),
                "parallel": parallel,
                "hint": "总计划 + 子计划已创建为 draft。调用 agx_activate_plan(plan_id=总计划ID) 激活。子计划由 plan_execute task 自动激活。",
            },
            ensure_ascii=False,
            indent=2,
        )

    except Exception as e:
        import traceback

        return json.dumps(
            {
                "success": False,
                "errors": [f"{type(e).__name__}: {e}"],
                "traceback": traceback.format_exc(),
            },
            ensure_ascii=False,
            indent=2,
        )


@mcp.tool()
@require_level(5)
def agx_plan_query(caller_agent_id: str, plan_id: str, limit: int = 50, offset: int = 0) -> str:
    """
    [Query one plan] Get plan metadata + the step status list (paginated).

    Args:
        caller_agent_id (str): caller agent ID.
        plan_id (str): plan ID (plan_xxx).
        limit (int): steps per page, default 50.
        offset (int): paging offset, default 0.

    Returns:
        str: JSON containing plan metadata + the step list (with each step's status) + paging info.
    """
    log(f"[PLAN_QUERY] called by {caller_agent_id}, plan={plan_id}, level=5")
    try:
        # query the plan
        plan_rows = execute_sql(
            "SELECT * FROM agx_plan WHERE plan_id = %s",
            (plan_id,),
        )
        if not plan_rows:
            return json.dumps({"error": f"plan '{plan_id}' not found"}, indent=2)

        plan = plan_rows[0]

        # total
        total_rows = execute_sql(
            "SELECT COUNT(*) AS cnt FROM agx_task_execution WHERE plan_id = %s",
            (plan_id,),
        )
        total = total_rows[0]["cnt"] if total_rows else 0

        # status breakdown
        stats_rows = execute_sql(
            "SELECT run_status, COUNT(*) AS cnt FROM agx_task_execution WHERE plan_id = %s GROUP BY run_status",
            (plan_id,),
        )
        status_counts = {r["run_status"]: r["cnt"] for r in (stats_rows or [])}

        # query steps (paginated)
        exec_rows = execute_sql(
            "SELECT task_run_uuid, agent_id, task_id, run_status, exec_instruction, parent_run_uuid, prev_task, next_task, timeout_seconds, started_at, finished_at FROM agx_task_execution WHERE plan_id = %s ORDER BY pkid ASC LIMIT %s OFFSET %s",
            (plan_id, limit, offset),
        )

        steps = []
        for row in exec_rows:
            steps.append(
                {
                    "run_uuid": row["task_run_uuid"],
                    "agent_id": row["agent_id"],
                    "task_id": row["task_id"],
                    "run_status": row["run_status"],
                    "instruction": row["exec_instruction"],
                    "parent_run_uuid": row.get("parent_run_uuid") or "",
                    "prev_task": row.get("prev_task") or "",
                    "next_task": row.get("next_task") or "",
                    "timeout_seconds": row.get("timeout_seconds"),
                    "started_at": str(row["started_at"]) if row.get("started_at") else None,
                    "finished_at": str(row["finished_at"]) if row.get("finished_at") else None,
                }
            )

        result = {
            "plan_id": plan["plan_id"],
            "project_id": plan["project_id"],
            "created_by": plan["created_by"],
            "description": plan["description"],
            "plan_status": plan["plan_status"],
            "created_at": str(plan["created_at"]) if plan.get("created_at") else None,
            "completed_at": str(plan["completed_at"]) if plan.get("completed_at") else None,
            "total_steps": total,
            "status_counts": status_counts,
            "page": {"offset": offset, "limit": limit, "returned": len(steps)},
            "steps": steps,
        }

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_plan_query", e)}, indent=2)


@mcp.tool()
@require_level(5)
def agx_plan_list(caller_agent_id: str, project_id: str) -> str:
    """
    [Project plan overview] List every plan under a project and how far it got.

    Args:
        caller_agent_id (str): caller agent ID.
        project_id (str): project ID.

    Returns:
        str: a JSON array, each element with plan_id, description, plan_status, steps_count, created_at.
    """
    try:
        log(f"[PLAN_LIST] called by {caller_agent_id}, project={project_id}, level=5")
        plans = execute_sql(
            "SELECT p.*, COUNT(e.task_run_uuid) AS steps_count FROM agx_plan p LEFT JOIN agx_task_execution e ON p.plan_id = e.plan_id WHERE p.project_id = %s GROUP BY p.plan_id ORDER BY p.created_at DESC",
            (project_id,),
        )

        result = []
        for p in plans:
            result.append(
                {
                    "plan_id": p["plan_id"],
                    "description": p["description"],
                    "plan_status": p["plan_status"],
                    "steps_count": p["steps_count"],
                    "created_at": str(p["created_at"]) if p.get("created_at") else None,
                }
            )

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_plan_list", e)}, indent=2)


@mcp.tool()
@require_level(5)
def agx_plan_cancel(caller_agent_id: str, plan_id: str) -> str:
    """
    [Cancel plan] Mark every pending execution under the plan as cancelled.

    Running tasks are unaffected (they are allowed to finish naturally).

    Args:
        caller_agent_id (str): caller agent ID.
        plan_id (str): plan ID (plan_xxx).

    Returns:
        str: JSON containing the cancelled count and the plan's new status.
    """
    max_retries = 3
    last_error = None
    # -- resource-level ownership check: only the plan's owning agent / project admin / global roles may cancel --
    _deny = _check_plan_ownership(caller_agent_id, plan_id)
    if _deny:
        return json.dumps({"error": _deny, "permission_denied": True}, indent=2, ensure_ascii=False)
    for attempt in range(max_retries):
        try:
            # cancel every pending execution
            execute_sql(
                "UPDATE agx_task_execution SET run_status = 'cancelled' WHERE plan_id = %s AND run_status IN ('draft', 'pending')",
                (plan_id,),
                commit=True,
            )

            # update the plan status
            remaining = execute_sql(
                "SELECT COUNT(*) AS cnt FROM agx_task_execution WHERE plan_id = %s AND run_status = 'running'",
                (plan_id,),
            )
            running_count = remaining[0]["cnt"] if remaining else 0

            new_status = "cancelled" if running_count == 0 else "cancelling"
            execute_sql(
                "UPDATE agx_plan SET plan_status = %s, completed_at = NOW() WHERE plan_id = %s",
                (new_status, plan_id),
                commit=True,
            )

            # how many were actually cancelled
            cancelled_rows = execute_sql(
                "SELECT task_run_uuid, pkid FROM agx_task_execution WHERE plan_id = %s AND run_status = 'cancelled'",
                (plan_id,),
            )
            cancelled_count = len(cancelled_rows) if cancelled_rows else 0

            # sync Redis
            _sync_entity("plan", plan_id, {"plan_status": new_status})
            for row in cancelled_rows or []:
                _sync_entity("execution", row["task_run_uuid"], {"run_status": "cancelled", "pkid": row["pkid"]})

            result = {
                "plan_id": plan_id,
                "cancelled": cancelled_count,
                "plan_status": new_status,
            }
            return json.dumps(result, indent=2, ensure_ascii=False)

        except Exception as e:
            last_error = e
            err_msg = str(e)
            if ("1213" in err_msg or "Deadlock" in err_msg) and attempt < max_retries - 1:
                sleep_ms = random.randint(100, 300 + attempt * 200)
                log(
                    f"agx_plan_cancel deadlock on attempt {attempt + 1}/{max_retries}, retrying in {sleep_ms}ms: {e}",
                    "WARN",
                )
                time.sleep(sleep_ms / 1000)
                continue
            break

    return json.dumps({"error": _log_exception("agx_plan_cancel", last_error)}, indent=2)


@mcp.tool()
@require_level(5)
def agx_activate_plan(caller_agent_id: str, plan_id: str, task_run_uuid: str = "") -> str:
    """
    [Activate plan] Activate the plan according to activate_mode.

    Two modes:
    - watchdog: every step draft -> pending; the watchdog activates them as their children complete.
    - chain: behavior unified with watchdog; every step draft -> pending and the watchdog schedules them all.
      activate_mode is recorded in agx_plan so the watchdog can tell the advancement strategy apart.

    After a plan is deployed its tasks stay draft, and the watchdog does not scan them.

    task_run_uuid: task_run_uuid of the execution that activates this plan.
    Taken from the task_run_uuid field of the message.
    Whoever activates owns it to the end -- when the plan completes the watchdog uses it to find the notification target.
    May be left empty (default "") in modes other than activate_plan_id.

    Args:
        caller_agent_id (str): caller agent ID.
        plan_id (str): plan ID (plan_xxx).
        task_run_uuid (str, optional): activator execution ID.

    Returns:
        str: JSON containing plan_id, activated_count, activate_mode, plan_status.
    """
    try:
        # -- resource-level ownership check: only the plan's owning agent / project admin / global roles may activate --
        _deny = _check_plan_ownership(caller_agent_id, plan_id)
        if _deny:
            return json.dumps({"error": _deny, "permission_denied": True}, indent=2, ensure_ascii=False)

        # 1. read the plan status + activate_mode
        plan_rows = execute_sql(
            "SELECT plan_status, activate_mode, start_task_run_uuid FROM agx_plan WHERE plan_id = %s",
            (plan_id,),
        )
        if not plan_rows:
            return json.dumps({"error": f"Plan '{plan_id}' not found"}, indent=2)

        plan_row = plan_rows[0]
        plan_status = plan_row["plan_status"]
        if plan_status not in ("draft",):
            return json.dumps({"error": f"Plan '{plan_id}' status is '{plan_status}', not 'draft'"}, indent=2)

        activate_mode = plan_row.get("activate_mode", "watchdog") or "watchdog"
        start_task_run_uuid = plan_row.get("start_task_run_uuid", "") or ""

        # 2. all draft -> pending, the watchdog takes over uniformly (chain/watchdog behave the same)
        execute_sql(
            "UPDATE agx_task_execution SET run_status = 'pending' WHERE plan_id = %s AND run_status = 'draft'",
            (plan_id,),
            commit=True,
        )
        rows = execute_sql(
            "SELECT task_run_uuid, pkid FROM agx_task_execution WHERE plan_id = %s AND run_status = 'pending'",
            (plan_id,),
        )
        activated_count = len(rows) if rows else 0

        # 3. MySQL: update the plan status + task_run_uuid
        if task_run_uuid:
            execute_sql(
                "UPDATE agx_plan SET plan_status = 'activated', task_run_uuid = %s WHERE plan_id = %s",
                (task_run_uuid, plan_id),
                commit=True,
            )
        else:
            execute_sql(
                "UPDATE agx_plan SET plan_status = 'activated' WHERE plan_id = %s",
                (plan_id,),
                commit=True,
            )

        # 4. Redis: sync all tasks + the plan
        sync_data = {"plan_status": "activated", "activate_mode": activate_mode}
        if task_run_uuid:
            sync_data["task_run_uuid"] = task_run_uuid
        if start_task_run_uuid:
            sync_data["start_task_run_uuid"] = start_task_run_uuid
        _sync_entity("plan", plan_id, sync_data)
        for row in rows or []:
            _sync_entity("execution", row["task_run_uuid"], {"run_status": "pending", "pkid": row["pkid"]})

        result = {
            "plan_id": plan_id,
            "activated_count": activated_count,
            "activate_mode": activate_mode,
            "plan_status": "activated",
        }
        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_activate_plan", e)}, indent=2)


# ============================================================

# Tools: generic update -- dynamic fields + dual write MySQL & Redis

# ============================================================


# fields users may update (excluding primary keys / automatic fields / fields business logic cannot change)

# entity type -> MySQL table name mapping (used by _do_update to build the UPDATE dynamically)

TABLE_MAP = {
    "project": "agx_project",
    "host": "agx_host_inventory",
    "agent": "agx_agent_registry",
    "task": "agx_task_base",
    "execution": "agx_task_execution",
    "agent_group": "agx_agent_group",
    "agent_group_member": "agx_agent_group_member",
    "plan": "agx_plan",
}

# * the JSON column set (established 2026-10-23): _do_update must json.dumps these before writing
#
# Why they must be registered explicitly (not left to SQL introspection):
#   pymysql's escaper only knows str/bytes/int/float/None/list/tuple...:
#     - passing a dict -> TypeError: dict can not be used as parameter
#     - passing a list -> OperationalError 1241 "Operand should contain 1 column(s)"
#   both errors happen at mogrify time and the stack points into the mysql layer, looking like a connection/permission problem.
#
# Pre-existing latent bug (registered here, not fixed in this change):
#   _UPDATEABLE_FIELDS has long allowed writing mcp_config / agent_email_config,
#   but _do_update never json.dumps them -> passing a dict always blows up. Any agent calling
#   agx_update_agent(updates={'mcp_config': {...}}) gets a TypeError.
#   Once this set landed, those two fields were fixed as well.
#
# When adding a column, update all three: this set + the column type in agx.sql + the _UPDATEABLE_FIELDS allowlist.
_JSON_COLUMNS = {
    "mcp_config",
    "agent_email_config",
    "skills_allowlist",
    # 2026-11-04: agent platforms available on a host and how to reach them (JSON array)
    "platforms",
}


_UPDATEABLE_FIELDS = {
    "project": ["project_name", "project_abbr", "project_logo", "x_desc", "source_dir", "active_status"],
    "host": [
        "host_alias",
        "endpoint_ip",
        "ssh_port",
        "os_platform",
        # 2026-11-04: agent platforms available on this machine and how to reach them (JSON array)
        "platforms",
        "task_slots_limit",
        "active_task_count",
        "active_status",
        "presence_status",
        "x_desc",
    ],
    "agent": [
        "host_id",
        "agent_name",
        "description",
        "x_desc",
        "agent_role",
        "agent_labs",
        "agent_group_id",
        "agent_root_dir",
        "agent_workspace_dir",
        "agents_md",
        "heartbeat_md",
        "identity_md",
        "soul_md",
        "tools_md",
        "user_md",
        "skills_allowlist",
        # temporary dispatch exemption (the only write path of agx_set_dispatch_exempt)
        "dispatch_exempt_until",
        "agent_email",
        "agent_email_config",
        "agent_telegram_token",
        "agent_feishu_token",
        "agent_avatar",
        "base_model",
        "system_prompt",
        "mcp_config",
        "project_id",
        "peer_agent_id",
        "active_status",
        "deploy_status",
    ],
    "task": [
        "task_name",
        "task_labs",
        "description",
        "task_logo",
        "x_desc",
        "target_object",
        "payload",
        "priority",
        "is_private",
        "active_status",
        "reply_mode",
    ],
    "execution": [
        "session_id",
        "exec_instruction",
        "x_desc",
        "exec_name",
        "send_agent_id",
        "send_host_id",
        "prev_task",
        "next_task",
        "scheduled_at",
        "plan_id",
        "timeout_seconds",
        # * 2026-11-06 human release: task-level property 0/1 (can still be set after UI dispatch, only while non-terminal)
        "needs_confirm",
        # * 2026-10-26 the receipt-acknowledgement axis (paired with agx_ack_receipt)
        "reply_read",
        "reply_read_at",
        # * 2026-10-28 alert handling conclusion (the initiator writes the impact summary, replacing the old alert execution.summary)
        "alert_note",
    ],
    "agent_group": ["group_name", "x_desc", "active_status"],
}


# runtime state is managed by agx_finish_task and must not be modified at will

_PROTECTED_STATUS = {"task": ["run_status"], "execution": ["run_status"]}


# -- field-level write policy for executions (decided by Lao Feng on 2026-10-29) --
# The allowlist (_UPDATEABLE_FIELDS) only answers "may this field be written", not "who may write it, and to what value".
# timeout_seconds used to be missing on both sides:
#   (1) the initiator could not change a task it dispatched -- ownership only recognized the executor (execution.agent_id)
#   (2) nothing bounded the value written -- an executor could set the timeout to ten years and be immune to wakeups/alerts forever
# So who + constraint are declared per field. Fields not listed keep the original ownership rule.

_EXEC_TIMEOUT_MIN = 60
_EXEC_TIMEOUT_MAX_DEFAULT = 86400  # 24h


def _exec_timeout_max() -> int:
    try:
        return int(config.daemon.get("watchdog_max_task_timeout", _EXEC_TIMEOUT_MAX_DEFAULT))
    except Exception:
        return _EXEC_TIMEOUT_MAX_DEFAULT


# field -> set of allowed identities. owner=executor sender=initiator admin=project admin global=global role
_EXEC_FIELD_ACTORS = {
    "timeout_seconds": ("owner", "sender", "admin", "global"),
    "exec_instruction": ("owner", "sender", "admin", "global"),
    "alert_note": ("owner", "sender", "admin", "global"),
    "scheduled_at": ("owner", "sender", "admin", "global"),
    "x_desc": ("owner", "sender", "admin", "global"),
    "session_id": ("owner", "admin", "global"),
    "send_agent_id": ("owner", "admin", "global"),
    "send_host_id": ("owner", "admin", "global"),
    "prev_task": ("owner", "admin", "global"),
    "next_task": ("owner", "admin", "global"),
    "plan_id": ("owner", "admin", "global"),
    "exec_name": ("owner", "admin", "global"),
    "reply_read": ("owner", "admin", "global"),
    "reply_read_at": ("owner", "admin", "global"),
}


def _exec_caller_identities(caller_agent_id: str, exec_row: dict) -> set:
    """Return the set of identities the caller holds on this execution (owner/sender/admin/global)."""
    ids = set()
    if not caller_agent_id:
        return ids
    owner = (exec_row.get("agent_id") or "").strip()
    sender = (exec_row.get("send_agent_id") or "").strip()
    project_id = (exec_row.get("project_id") or "").strip()
    if owner and caller_agent_id == owner:
        ids.add("owner")
    if sender and caller_agent_id == sender:
        ids.add("sender")
    if _resolve_agent_role(caller_agent_id) in ("host_manager", "workshop_manager"):
        ids.add("global")
    if project_id:
        rows = execute_sql(
            "SELECT admin_agent_id FROM agx_project WHERE project_id = %s AND active_status = 1",
            (project_id,),
        )
        if rows and caller_agent_id == (rows[0].get("admin_agent_id") or "").strip():
            ids.add("admin")
    return ids


def _check_execution_field_policy(caller_agent_id: str, exec_row: dict, updates: dict) -> str | None:
    """Field-level write permission + value validation for executions. None = allowed, str = reason for denial."""
    updates = updates or {}
    ids = _exec_caller_identities(caller_agent_id, exec_row)
    if not ids:
        return (
            f"权限不足:Agent '{caller_agent_id}' 无权操作该 execution"
            f"(执行方 {(exec_row.get('agent_id') or '?')} / 发起方 {(exec_row.get('send_agent_id') or '?')}"
            f" / 项目 {(exec_row.get('project_id') or '?')})。"
            "仅执行方、发起方、所属项目管理员、host_manager、workshop_manager 可操作。"
        )

    # -- timeout_seconds: domain + extend-only --
    if "timeout_seconds" in updates:
        if not ids & set(_EXEC_FIELD_ACTORS["timeout_seconds"]):
            return f"权限不足:身份 {sorted(ids)} 不可修改 timeout_seconds。"
        raw = updates["timeout_seconds"]
        try:
            new_v = int(raw)
        except (TypeError, ValueError):
            return f"timeout_seconds 必须是整数秒,收到 {raw!r}。"
        lo, hi = _EXEC_TIMEOUT_MIN, _exec_timeout_max()
        if new_v < lo or new_v > hi:
            return f"timeout_seconds 超出允许范围 [{lo}, {hi}] 秒,收到 {new_v}。"
        try:
            cur_v = int(str(exec_row.get("timeout_seconds") or 0) or 0)
        except Exception:
            cur_v = 0
        if cur_v and new_v < cur_v and not ids & {"admin", "global"}:
            return (
                f"不允许缩短超时:{cur_v}s → {new_v}s。"
                "缩短会立刻触发唤醒/告警档位(0.7/0.8/0.9)。"
                "仅项目管理员或全局角色可缩短。"
            )
        updates["timeout_seconds"] = new_v   # normalize to int before storing

    # -- needs_confirm: may only be set on a [non-terminal] task, and the domain is fixed --
    #   * 2026-11-06: this field is a [task property] meaning "a human must review it before it is released".
    #   Setting 1 on an already-finished task creates a succeeded task that will never be released
    #   (chaining has already advanced and cannot be rolled back); and changing the value on an awaiting_confirm task bypasses
    #   the release/reject semantics of agx_confirm_task, i.e. manually forging a confirmation conclusion. Both holes must be closed.
    if "needs_confirm" in updates:
        try:
            _nc = int(updates["needs_confirm"])
        except (TypeError, ValueError):
            return f"needs_confirm 必须是整数 0/1/2/3,收到 {updates['needs_confirm']!r}。"
        if _nc not in (0, 1, 2, 3):
            return f"needs_confirm 只接受 0(不需要)/1(需放行)/2(已放行)/3(已驳回),收到 {_nc}。"
        _cur_rs = (exec_row.get("run_status") or "").strip()
        if _cur_rs == "awaiting_confirm":
            return (
                "拒绝:任务处于 awaiting_confirm，放行/驳回请调 "
                "agx_confirm_task(action='approve'|'reject')，不得直接改 needs_confirm。"
            )
        if _nc == 1 and _cur_rs not in ("pending", "running", "draft"):
            return (
                f"拒绝:任务当前 run_status='{_cur_rs}'，不能设为「需要人类放行」。"
                "needs_confirm=1 必须在未终态（pending/running/draft）时设定；"
                "对已结案任务补设，只会得到一个永远等不到放行的已完成任务。"
            )
        updates["needs_confirm"] = _nc

    # -- remaining fields: decide identity from the declaration table --
    for field in list(updates.keys()):
        if field == "timeout_seconds":
            continue
        allowed = _EXEC_FIELD_ACTORS.get(field)
        if allowed is None:
            continue        # undeclared fields fall back to the existing ownership rule
        if not ids & set(allowed):
            return f"权限不足:身份 {sorted(ids)} 不可修改字段 '{field}'。"
    return None


# -- exception logging helper --
# -- dispatch gate: deploy-status triage --
# * established 2026-11-05 (measured in the XHOST cross-host test):
#   deploy_status is meant to say "how far the deployment flow got", but 7 dispatch gates use it as
#   the existence criterion for "can this agent receive tasks". Those are not the same thing.
#
#   Evidence run_agent_deploy_ca0e215a8582 (188/17b): files written, OpenClaw registered
#   (agents.create returned ok=true), config hot-loaded (agents.entries hit the hot rule),
#   and only because the host agent did not write back the status bit -> stuck at deploying -> all 7 gates rejected -> the agent went mute,
#   with no alert anywhere. Worse, the old error only said "not deployed", making people rerun agx_deploy_agent
#   and overwrite the files that were already written.
#
#   So the error must triage: let a human see at a glance whether to "fill in the status bit" or "redeploy".
_DEPLOY_STATE_HINTS = {
    "pending": (
        "尚未部署（克隆或新建后的初始态）。请把 agent_deploy 派给该 agent 的 host_manager。"
    ),
    "deploying": (
        "部署已发起，但状态位未回填。若文件与 OpenClaw 注册都已完成，"
        "只需由 host_manager 调 agx_update_agent(agent_id=..., updates={'deploy_status': 'deployed'}) 回填即可；"
        "切勿重跑 agx_deploy_agent —— 会覆盖已写好的文件。"
    ),
    "failed": "上次部署失败。排查失败原因后重新派 agent_deploy。",
    "template": "这是模板 agent，不允许部署或派活。请先克隆出实例。",
}


def _deploy_gate_msg(agent_id: str, deploy_status, prefix: str = "") -> dict:
    """Build the dispatch gate denial body, triaging the next action by deploy_status.

    prefix: some call sites need a context prefix (e.g. "step run_xxx: ").
    """
    _s = (deploy_status or "").strip() or "unknown"
    hint = _DEPLOY_STATE_HINTS.get(
        _s,
        "部署状态未知。请检查 agx_agent_registry.deploy_status；"
        "若文件与注册都已就绪，由 host_manager 调 agx_update_agent 回填。",
    )
    return {
        "error": f"{prefix}agent '{agent_id}' 未就绪，无法派活 (deploy_status={_s})",
        "deploy_status": _s,
        "hint": hint,
    }


def _fail_deploy(agent_id: str, caller_agent_id: str, agent_name: str = "", work_dir: str = ""):
    """Mark the agent deployment as failed and clean up the files.

    Rollback strategy: delete the directory already written, so no half-finished artifact remains.
    Setting DB deploy_status -> failed guarantees the next deploy can retry.
    """
    # 1. mark the DB state
    try:
        agx_update_agent(caller_agent_id, agent_id, {"deploy_status": "failed"})
    except Exception as exc:
        log(f"[_fail_deploy] agx_update_agent failed for {agent_id}: {exc}", "ERROR")

    # 2. clear the recorded locations (* 2026-11-05)
    #    a failure means there is no valid location. Keeping the previous path is more dangerous than NULL --
    #    it would let tools that "find the deploy location from the DB" operate on a directory that may already be deleted.
    try:
        execute_sql(
            "UPDATE agx_agent_registry SET agent_root_dir=NULL, agent_workspace_dir=NULL "
            "WHERE agent_id=%s",
            (agent_id,),
            commit=True,
        )
    except Exception as exc:
        log(f"[_fail_deploy] 清空落点记录失败 for {agent_id}: {exc}", "WARN")

    # 3. clean up the files already written (rollback)
    if agent_name and work_dir:
        agent_dir = _agent_dir(work_dir, agent_id)
        if os.path.exists(agent_dir):
            try:
                import shutil

                shutil.rmtree(agent_dir, ignore_errors=True)
                log(f"[_fail_deploy] 已清理: {agent_dir}")
            except Exception as e:
                log(f"[_fail_deploy] 清理文件失败: {e}", "WARN")


def _log_exception(func_name: str, e: Exception) -> str:
    """Log the exception and return an Error message."""
    log(f"{func_name} FAILED: {e}\n{traceback.format_exc()}", "ERROR")
    return f"Error: {str(e)}"


# -- task manual loading (the canonical source: the tasks/ directory) --
# Design principle (Lao Feng, 2026-09-14): task descriptions must live in tasks/<task_labs>.md,
# so future extension and internationalization only change files, never code.
# Hard-coding task descriptions in MCP code is forbidden.
_TASK_DESC_CACHE = {}


def _load_task_desc(task_labs: str) -> str:
    """Read the task manual body from tasks/<task_labs>.md.

    Same source as install.py loading task_base.x_desc (the same tasks/ directory).
    Returns the body with the YAML frontmatter removed; returns "" when the file is missing or empty.
    The result is cached in-process (task manuals do not change at runtime).
    """
    if not task_labs:
        return ""
    if task_labs in _TASK_DESC_CACHE:
        return _TASK_DESC_CACHE[task_labs]
    content = ""
    try:
        path = os.path.join(_project_root, "tasks", f"{task_labs}.md")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                raw = f.read()
            # strip the leading YAML frontmatter (--- ... ---), keeping only the body
            if raw.lstrip().startswith("---"):
                parts = raw.split("---", 2)
                raw = parts[2] if len(parts) >= 3 else raw
            content = raw.strip()
        else:
            log(f"_load_task_desc: tasks/{task_labs}.md not found", "WARN")
    except Exception as e:
        log(f"_load_task_desc({task_labs}) failed: {e}", "ERROR")
        content = ""
    _TASK_DESC_CACHE[task_labs] = content
    return content


def _do_update(
    entity_type: str,
    id_col: str,
    id_value: str,
    updates: dict,
) -> str:
    """
    Generic update engine: dynamically builds the MySQL UPDATE from the given updates dict,

    then syncs the Redis cache and finally publishes a change event over Pub/Sub.

    Workflow:

    1. Field validation - keep only the fields in the _UPDATEABLE_FIELDS allowlist

    2. Protection check - reject direct modification of the fields in _PROTECTED_STATUS

    3. Dynamic SQL - build the SET col1=%s, col2=%s statement automatically from the given fields

    4. MySQL execution - parameterized query to prevent SQL injection

    5. Redis sync - call _sync_entity to flush the change into the cache hash

    6. Event publishing - broadcast the change notification on the agx:events channel

    Allowlist design considerations:

    - project: name, description, enable/disable

    - host: network parameters, resource config, running state

    - agent: role config, connection credentials, model selection

    - task: task description, priority, switch state (excluding run_status)

    - execution: session binding, execution instruction (excluding run_status)

    Args:

        entity_type (str): entity type (project/host/agent/task/execution).

            TABLE_MAP derives the corresponding MySQL table name automatically.

        id_col (str): the WHERE column name (e.g. project_id, host_id).

        id_value (str): the entity's unique ID value.

        updates (dict): the field dictionary to update. Pass only the fields that change;

            there is no need to pass them all - the dynamic SET contains only the keys given.

    Returns:

        str: on success "{EntityType} {id} updated: [field1, field2]";

             on failure "Error: ..." with a description.

    Usage examples:

        Update the host alias and port:

            _do_update("host", "host_id", "host_abc123",

                      {"host_alias": "new name", "ssh_port": "2222"})

            -> "Host host_abc123 updated: [host_alias, ssh_port]"

        Update the task priority:

            _do_update("task", "task_id", "task_xyz",

                      {"priority": "5"})

            -> "Task task_xyz updated: [priority]"

        Reject modifying run_status (a protected field):

            _do_update("task", "task_id", "task_xyz",

                      {"run_status": "succeeded"})

            -> "Error: field 'run_status' is protected and cannot be

                updated directly. Use agx_finish_task instead."

    """
    try:
        updateable = _UPDATEABLE_FIELDS.get(entity_type, [])

        protected = _PROTECTED_STATUS.get(entity_type, [])

        # keep only the fields allowed to be updated

        filtered = {}

        for k, v in updates.items():
            if k in updateable:
                if k in protected:
                    continue  # protected fields can only be changed with a dedicated tool

                filtered[k] = v

            elif k in protected:
                return f"Error: field '{k}' is protected and cannot be updated directly. Use agx_finish_task instead."

            else:
                return f"Error: field '{k}' is not updatable for {entity_type}"

        if not filtered:
            return f"Error: no valid fields to update for {entity_type} {id_value}"

        # --- JSON column serialization (must happen before params are built) ---
        # pymysql does not accept dict/list as parameters: dict -> TypeError, list -> errno 1241.
        # The error occurs at mogrify time and the stack points into the mysql layer, easily misjudged as a connection problem.
        # Everything is dumps'd into a str here, and MySQL's json column parses it itself.
        for col in list(filtered.keys()):
            if col in _JSON_COLUMNS and filtered[col] is not None:
                v = filtered[col]
                if isinstance(v, (dict, list)):
                    filtered[col] = json.dumps(v, ensure_ascii=False)
                elif isinstance(v, str):
                    # The caller may pass a JSON string directly; invalid JSON fails early here
                    # instead of silently writing a bad value.
                    try:
                        json.loads(v)
                    except (json.JSONDecodeError, ValueError):
                        return (
                            f"Error: field '{col}' is a JSON column but got a non-JSON string. "
                            f"Pass a dict/list, or a valid JSON string."
                        )

        # --- MySQL dynamic SET ---

        set_clauses = [f"{col} = %s" for col in filtered]

        params = list(filtered.values())

        params.append(id_value)

        table_name = TABLE_MAP.get(entity_type)

        sql = f"UPDATE {table_name} SET {', '.join(set_clauses)} WHERE {id_col} = %s"

        execute_sql(sql, tuple(params), commit=True)

        # --- write verification: read back large text fields and compare, detecting JSON-RPC transport truncation ---
        _LARGE_FIELDS = {
            "agents_md",
            "heartbeat_md",
            "identity_md",
            "soul_md",
            "tools_md",
            "user_md",
            "notebook_md",
            "self_md",
            "system_prompt",
            "x_desc",
            "payload",
        }
        for lf in _LARGE_FIELDS:
            if lf in filtered:
                sent_val = filtered[lf]
                sent_len = len(sent_val) if isinstance(sent_val, str) else len(str(sent_val))
                if sent_len > 500:  # only verify values longer than 500 characters
                    verify_rows = execute_sql(f"SELECT {lf} FROM {table_name} WHERE {id_col} = %s", (id_value,))
                    if verify_rows:
                        stored_val = verify_rows[0].get(lf, "") or ""
                        stored_len = len(stored_val) if isinstance(stored_val, str) else len(str(stored_val))
                        if stored_len != sent_len:
                            return f"Error: TRUNCATION DETECTED for field '{lf}'! Sent {sent_len} chars but MySQL stored {stored_len} chars. Likely JSON-RPC transport truncation."

        # --- Redis sync (skipping oversized content fields) ---

        _NO_CACHE_FIELDS = {"agents_md", "heartbeat_md", "identity_md", "soul_md", "tools_md", "user_md"}

        cache_filtered = {k: v for k, v in filtered.items() if k not in _NO_CACHE_FIELDS}

        if cache_filtered:
            try:
                # None values cannot be hset (they would be serialized to the string "None" and pollute the cache),
                # and must not simply be skipped either -- skipping leaves the old value in the cache when a field is cleared.
                # The right approach: None -> hdel that field, so later reads fall back to the real NULL.
                none_fields = [k for k, v in cache_filtered.items() if v is None]
                set_fields = {k: v for k, v in cache_filtered.items() if v is not None}

                if set_fields:
                    _sync_entity(entity_type, id_value, set_fields)

                if none_fields:
                    from agx_mcp.agx_core import get_redis_client as _grc

                    _r = _grc()
                    _key = f"{CACHE_PREFIX.get(entity_type, 'agx:entity:' + entity_type + ':')}{id_value}"
                    _r.hdel(_key, *none_fields)

            except Exception as e:
                log(f"{entity_type} {id_value} Redis sync failed: {e}", "WARN")

        try:
            _publish_event(entity_type, "updated", id_value, {"fields": list(filtered.keys())})

        except Exception as e:
            log(f"{entity_type} {id_value} Pub/Sub event failed: {e}", "WARN")

        # -- host slot change: invalidate the daemon's local cache immediately --
        if entity_type == "host" and "task_slots_limit" in filtered:
            try:
                r = get_redis_client()
                r.delete(f"agx:host_slots:{id_value}")
            except Exception as e:
                log(f"{entity_type} {id_value} host slots cache cleanup failed: {e}", "WARN")

        updated_keys = ", ".join(filtered.keys())

        return f"{entity_type.capitalize()} {id_value} updated: [{updated_keys}]"

    except Exception as e:
        log(f"{entity_type} {id_value} update FAILED: {e}\n{traceback.format_exc()}", "ERROR")
        return f"Error: {str(e)}"


@mcp.tool()
@require_level(5)
def agx_update_project(caller_agent_id: str, project_id: str, updates: dict) -> str:
    """
    [Update project] Dynamically update selected fields of agx_project, dual-writing MySQL + Redis.

    Dynamic field design: pass only the fields and values that change; the tool filters against the allowlist

    and builds the corresponding UPDATE statement; fields not passed are untouched.

    Args:

        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        project_id (str): project ID, e.g. "proj_da3046e8f017".

        updates (dict): the field dictionary to update.

            Updatable fields: project_name, project_abbr, project_logo, x_desc, source_dir, active_status.

            * Field grading (decided by Lao Feng on 2026-10-13: graded by [field blast radius]):
              - (1) operational fields project_name/project_abbr/project_logo/x_desc/source_dir
                    -> a PM may change [projects it administers] (reversible, does not affect lifecycle)
              - (2) enable/disable field active_status (disable/enable [the whole project])
                    -> L4 only (project-level blast radius is far larger than member level; goes through system roles)
              - guard: a PM may only change projects it administers; agx_projectmother keeps its system-level exception.

            Example: {"project_name": "new name", "project_abbr": "new", "x_desc": "new description"}

                   {"project_logo": "https://.../logo.png"}  (project logo, URL or local path, no binary stored)

                   {"active_status": "0"}  (disable the project -- requires L4)

    Returns:

        str: success -> "Project proj_xxx updated: [field1, field2]"

             failure -> "Error: ..."

    Example:

        agx_update_project(

            project_id="proj_da3046e8f017",

            updates={"project_name": "dealer analysis V5"}

        )

    """
    # ══════════════════════════════════════════════════════════
    # field-level permission check (decided by Lao Feng on 2026-10-13: symmetric with agx_update_agent, graded by [field blast radius])
    # ══════════════════════════════════════════════════════════
    # (1) operational fields project_name / project_abbr / project_logo / x_desc / source_dir
    #      -> a PM may change [projects it administers] (reversible, does not affect the project lifecycle)
    # (2) enable/disable field active_status (disable/enable [the whole project])
    #      -> raised to L4 only (project-level blast radius far exceeds member level -- "disable one member" vs "disable the whole project";
    #        and Lao Feng ruled "a finished project stays as-is" = the project lifecycle is a heavy decision)
    # guard: a PM may only change projects it administers (ownership, see below); "cannot modify yourself" does not apply (a project has no self).
    # criterion in one line: reversible mistake -> delegate; irreversible/escalating mistake -> exclusive.
    # ══════════════════════════════════════════════════════════
    OPERATIONAL_FIELDS_P = {"project_name", "project_abbr", "project_logo", "x_desc", "source_dir"}
    ACTIVATE_FIELD_P = "active_status"

    caller_role = _lookup_agent_role(caller_agent_id) or "unknown"
    caller_allowed = ROLE_ALLOWED_LEVELS.get(caller_role, ROLE_ALLOWED_LEVELS["unknown"])
    req_fields = set(updates.keys())

    # active_status is a project-level heavy action -> requires L4
    if ACTIVATE_FIELD_P in req_fields and 4 not in caller_allowed:
        return json.dumps(
            {
                "error": f"权限不足:更新项目级 active_status（停用/启用整个项目）需 L4。Agent '{caller_agent_id}' (role={caller_role}) 仅有 L{sorted(caller_allowed)}",
                "denied_reason": "project_activation_restricted",
                "hint": "项目级启停破坏半径远大于成员级，走系统角色（workshop_manager / agentfather）。",
            },
            ensure_ascii=False,
        )

    # -- resource-level ownership check (rule set by Lao Feng on 2026-10-10) --
    # criterion: (1) a worker cannot (no L5, already blocked upstream) (2) a PM may only change projects it administers
    #           (3) system admins follow current permissions (workshop_manager all-access; host_manager unreachable without L5)
    # exception: agx_projectmother is the project superintendent with system-level archiving duties -> allowed on any project
    #           (the hole is opened precisely by agent_id, not by role, so every project_manager is not opened up)
    if 4 not in caller_allowed:
        if caller_agent_id not in _sys_names(SYS_PROJECTMOTHER):
            _deny = _check_resource_ownership(caller_agent_id, "", project_id)
            if _deny:
                return json.dumps(
                    {
                        "error": f"权限不足:项目 '{project_id}' 不属于调用方管辖。{_deny}",
                        "denied_reason": "ownership_denied",
                    },
                    ensure_ascii=False,
                )
    return _do_update("project", "project_id", project_id, updates)


@mcp.tool()
@require_level(6)
def agx_update_host(caller_agent_id: str, host_id: str, updates: dict) -> str:
    """
    [Update host] Dynamically update selected fields of agx_host_inventory, dual-writing MySQL + Redis.

    Used to change a host's network config, resource quota or running state.

    Only the fields passed are updated; everything else is untouched.

    Args:

        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        host_id (str): host ID, e.g. "host_6dfc2cdf2302".

        updates (dict): the field dictionary to update.

            Updatable fields: host_alias, endpoint_ip, ssh_port, os_platform,

            platforms, task_slots_limit, active_task_count, active_status,

            presence_status, x_desc.

            * platforms (added 2026-11-04, JSON array): the agent platforms available on this host and how to reach them.
              Pass a list directly (_JSON_COLUMNS json.dumps it automatically).
              Format spec (same as the column comment, **do not invent keys**):
                required {platform, name, mode, port, base_path}
                  platform  platform id, e.g. "openclaw"
                  name      display name, e.g. "OpenClaw #2"
                  mode      "process" | "container"
                  port      integer port, e.g. 19789
                  base_path HTTP path, e.g. "/chat"
                conditional keys {addr, profile, note}
                  addr      required only when mode=container (a container/separate network namespace owns its address);
                            **forbidden** when mode=process (the address is built from endpoint_ip+port)
                  profile   OpenClaw profile name, decides which ~/.openclaw-{profile}/ is read/written;
                            required for multiple instances, empty string "" for the primary instance
                  note      free-form comment; **must not carry information the machine needs to read**
                            (historically "profile xxx" was written into note and the machine could not parse it)
              Example: [{"platform":"openclaw","name":"OpenClaw #2","mode":"process",
                      "port":19789,"base_path":"/chat","profile":"openclaw2"}]
              Passing [] or null clears it.

            Example: {"host_alias": "prod-server-01", "task_slots_limit": "8"}

                   {"active_status": "0", "x_desc": "retired"}

    Returns:

        str: success -> "Host host_xxx updated: [field1, field2]"

             failure -> "Error: ..."

    Example:

        agx_update_host(

            host_id="host_6dfc2cdf2302",

            updates={"endpoint_ip": "10.0.0.50", "ssh_port": "2222"}

        )

    """
    return _do_update("host", "host_id", host_id, updates)


@mcp.tool()
@require_level(1)  # the actual permission check is inside the function body (host_manager may update deploy fields)
def agx_update_agent(caller_agent_id: str, agent_id: str, updates: dict) -> str:
    """
    [Update agent] Dynamically update selected fields of agx_agent_registry, dual-writing MySQL + Redis.

    Used to change an agent's role, model, credentials or enabled/disabled state.

    Supports updating token configuration for channels such as Telegram/Feishu.

    Args:

        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        agent_id (str): agent ID, e.g. "agt_7372fc41b3da".

        updates (dict): the field dictionary to update.

            Updatable fields: host_id, agent_name, x_desc, agent_role,

            agent_email, agent_email_config, agent_telegram_token,

            agent_feishu_token, base_model, system_prompt, mcp_config,

            skills_allowlist, active_status.

            * JSON fields (mcp_config / agent_email_config / skills_allowlist):
              pass a dict/list directly; _do_update json.dumps it automatically.
              (passing a list to pymysql raises errno 1241, passing a dict raises TypeError;
               this method handles both, so the caller need not serialize.)

            * Field grading (decided by Lao Feng on 2026-10-13: graded by [field blast radius]):
              - (1) operational fields agent_name/x_desc -> a PM may change [members of its own project] (including the PM seat)
              - (2) enable/disable field active_status    -> a PM may change [workers of its own project] (the PM seat still needs L4)
              - (3) contract fields agent_role/base_model/system_prompt/mcp_config/host_id
                    -> L4 only (agent_role is a privilege-escalation channel, never delegated)
              - deployment fields deploy_status/agent_root_dir/agent_workspace_dir -> L6 or L4
              - universal guard: modifying yourself is forbidden (caller == target)

            Example: {"agent_name": "analyzer V2", "base_model": "gpt-4o"}

                   {"system_prompt": "you are a quantitative analysis expert"}

                   {"active_status": "0"}  (disable)

    Returns:

        str: success -> "Agent agt_xxx updated: [field1, field2]"

             failure -> "Error: ..."

    Example:

        agx_update_agent(

            agent_id="agt_7372fc41b3da",

            updates={"agent_name": "A320-Logic-V3",

                     "system_prompt": "handles log analysis tasks only"}

        )

    """
    # ══════════════════════════════════════════════════════════════════
    # field-level permission check (decided by Lao Feng on 2026-10-13: graded by [field blast radius], see the comment below)
    # ══════════════════════════════════════════════════════════════════
    # three tiers of fields (blast radius from small to large):
    #   (1) operational fields  agent_name / x_desc  -> a PM may change [members of its own project] (including the PM seat)
    #      (smallest blast radius: renaming/re-describing is reversible and affects neither personality nor contract)
    #   (2) enable/disable field  active_status        -> a PM may change [workers of its own project]
    #      (for a worker = an administrative action on a "soldier"; for the PM seat = an act of authority over an "officer", hence L4)
    #   (3) contract fields  agent_role / base_model / system_prompt / mcp_config / host_id
    #      -> L4 only (changing role = privilege escalation! changing prompt/model = changing personality; changing host = relocating)
    #   deployment fields deploy_status / agent_root_dir / agent_workspace_dir -> L6 or L4 (kept as-is)
    #
    # universal guard: modifying yourself is forbidden (caller == target) -- prevents a PM from self-escalating or self-disabling (self-lock).
    # criterion in one line: reversible mistake -> delegate; irreversible/escalating mistake -> exclusive.
    # ══════════════════════════════════════════════════════════════════
    HOST_DEPLOY_FIELDS = {"deploy_status", "agent_root_dir", "agent_workspace_dir", "host_id"}
    OPERATIONAL_FIELDS = {"agent_name", "x_desc"}  # (1) a PM may change members of its own project
    ACTIVATE_FIELD = "active_status"                 # (2) a PM may change workers of its own project

    caller_role = _lookup_agent_role(caller_agent_id) or "unknown"
    caller_allowed = ROLE_ALLOWED_LEVELS.get(caller_role, ROLE_ALLOWED_LEVELS["unknown"])
    req_fields = set(updates.keys())
    non_deploy = req_fields - HOST_DEPLOY_FIELDS

    # universal guard: modifying yourself is forbidden
    if caller_agent_id == agent_id and req_fields:
        return json.dumps(
            {
                "error": f"禁止通过本工具修改自己的配置（caller == target = '{agent_id}'）。如需变更请联系上级/系统角色。",
                "denied_reason": "self_modification_forbidden",
            },
            ensure_ascii=False,
        )

    # deployment fields: require L6 or L4
    if (req_fields & HOST_DEPLOY_FIELDS) and 6 not in caller_allowed and 4 not in caller_allowed:
        return json.dumps(
            {
                "error": f"权限不足:更新 deploy 字段 {sorted(req_fields & HOST_DEPLOY_FIELDS)} 需要 L6 或 L4。Agent '{caller_agent_id}' (role={caller_role}) 仅有 L{sorted(caller_allowed)}",
            },
            ensure_ascii=False,
        )

    # non-deployment fields: L4 passes directly; otherwise the PM allowlist + ownership two-stage check
    if non_deploy and 4 not in caller_allowed:
        # admission: operational fields within this project / worker enable-disable
        pm_fields = (OPERATIONAL_FIELDS | {ACTIVATE_FIELD})
        unauthorized = non_deploy - pm_fields
        if unauthorized:
            return json.dumps(
                {
                    "error": f"权限不足:字段 {sorted(unauthorized)} 属契约字段，需 L4（agentfather 独占）。Agent '{caller_agent_id}' (role={caller_role}) 仅有 L{sorted(caller_allowed)}",
                    "allowed_pm_fields": sorted(pm_fields),
                    "hint": "agent_role/base_model/system_prompt/mcp_config/host_id 等契约字段不可下放（agent_role 是提权通道）。",
                },
                ensure_ascii=False,
            )

        # read the target agent's project + role (shared by ownership and the PM-seat decision)
        target_rows = execute_sql(
            "SELECT project_id, agent_role FROM agx_agent_registry WHERE agent_id = %s",
            (agent_id,),
        )
        if not target_rows:
            return f"Error: agent_id '{agent_id}' not found"
        target_project = target_rows[0].get("project_id") or ""
        target_role = target_rows[0].get("agent_role") or ""

        # ownership: must be the admin of this project (or the resource owner itself, but modifying yourself was already blocked above)
        ownership_err = _check_resource_ownership(caller_agent_id, agent_id, target_project)
        if ownership_err:
            return json.dumps(
                {
                    "error": f"权限不足:目标 Agent '{agent_id}' 不属于调用方管辖（项目 {target_project}）。{ownership_err}",
                    "denied_reason": "ownership_denied",
                },
                ensure_ascii=False,
            )

        # extra constraint for enable-disable of the PM seat: a PM may only disable [workers]; disabling the PM seat still requires L4
        if ACTIVATE_FIELD in non_deploy and target_role == "project_manager":
            return json.dumps(
                {
                    "error": f"权限不足:目标 '{agent_id}' 是 project_manager 席位，启停需 L4（agentfather）。PM 只能启停本项目 worker。",
                    "denied_reason": "pm_seat_activation_restricted",
                },
                ensure_ascii=False,
            )

    # detect whether the worker_queues cache needs refreshing
    needs_worker_refresh = any(k in updates for k in ("host_id", "agent_role", "active_status"))
    old_host = None
    if needs_worker_refresh and "host_id" in updates:
        old = execute_sql("SELECT host_id FROM agx_agent_registry WHERE agent_id = %s", (agent_id,), commit=False)
        if old:
            old_host = old[0].get("host_id", "")

    result = _do_update("agent", "agent_id", agent_id, updates)

    if needs_worker_refresh and not result.startswith("Error:"):
        if old_host and old_host != updates.get("host_id", old_host):
            _invalidate_worker_queues_cache(old_host)
        # refresh the current host (re-read from the DB to get the latest host_id)
        try:
            current = execute_sql(
                "SELECT host_id FROM agx_agent_registry WHERE agent_id = %s", (agent_id,), commit=False
            )
            if current:
                _invalidate_worker_queues_cache(current[0]["host_id"])
        except Exception as exc:
            log(f"agx_update_agent _invalidate_worker_queues_cache failed for {agent_id}: {exc}", "WARN")

    return result

# ══════════════════════════════════════════════════════════════════
# [agent skill allowlist] dedicated write path (decided by Lao Feng on 2026-10-23)
#
# Why not reuse agx_update_agent:
#   agx_update_agent has the global guard "modifying yourself is forbidden" (caller == target is rejected outright).
#   That guard exists for irreversible fields such as agent_role / active_status (to prevent self-escalation / self-lock).
#   skills_allowlist is reversible by nature (configure it wrong and configure it back), a completely different blast radius.
#   Rather than opening a "self-modify" hatch on every field (too much risk surface), open a dedicated door for this one field.
#   This method is the only write path for skills_allowlist and touches nothing else.
# ══════════════════════════════════════════════════════════════════
def _skill_search_roots(agent_id: str) -> list:
    """Return the roots where this agent looks for skills, in priority order.

    Same priority as OpenClaw's loading: workspace (priority 1, written specifically for that agent)
    > shared library ~/.agents/skills (one root shared by all agents) > ~/.openclaw/skills.
    """
    # * 2026-10-25: support the new directory layout (agx_agents + the host/ subdirectory) and follow the runtime work_dir
    _wd = ""
    try:
        _wd = os.path.expanduser(config.get("work_dir", "") or "")
    except Exception:
        _wd = ""
    if not _wd:
        _wd = os.path.expanduser("~/.agx")
    return [
        ("workspace", os.path.join(_agent_dir(_wd, agent_id, "workspace", "skills"))),
        ("shared", os.path.expanduser("~/.agents/skills")),
        ("openclaw", os.path.expanduser("~/.openclaw/skills")),
    ]


@mcp.tool()
@require_level(1)  # threshold at L1 (reachable by workers); the real three-tier permission is decided inside the function body
def agx_set_agent_skills(
    caller_agent_id: str,
    agent_id: str,
    skill_names: Union[list, str, None],
    allow_unknown: bool = False,
) -> str:
    """
    [Set the agent's skill allowlist] Changes exactly one field: agx_agent_registry.skills_allowlist.

    * This method is the [only write path] for skills_allowlist; it does not go through agx_update_agent.
      Reason: the "cannot modify yourself" guard in agx_update_agent exists for irreversible fields,
      while the skill allowlist is reversible, so it gets its own door that opens only this one field --
      no generic self-modification hatch.

    * Three permission tiers (decided by Lao Feng on 2026-10-23):
      (1) write your own        caller_agent_id == agent_id        -> always allowed
      (2) PM writes a member    caller is the admin_agent_id of the target's project -> allowed
      (3) super role writes any caller's role in {agent_manager, workshop_manager}
                          (currently only agx_agentfather / agx_core) -> allowed
      Everything else is rejected, returning denied plus the allowlist.

    * Skill-name validity check (on by default):
      Checks <root>/<name>/SKILL.md one by one (workspace > shared library > openclaw).
      A single missing name [rejects the whole batch] -- a name that does not take effect is garbage data.
      For skills that do not come from files (e.g. a plugin), pass allow_unknown=True to skip the check.

    Args:
        caller_agent_id (str): caller agent ID (identity audit + permission decision).
        agent_id (str): target agent ID.
        skill_names (list | str | None): list of skill names (= skill directory names, not titles).
            - passing [] / None / ""  -> clear the allowlist (set NULL = not configured, inherit the global setting)
            - passing a non-empty list -> overwrite (replace, not merge)
            - a JSON array string or a comma-separated string is also accepted (tolerant)
        allow_unknown (bool): whether to skip the file-existence check, default False.

    Returns:
        JSON string:
          - status=success -> agent_id / count / skills / permission / verified_length
          - status=denied  -> the reason permission was refused
          - status=error   -> bad parameter / missing skill name (with the missing list)

    Example:
        # configure your own (workers can use this too)
        agx_set_agent_skills(
            caller_agent_id="agx_platform_codex",
            agent_id="agx_platform_codex",
            skill_names=["python-patterns", "bash-linux"],
        )

        # a PM configures a member of its project
        agx_set_agent_skills(
            caller_agent_id="agentgeex_agent",
            agent_id="agx_ui_lead",
            skill_names=["api-patterns"],
        )

        # clear (back to inheriting the global setting)
        agx_set_agent_skills(
            caller_agent_id="agentgeex_agent",
            agent_id="agx_ui_lead",
            skill_names=[],
        )
    """
    # -- 1. parameter normalization (tolerant: list / JSON string / comma separated / None) --
    raw = skill_names
    if raw is None:
        names = []
    elif isinstance(raw, str):
        s = raw.strip()
        if not s:
            names = []
        elif s.startswith("["):
            try:
                parsed = json.loads(s)
            except json.JSONDecodeError as exc:
                return json.dumps(
                    {"status": "error", "message": f"skill_names 不是合法 JSON: {exc}"},
                    ensure_ascii=False,
                )
            if not isinstance(parsed, list):
                return json.dumps(
                    {"status": "error", "message": "skill_names 的 JSON 必须是数组"},
                    ensure_ascii=False,
                )
            names = [str(x) for x in parsed]
        else:
            names = [p.strip() for p in s.split(",") if p.strip()]
    elif isinstance(raw, (list, tuple)):
        names = [str(x) for x in raw]
    else:
        return json.dumps(
            {"status": "error", "message": f"skill_names 类型不支持: {type(raw).__name__}"},
            ensure_ascii=False,
        )

    # trim whitespace + dedupe preserving order
    _seen, _deduped = set(), []
    for _n in names:
        _n = _n.strip()
        if _n and _n not in _seen:
            _seen.add(_n)
            _deduped.append(_n)
    names = _deduped

    # -- 2. the target agent must exist; take its project_id --
    target_rows = execute_sql(
        "SELECT project_id, agent_role FROM agx_agent_registry WHERE agent_id = %s",
        (agent_id,),
    )
    if not target_rows:
        return json.dumps(
            {"status": "error", "message": f"agent_id '{agent_id}' not found"},
            ensure_ascii=False,
        )
    target_project = target_rows[0].get("project_id") or ""

    # -- 3. three-tier permission decision --
    caller_role = _lookup_agent_role(caller_agent_id) or "unknown"
    is_self = caller_agent_id == agent_id
    is_super = caller_role in ("agent_manager", "workshop_manager")

    is_pm = False
    if not (is_self or is_super) and target_project:
        adm = execute_sql(
            "SELECT admin_agent_id FROM agx_project WHERE project_id = %s AND active_status = 1",
            (target_project,),
        )
        is_pm = bool(adm and adm[0].get("admin_agent_id") == caller_agent_id)

    if not (is_self or is_super or is_pm):
        return json.dumps(
            {
                "status": "denied",
                "message": (
                    f"权限不足：Agent '{caller_agent_id}'(role={caller_role}) 无权设置 "
                    f"'{agent_id}' 的 skill 白名单。"
                    f"仅 [本人 / 目标所属项目管理员 / agent_manager / workshop_manager] 可操作。"
                ),
                "target_project": target_project,
                "hint": "项目管理员指 agx_project.admin_agent_id，不是任意 project_manager 角色。",
            },
            ensure_ascii=False,
        )

    permission = "self" if is_self else ("super_role" if is_super else "project_admin")

    # -- 4. skill-name validity check (on by default, prevents writing garbage names) --
    roots = _skill_search_roots(agent_id)
    resolved, missing = {}, []
    if names and not allow_unknown:
        for _n in names:
            hit = None
            for _label, _root in roots:
                if os.path.isfile(os.path.join(_root, _n, "SKILL.md")):
                    hit = _label
                    break
            if hit:
                resolved[_n] = hit
            else:
                missing.append(_n)
        if missing:
            return json.dumps(
                {
                    "status": "error",
                    "message": (
                        f"{len(missing)}/{len(names)} 个 skill 名在文件系统里找不到 "
                        f"({missing[0]}/SKILL.md 不存在)，整批未写入。"
                    ),
                    "missing": missing[:50],
                    "searched_roots": [r for _l, r in roots],
                    "hint": (
                        "名字要用 skill 目录名（不是 SKILL.md 里的标题）。"
                        "若是 plugin 等非文件来源的 skill，传 allow_unknown=True 跳过校验。"
                    ),
                },
                ensure_ascii=False,
            )

    # -- 5. write (lists are serialized automatically by _do_update's _JSON_COLUMNS) --
    payload = names if names else None   # empty list -> NULL (not configured, inherits the global setting)
    result = _do_update("agent", "agent_id", agent_id, {"skills_allowlist": payload})
    if result.startswith("Error:"):
        return json.dumps({"status": "error", "message": result}, ensure_ascii=False)

    # -- 6. read-back verification (trust the DB, not ourselves) --
    verified_len = None
    try:
        chk = execute_sql(
            "SELECT JSON_TYPE(skills_allowlist) AS t, "
            "JSON_LENGTH(skills_allowlist) AS n "
            "FROM agx_agent_registry WHERE agent_id = %s",
            (agent_id,),
        )
        if chk:
            verified_len = chk[0].get("n")
    except Exception as exc:  # noqa: BLE001
        log(f"agx_set_agent_skills 回读失败 {agent_id}: {exc}", "WARN")

    return json.dumps(
        {
            "status": "success",
            "agent_id": agent_id,
            "count": len(names),
            "cleared": not names,
            "skills": names,
            "written_by": caller_agent_id,
            "permission": permission,
            "resolved_roots": resolved,
            "verified_length": verified_len,
            "note": "本方法只写 DB 存档；生效仍需同步到 OpenClaw 配置（见 workspace skills 加载）。",
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
@require_level(1)  # low threshold (reachable by workers); the real three-tier permission is decided inside the function body
def agx_set_dispatch_exempt(
    caller_agent_id: str,
    agent_id: str,
    minutes: int = 0,
    until: str = "",
    reason: str = "",
) -> str:
    """
    [Temporary dispatch exemption] Changes exactly one field: agx_agent_registry.dispatch_exempt_until.

    * What problem it solves
      A worker's role level is {0,1,2,3}, while agx_dispatch_task / agx_schedule_task /
      agx_quick_cmd_send are L5, so a worker can never dispatch work to another agent. That restriction is
      correct (otherwise agents chatting with each other = out of control), but a blanket rule is too rigid in
      practice: there really are cases where a given worker needs command ability for a while.

    * Semantics: expires on its own, no need to remember to turn it off
      The field is a timestamp, not a boolean switch:
        NULL / expired   -> no exemption (default state, behavior identical to before this feature)
        later than now   -> that agent may call the dispatch tools
      Why not 0/1: granting a worker dispatch authority has consequences. A boolean switch left on is a permanent
      grant, and nothing would remind anyone. A timestamp makes "forgetting" expire by itself.

    * Opens dispatch only, not the other L5 capabilities
      The exemption applies only to the three DISPATCH_TOOL_NAMES tools. Creating a project (agx_create_project),
      changing an agent (agx_update_agent), and password/host/system management are still rejected -- a temporary
      exemption is "let it dispatch", not "promote it to PM".

    * Three permission tiers (aligned with agx_set_agent_skills)
      (1) super role     caller's role in {agent_manager, workshop_manager} -> any agent
      (2) project admin  caller == admin_agent_id of the target's project   -> members of that project
      (3) anything else                                                    -> rejected
      * Deliberately not allowed to grant it to yourself: that is self-escalation, the opposite of the intended
        purpose (someone else grants it).

    Args:
        caller_agent_id (str): caller agent ID (identity audit + permission decision).
        agent_id (str): target agent ID.
        minutes (int): exemption duration in minutes, e.g. 30 / 120 / 1440. Passing 0 with an empty until
            -> clears the exemption (equivalent to "turn it off"). Upper bound 43200 (30 days); larger values are clamped.
        until (str): give the expiry moment directly, "YYYY-MM-DD HH:MM:SS", must be later than now.
            Use either this or minutes; passing both -> error (so two intents do not fight).
        reason (str): why this door is being opened (written into the return value and the log, for later audit).

    Returns:
        JSON: status=success -> agent_id / until / remaining_seconds / permission /
              written_by / reason; status=denied / error explains why.

    Example:
        # give a worker 2 hours of dispatch ability
        agx_set_dispatch_exempt(
            caller_agent_id="agentgeex_agent",
            agent_id="agx_platform_codex",
            minutes=120,
            reason="let it hand the result to book_reviewer for now",
        )

        # turn it off (takes effect immediately)
        agx_set_dispatch_exempt(
            caller_agent_id="agentgeex_agent",
            agent_id="agx_platform_codex",
        )

        # keep it open until midnight today
        agx_set_dispatch_exempt(
            caller_agent_id="agentgeex_agent",
            agent_id="agx_platform_codex",
            until="2026-10-31 23:59:59",
        )
    """
    MAX_MINUTES = 60 * 24 * 30  # 30-day cap: anything longer is no longer "temporary"

    # -- 1. target existence + fetch the project (needed for the permission decision) --
    try:
        target_rows = execute_sql(
            "SELECT project_id, agent_role, dispatch_exempt_until "
            "FROM agx_agent_registry WHERE agent_id = %s",
            (agent_id,),
        )
    except Exception as exc:
        return _log_exception("agx_set_dispatch_exempt", exc)

    if not target_rows:
        return json.dumps(
            {"status": "error", "message": f"agent_id '{agent_id}' not found"},
            ensure_ascii=False,
        )
    target_project = target_rows[0].get("project_id") or ""
    target_role = target_rows[0].get("agent_role") or ""

    # -- 2. three tiers of permission (self-escalation not allowed) --
    caller_role = _lookup_agent_role(caller_agent_id) or "unknown"
    is_super = caller_role in ("agent_manager", "workshop_manager")

    if caller_agent_id == agent_id:
        return json.dumps(
            {
                "status": "denied",
                "message": (
                    f"不允许自己给自己开豁免（'{caller_agent_id}' 就是目标）。"
                    "自提权绕过了权限体系的设计意图，请让上级 Agent 来开。"
                ),
            },
            ensure_ascii=False,
        )

    is_pm = False
    if not is_super and target_project:
        adm = execute_sql(
            "SELECT admin_agent_id FROM agx_project WHERE project_id = %s AND active_status = 1",
            (target_project,),
        )
        is_pm = bool(adm and adm[0].get("admin_agent_id") == caller_agent_id)

    if not (is_super or is_pm):
        return json.dumps(
            {
                "status": "denied",
                "message": (
                    f"权限不足：Agent '{caller_agent_id}'(role={caller_role}) 无权设置 "
                    f"'{agent_id}' 的调度豁免。仅 [目标所属项目管理员 / agent_manager / "
                    f"workshop_manager] 可操作。"
                ),
                "target_project": target_project,
                "hint": "项目管理员指 agx_project.admin_agent_id，不是任意 project_manager 角色。",
            },
            ensure_ascii=False,
        )
    permission = "super_role" if is_super else "project_admin"

    # -- 3. parameter normalization -> target expiry moment (None means clear) --
    now = datetime.now()
    if minutes and until:
        return json.dumps(
            {
                "status": "error",
                "message": "minutes 和 until 只能给一个：一个是相对时长，一个是绝对时刻。",
            },
            ensure_ascii=False,
        )

    target_until = None
    if minutes:
        try:
            mins = int(minutes)
        except (TypeError, ValueError):
            return json.dumps(
                {"status": "error", "message": f"minutes 必须是整数，收到 {minutes!r}"},
                ensure_ascii=False,
            )
        if mins < 0:
            return json.dumps(
                {"status": "error", "message": "minutes 不能为负；要关闭请传 0。"},
                ensure_ascii=False,
            )
        if mins > MAX_MINUTES:
            mins = MAX_MINUTES
        target_until = now + timedelta(minutes=mins) if mins else None
    elif until:
        text = str(until).strip()
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return json.dumps(
                {
                    "status": "error",
                    "message": f"until 格式无法解析: {until!r}，需要 'YYYY-MM-DD HH:MM:SS'",
                },
                ensure_ascii=False,
            )
        if parsed <= now:
            return json.dumps(
                {
                    "status": "error",
                    "message": f"until 必须晚于当前时间（现在 {now:%Y-%m-%d %H:%M:%S}）",
                },
                ensure_ascii=False,
            )
        target_until = parsed

    # -- 4. write (the only write path; agx_update_agent is not used) --
    payload = target_until.strftime("%Y-%m-%d %H:%M:%S") if target_until else None
    result = _do_update("agent", "agent_id", agent_id, {"dispatch_exempt_until": payload})
    if result.startswith("Error:"):
        return json.dumps({"status": "error", "message": result}, ensure_ascii=False)

    # -- 5. read-back verification: trust the DB, not ourselves --
    verified_raw, verified_live = None, False
    try:
        chk = execute_sql(
            "SELECT dispatch_exempt_until FROM agx_agent_registry WHERE agent_id = %s",
            (agent_id,),
        )
        if chk:
            verified_raw = chk[0].get("dispatch_exempt_until")
            verified_live = _exempt_is_live(verified_raw)
    except Exception as exc:  # noqa: BLE001
        log(f"agx_set_dispatch_exempt 回读失败 {agent_id}: {exc}", "WARN")

    remaining = None
    if verified_raw is not None and hasattr(verified_raw, "year"):
        remaining = int((verified_raw - datetime.now()).total_seconds())

    log(
        f"[EXEMPT] {caller_agent_id} -> {agent_id} "
        f"until={verified_raw} live={verified_live} reason={reason or '-'}",
        "AUDIT",
    )

    return json.dumps(
        {
            "status": "success",
            "agent_id": agent_id,
            "agent_role": target_role,
            "until": str(verified_raw) if verified_raw else None,
            "remaining_seconds": remaining,
            "cleared": target_until is None,
            "effective_now": verified_live,
            "written_by": caller_agent_id,
            "permission": permission,
            "reason": reason,
            "scope": "仅 agx_dispatch_task / agx_schedule_task / agx_quick_cmd_send；"
                     "其他 L5 能力（建项目、改 Agent 等）不受影响",
            "note": "到期自动失效，无需手动关闭。缓存已同步，判定立即生效。",
        },
        ensure_ascii=False,
        indent=2,
    )


def _local_host_ids() -> set:
    """Every host_id on this physical machine (the primary instance + the extra instances in config.gateways).

    Multi-instance case: one machine running two OpenClaw instances = two host_ids, but they share one
    physical machine. Deciding "does this agent belong to this machine" must not use a single config.host_id,
    otherwise an agent deployed to the second instance would be wrongly judged as "not from this machine".

    The single-instance case returns {config.host_id}, equivalent to before this change.
    """
    ids = set()
    h = (config.get("host_id") or "").strip()
    if h:
        ids.add(h)
    for g in config.get("gateways") or []:
        if isinstance(g, dict):
            gh = str(g.get("host_id") or "").strip()
            if gh:
                ids.add(gh)
    return ids


@mcp.tool()
@require_level(6)
def agx_deploy_agent(caller_agent_id: str, agent_id: str, force: bool = False, task_run_uuid: str = "") -> str:
    """
    [Agent deployment] Materialize an agent's configuration from the DB onto the local filesystem and register it with OpenClaw.

    This is the MCP implementation of the agent_deploy task, replacing the old "host agent executes the manual step by step" mode.
    One call does it all: validate, create directories, write files, push the OpenClaw registration (asynchronous pending).

    Design principles:
        - the DB is the authoritative source; the filesystem is a materialized copy
        - task_run_uuid: the host agent's task_execution ID, used to correlate the asynchronous callback.
        - write files before registering (wx mode protection)
        - idempotent: skipped by default when deploy_status=deployed (force=true overrides)

    Args:
        caller_agent_id (str): caller agent ID.
        agent_id (str): the agent ID to deploy.
        force (bool): whether to force overwriting existing files. Default False (idempotent skip).

    Returns:
        str: JSON, {"status": "deployed"|"skipped"|"failed", "agent_id": "...", "agent_name": "...", ...}
    """
    try:
        # -- Step 1: get this machine's host_id --
        my_host_id = config.get("host_id", "")
        if not my_host_id:
            return json.dumps({"status": "failed", "error": "无法获取本机 host_id,请检查 agx_config.json"})
        my_network_id = config.get("network_id", "")
        work_dir = os.path.expanduser(config.get("work_dir", "~/.agx"))

        # -- Step 2: query the agent's full information (raw SQL, MySQL not cache) --
        # agx_get_agent does not return large fields such as agents_md/soul_md (filtered by _NO_CACHE_MD),
        # while deployment needs every field, hence the raw SQL query.
        rows = executeSql(
            """SELECT agent_id, agent_name, agent_role, host_id, active_status,
                      deploy_status, project_id, network_id, base_model, agent_type,
                      agents_md, soul_md, identity_md, user_md, tools_md,
                      notebook_md, heartbeat_md
               FROM agx_agent_registry WHERE agent_id = %s""",
            (agent_id,),
        )
        if not rows:
            return json.dumps({"status": "failed", "error": f"Agent '{agent_id}' 不存在"})
        agent = rows[0]

        agent_name = agent.get("agent_name", "")
        agent_role = agent.get("agent_role", "")
        host_id = agent.get("host_id", "")
        active_status = str(agent.get("active_status", "0"))
        _deploy_status = agent.get("deploy_status", "pending")
        project_id = agent.get("project_id", "")
        _network_id = agent.get("network_id", my_network_id)
        _base_model = agent.get("base_model", "")
        _agent_type = (agent.get("agent_type") or "openclaw").strip().lower()

        # -- Step 3: validate --
        # * multi-instance: "this machine" = every host_id on this physical machine (not a single config.host_id),
        #   otherwise an agent deployed to the second instance would be wrongly judged "not from this machine".
        _local = _local_host_ids()
        if host_id not in _local:
            return json.dumps(
                {
                    "status": "failed",
                    "error": f"Agent '{agent_id}' 不属于本机(归属: {host_id}, 本机: {','.join(sorted(_local)) or my_host_id})",
                }
            )
        if active_status != "1":
            return json.dumps(
                {
                    "status": "failed",
                    "error": f"Agent '{agent_id}' 已停用(active_status={active_status}),无法部署",
                }
            )
        if _deploy_status == "template":
            return json.dumps(
                {
                    "status": "failed",
                    "error": f"Agent '{agent_id}' 是模板 Agent(deploy_status=template),不允许部署",
                }
            )

        # -- Step 4: idempotency check (state + files) --
        agent_dir = _agent_dir(work_dir, agent_id, "agent")
        workspace_dir = _agent_dir(work_dir, agent_id, "workspace")
        env_path_check = os.path.join(workspace_dir, "agx.env")
        if _deploy_status == "deployed" and not force:
            return json.dumps(
                {
                    "status": "skipped",
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "deploy_status": "deployed",
                    "reason": "Agent 已部署(deploy_status=deployed)。使用 force=true 强制重新部署。",
                }
            )
        if not force and os.path.isfile(env_path_check):
            return json.dumps(
                {
                    "status": "skipped",
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "reason": "文件已落地(agx.env 存在),跳过。使用 force=true 强制重新部署。",
                }
            )

        # -- Step 5: mark deploying --
        agx_update_agent(caller_agent_id, agent_id, {"deploy_status": "deploying"})

        # -- Step 5.5: first-deploy memory isolation (approved by Lao Feng on 2026-09-28) --
        # a cloned agent whose workspace directory reuses old leftovers may carry the template/parent instance's
        # MEMORY.md / memory/*.md. Here the memory is **moved into** .memory_quarantine_<ts>/:
        # move, never delete; reversible; idempotent. It is not physically deleted and does not touch a running agent's normal memory
        # (only this deploy's target agent workspace is affected).
        memory_quarantine = {}
        try:
            memory_quarantine = _quarantine_workspace_memory(workspace_dir)
            if memory_quarantine.get("quarantined"):
                log(
                    f"[agx_deploy_agent] 记忆隔离: {agent_id} → "
                    f"{memory_quarantine['dir']} | {memory_quarantine['quarantined']}"
                )
        except Exception as e:
            # an isolation failure does not block deployment (alert only), but it is recorded for audit
            memory_quarantine = {"error": str(e)}
            log(f"[agx_deploy_agent] 记忆隔离失败: {agent_id} | {e}", "WARN")

        # -- Step 6: create the directory structure --
        notices_dir = os.path.join(workspace_dir, "notices")
        docs_dir = os.path.join(workspace_dir, "docs")

        for d in (agent_dir, notices_dir, docs_dir):
            os.makedirs(d, exist_ok=True)
            log(f"[agx_deploy_agent] 目录已创建: {d}")

        # -- Step 7: write the config files (DB -> disk) --
        # wx mode: skip when the file exists (unless force=true)
        write_mode = "w" if force else "x"
        files_written = []
        files_skipped = []

        file_map = {
            "AGENTS.md": agent.get("agents_md", ""),
            "SOUL.md": agent.get("soul_md", ""),
            "IDENTITY.md": agent.get("identity_md", ""),
            "USER.md": agent.get("user_md", ""),
            "TOOLS.md": agent.get("tools_md", ""),
            "NOTEBOOK.md": agent.get("notebook_md", ""),
            "HEARTBEAT.md": agent.get("heartbeat_md", ""),
        }

        for filename, content in file_map.items():
            filepath = os.path.join(workspace_dir, filename)
            try:
                if not force and os.path.exists(filepath):
                    files_skipped.append(filename)
                else:
                    with open(filepath, write_mode, encoding="utf-8") as f:
                        f.write(content or "")
                    files_written.append(filename)
            except FileExistsError:
                files_skipped.append(filename)
            except Exception as e:
                return json.dumps(
                    {
                        "status": "failed",
                        "error": f"写入文件 {filename} 失败: {e}",
                    }
                )

        # agx.env is always overwritten (identity information may change)
        env_path = os.path.join(workspace_dir, "agx.env")
        env_content = f"""# agx.env - AgentGeeX 身份配置
# 由 agx_deploy_agent 生成
# 此文件由系统管理,请勿手动修改
AGX_AGENT_ID="{agent_id}"
AGX_AGENT_NAME="{agent_name}"
AGX_AGENT_ROLE="{agent_role}"
AGX_PROJECT_ID="{project_id}"
"""
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(env_content)
        files_written.append("agx.env")

        # an empty notices/self.md
        self_md_path = os.path.join(notices_dir, "self.md")
        if not os.path.exists(self_md_path) or force:
            with open(self_md_path, "w", encoding="utf-8") as f:
                f.write("")

        log(f"[agx_deploy_agent] 文件写入完成: written={len(files_written)}, skipped={len(files_skipped)}")

        # -- Step 7.5: write the landing locations back to the DB (* added 2026-11-05) --
        # Root cause: the deployer materialized every file but never wrote "where it was installed" back to the DB.
        #   Evidence: three test agents deployed, deploy_status=deployed, all files present,
        #   yet agent_root_dir / agent_workspace_dir were still NULL.
        #   Consequence: an audit of "which machine and path is this agent deployed to" cannot be answered from the DB;
        #   cleanup scripts / migration scripts / drift detection all lose their single authoritative source for the landing point.
        # Why write it in the deployer instead of letting the host agent fill it in:
        #   the path is computed and created by the deployer itself, so what it knows is the fact;
        #   letting an LLM backfill downgrades "fact" into "report", adding another place where it can be wrong.
        try:
            # * root is the agent home (.../{agent_id}), not .../{agent_id}/agent.
            #   That agent/ subdirectory is only created by the deployer and never written to; it is a leftover of the old layout (an empty shell).
            #   Treating the empty shell as the "root" makes the field and the workspace siblings, splitting the semantics:
            #   root should be the parent directory of the workspace. Evidence on 118: agent/ is empty, workspace/
            #   holds 10 files, and root recorded that empty directory.
            #   Note: this is not OpenClaw's ~/.openclaw-{profile}/agents/{id}/agent,
            #   which is OpenClaw's own directory (it changes with the profile) and is not managed by this table.
            _agent_home = _agent_dir(work_dir, agent_id)
            execute_sql(
                "UPDATE agx_agent_registry SET agent_root_dir=%s, agent_workspace_dir=%s "
                "WHERE agent_id=%s",
                (_agent_home, workspace_dir, agent_id),
                commit=True,
            )
            log(f"[agx_deploy_agent] 落点已回写: {_agent_home} | {workspace_dir}")
            # sync the two fields into the Redis cache.
            # * write only when the hash already exists: hset on a non-existent key creates a truncated entity
            #   holding only those two fields (the same class of incident as 2026-10-22).
            try:
                _rc = get_redis_client()
                _akey = f"{CACHE_PREFIX['agent']}{agent_id}"
                if _rc.exists(_akey):
                    _rc.hset(
                        _akey,
                        mapping={
                            "agent_root_dir": _agent_home,
                            "agent_workspace_dir": workspace_dir,
                        },
                    )
            except Exception as _ce:
                log(f"[agx_deploy_agent] 落点缓存同步失败: {_ce}", "WARN")
        except Exception as _de:
            # a failed landing-point write-back does not block deployment (the files are already written; just report it honestly)
            log(f"[agx_deploy_agent] 落点回写 DB 失败: {_de}", "WARN")

        # -- Step 8: register with OpenClaw (push to ws_client, asynchronous pending) --
        # the correlation ID = the task_run_uuid of "this run of the calling host agent" -- it runs through
        # the whole MCP -> ws_client -> callback chain and decides which session agx_deploy_callback is delivered to.
        # after ws_client finishes agents.create it calls the host agent back via sessions.send.
        #
        # * 2026-10-13 FIX (BUG-A2 root fix, plan A):
        #   the old implementation did `task_run_uuid or run_deploy_<random>` -- as soon as the executor omitted task_run_uuid,
        #   it silently degraded to a random id and host_session_id stayed empty -> the ws_client callback branch
        #   `if host_sid and ok` was false -> the callback was silently lost -> the execution stayed running forever
        #   (a recurrence of BUG-A2 recorded on 2026-09-22).
        #   Changed to: (1) when missing, derive it from the caller's latest running execution;
        #         (2) if still none -> fail explicitly (fail fast, never silently degrade to having no callback target).
        #   Double safety: on the execution side _inject_session_context feeds this run's task_run_uuid to the agent explicitly.
        resolved_run_uuid = task_run_uuid
        if not resolved_run_uuid:
            fb = execute_sql(
                "SELECT task_run_uuid FROM agx_task_execution "
                "WHERE agent_id = %s AND run_status = 'running' "
                "ORDER BY started_at DESC LIMIT 1",
                (caller_agent_id,),
            )
            if fb and fb[0].get("task_run_uuid"):
                resolved_run_uuid = fb[0]["task_run_uuid"]
                log(
                    f"[agx_deploy_agent] task_run_uuid 缺失, 兜底推导 → "
                    f"{resolved_run_uuid} (caller={caller_agent_id} 最新 running execution)",
                    "WARN",
                )

        if not resolved_run_uuid:
            _fail_deploy(agent_id, caller_agent_id, agent_name, work_dir)
            return json.dumps(
                {
                    "status": "failed",
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "error": (
                        "无法确定部署回调关联 ID: 未传 task_run_uuid 且 caller"
                        f"('{caller_agent_id}') 无 running execution。请以「本次执行的 "
                        "task_run_uuid」调用 agx_deploy_agent(见 exec_instruction 顶部"
                        "【会话上下文】块的「你本次执行的执行编号」)。已回滚本次物化文件。"
                    ),
                },
                ensure_ascii=False,
            )

        deploy_run_uuid = resolved_run_uuid

        # query the host agent's (caller's) session_id, for the ws_client callback
        host_exec = execute_sql(
            "SELECT session_id FROM agx_task_execution WHERE task_run_uuid = %s",
            (deploy_run_uuid,),
        )
        host_session_id = ""
        if host_exec and host_exec[0].get("session_id"):
            host_session_id = host_exec[0]["session_id"]

        if not host_session_id:
            # there is an execution but no session_id -> the callback still has nowhere to go, so it also fails explicitly (not silently)
            _fail_deploy(agent_id, caller_agent_id, agent_name, work_dir)
            return json.dumps(
                {
                    "status": "failed",
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "error": (
                        f"部署回调目标会话为空: execution '{deploy_run_uuid}' 无 session_id。"
                        "已回滚本次物化文件。"
                    ),
                },
                ensure_ascii=False,
            )

        # -- Step 8a: DSH platform branch -- no OpenClaw registration, send the deploy-complete callback directly --
        # * decided by Lao Feng on 2026-11-16: do not break the existing "deploy -> callback -> host agent finishes" flow.
        #   A DSH agent is only missing the agents.create step, so the callback is [sent directly, without waiting for registration];
        #   the message carries a platform marker; on receipt the host agent still follows Step 4 of the agent_deploy manual
        #   to the end (write back deploy_status=deployed + finish). Same flow, only the wait is skipped.
        #
        #   Why mode=send_text instead of inventing a new command:
        #   send_text is the existing fire-and-forget bypass (daemon 1745 -> ws_client 2127
        #   -> sessions.send); it creates no execution and subscribes to no answer -- exactly the semantics of "send a message",
        #   and ws_client needs no new branch for it.
        #
        #   * the queue delivery target must be [the host where the caller (host agent) lives], not the deployed agent's
        #     host: a DSH-only logical host (e.g. test_host_srv) has no ws_client, so pushing there leaves it unconsumed.
        if _agent_type == "dsh":
            _cb_rows = executeSql(
                "SELECT host_id FROM agx_agent_registry WHERE agent_id = %s",
                (caller_agent_id,),
            )
            _cb_host = ((_cb_rows[0].get("host_id") if _cb_rows else "") or "").strip() or host_id
            _dsh_callback = {
                "type": "agx_deploy_callback",
                "status": "succeeded",
                "run_uuid": deploy_run_uuid,
                "agent_id": agent_id,
                "agent_name": agent_name,
                "platform": "dsh",          # * marker: DSH platform
                "skip_openclaw": True,      # * marker: no OpenClaw registration step
                "result": {
                    "ok": True,
                    "platform": "dsh",
                    "note": "DSH agent 不注册 OpenClaw；部署产物 = registry 行 + workspace（桥侧按需建）。请按 Step 4 回填并结案。",
                },
            }
            ws_task = {
                "run_uuid": "",
                "agent_id": caller_agent_id,
                "session_key": host_session_id,
                "instruction": json.dumps(_dsh_callback, ensure_ascii=False),
                "mode": "send_text",
                "timestamp": datetime.now().isoformat(),
            }
            ws_queue_key = f"agx:ws:task:{_cb_host}"
            try:
                r = get_redis_client()
                r.rpush(ws_queue_key, json.dumps(ws_task, ensure_ascii=False))
                log(
                    f"[agx_deploy_agent] DSH 部署回调已推送 | {ws_queue_key} "
                    f"| run={deploy_run_uuid} | target_session={host_session_id}"
                )
                openclaw_register_result = "skipped_dsh"
            except Exception as e:
                _fail_deploy(agent_id, caller_agent_id, agent_name, work_dir)
                return json.dumps(
                    {"status": "failed", "error": f"推送 DSH 部署回调到 Redis 失败: {e}"},
                    ensure_ascii=False,
                )

        # -- Step 8b: openclaw platform (original logic, not a single line changed) --
        else:
            ws_task = {
                "run_uuid": deploy_run_uuid,
                "command": "register_agent",
                "agent_id": caller_agent_id,  # ws_task is executed by the host agent itself (the local ws_client)
                "host_session_id": host_session_id,  # the session key of the callback target
                "instruction": json.dumps(
                    {
                        "agent_id": agent_id,
                        "agent_name": agent_name,
                        "agent_dir": agent_dir,
                        "workspace": workspace_dir,
                    },
                    ensure_ascii=False,
                ),
                # * fixed an incident on 2026-10-29: it used to be 30s, which was too tight.
                #   Measured (gateway with 88 agents): agents.create writing config + reloading can exceed 30s,
                #   was hard-interrupted by ws_client's asyncio.wait_for -> the callback was lost forever ->
                #   the deploy execution sat at running for 10 minutes (run_cagent_deploy_ab02c5bcf83d).
                #   180s works with ws_client's register_call_timeout(150s): the inner layer times out first
                #   and can log and return failed, while the outer 180s is only a safety net.
                "timeout_seconds": 180,
                "timestamp": datetime.now().isoformat(),
            }
            # * multi-instance: a registration request must be pushed to the ws queue of [the host the agent belongs to],
            #   not to the gateway process's own host (which is the primary instance).
            ws_queue_key = f"agx:ws:task:{host_id}"
            try:
                r = get_redis_client()
                r.rpush(ws_queue_key, json.dumps(ws_task, ensure_ascii=False))
                log(f"[agx_deploy_agent] 注册请求已推送 WS 队列: {ws_queue_key} | run_uuid={deploy_run_uuid}")
                openclaw_register_result = "pending"
            except Exception as e:
                _fail_deploy(agent_id, caller_agent_id, agent_name, work_dir)
                return json.dumps(
                    {
                        "status": "failed",
                        "error": f"推送注册请求到 Redis 失败: {e}",
                    }
                )

        # -- Step 9: project_manager only - copy project-management.md --
        if agent_role == "project_manager":
            # * 2026-11-24: docs/ was reorganised into subdirectories, so both src and dst now use the
            #   nested layout. dst is a directory that does not exist yet on a fresh deploy, hence the
            #   makedirs -- without it copy2 would raise FileNotFoundError and the file would silently
            #   never be copied (caught below as a WARN).
            src = os.path.join(work_dir, "docs", "internal", "project-management.md")
            dst = os.path.join(docs_dir, "internal", "project-management.md")
            try:
                if os.path.exists(src):
                    import shutil

                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    log(f"[agx_deploy_agent] internal/project-management.md 已复制到 {dst}")
            except Exception as e:
                log(f"[agx_deploy_agent] 复制 internal/project-management.md 失败: {e}", "WARN")

        # -- Step 10: verify the deployment result (self-check) --
        verification = {}

        # 10.1 filesystem verification
        # the DB fields of SOUL/IDENTITY/USER/TOOLS/NOTEBOOK/HEARTBEAT may be empty strings,
        # and an empty field writes an empty file with size=0, so verification must distinguish:
        #   - core files (AGENTS.md, agx.env): must exist and be non-empty
        #   - other files: existing is enough (size>=0); writing an empty file for an empty DB field is a valid state
        for fname in (
            "AGENTS.md",
            "SOUL.md",
            "IDENTITY.md",
            "USER.md",
            "TOOLS.md",
            "NOTEBOOK.md",
            "HEARTBEAT.md",
            "agx.env",
        ):
            fpath = os.path.join(workspace_dir, fname)
            exists = os.path.isfile(fpath)
            nonzero = exists and os.path.getsize(fpath) > 0
            if fname in ("AGENTS.md", "agx.env"):
                verification[f"file_{fname}"] = nonzero  # core files must be non-empty
            else:
                verification[f"file_{fname}"] = exists  # other files only need to exist

        # agx.env content integrity
        with open(env_path, encoding="utf-8") as f:
            env_text = f.read()
        verification["env_has_agent_id"] = "AGX_AGENT_ID" in env_text
        verification["env_has_project_id"] = "AGX_PROJECT_ID" in env_text

        # 10.2 OpenClaw registration verification
        verification["openclaw_registered"] = openclaw_register_result == "ok"

        # 10.3 self-check summary
        core_checks = [
            verification.get("file_agx.env", False),
            verification.get("env_has_agent_id", False),
            verification.get("env_has_project_id", False),
        ]
        all_core_ok = all(core_checks)
        _all_checks_ok = all(v for v in verification.values() if isinstance(v, bool))

        # -- Step 11: mark the deploy state and finish --
        if not all_core_ok:
            _fail_deploy(agent_id, caller_agent_id, agent_name, work_dir)
            return json.dumps(
                {
                    "status": "failed",
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "error": f"自检失败: {json.dumps(verification, ensure_ascii=False)}",
                    "verification": verification,
                },
                ensure_ascii=False,
                default=str,
            )

        # 11.1 the files deployed successfully; registration is completed asynchronously by ws_client
        # after ws_client finishes agents.create it calls the host agent's session back via sessions.send
        #   (callback content: type=agx_deploy_callback / status=succeeded / run_uuid / agent_id
        #    / next_action -- since 2026-11-05 the callback states directly what to do next)
        #
        # * corrected 2026-11-05: deploy_status is **not** written by ws_client.
        #   ws_client is deliberately lightweight (Redis only, zero DB dependency); it only sends the callback.
        #   Flipping deploy_status from "deploying" to "deployed" is the host agent's job,
        #   see step 1 of "after receiving the callback" in the agent_deploy task manual.
        #   The original comment here said "updated by ws_client", which did not match the implementation and has been corrected --
        #   that wrong comment would make people assume the state flips itself and skip checking, until a gate rejects work.

        return json.dumps(
            {
                "status": "succeeded",
                "agent_id": agent_id,
                "agent_name": agent_name,
                "agent_role": agent_role,
                # * corrected 2026-11-05: this must be the agent's host_id, not the gateway process's
                #   config.host_id. Commit 5a68bfb (multi-instance rework) changed routing to
                #   ws_queue_key=f"agx:ws:task:{host_id}" (agent level, correct),
                #   but this line of the returned body was missed, so test_srv_a2 / openclaw2 / test_host_srvc all reported
                #   being deployed to the primary instance (test_srv_a / test_host_srv) -- right routing, wrong echo.
                "host_id": host_id,
                "workspace": workspace_dir,
                "files_written": files_written,
                "files_skipped": files_skipped,
                "memory_quarantine": memory_quarantine,
                # the DSH platform has no OpenClaw registration step, so report it honestly as skipped_dsh (not pending)
                "openclaw_registered": ("skipped_dsh" if _agent_type == "dsh" else "pending"),
                "task_run_uuid": deploy_run_uuid,
                "verification": verification,
            },
            ensure_ascii=False,
            default=str,
        )

    except Exception as e:
        # when the top-level exception is caught, agent_name/work_dir may be unbound; use get as a fallback
        _agent_name = agent_name if "agent_name" in dir() else ""
        _work_dir = work_dir if "work_dir" in dir() else ""
        _fail_deploy(agent_id, caller_agent_id, _agent_name, _work_dir)
        return _log_exception("agx_deploy_agent", e)


@mcp.tool()
@require_level(2)
def agx_update_task(caller_agent_id: str, task_id: str, updates: dict) -> str:
    """
    [Update task] Dynamically update selected fields of agx_task_base, dual-writing MySQL + Redis.

    Note: agx_task_base stores an agent's capability templates; it is not bound to a project.

    Task description, priority and payload can be changed here.

     run_status is a protected field and cannot be modified through this interface --

       please use agx_dispatch_task (-> processing) or

       agx_finish_task (-> succeeded/failed).

    Args:

        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        task_id (str): task ID, e.g. "task_16931d412509".

        updates (dict): the field dictionary to update.

            Updatable fields: x_desc, target_object, payload, priority, is_private, active_status.

            Example: {"x_desc": "new analysis: filtered data"}

                   {"priority": "5", "payload": {"max_records": 1000}}

                   {"is_private": 1}  (visible only to the owning agent / project admin)

                   {"active_status": "0"}  (disable this task template)

    Returns:

        str: success -> "Task task_xxx updated: [field1, field2]"

             failure -> "Error: ..."

    Example:

        agx_update_task(

            task_id="task_16931d412509",

            updates={"priority": "3", "x_desc": "high-priority analysis"}

        )

    """
    # -- resource-level ownership check: a task belongs to agx_task_base.agent_id --
    # Note: agx_task_base is not bound to a project (no project_id column), so only the owning agent is judged;
    #     the project dimension is covered by the global roles (host_manager/workshop_manager) and the administrators.
    _own_rows = execute_sql("SELECT agent_id FROM agx_task_base WHERE task_id = %s", (task_id,))
    if not _own_rows:
        return f"Error: task '{task_id}' not found"
    _deny = _check_resource_ownership(caller_agent_id, _own_rows[0].get("agent_id", ""), "")
    if _deny:
        return f"Error: {_deny}"
    return _do_update("task", "task_id", task_id, updates)


@mcp.tool()
@require_level(3)
def agx_update_task_by_labs(caller_agent_id: str, task_labs: str, x_desc: str, scope: str = "own_project", is_private: int = None) -> str:
    """
    [Bulk update task content by task_labs] Using task_labs (the task file name, e.g. "book_project_orchestrate")
    as the key, bulk-update the x_desc field of same-named tasks (optionally is_private too).

    * Scope (scope, tightened 2026-09-14) -- prevents accidental changes across the whole network
        - "own_project" (default): only updates same-named tasks of [agents inside the caller's own project group].
          A PM goes through this by default; scope = its own project group, matching the "a PM only manages its own project" positioning.
        - "global": updates same-named tasks across the whole network. **Only host_manager / workshop_manager /
          agent_manager (incl. agx_agentfather) may use it**; anyone else is rejected outright.
        Regardless of scope, the update first validates: if the targets include a task that is [outside this scope
        and not your own], it is rejected (unless the caller is a global role).

    When to use:
    - after editing a tasks/xxx.md file, sync the same-named tasks of every agent in this project group
    - (global) after changing a template/shared task, sync it everywhere (needs a global role)

    Difference from agx_update_task:
    - agx_update_task: precise single-row update by task_id, supports multiple fields
    - agx_update_task_by_labs: bulk x_desc update by task_labs, content only

    Args:

        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        task_labs (str): task file name (the task_labs column), e.g. "book_project_orchestrate".
        x_desc (str): the full task manual content (Markdown).
        scope (str): scope, "own_project" (default) | "global". See above.
        is_private (int, optional): optional. When passed, is_private is updated as well (0=visible to all,
            1=visible only to the owning agent / project admin / global roles). When omitted only x_desc changes.

    Returns:

        str: success -> "Updated N task(s) with task_labs='{task_labs}' (scope={scope}): [task_id1, ...]"
             failure -> "Error: ..."

    Example:

        agx_update_task_by_labs(
            caller_agent_id="your_agent_id",
            task_labs="book_project_orchestrate",
            x_desc="---
reply_mode: result
---

# ..."
        )
    """
    try:
        scope = (scope or "own_project").strip().lower()
        if scope not in ("own_project", "global"):
            return f"Error: invalid scope='{scope}', must be 'own_project' or 'global'"

        # --- resolve the caller identity ---
        caller_role = _resolve_agent_role(caller_agent_id)
        global_roles = ("host_manager", "workshop_manager", "agent_manager")
        is_global = caller_role in global_roles or caller_agent_id in _sys_names(SYS_AGENTFATHER)

        # --- take all same-named tasks and join the owning agent's project_id ---
        rows = execute_sql(
            """SELECT t.task_id, t.agent_id, a.project_id
               FROM agx_task_base t
               LEFT JOIN agx_agent_registry a ON t.agent_id = a.agent_id
               WHERE t.task_labs = %s""",
            (task_labs,),
        )
        if not rows:
            return f"Error: no task found with task_labs='{task_labs}'"

        # --- scope filtering ---
        if scope == "global":
            if not is_global:
                return (
                    f"权限不足:scope='global' 需要全局角色(host_manager / workshop_manager / "
                    f"agent_manager)。caller '{caller_agent_id}' role={caller_role}。"
                    f"请改用 scope='own_project'。"
                )
            target_rows = rows
        else:  # own_project
            if is_global:
                target_rows = rows  # global roles are not restricted by project
            else:
                # what the caller may change: itself + the project groups it manages
                caller_project_ids = set()
                caller_own = execute_sql(
                    "SELECT project_id FROM agx_agent_registry WHERE agent_id = %s", (caller_agent_id,)
                )
                if caller_own and caller_own[0].get("project_id"):
                    caller_project_ids.add(caller_own[0].get("project_id"))
                admin_rows = execute_sql(
                    "SELECT project_id FROM agx_project WHERE admin_agent_id = %s AND active_status = 1",
                    (caller_agent_id,),
                )
                for ar in admin_rows:
                    if ar.get("project_id"):
                        caller_project_ids.add(ar.get("project_id"))

                target_rows = []
                blocked = []
                for r in rows:
                    if r.get("agent_id") == caller_agent_id:
                        target_rows.append(r)  # its own is always changeable
                    elif r.get("project_id") and r.get("project_id") in caller_project_ids:
                        target_rows.append(r)  # the ones in this project group
                    else:
                        blocked.append(r)
                if blocked and not target_rows:
                    return (
                        f"权限不足:task_labs='{task_labs}' 的 {len(blocked)} 个 task 均不在你的项目组内,"
                        f"且非你自己。若需全量同步请由全局角色使用 scope='global'。"
                    )
                if blocked:
                    log(
                        f"agx_update_task_by_labs: caller {caller_agent_id} skipped {len(blocked)} out-of-scope task(s)",
                        "WARN",
                    )

        if not target_rows:
            return f"Error: no in-scope task found with task_labs='{task_labs}' (scope={scope})"

        task_ids = [r["task_id"] for r in target_rows]

        # --- execute the update (row by row, only in-scope task_ids) ---
        # when is_private is None only x_desc changes; when passed it is updated too (0/1 are valid values, so use is not None)
        update_private = is_private is not None
        priv_val = None
        if update_private:
            try:
                priv_val = int(is_private)
            except (TypeError, ValueError):
                return f"Error: invalid is_private='{is_private}', must be 0 or 1"
            if priv_val not in (0, 1):
                return f"Error: invalid is_private='{is_private}', must be 0 or 1"
        for tid in task_ids:
            if update_private:
                execute_sql(
                    "UPDATE agx_task_base SET x_desc = %s, is_private = %s, update_dt = NOW() WHERE task_id = %s",
                    (x_desc, priv_val, tid),
                    commit=True,
                )
            else:
                execute_sql(
                    "UPDATE agx_task_base SET x_desc = %s, update_dt = NOW() WHERE task_id = %s",
                    (x_desc, tid),
                    commit=True,
                )

        # write verification: read the first row back and compare lengths, detecting JSON-RPC transport truncation
        sent_len = len(x_desc)
        if sent_len > 500:
            verify_rows = execute_sql(
                "SELECT task_id, x_desc FROM agx_task_base WHERE task_id = %s", (task_ids[0],)
            )
            if verify_rows:
                stored_val = verify_rows[0].get("x_desc", "") or ""
                stored_len = len(stored_val)
                if stored_len != sent_len:
                    return f"Error: TRUNCATION DETECTED for x_desc! Sent {sent_len} chars but MySQL stored {stored_len} chars. Likely JSON-RPC transport truncation."

        # Redis sync: sync the cache row by row
        for tid in task_ids:
            try:
                sync_fields = {"x_desc": x_desc, "update_dt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                if update_private:
                    sync_fields["is_private"] = priv_val
                _sync_entity("task", tid, sync_fields)
            except Exception as e:
                log(f"task {tid} Redis sync failed: {e}", "WARN")

        # event publishing
        event_fields = ["x_desc"] + (["is_private"] if update_private else [])
        for tid in task_ids:
            try:
                _publish_event("task", "updated", tid, {"fields": event_fields})
            except Exception as e:
                log(f"task {tid} Pub/Sub event failed: {e}", "WARN")

        ids_str = ", ".join(task_ids)
        extra = f", is_private={priv_val}" if update_private else ""
        return f"Updated {len(task_ids)} task(s) with task_labs='{task_labs}' (scope={scope}{extra}): [{ids_str}]"

    except Exception as e:
        log(f"agx_update_task_by_labs error: {e}", "ERROR")
        return f"Error: {e}"


@mcp.tool()
@require_level(5)
def agx_archive_task(caller_agent_id: str, task_id: str) -> str:
    """
    [Archive task] Set the given task to active_status=0 (soft delete; audit history is kept).

    * Difference from agx_delete_task (prefer this tool):
        - archive: soft delete, keeps the row and history, only removes it from active lists (recoverable)
        - delete : physical delete, unrecoverable (for genuinely dirty data)

    * Permission (resource-level ownership, consistent with the global rule):
        (a) the resource-owning agent (caller == task.agent_id)
        (b) the owning project's admin (task.agent_id -> project.admin_agent_id)
        (c) global roles (host_manager / workshop_manager)

    Args:
        caller_agent_id (str): caller agent ID.
        task_id (str): ID of the task to archive.

    Returns:
        str: success message / error.
    """
    try:
        rows = execute_sql(
            "SELECT task_id, agent_id, task_labs FROM agx_task_base WHERE task_id = %s", (task_id,)
        )
        if not rows:
            return f"Error: task '{task_id}' not found"

        owner_agent_id = rows[0].get("agent_id", "") or ""
        project_id = ""
        if owner_agent_id:
            ar = execute_sql("SELECT project_id FROM agx_agent_registry WHERE agent_id = %s", (owner_agent_id,))
            if ar:
                project_id = ar[0].get("project_id", "") or ""

        deny = _check_resource_ownership(caller_agent_id, owner_agent_id, project_id)
        if deny:
            return f"Error: {deny}"

        execute_sql(
            "UPDATE agx_task_base SET active_status = 0, update_dt = NOW() WHERE task_id = %s",
            (task_id,),
            commit=True,
        )
        try:
            _sync_entity("task", task_id, {"active_status": "0", "update_dt": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
            _publish_event("task", "archived", task_id)
        except Exception as e:
            log(f"task {task_id} cache/event sync failed: {e}", "WARN")

        return f"Task '{task_id}' archived (active_status=0)."
    except Exception as e:
        return _log_exception("agx_archive_task", e)


@mcp.tool()
@require_level(5)
def agx_delete_task(caller_agent_id: str, task_id: str) -> str:
    """
    [Physically delete task] Delete a task from agx_task_base for good (unrecoverable).

    !! Prefer agx_archive_task (soft delete); this tool is only for cleaning genuinely dirty data.

    * Permission: same as agx_archive_task (resource-level ownership: owning agent / project admin / global roles).
    * Protection: if the task already has historical executions (referenced by agx_task_execution.task_id),
        physical deletion is refused (to avoid breaking the execution chain), suggesting archive instead.

    Args:
        caller_agent_id (str): caller agent ID.
        task_id (str): ID of the task to delete.

    Returns:
        str: success message / error.
    """
    try:
        rows = execute_sql(
            "SELECT task_id, agent_id, task_labs FROM agx_task_base WHERE task_id = %s", (task_id,)
        )
        if not rows:
            return f"Error: task '{task_id}' not found"

        owner_agent_id = rows[0].get("agent_id", "") or ""
        project_id = ""
        if owner_agent_id:
            ar = execute_sql("SELECT project_id FROM agx_agent_registry WHERE agent_id = %s", (owner_agent_id,))
            if ar:
                project_id = ar[0].get("project_id", "") or ""

        deny = _check_resource_ownership(caller_agent_id, owner_agent_id, project_id)
        if deny:
            return f"Error: {deny}"

        # protection: physical deletion is not allowed when execution history exists
        exec_refs = execute_sql(
            "SELECT COUNT(*) AS n FROM agx_task_execution WHERE task_id = %s", (task_id,)
        )
        if exec_refs and int(exec_refs[0].get("n", 0) or 0) > 0:
            return (
                f"Error: task '{task_id}' 已有 {exec_refs[0]['n']} 条执行历史(agx_task_execution 引用),"
                f"拒绝物理删除。请改用 agx_archive_task 归档。"
            )

        # * 2026-10-21: fetch the whole row before deleting from the DB, so _invalidate_entity can locate the reverse indexes
        #   (a task needs agent_id to clear agx:idx:task:by_agent:{agent_id})
        _pre_row = None
        try:
            _r0 = execute_sql("SELECT * FROM agx_task_base WHERE task_id = %s", (task_id,))
            _pre_row = _r0[0] if _r0 else None
        except Exception as _e:
            log(f"agx_delete_task prerow fetch failed: {_e}", "WARN")

        execute_sql("DELETE FROM agx_task_base WHERE task_id = %s", (task_id,), commit=True)
        try:
            _invalidate_entity("task", task_id, _pre_row)
            _publish_event("task", "deleted", task_id)
        except Exception as e:
            log(f"task {task_id} cache/event cleanup failed: {e}", "WARN")

        return f"Task '{task_id}' deleted permanently."
    except Exception as e:
        return _log_exception("agx_delete_task", e)


@mcp.tool()
@require_level(3)
def agx_update_execution(caller_agent_id: str, task_run_uuid: str, updates: dict) -> str:
    """
    [Update execution] Dynamically update selected fields of agx_task_execution, dual-writing MySQL + Redis.

    Used to modify the execution instruction, bind a session, etc. while an execution is in flight.

     run_status is a protected field and cannot be modified through this interface --

       please use agx_finish_task (-> succeeded/failed).

     Permission model: four identities -- executor / initiator / project admin / global role -- authorized per field
    (since 2026-10-29, decided field by field by _check_execution_field_policy):

        Identities:
          owner  = executor             (caller_agent_id == execution.agent_id)
          sender = initiator            (caller_agent_id == execution.send_agent_id)
          admin  = owning project admin (caller_agent_id == project.admin_agent_id)
          global = global role          (host_manager / workshop_manager)

        Fields writable per identity:
          all four: timeout_seconds / exec_instruction / alert_note / scheduled_at / x_desc
          owner+admin+global only: session_id / send_agent_id / send_host_id /
                        prev_task / next_task / plan_id / exec_name /
                        reply_read / reply_read_at
          -> the initiator may change "how it runs" and "record the conclusion", but not "who it is assigned to /
             which chain it hangs on / which session it binds to".

        Constraints:
          · timeout_seconds must be an integer in [60, watchdog_max_task_timeout or 86400] seconds
          · extending only; shortening (which immediately trips the 0.7/0.8/0.9 wakeup tiers) is limited to admin / global
          · fields not listed above fall back to the generic ownership check of _check_resource_ownership

    Args:

        task_run_uuid (str): execution instance ID, e.g. "run_5c3bc7b2cfd2".

        updates (dict): the field dictionary to update.

            Updatable fields (14 in total): session_id, exec_instruction, x_desc, exec_name,
                        send_agent_id, send_host_id, prev_task, next_task, plan_id,
                        scheduled_at, timeout_seconds, alert_note, reply_read, reply_read_at.
            * reply_read / reply_read_at should generally not be written directly -- use agx_ack_receipt
              (it includes the ownership check and idempotency).

            Example: {"exec_instruction": "switch to the latest dataset for the analysis"}

                   {"session_id": "agent_session_001",

                    "x_desc": "second iteration"}

                   {"send_agent_id": "agt_xxx"}

    Returns:

        str: success -> "Execution run_xxx updated: [field1, field2]"

             failure -> "Error: ..."

    Example:

        agx_update_execution(

            task_run_uuid="run_5c3bc7b2cfd2",

            updates={"exec_instruction": "focus on outliers"}

        )

    """
    # * decided by Lao Feng on 2026-10-29: the field-level policy replaces the old narrow gate of "the whole batch equals alert_note".
    #   The initiator may now change exec_instruction / timeout_seconds (extend only) / alert_note / scheduled_at;
    #   timeout_seconds also gets domain validation (60..limit) and non-admins may not shorten it.
    exec_row = execute_sql(
        "SELECT agent_id, project_id, send_agent_id, timeout_seconds, run_status, needs_confirm "
        "FROM agx_task_execution WHERE task_run_uuid = %s",
        (task_run_uuid,),
    )
    if not exec_row:
        raise ValueError(f"execution '{task_run_uuid}' not found")

    _policy_err = _check_execution_field_policy(caller_agent_id, exec_row[0], updates or {})
    if _policy_err:
        raise PermissionError(_policy_err)

    return _do_update("execution", "task_run_uuid", task_run_uuid, updates)


# ============================================================

# Tools: cache monitoring tools

# ============================================================


@mcp.tool()
@require_level(0)
def agx_cache_stats(caller_agent_id: str) -> dict:
    """
    [Cache monitoring] View real-time statistics of the AgentGeeX Redis cache layer.

    Returns the cached entry count and index health of every entity type in Redis.

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity and enables an audit trail. Read-only tools are no exception -- every call chain must be traceable.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).

    Returns:

        dict: cache statistics.

    """
    try:
        r = get_redis_client()

        stats = {}

        for entity_type, prefix in CACHE_PREFIX.items():
            entity_count = len(list(r.scan_iter(match=f"{prefix}*", count=500)))

            stats[f"cache:{entity_type}"] = entity_count

        # index statistics

        for idx_name, idx_key in IDX_PREFIX.items():
            try:
                idx_type = r.type(idx_key)

                if idx_type == "zset":
                    count = r.zcard(idx_key)

                elif idx_type == "set":
                    count = r.scard(idx_key)

                else:
                    count = 0

                stats[f"idx:{idx_name}"] = count

            except Exception as exc:
                log(f"agx_cache_stats zcard({idx_name}) failed: {exc}", "WARN")
                stats[f"idx:{idx_name}"] = -1

        # status index statistics

        for s in ("running", "succeeded", "failed"):
            try:
                stats[f"idx:execution_status:{s}"] = r.zcard(f"{EXEC_STATUS_IDX}{s}")

            except Exception as exc:
                log(f"agx_cache_stats zcard execution_status:{s} failed: {exc}", "WARN")
                stats[f"idx:execution_status:{s}"] = -1

        return stats

    except Exception as e:
        return {"error": _log_exception("agx_cache_stats", e)}


@mcp.tool()
@require_level(0)
def agx_cache_get(caller_agent_id: str, entity_type: str, entity_id: str) -> str:
    """
    [Cache query] View detailed information of a given entity in the Redis cache.

    Entity types:

    - 'project': project

    - 'host': compute host

    - 'agent': agent

    - 'task': task

    - 'execution': execution

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity and enables an audit trail. Read-only tools are no exception -- every call chain must be traceable.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).

        entity_type (str): entity type.

        entity_id (str): entity ID.

    Returns:

        str: entity details as JSON.

    """
    if entity_type not in CACHE_PREFIX:
        return json.dumps({"error": f"Invalid entity_type: {entity_type}. Valid: {list(CACHE_PREFIX.keys())}"})

    try:
        data = _get_entity(entity_type, entity_id)

        if data:
            return json.dumps(data, indent=2, ensure_ascii=False)

        return json.dumps({"error": "Not found in cache"}, indent=2)

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_cache_get", e)}, indent=2)


# ============================================================

# Resources: asset overview (read from the Redis cache, faster than SQL)

# ============================================================


@mcp.tool()
@require_level(7)
def agx_cache_clear(caller_agent_id: str, entity_type: str = None, entity_id: str = None, confirm: bool = False) -> str:
    """
    [Cache clear] Empty the cache of the given entity, or all caches.

    Args:

        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        entity_type (str, optional): entity type (project/host/agent/task/execution);

                                     when omitted, all caches are cleared.

        entity_id (str, optional): entity ID; when omitted, all caches of that type are cleared.

    Returns:

        str: cleanup result.

    """
    try:
        r = get_redis_client()

        if entity_type and entity_id:
            if entity_type not in CACHE_PREFIX:
                return f"Error: Invalid entity_type: {entity_type}"

            key = f"{CACHE_PREFIX[entity_type]}{entity_id}"

            r.delete(key)
            # * corrected 2026-10-21: the original implementation deleted only the entity hash and left the indexes -> dirty indexes
            #   now it removes this entity id's membership from every index.
            #   Note: no DB row is available here, so reverse indexes (by_plan/by_task etc.) cannot be located,
            #   but the generic all/status/online indexes are cleaned;
            #   for a thorough cleanup use scripts/audit_cache_index.py --ghosts.
            _remove_from_indexes(entity_type, entity_id)

            return f"Cache cleared: {key} (+index entries)"

        elif entity_type:
            if entity_type not in CACHE_PREFIX:
                return f"Error: Invalid entity_type: {entity_type}"

            prefix = CACHE_PREFIX[entity_type]

            keys = list(r.scan_iter(match=f"{prefix}*", count=500))

            if keys:
                r.delete(*keys)

            # * corrected 2026-10-21: when clearing the cache by type, that type's index members must be removed as well,
            #   otherwise the indexes are full of ghost ids with no entity behind them.
            idx_all_key = IDX_PREFIX.get(f"{entity_type}_all")
            if keys and idx_all_key:
                for k in keys:
                    eid = k[len(prefix):]
                    try:
                        r.zrem(idx_all_key, eid)
                    except Exception:
                        pass

            return f"Cache cleared: {len(keys)} keys for {entity_type} (+index entries)"

        else:
            all_keys = []

            for prefix in CACHE_PREFIX.values():
                all_keys.extend(r.scan_iter(match=f"{prefix}*", count=500))

            for idx_key in IDX_PREFIX.values():
                all_keys.append(idx_key)

            all_keys.extend(r.scan_iter(match="agx:idx:*", count=500))

            unique = list(set(all_keys)) if all_keys else []

            # * FIX (2026-10-25, DH-064 critical): clearing everything with no arguments had no protection at all --
            #   it did r.delete(*unique) on all agx:entity:* + agx:idx:* keys (measured 1872 keys),
            #   and did [not rebuild]. The evidence chain:
            #     - agx:notified:* is outside the agx:entity:/agx:idx: prefixes -> nothing is resent, no error,
            #       the logs look perfectly normal (more insidious than the 10-25 incident)
            #     - but agx:idx:plan:all was deleted -> the watchdog's zrange returns nothing -> it advances no plan and
            #       reports no timeout (silent standstill)
            #     - entity hashes can be restored by read-through, [an index has no self-healing path at all]
            #   The fix aligns with agx_cache_rebuild: inventory the impact first; confirm=False previews without executing.
            _PROTECTED_PREFIXES = (
                "agx:notified",         # plan notification idempotency marker
                "agx:notified_fail",    # plan notification failure counter
                "agx:watchdog:",        # watchdog alert/crisis/wake state
                "agx:worker_queues:",   # queue runtime state
                "agx:host_slots:",      # host slot occupancy
                "agx:ws:",              # WS bridge runtime markers + delivery keys
                "agx:dsh:",             # DSH bridge runtime marker
                "agx:queue:",           # message queues (must never be deleted)
                "agx:lock:",            # distributed locks
            )
            # explicitly protected from deletion: these two index families have no self-healing path, and deleting them stops scheduling outright
            _CRITICAL_INDEX_PREFIXES = (
                "agx:idx:plan:",
                "agx:idx:execution:",
            )

            killable = []
            kept_protected = 0
            kept_critical_idx = 0
            for _k in unique:
                if _k.startswith(_PROTECTED_PREFIXES):
                    kept_protected += 1
                    continue
                if _k.startswith(_CRITICAL_INDEX_PREFIXES):
                    kept_critical_idx += 1
                    continue
                killable.append(_k)

            if not confirm:
                _by_prefix = {}
                for _k in killable:
                    _p = ":".join(_k.split(":")[:2])
                    _by_prefix[_p] = _by_prefix.get(_p, 0) + 1
                _preview = sorted(_by_prefix.items(), key=lambda x: -x[1])[:10]
                return json.dumps(
                    {
                        "status": "preview",
                        "executed": False,
                        "will_delete": {"total": len(killable), "by_prefix": dict(_preview)},
                        "will_keep": {
                            "protected_state_keys": kept_protected,
                            "critical_index_keys": kept_critical_idx,
                        },
                        "warnings": [
                            "本次【未执行】任何删除。请核对影响面后，传 confirm=True 重新调用。",
                            "agx:idx:plan:* 与 agx:idx:execution:* 已被显式禁删（无自愈路径）。",
                            "agx:entity:* 删后可靠 readthrough 回源；但索引不会自动重建，",
                            "  确需重建请用 agx_cache_rebuild(confirm=True)，不要用本方法。",
                            "如果只是想让某个实体生效，优先用 agx_update_* 走增量同步。",
                        ],
                    },
                    ensure_ascii=False,
                )

            if killable:
                r.delete(*killable)

            return (
                f"All cache cleared: {len(killable)} keys "
                f"(kept {kept_protected} protected state keys, "
                f"{kept_critical_idx} critical index keys)"
            )

    except Exception as e:
        return _log_exception("agx_cache_clear", e)


# ============================================================

# Tools: cache rebuild -- rebuild the Redis cache from MySQL

# ============================================================


@mcp.tool()
@require_level(7)
def agx_cache_rebuild(caller_agent_id: str, entity_type: str = None, confirm: bool = False) -> str:
    """
    [Cache rebuild] Read entity data from MySQL and rebuild the Redis cache and indexes.

    Rebuild flow:

    1. Clear every agx:* cache key of that type

    2. Query the corresponding MySQL table for all activated entities

    3. Call _sync_entity for each row to write the Redis hash and rebuild indexes

    4. Indexes include: the all index (zset), active-status indexes, network indexes, etc.

    Args:

        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        entity_type (str, optional): the entity type to rebuild.

            Allowed values: project / host / agent / agent_group / task / execution

            When omitted, all types are rebuilt.

        confirm (bool, optional): * destructive-operation confirmation switch, default False.

            Not passed / False -> [inventory only, do not execute]; returns an impact preview and refuses outright:

                {
                  "status": "preview",
                  "will_delete": {"total": 5709, "by_prefix": {"agx:entity": 5063, "agx:idx": 646}},
                  "will_keep":   {"total": 228,  "by_prefix": {"agx:notified": 185, ...}},
                  "warnings": [...]
                }

            Pass True -> actually clear and rebuild.

            * Why the default is refusal (2026-10-23 incident):
              This method previously gave no impact preview, so one call could silently wipe the whole cache.
              Measured cost: after clearing agx:idx:plan:all the watchdog lost its traversal source ->
              (a) silent standstill (no plans visible, scheduling dead);
              (b) index rebuild timing skewed -> 35 historical plan_completed events were re-sent within 44 seconds.
              Hence "look at the impact first, then decide".

    Returns:

        str: a JSON preview when confirm=False; a rebuild summary when confirm=True.

    """
    try:
        from common.mysql.mysqlservice import getRowsByFullSQl

        ENTITY_ID_COL = {
            "project": "project_id",
            "host": "host_id",
            "agent": "agent_id",
            "agent_group": "group_id",
            "agent_group_member": None,  # composite primary key, handled separately
            "task": "task_id",
            "plan": "plan_id",
            "execution": "task_run_uuid",
        }

        types_to_rebuild = [entity_type] if entity_type else [t for t in TABLE_MAP if t != "subtask"]

        results = {}

        # clean the cache keys first
        #
        # *** 2026-10-22 fix BUG-B3: the original implementation did scan match="agx:*" then [delete(*keys) unconditionally]
        #   which also deleted [business state keys], causing large collateral damage across days / on accidental triggers:
        #
        #   keys wrongly deleted (measured: wiped by a single rebuild at 2026-10-22 23:34):
        #     agx:notified:plan_completed:*  -> 130 keys. After deletion the watchdog treated every historical
        #                                     plan as "never notified" -> resent hundreds of
        #                                     plan_completed notifications at once (measured 23:34-23:38:
        #                                     ~110 sent in a row, including old plans from 2026-09-14).
        #     agx:worker_queues:*            -> queue runtime state
        #     agx:host_slots:*               -> host slot occupancy
        #     agx:ws:running / agx:dsh:running -> bridge runtime markers
        #
        #   Criterion: within agx:* only agx:entity:* (entity hashes) and agx:idx:* (indexes) are "cache";
        #         every other prefix is runtime state / idempotency markers and must be kept.
        #   The fix: clear by [allowlisted prefix] instead of deleting everything matching "agx:*".
        #
        # *** 2026-10-23 addition: on top of the allowlist, add an [explicit protection list] as a safety net.
        #   Cause: another fix used a blacklist _KEEP_PREFIXES and measured 3 families missed:
        #     agx:watchdog:alert:*   -> watchdog alert dedup state (a permanent key with ttl=-1! Missing it makes
        #                               the same timed-out task alert over and over, and once grace_until is lost
        #                               it may escalate repeatedly past the grace period)
        #     agx:watchdog:crisis    -> system crisis state
        #     agx:watchdog:wake      -> wakeup state (written by MCP, consumed by the watchdog within 2s)
        #     agx:worker_queues:*    -> the queue list
        #     agx:dsh:running:*      -> DSH bridge runtime marker
        #   A blacklist [enumerates what to keep] (miss one and it breaks); an allowlist [enumerates what to delete] (one extra does no harm).
        #   So: the allowlist is primary, plus an explicit assertion so nobody widens the allowlist in the future.
        _PROTECTED_KEY_PREFIXES = (
            "agx:notified",       # plan notification idempotency marker (the main culprit of BUG-B3)
            "agx:notified_fail",  # plan notification failure counter
            "agx:watchdog:",      # watchdog alert/crisis/wake state
            "agx:worker_queues:", # queue runtime state
            "agx:host_slots:",    # host slot occupancy
            "agx:ws:",            # WS bridge runtime markers + delivery keys
            "agx:dsh:",           # DSH bridge runtime marker
            "agx:queue:",         # message queues (must never be deleted)
            "agx:lock:",          # distributed locks
        )

        r = get_redis_client()

        batch = 500

        # * fixed 2026-10-23: when entity_type is given, [only that type is cleared]; no more clearing everything.
        #   Old behavior: whatever entity_type was passed, the clear phase deleted all agx:entity:* + agx:idx:*,
        #   and then rebuilt only one type -> every other type's indexes were empty and its cache missing.
        #   Measured (I hit it at 07:48): after passing entity_type='plan'
        #     agx:entity was left with only the 185 plan hashes; the execution/agent/task indexes all vanished.
        #   Now it matches precisely by type:
        #     entity      → agx:entity:{etype}:*
        #     index       → agx:idx:{etype}:*
        #     !! the actual naming of agx:idx is agx:idx:{etype}:all / by_xxx / {etype}_status:xxx,
        #       so match the prefix agx:idx:{etype}* (no colon added, tolerating the old naming agx:idx:execution_status).
        if entity_type:
            # * naming boundary (measured, do not widen):
            #   agx:idx:{et}:*  exists for every type (:all / :active / :by_xxx / :status:xxx)
            #   agx:idx:{et}_*  * only execution has this old naming (agx:idx:execution_status*).
            #     Never add agx:idx:{et}_* for every type -- measured: et='agent' swallows 33 keys of
            #     agx:idx:agent_group:* / agent_group_member:*
            #     (in fnmatch * crosses colons, so the agent_ prefix eats all of agent_group).
            _CACHE_KEY_PATTERNS = [
                f"agx:entity:{entity_type}:*",
                f"agx:idx:{entity_type}:*",
            ]

            if entity_type == "execution":
                _CACHE_KEY_PATTERNS.append("agx:idx:execution_status*")

            _CACHE_KEY_PATTERNS = tuple(_CACHE_KEY_PATTERNS)
        else:
            _CACHE_KEY_PATTERNS = ("agx:entity:*", "agx:idx:*")

        # -- phase A: inventory the impact (read-only, no deletion) --
        #
        # * added 2026-10-23: compute "what will be deleted and what will be kept" before acting.
        #   There was no such phase before -> one call silently wiped the whole cache and the caller saw no impact preview.
        _will_del_by_prefix = {}

        _will_keep_by_prefix = {}

        _del_total = 0

        for _pat in _CACHE_KEY_PATTERNS:
            cursor_val = 0

            while True:
                cursor_val, keys = r.scan(cursor=cursor_val, match=_pat, count=batch)

                for _k in keys:
                    _parts = _k.split(":")

                    _pfx = ":".join(_parts[:2]) if len(_parts) >= 2 else _k

                    if _k.startswith(_PROTECTED_KEY_PREFIXES):
                        _will_keep_by_prefix[_pfx] = _will_keep_by_prefix.get(_pfx, 0) + 1
                    else:
                        _will_del_by_prefix[_pfx] = _will_del_by_prefix.get(_pfx, 0) + 1

                        _del_total += 1

                if cursor_val == 0:
                    break

        # among the protected keys there are also ones [not inside any allowlist pattern] (e.g. agx:notified:*);
        # they are not scanned, but the inventory must still show the caller "these I kept".
        for _k in r.scan_iter("agx:*"):
            if not _k.startswith(_PROTECTED_KEY_PREFIXES):
                continue

            if _k.startswith(_CACHE_KEY_PATTERNS):
                continue

            _parts = _k.split(":")

            _pfx = ":".join(_parts[:2]) if len(_parts) >= 2 else _k

            _will_keep_by_prefix[_pfx] = _will_keep_by_prefix.get(_pfx, 0) + 1

        _keep_total = sum(_will_keep_by_prefix.values())

        # -- phase B: refuse to execute when not confirmed, return the impact preview --
        if not confirm:
            _warnings = [
                "本次【未执行】任何删除。请核对影响面后，传 confirm=True 重新调用。",
                "agx:entity:* / agx:idx:* 虽名为缓存，但 agx:idx:* 是看门狗的遍历源"
                "（_scan_plan_completions 用 zrange agx:idx:plan:all）。清空会导致：",
                "  (1) 看门狗看不到 plan → 静默停摆，调度失效，且不报错；",
                "  (2) 索引重建过程中时序错开 → 短时间内重发大量历史 plan_completed"
                "（2026-10-23 实证：44 秒内 35 条，含 2 个月前的旧计划）。",
                "如果只是想让某个实体生效，优先用 agx_update_* 走增量同步，不要全量重建。",
            ]

            if entity_type:
                _warnings.insert(
                    0,
                    f"本次只影响 entity_type='{entity_type}'："
                    f"清空范围 = {' / '.join(_CACHE_KEY_PATTERNS)}，其余类型的缓存与索引不受影响。",
                )
            else:
                _warnings.insert(
                    0,
                    "本次是【全量重建】：清空所有 agx:entity:* 与 agx:idx:*，"
                    "重建全部 8 种实体。若只想修某一种，请显式传 entity_type。",
                )

            return json.dumps(
                {
                    "status": "preview",
                    "executed": False,
                    "entity_types_to_rebuild": types_to_rebuild,
                    "will_delete": {
                        "total": _del_total,
                        "by_prefix": dict(sorted(_will_del_by_prefix.items(), key=lambda x: -x[1])),
                    },
                    "will_keep": {
                        "total": _keep_total,
                        "by_prefix": dict(sorted(_will_keep_by_prefix.items(), key=lambda x: -x[1])),
                    },
                    "warnings": _warnings,
                },
                ensure_ascii=False,
                indent=2,
            )

        # -- phase C: confirmed, execute the deletion --
        total_deleted = 0

        for _pat in _CACHE_KEY_PATTERNS:
            cursor_val = 0

            while True:
                cursor_val, keys = r.scan(cursor=cursor_val, match=_pat, count=batch)

                if keys:
                    # defence line: the allowlist should never match a protected prefix, but assert it explicitly once,
                    # so nobody widens _CACHE_KEY_PATTERNS in the future (e.g. to "agx:*").
                    _would_del = [k for k in keys if not k.startswith(_PROTECTED_KEY_PREFIXES)]

                    _skipped = len(keys) - len(_would_del)

                    if _skipped:
                        log(
                            f"[cache_rebuild] 跳过 {_skipped} 个受保护键（前缀白名单兜底）",
                            "WARN",
                        )

                    if _would_del:
                        r.delete(*_would_del)

                        total_deleted += len(_would_del)

                if cursor_val == 0:
                    break

        log(
            f"[cache_rebuild] 已清空缓存 | 删除={total_deleted} 保留={_keep_total} "
            f"| 删除明细={_will_del_by_prefix} | entity_types={types_to_rebuild}",
            "WARN",
        )

        for etype in types_to_rebuild:
            if etype not in TABLE_MAP:
                results[etype] = "Invalid entity_type"

                continue

            table = TABLE_MAP[etype]

            id_col = ENTITY_ID_COL[etype]

            # each type has a different availability condition

            if etype == "execution":
                rows = getRowsByFullSQl(f"SELECT * FROM `{table}` WHERE run_status != 'finished'")
            elif etype == "plan":
                # * fixed 2026-10-22 (defect 5, the condition was inverted):
                #   the old condition `!= 'completed'` excluded completed but **included archived** -- exactly backwards.
                #   Evidence:
                #     - the watchdog's _scan_plan_completions explicitly drops draft/archived from
                #       agx:idx:plan:all and zrem's them (measured 10-13: 5 wrong sends at 16:36, all archived);
                #     - completed must stay in the index (waiting for notify_key to confirm the notification).
                #   The old condition meant: run rebuild once -> archived is put back into the index -> the watchdog kicks it out next round
                #   -> the next rebuild puts it back, sawing back and forth.
                #   Now it aligns with the watchdog: exclude draft (never finishes) and archived (a terminal state that is not notified).
                rows = getRowsByFullSQl(
                    f"SELECT * FROM `{table}` WHERE plan_status NOT IN ('draft', 'archived')"
                )
            else:
                rows = getRowsByFullSQl(f"SELECT * FROM `{table}` WHERE active_status = '1'")

            count = 0

            for row in rows:
                eid = f"{row['group_id']}:{row['agent_id']}" if etype == "agent_group_member" else row[id_col]

                _sync_entity(etype, eid, row)

                count += 1

            results[etype] = count

        summary = "; ".join(f"{k}={v}" for k, v in results.items())

        return (
            f"Cache rebuilt (cleared {total_deleted} keys, kept {_keep_total} protected keys "
            f"before rebuild): {summary}"
        )

    except Exception as e:
        return _log_exception("agx_cache_rebuild", e)


# ============================================================

# Tools: runtime monitoring -- running executions/sub-tasks and their durations

# ============================================================


def _load_running_exec_ids(r) -> list:
    """Return the [real] list of running execution IDs, falling back to MySQL when the index is empty/lost.

    * Why it is needed (2026-10-25, DH-062 patch GAP-2):
      The original agx_running_tasks "fallback" was a fake fallback --
        primary: r.zrange("agx:idx:execution:status:running", 0, -1)
        backup : r.zrange(f"{EXEC_STATUS_IDX}running", 0, -1)   <- the same key
      and zrange returns [] when the key does not exist (no exception) -> the except branch never fired.
      Result: once the index was lost, the tool silently returned [], the same symptom as DH-062 itself -- a monitoring blind spot.

    * The three-stage strategy of this function:
      (1) index has values -> use it directly (fast path, no DB access);
      (2) index empty or access failed -> query MySQL for the running list;
      (3) when falling back returns data, also refill the execution entities into the cache (read-through),
          because downstream agx_running_tasks calls _get_entity() per row, and _get_entity only reads Redis,
          never the DB -> without the refill it would keep returning [] with "index has ids but entities cannot be fetched".

    * Why not just use the DB: running is a high-frequency query (repeated calls while hunting a stuck task),
      and going through the Redis index in the normal case is the design intent; a DB fallback is only insurance
      for when the index is invalid, and must not become the routine cost.

    Args:
        r: Redis client.

    Returns:
        list: list of execution IDs (may be empty).
    """
    try:
        ids = list(r.zrange(f"{EXEC_STATUS_IDX}running", 0, -1))
        if ids:
            return ids
    except Exception as exc:
        log(f"_load_running_exec_ids zrange failed, fallback to DB: {exc}", "WARN")
        ids = []

    # -- index empty or error: fall back to MySQL --
    try:
        rows = execute_sql(
            "SELECT task_run_uuid FROM agx_task_execution WHERE run_status='running'"
        )
        if not rows:
            return []

        ids = [row.get("task_run_uuid") for row in rows if row.get("task_run_uuid")]
        if not ids:
            return []

        log(
            f"agx_running_tasks: 索引为空，已从 DB 回源 {len(ids)} 条 running（索引可能丢失）",
            "WARN",
        )

        # -- also rebuild the index + refill the entity cache (read-through) --
        #   fill only, never delete: missing index entries are added; extra dirty members are left to the audit script,
        #   no destructive zrem here (avoids racing with concurrent writes).
        try:
            _now_ts = int(__import__("time").time())
            r.zadd(f"{EXEC_STATUS_IDX}running", {eid: _now_ts for eid in ids})
        except Exception as exc:
            log(f"_load_running_exec_ids 回填索引失败: {exc}", "WARN")

        try:
            #   Note: no column allowlist; SELECT * like agx_cache_rebuild (which builds the same way),
            #   ensuring _sync_entity gets pkid/active_status and the other fields the indexes need, otherwise score is written as 0.
            rows_full = execute_sql(
                "SELECT * FROM agx_task_execution WHERE run_status='running'"
            )
            for _row in rows_full or []:
                _eid = _row.get("task_run_uuid")
                if _eid:
                    _sync_entity("execution", _eid, _row)
        except Exception as exc:
            log(f"_load_running_exec_ids 回填实体缓存失败: {exc}", "WARN")

        return ids

    except Exception as exc:
        log(f"_load_running_exec_ids DB 回源失败: {exc}", "WARN")
        return []


@mcp.tool()
@require_level(0)
def agx_running_tasks(caller_agent_id: str, project_id: str = None, agent_id: str = None) -> list:
    """
    [Runtime monitoring] Get the executions currently running and how long they have been running.

    Reads from the Redis cache and computes runtime (seconds) on the fly; no MySQL access.

    The returned rows already include duration_seconds, which is convenient for the UI.

    Filter logic:

    - execution: run_status = 'running'

    Optional filters:

    - project_id: only return running records of the given project

    - agent_id: only return running records of the given agent

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity and enables an audit trail. Read-only tools are no exception -- every call chain must be traceable.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).
        project_id (str, optional): project ID filter.
        agent_id (str, optional): agent ID filter.

    Returns:

        list: the running executions, each containing:

            - id: entity ID
            - project_id, agent_id, host_id, task_id
            - started_at: start time in ISO format
            - duration_seconds: seconds elapsed
            - run_status: 'running'
            - exec_instruction
            - session_id

    """
    try:
        r = get_redis_client()

        now = __import__("datetime").datetime.now()

        results = []

        # 1. get all executions in the running state

        # * FIX (2026-10-25, DH-062 patch GAP-2): the old "fallback" was fake.
        #   Old code:
        #       try:    exec_ids = list(r.zrange("agx:idx:execution:status:running", 0, -1))
        #       except: exec_ids = list(r.zrange(f"{EXEC_STATUS_IDX}running", 0, -1))
        #   Two problems:
        #     (1) both expressions point at [the same key] (EXEC_STATUS_IDX = "agx:idx:execution:status:")
        #       -> the fallback was no fallback at all;
        #     (2) zrange returns [] when the key does not exist and [raises nothing] -> the except branch never fires.
        #   Consequence: once the index was lost/empty (e.g. agx_cache_clear with no arguments), the tool still returned [],
        #       exactly the original DH-062 symptom -- the key name was fixed but the "silent failure" was not.
        #   Now it is a real DB fallback: index empty or error -> query MySQL for the running list directly,
        #   and refill the missing executions into the cache as a side effect (read-through), avoiding "found but cannot continue".
        exec_ids = _load_running_exec_ids(r)

        for eid in exec_ids:
            data = _get_entity("execution", eid)

            if not data:
                continue

            # filter

            if project_id and data.get("project_id") != project_id:
                continue

            if agent_id and data.get("agent_id") != agent_id:
                continue

            if data.get("run_status") != "running":
                continue

            started_at = data.get("started_at")

            # * 2026-10-19 lazy write of started_at: empty = delivered but not yet read by the receiver (clock not started).
            #   In that case created_at must not masquerade as "elapsed run time" (that is queue wait, different semantics),
            #   so duration_seconds is set to None with queue_wait_seconds attached for the caller to tell them apart.
            if not started_at:
                duration = None
                try:
                    _ca = data.get("created_at")
                    if _ca:
                        _dt_ca = __import__("datetime").datetime.strptime(str(_ca)[:19], "%Y-%m-%d %H:%M:%S")
                        queue_wait = int((now - _dt_ca).total_seconds())
                        if queue_wait < 0:
                            queue_wait = 0
                    else:
                        queue_wait = None
                except Exception:
                    queue_wait = None
            else:
                queue_wait = None
                try:
                    dt = __import__("datetime").datetime.strptime(str(started_at), "%Y-%m-%d %H:%M:%S")

                    duration = int((now - dt).total_seconds())

                except Exception as exc:
                    log(f"agx_running_tasks duration calc failed for {eid}: {exc}", "WARN")
                    duration = 0

            results.append(
                {
                    "type": "execution",
                    "id": eid,
                    "project_id": data.get("project_id"),
                    "agent_id": data.get("agent_id"),
                    "host_id": data.get("host_id"),
                    "task_id": data.get("task_id"),
                    "session_id": data.get("session_id"),
                    "started_at": str(started_at) if started_at else None,
                    "duration_seconds": duration,
                    "queue_wait_seconds": queue_wait,
                    "phase": "executing" if started_at else "queued",
                    "run_status": "running",
                    "instruction": data.get("exec_instruction"),
                }
            )

        return results

    except Exception as e:
        return [{"error": _log_exception("agx_running_tasks", e)}]


# ============================================================

# Tools: entity queries -- read the cache first, fall back to MySQL on miss

# ============================================================


# the ID column name of each entity type

_ENTITY_ID_COL = {
    "project": "project_id",
    "host": "host_id",
    "agent": "agent_id",
    "agent_group": "group_id",
    "agent_group_member": "pkid",
    "task": "task_id",
    "execution": "task_run_uuid",
    "skill": "skill_id",
}


def _resolve_agent_name(agent_id: str) -> str:
    """Look up agent_name by agent_id, used to build human-readable context."""
    if not agent_id:
        return "unknown"
    try:
        data = _get_with_readthrough("agent", agent_id)
        if "error" not in data:
            return _get_field(data, "agent_name") or agent_id
    except Exception:
        pass
    return agent_id


def _get_field(data: dict, key: str, default=None):
    """Safely read a dict field, tolerating the bytes keys returned by Redis hgetall.

    Redis hgetall returns {b'key': b'value'}, and in Python 3 data.get('key') does not match a bytes key.
    This function tries both the str and the bytes key.
    """
    # try the str key first (MySQL fallback path)
    v = data.get(key)
    if v is not None:
        return v.decode() if isinstance(v, bytes) else v
    # then try the bytes key (Redis cache path)
    v = data.get(key.encode())
    if v is not None:
        return v.decode() if isinstance(v, bytes) else v
    return default


def _build_prev_execution_insight(
    prev_agent_name: str,
    prev_summary: str,
    current_agent_name: str,
) -> str:
    """Build a role-oriented insight hint for the preceding task.

    Instead of mechanically concatenating the summary, tell the current agent:
    "your predecessor xxx has finished yyy; you should now do zzz on top of that."
    """
    if not prev_summary:
        return f"前置任务由 {prev_agent_name} 完成,但未产出 summary。请基于 context 自行判断。"

    # short digest (first 300 characters)
    brief = prev_summary[:300]
    if len(prev_summary) > 300:
        brief += "..."

    return f"你的前置任务由【{prev_agent_name}】完成。以下是其工作总结:\n---\n{brief}\n---\n你是【{current_agent_name}】,请基于以上前置产出继续工作。关注前置任务提到的待改进点/遗留问题,并在你的工作中做出响应。"


def _get_with_readthrough(entity_type: str, entity_id: str, id_col: str = None) -> dict:
    """Read the cache first; on miss, fall back to MySQL and fill the cache."""
    # 1. try the cache

    data = _get_entity(entity_type, entity_id)

    # cache hit returns directly
    if data:
        return data

    # 2. cache miss - fall back to MySQL

    col = id_col or _ENTITY_ID_COL.get(entity_type)

    if not col:
        return {"error": f"Unknown entity_type: {entity_type}"}

    rows = execute_sql(f"SELECT * FROM `{TABLE_MAP[entity_type]}` WHERE {col} = %s", (entity_id,))

    if not rows:
        return {"error": "Not found"}

    row = rows[0]

    _sync_entity(entity_type, entity_id, row)

    # convert every datetime/date to a string so it is JSON serializable

    result = {}

    for k, v in row.items():
        if hasattr(v, "isoformat"):
            result[k] = v.isoformat()

        else:
            result[k] = v

    return result


@mcp.tool()
@require_level(0)
def agx_get_project(caller_agent_id: str, project_id: str) -> str:
    """
    [Query project] Get project details, including active_status and more.

    Reads the cache first; auto fallback to MySQL on miss.

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity and enables an audit trail. Read-only tools are no exception -- every call chain must be traceable.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).

        project_id (str): project ID.

    """
    data = _get_with_readthrough("project", project_id)

    return json.dumps(data, indent=2, ensure_ascii=False)


@mcp.tool()
@require_level(0)
def agx_get_project_config(caller_agent_id: str, project_id: str = "") -> str:
    """
    [Read project description] Read project information from the agx_project.x_desc field.

    x_desc may hold content in any format (text/JSON/Markdown, etc.); the raw content is returned as-is.
    The format is not constrained; the agent decides how to use it.

    Purpose: let an agent in a project group read its own project's information after startup,
    instead of every agent maintaining its own copy of the configuration.

    Note: system_project has no project description (returns empty); only business projects are meaningful.

    Args:

        caller_agent_id (str): caller agent ID.

        project_id (str): project ID. Derived from the agent's project_id when empty.

    Returns:

        str: the raw x_desc content. An empty value or system_project returns "{}".
    """
    # derive project_id automatically
    if not project_id:
        project_id = _resolve_project_id_from_agent(caller_agent_id)

    # the system project needs no configuration
    if project_id == SYSTEM_PROJECT_ID:
        return "{}"

    data = _get_with_readthrough("project", project_id)

    if not data:
        return "Error: project not found"

    x_desc = (data.get("x_desc") or "").strip()

    if not x_desc:
        return "{}"

    return x_desc


# -- caller agent -> owning host_id resolution (multi-instance) --
# one machine may run several OpenClaw instances, each with its own host_id (e.g. test_srv_a / test_srv_a2).
# the MCP gateway is [one process serving all instances]; config.host_id is only the gateway process's own identity,
# not the calling agent's owning host. This used to cause errors: an agent of the second instance calling
# agx_get_global_config read the primary instance's host_id and itself reported "inconsistent with the registry".
# So wherever "which host does this request belong to" matters, look it up in the registry by caller_agent_id.
_CALLER_HOST_CACHE: dict = {}  # agent_id -> (ts, host_id)
_CALLER_HOST_TTL = 60.0


def _resolve_caller_host_id(caller_agent_id: str, fallback: str = "") -> str:
    """Resolve the owning host_id of the calling agent.

    Fallback chain: agx_agent_registry.host_id -> fallback -> config.host_id
    In the single-machine case all three are equal, so behavior is identical to before this change.
    Cached for 60s: changing an agent's host is rare, while MCP tool calls are frequent.
    """
    fb = fallback or config.get("host_id", "")
    aid = (caller_agent_id or "").strip()
    if not aid:
        return fb

    now = time.time()
    hit = _CALLER_HOST_CACHE.get(aid)
    if hit and now - hit[0] < _CALLER_HOST_TTL:
        return hit[1] or fb

    hid = ""
    try:
        rows = execute_sql(
            "SELECT host_id FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
            (aid,),
            commit=False,
        )
        hid = (rows[0].get("host_id") or "").strip() if rows else ""
    except Exception as exc:
        log(f"_resolve_caller_host_id {aid} failed: {exc}", "WARN")

    _CALLER_HOST_CACHE[aid] = (now, hid)
    return hid or fb


def _host_of_session(session_key: str, fallback: str = "") -> str:
    """Derive the host_id a session belongs to from its session_key (agent:{agent_id}:...).

    A session follows its agent, and an agent follows its host -- so a session's home host is the agent's
    owning host. Falls back to fallback/config when it cannot be resolved.
    """
    fb = fallback or config.get("host_id", "")
    parts = (session_key or "").split(":")
    if len(parts) >= 2 and parts[0] == "agent" and parts[1]:
        return _resolve_caller_host_id(parts[1], fb)
    return fb


@mcp.tool()
@require_level(0)
def agx_get_global_config(caller_agent_id: str) -> str:
    """
    [Read global config] Get the AgentGeeX global runtime configuration (public to all agents).

    Read from the global config file; exposes several key parameters about the agent runtime:
        - host_id: this machine's host ID
        - network_id: owning network ID
        - mode: install mode (core/host)
        - work_dir: AgentGeeX runtime root
        - agent_id: the calling agent's registry ID (from caller_agent_id)
        - agent_name: the calling agent's display name (from agx_agent_registry)
        - agent_role: the calling agent's system role (workshop_manager/host_manager/project_manager/agent_manager/worker)
        - project_id: the project ID the calling agent is currently bound to

    MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity and enables an audit trail. Read-only tools are no exception -- every call chain must be traceable.

    Args:
        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks;
                               also the key used to look up agent_name/agent_role/project_id).

    Returns:
        str: the runtime configuration as JSON, with 8 fields:
            host_id, network_id, mode, work_dir
            agent_id, agent_name, agent_role, project_id

    Example:
        agx_get_global_config("agx_core")
        -> {
            "host_id": "test_host_mac",
            "network_id": "lan_001",
            "mode": "core",
            "work_dir": "~/.agx",
            "agent_id": "agx_core",
            "agent_name": "agx_core",
            "agent_role": "workshop_manager",
            "project_id": "system_project"
        }
    """
    # base configuration comes from agx_config.json
    result = {
        # * host_id prefers the calling agent's owning host: in a multi-instance setup one machine has several
        #   host_ids, and the one in config is the [gateway process] identity, not this agent's.
        #   It will be overwritten once the registry lookup below returns the real value.
        "host_id": config.get("host_id", ""),
        "network_id": config.get("network_id", ""),
        "mode": config.get("mode", ""),
        "work_dir": config.get("work_dir", "~/.agx"),
        "agent_id": caller_agent_id,
        "agent_name": "",
        "agent_role": "",
        "project_id": "",
    }
    # query the calling agent's runtime identity from the DB (fetching host_id along the way, no extra query)
    try:
        rows = executeSql(
            "SELECT agent_name, agent_role, project_id, host_id FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
            (caller_agent_id,),
        )
        if rows:
            result["agent_name"] = rows[0].get("agent_name", "") or ""
            result["agent_role"] = rows[0].get("agent_role", "") or ""
            result["project_id"] = rows[0].get("project_id", "") or ""
            _reg_host = (rows[0].get("host_id") or "").strip()
            if _reg_host:
                result["host_id"] = _reg_host
    except Exception as exc:
        log(f"agx_get_global_config agent lookup MySQL failed for {caller_agent_id}: {exc}", "WARN")
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
@require_level(0)
def agx_project_integrity_check(caller_agent_id: str, project_id: str = "") -> str:
    """
    [Project integrity check] Inspect whether a project is missing key configuration and return the unhealthy items.

    Current checks (extensible):
    1. admin_agent_id missing: the project has no admin, so management capabilities that depend on the project-admin
       view (e.g. visibility of members' private tasks) are missing.

    When to use:
    - inspection right after project creation (a new project may have an empty admin because the PM was unknown)
    - periodic integrity inspection (can be combined with the watchdog/scheduling)

    Args:
        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).
        project_id (str, optional): restrict to this project; when empty, check all active projects.

    Returns:
        str: JSON with summary (overview) + issues (unhealthy items, each with project_id/check/item description).

    """
    try:
        if project_id:
            rows = execute_sql(
                "SELECT project_id, project_name, admin_agent_id, active_status FROM agx_project WHERE project_id = %s AND active_status = 1",
                (project_id,),
            )
            scope = f"project {project_id}"
        else:
            rows = execute_sql(
                "SELECT project_id, project_name, admin_agent_id, active_status FROM agx_project WHERE active_status = 1 ORDER BY project_id"
            )
            scope = "all active projects"

        issues = []
        checked = 0
        for r in rows:
            checked += 1
            pid = r.get("project_id", "")
            pname = r.get("project_name", "")

            # check 1: admin_agent_id missing
            if not r.get("admin_agent_id"):
                issues.append(
                    {
                        "project_id": pid,
                        "project_name": pname,
                        "check": "admin_agent_id_missing",
                        "severity": "warn",
                        "detail": "项目无管理员(admin_agent_id 为空),项目管理员无法查看成员私有 task,建议通过 agx_create_project 传 admin_agent_id 或克隆 project_manager 成员自动补位",
                    }
                )

        summary = {
            "scope": scope,
            "checked": checked,
            "healthy": len(issues) == 0,
            "issue_count": len(issues),
        }
        return json.dumps({"summary": summary, "issues": issues}, indent=2, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_project_integrity_check", e)}, indent=2)


@mcp.tool()
@require_level(0)
def agx_resolve_host_manager(caller_agent_id: str, host_id: str) -> str:
    """
    [Resolve host manager agent] Find the host_manager agent for a given host_id.

    In a multi-host setup each host has its own separately named host_manager agent
    (e.g. agx_host_srv_a), so a dispatcher need not hard-code an agent_id --
    it only needs the target host_id and this tool resolves the right agent_id.

    Lookup logic:
    1. exact host_id match + agent_role='host_manager' (multi-host)
    2. fall back to agent_name = 'agx_host' (single-machine compatibility)

    Args:
        caller_agent_id (str): caller agent ID.
        host_id (str): target host ID.

    Returns:
        str: JSON {"agent_id": "...", "agent_name": "...", "host_id": "..."}
    """
    return json.dumps(_resolve_host_manager(host_id), indent=2, ensure_ascii=False)


@mcp.tool()
@require_level(0)
def agx_get_host(caller_agent_id: str, host_id: str) -> str:
    """
    [Query host] Get host details, including endpoint_ip, presence_status, platforms and more.

    Reads the cache first; auto fallback to MySQL on miss.

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity and enables an audit trail. Read-only tools are no exception -- every call chain must be traceable.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).

        host_id (str): host ID.

    """
    data = _get_with_readthrough("host", host_id)

    return json.dumps(data, indent=2, ensure_ascii=False)


@mcp.tool()
@require_level(0)
def agx_get_agent(caller_agent_id: str, agent_id: str) -> str:
    """
    [Query agent] Get agent details, including the list of teams it belongs to (teams).

    Reads the cache first; auto fallback to MySQL on miss.

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity and enables an audit trail. Read-only tools are no exception -- every call chain must be traceable.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).

        agent_id (str): agent ID.

    """
    data = _get_with_readthrough("agent", agent_id)

    if "error" not in data:
        # query every team the agent belongs to (many-to-many)

        teams = execute_sql(
            """
            SELECT ag.group_id, ag.group_name

            FROM agx_agent_group_member gm

            LEFT JOIN agx_agent_group ag ON gm.group_id = ag.group_id

            WHERE gm.agent_id = %s AND gm.active_status = 1 AND ag.active_status = 1

            """,
            (agent_id,),
        )

        data["teams"] = teams if teams else []

    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


# ============================================================
# task visibility control
# ============================================================


def _get_caller_role_info(caller_agent_id: str) -> dict:
    """Get the calling agent's role information (used for visibility filtering)."""
    rows = execute_sql(
        """SELECT agent_role, project_id, host_id, scope, is_project_admin
           FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1""",
        (caller_agent_id,),
    )
    if not rows:
        raise PermissionError(f"调用方 '{caller_agent_id}' 不存在或已停用")
    r = rows[0]
    return {
        "role": r["agent_role"],
        "project_id": r["project_id"],
        "host_id": r["host_id"],
        "scope": r["scope"],
        "is_project_admin": r["is_project_admin"],
    }


def _build_task_visibility_filter(caller_agent_id: str, info: dict) -> tuple:
    """
    Build the task visibility SQL filter from the caller's role.
    Returns a (where_clause, params) tuple to be appended to the query conditions.
    """
    role = info["role"]
    # any agent can always see its own tasks
    # !! inline caller_agent_id as a string rather than %s, to avoid cascading with the caller's extra params
    own_sql = f"agent_id = '{caller_agent_id}'"
    if role == "workshop_manager":
        return ("1=1", ())
    elif role == "project_manager":
        # project admin: its own + members of projects it administers + those under system_project (system-level collaboration) + public
        return (
            f"""({own_sql} OR agent_id IN (
                SELECT ar.agent_id FROM agx_agent_registry ar
                JOIN agx_project p ON ar.project_id = p.project_id
                WHERE p.admin_agent_id = %s AND ar.active_status = 1
            ) OR agent_id IN (
                SELECT agent_id FROM agx_agent_registry
                WHERE project_id = 'system_project' AND active_status = 1
            ) OR is_private = 0)""",
            (caller_agent_id,),
        )
    elif role == "host_manager":
        return (
            f"""({own_sql} OR agent_id IN (
                SELECT agent_id FROM agx_agent_registry
                WHERE host_id = %s AND active_status = 1
            ) OR is_private = 0)""",
            (info["host_id"],),
        )
    elif role == "agent_manager":
        # agent manager: its own + system-level agents (managing the whole agent lifecycle) + public
        return (
            f"""({own_sql} OR agent_id IN (
                SELECT agent_id FROM agx_agent_registry
                WHERE project_id = 'system_project' AND active_status = 1
            ) OR is_private = 0)""",
            (),
        )
    else:
        # worker: its own + public
        return (f"({own_sql} OR is_private = 0)", ())


def _check_task_visibility(caller_agent_id: str, task_agent_id: str, is_private: int) -> bool:
    """Check whether the caller may see the given task."""
    if not caller_agent_id:
        return True  # legacy call compatibility, no filtering
    # any agent can see its own tasks (including private ones)
    if task_agent_id == caller_agent_id:
        return True
    info = _get_caller_role_info(caller_agent_id)
    role = info["role"]
    if role == "workshop_manager":
        return True
    if not is_private:
        return True  # public tasks are visible to everyone
    # the following only covers is_private=1 and not the owner
    if role == "host_manager":
        rows = execute_sql(
            "SELECT 1 FROM agx_agent_registry WHERE agent_id = %s AND host_id = %s",
            (task_agent_id, info["host_id"]),
        )
        return bool(rows)
    if role == "project_manager":
        rows = execute_sql(
            """SELECT 1 FROM agx_agent_registry ar
               JOIN agx_project p ON ar.project_id = p.project_id
               WHERE ar.agent_id = %s AND p.admin_agent_id = %s""",
            (task_agent_id, caller_agent_id),
        )
        return bool(rows)
    if role == "agent_manager":
        # can see the tasks of every system_project member
        rows = execute_sql(
            "SELECT 1 FROM agx_agent_registry WHERE agent_id = %s AND project_id = 'system_project' AND active_status = 1",
            (task_agent_id,),
        )
        return bool(rows)
    # worker: is_private=1 and not the owner -> not visible
    return False


@mcp.tool()
@require_level(0)
def agx_get_task(caller_agent_id: str, task_id: str) -> str:
    """
    [Query task] Get task details, including an overview list of related executions.

    Reads the cache first; auto fallback to MySQL on miss.

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity, enables an audit trail, and turns on task visibility filtering. Read-only tools are no
        exception -- every call chain must be traceable.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).

        task_id (str): task ID.

    """
    data = _get_with_readthrough("task", task_id)

    if "error" not in data:
        # visibility check
        task_agent_id = data.get("agent_id", "")
        task_private = data.get("is_private", 0)
        if not _check_task_visibility(caller_agent_id, task_agent_id, task_private):
            return json.dumps(
                {"error": f"Agent '{caller_agent_id}' 无权查看 task '{task_id}'"}, indent=2, ensure_ascii=False
            )

        # attach the related execution list
        try:
            r = get_redis_client()
            exec_ids = list(r.smembers(f"{EXEC_BY_TASK}{task_id}"))
            data["executions"] = exec_ids
        except Exception as e:
            log(f"agx_get_task({task_id}) Redis exec list failed: {e}", "WARN")

    return json.dumps(data, indent=2, ensure_ascii=False)


@mcp.tool()
@require_level(0)
def _claimed_by_agent(agent_id: str, run_uuid: str) -> bool:
    """Whether this run was really [delivered] to this agent (the activity marker written by ws_client).

    * Decided by Lao Feng on 2026-10-27 (part of plan B): one more gate for the lazy write of started_at.
      -- Why it is needed --
      `started_at` is the only criterion of plan B (empty = sent but not started; the watchdog ignores it).
      But rules (1)(2)(3) only ruled out "someone else querying", not "the agent querying another task of its own".
      Measured pollution (2026-10-27 09:49): the watchdog sent a "not started" alert -> while handling the alert the
      agent casually called agx_get_execution on the original -> caller was itself, and the original's agent_id was
      also itself -> hit -> a task that never started was written as "started 09:49:54".

      -- Gate signal --
      After sessions.send returns a runId (= the gateway really started a run for that session), ws_client writes
      `agx:active:run:{agent}:{run_uuid}`. This marker can only be produced by a [real delivery];
      no query action can forge it.

      -- Degradation --
      Returns True when Redis is unavailable (back to the old behavior):
      better to keep the known pollution risk than to leave every task unable to write started_at
      (which would make the watchdog treat every running task as "not started").
    """
    try:
        r = get_redis_client()
        return bool(r.hexists(f"agx:active:run:{agent_id}", run_uuid))
    except Exception as _e:
        log(f"_claimed_by_agent({agent_id},{run_uuid}) redis 不可用，降级放行: {_e}", "WARN")
        return True


@mcp.tool()
@require_level(0)
def agx_get_execution(caller_agent_id: str, task_run_uuid: str) -> str:
    """
    [Query execution] Get execution details and the surrounding execution-chain context.

    The response has three parts:
    1. all fields of this execution (agent_id, task_id, run_status, exec_instruction,
       summary, result_data, started_at, finished_at, duration_seconds,
       plan_id, prev_task, next_task, parent_run_uuid, session_id, etc.)
    2. child_executions: executions linked through parent_run_uuid, each with
       {"run_uuid": "...", "run_status": "..."}
    3. prev_execution_context: information about the preceding execution that this execution's prev_task points to,
       each with {"run_uuid": "...", "run_status": "..."}
       - when the predecessor succeeded it also carries summary + agent_name + description + insight
       - when it is unfinished or missing, a note is attached

    Reads the cache first; auto fallback to MySQL on miss.

    Args:
        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).
        task_run_uuid (str): execution instance ID.

    Returns:
        str: execution details as JSON, structured as follows:
        {
            # -- fields of this execution --
            "task_run_uuid": "run_xxx",
            "agent_id": "...",
            "task_id": "...",
            "host_id": "...",
            "project_id": "...",
            "network_id": "...",
            "plan_id": "...",
            "run_status": "succeeded|failed|running|pending|draft|cancelled",
            "exec_instruction": "...",
            "summary": "...",
            "result_data": "...",
            "started_at": "...",
            "finished_at": "...",
            "duration_seconds": 123,
            "timeout_seconds": "600",
            "exec_name": "short title: what this run is doing",
            "session_id": "...",
            "send_agent_id": "...",
            "send_host_id": "...",
            "prev_task": "run_xxx",
            "next_task": "run_xxx",
            "parent_run_uuid": "...",
            "scheduled_at": "...",
            "activate_plan_id": "...",
            # -- child executions (linked via parent_run_uuid) --
            "child_executions": [
                {"run_uuid": "run_child_xxx", "run_status": "succeeded"},
                ...
            ],
            # -- preceding execution context (the task prev_task points to) --
            "prev_execution_context": [
                {
                    "run_uuid": "run_prev_xxx",
                    "run_status": "succeeded",
                    "agent_name": "...",         # attached when succeeded
                    "description": "...",         # attached when succeeded
                    "summary": "...",             # attached when succeeded
                    "insight": "..."              # attached when succeeded
                },
                ...
            ]
        }
    """
    data = _get_with_readthrough("execution", task_run_uuid)

    # read-side visibility scope (shadow mode defaults to off -> behavior unchanged)
    if "error" not in data and not _can_read_scope(
        caller_agent_id,
        str(data.get("agent_id") or ""),
        str(data.get("project_id") or ""),
        tool="agx_get_execution",
    ):
        return json.dumps({
            "error": f"权限不足:Agent '{caller_agent_id}' 无权读取该执行实例(归属 {data.get('agent_id') or '?'} / 项目 {data.get('project_id') or '?'})。"
        }, indent=2, ensure_ascii=False)

    if "error" not in data:
        # * decided by Lao Feng on 2026-10-19: lazy write of started_at (the clock starts on the first read)
        #   it used to write NOW() at dispatch/activate, counting "queue wait + model load + context compression"
        #   into the timeout -> after daemon backlog a task was judged timed out the moment it was delivered (measured: 83 timeouts that had actually succeeded).
        #   Now the clock starts when the receiving agent first reads its own execution.
        #
        #   Trigger conditions (all four must hold, none optional):
        #     (1) caller_agent_id == data['agent_id']  -- only "the receiver itself" counts
        #       (a sender/PM/watchdog incidental read does not count, so nobody can move the clock by looking)
        #     (2) started_at is empty                  -- written only the first time, idempotent
        #     (3) run_status == 'running'              -- written only while non-terminal
        #     (4) _claimed_by_agent()                  -- this run really was delivered to it
        #       * added 2026-10-27 (part of plan B). (1)(2)(3) only excluded "someone else reading", not
        #       "the agent reading another task of its own" -- measured at 09:49:54: the watchdog sent a not-started alert,
        #       the agent read the original while handling the alert, and a task that never started was recorded as started.
        #       Now the ws_client delivery marker must hit before writing (see the _claimed_by_agent docstring).
        #   After writing, the Redis cache is synced (otherwise later cache reads miss the new value).
        if (
            str(caller_agent_id) == str(data.get("agent_id") or "")
            and not data.get("started_at")
            and str(data.get("run_status") or "") == "running"
            and _claimed_by_agent(str(caller_agent_id), task_run_uuid)
        ):
            try:
                _now = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # with the started_at IS NULL condition -> only one succeeds under concurrency, no repeated overwrite
                _updated = execute_sql(
                    "UPDATE agx_task_execution "
                    "SET started_at=%s, created_at=COALESCE(created_at, %s) "
                    "WHERE task_run_uuid=%s AND started_at IS NULL",
                    (_now, _now, task_run_uuid),
                    commit=True,
                )
                data["started_at"] = _now
                try:
                    # fixed defect 4 on 2026-10-22: a single-field hset -> a whole-row refresh (prevents truncated entities)
                    #   Redis hset is an upsert: when the key does not exist it creates a hash with just one field.
                    _rows = execute_sql(
                        "SELECT * FROM agx_task_execution WHERE task_run_uuid=%s", (task_run_uuid,)
                    )
                    if _rows:
                        _clean = {str(k): (str(v) if not isinstance(v, str) else v)
                                  for k, v in _rows[0].items() if v is not None}
                        if _clean:
                            _sync_entity("execution", task_run_uuid, _clean)
                except Exception as _e:
                    log(f"agx_get_execution({task_run_uuid}) started_at redis sync failed: {_e}", "WARN")
                log(
                    f"[LAZY-START] {task_run_uuid} started_at={_now} "
                    f"agent={caller_agent_id}（首次读取起算）"
                )
            except Exception as _e:
                # a failed clock start does not block the read (better no timing than the agent not getting its instruction)
                log(f"agx_get_execution({task_run_uuid}) lazy started_at failed: {_e}", "WARN")

        # compute the runtime

        started_at = data.get("started_at")

        # * 2026-10-19: label the phase explicitly, so the caller does not mistake "running but not started" for "running for ages".
        #   phase=queued    -> delivered, the receiver has not read it yet (started_at empty)
        #   phase=executing -> the clock started (started_at has a value)
        #   phase=finished  -> terminal state
        _rs = str(data.get("run_status") or "")
        if _rs in ("succeeded", "failed", "cancelled", "archived"):
            data["phase"] = "finished"
        elif _rs == "awaiting_confirm":
            # * 2026-11-06: the executor has delivered and is waiting for human release. Neither finished nor executing.
            #   Without a dedicated phase it would fall into the started_at logic below and be reported as executing/queued,
            #   looking on the UI like "it finished but is stuck executing".
            data["phase"] = "awaiting_confirm"
        elif started_at:
            data["phase"] = "executing"
        else:
            data["phase"] = "queued"

        if started_at:
            try:
                ts = str(started_at).replace("T", " ")
                dt = __import__("datetime").datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")

                # * corrected 2026-10-19: a terminal task must compute its total duration from finished_at,
                #   not now - started_at (which would treat "how long ago it ended" as "how long it ran"):
                #   measured: a receipt with started_at=finished_at=11:03:51 produced duration=54s.
                _fin = data.get("finished_at")
                if _fin:
                    _ts2 = str(_fin).replace("T", " ")
                    _dt2 = __import__("datetime").datetime.strptime(_ts2[:19], "%Y-%m-%d %H:%M:%S")
                    data["duration_seconds"] = max(0, int((_dt2 - dt).total_seconds()))
                else:
                    # not finished yet: here now - started_at really is "how long it has run", correct
                    now = __import__("datetime").datetime.now()
                    data["duration_seconds"] = max(0, int((now - dt).total_seconds()))

            except Exception as e:
                log(f"agx_get_execution({task_run_uuid}) duration calc failed: {e}", "WARN")

        # attach the child execution list (linked through parent_run_uuid)
        # each item has run_uuid + run_status
        try:
            r = get_redis_client()
            child_ids = set(r.smembers(f"{EXEC_BY_PARENT}{task_run_uuid}"))
            child_list = []
            for cid in child_ids:
                cid_str = cid.decode() if isinstance(cid, bytes) else cid
                child_data = _get_with_readthrough("execution", cid_str)
                child_status = _get_field(child_data, "run_status") or "unknown"
                child_list.append({"run_uuid": cid_str, "run_status": child_status})
            data["child_executions"] = child_list
        except Exception as e:
            log(f"agx_get_execution({task_run_uuid}) Redis child list failed: {e}", "WARN")

        # attach the preceding task context (prev_execution_context)
        # normalized to a list format: [{"run_uuid": "...", "run_status": "...", ...}, ...]
        # when the predecessor succeeded it also carries summary + agent_name + description + insight
        prev_task_raw = data.get("prev_task") or data.get(b"prev_task") or ""
        prev_task = (prev_task_raw.decode() if isinstance(prev_task_raw, bytes) else str(prev_task_raw)).strip()
        if prev_task:
            try:
                prev_data = _get_with_readthrough("execution", prev_task)
                if "error" not in prev_data:
                    prev_item = {
                        "run_uuid": prev_task,
                        "run_status": _get_field(prev_data, "run_status") or "unknown",
                    }
                    if prev_item["run_status"] == "succeeded":
                        prev_item["agent_name"] = _resolve_agent_name(_get_field(prev_data, "agent_id") or "")
                        prev_item["description"] = (_get_field(prev_data, "exec_instruction") or "")[:200]
                        prev_item["summary"] = _get_field(prev_data, "summary") or ""
                        prev_item["insight"] = _build_prev_execution_insight(
                            prev_agent_name=prev_item["agent_name"],
                            prev_summary=prev_item["summary"],
                            current_agent_name=_resolve_agent_name(_get_field(data, "agent_id") or ""),
                        )
                    else:
                        prev_item["note"] = "前置任务尚未完成,请忽略"
                    data["prev_execution_context"] = [prev_item]
                else:
                    data["prev_execution_context"] = [
                        {"run_uuid": prev_task, "run_status": "unknown", "note": "前置任务不存在"}
                    ]
            except Exception as e:
                log(f"agx_get_execution({task_run_uuid}) prev_task lookup failed: {e}", "WARN")
                data["prev_execution_context"] = {"error": str(e)}

        # attach the session name (after getting the execution, an agent immediately knows what its session is for)
        try:
            sess_key = data.get("session_id") or data.get(b"session_id") or ""
            if isinstance(sess_key, bytes):
                sess_key = sess_key.decode()
            sess_key = str(sess_key).strip()
            if sess_key:
                sess_rows = execute_sql(
                    "SELECT session_name, x_desc, run_count, last_active_at, active_status FROM agx_session WHERE session_id = %s",
                    (sess_key,),
                )
                if sess_rows:
                    data["session_name"] = sess_rows[0].get("session_name") or ""
                    data["session_x_desc"] = sess_rows[0].get("x_desc") or ""
                    data["session_run_count"] = sess_rows[0].get("run_count")
                    data["session_last_active_at"] = str(sess_rows[0].get("last_active_at") or "")
        except Exception as e:
            log(f"agx_get_execution({task_run_uuid}) session name lookup failed: {e}", "WARN")

    return json.dumps(data, indent=2, ensure_ascii=False)


# -- Agent Notice: centralized notice/log management --


@mcp.tool()
@require_level(1)
def agx_write_notice(
    caller_agent_id: str,
    agent_id: str,
    project_id: str,
    notice_type: str,
    content: str,
    title: str = "",
    task_id: str = "",
    run_uuid: str = "",
    visibility: str = "project",
    network_id: str = "",
) -> str:
    """
    [Write notice] An agent publishes a work log into the central management table.

    Used to record work progress, decisions, lessons learned, configuration changes, etc.,
    so knowledge carries across the team and the project stays auditable.

    Types (notice_type):
        log      - work log ("analysing module XX, found YY")
        decision - decision record ("chose plan A over B because ...")
        error    - lesson learned ("hit error YY, cause ZZ, fixed by ...")
        handoff  - handoff record ("who needs to do what next, watch out for XX")
        config   - configuration change ("changed timeout from 300 to 600")

    Visibility (visibility):
        private  - visible only to yourself
        project  - visible to all agents in the project (default)
        network  - visible across the whole network

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        agent_id: publisher agent ID
        project_id: owning project ID
        notice_type: type
        content: body
        title: title (optional)
        task_id: related task ID (optional)
        run_uuid: related execution instance ID (optional)
        visibility: visibility scope
        network_id: network ID (derived from project when empty)

    Returns:
        success message and notice_id
    """
    # -- identity check: a notice must be published by the publisher itself, impersonation forbidden (agent_id is the publisher) --
    if agent_id and caller_agent_id and agent_id != caller_agent_id:
        role = _resolve_agent_role(caller_agent_id)
        if role not in ("host_manager", "workshop_manager"):
            return json.dumps(
                {
                    "error": f"权限不足:Agent '{caller_agent_id}' 不能以 '{agent_id}' 名义发布公告。公告必须本人发布。",
                    "permission_denied": True,
                },
                indent=2,
                ensure_ascii=False,
            )

    # derive network_id
    if not network_id:
        proj = execute_sql(
            "SELECT network_id FROM agx_project WHERE project_id = %s",
            (project_id,),
        )
        if proj:
            network_id = proj[0]["network_id"]
        else:
            return json.dumps({"error": f"project {project_id} not found"}, indent=2)

    # generate notice_id
    notice_id = agx_generate_uuid(prefix="note")

    # validate the type
    valid_types = {"log", "decision", "error", "handoff", "config", "other"}
    if notice_type not in valid_types:
        return json.dumps({"error": f"invalid notice_type: {notice_type}"}, indent=2)

    # validate the visibility
    valid_vis = {"private", "project", "network"}
    if visibility not in valid_vis:
        return json.dumps({"error": f"invalid visibility: {visibility}"}, indent=2)

    # write to MySQL
    sql = """
        INSERT INTO agx_agent_notice
        (notice_id, network_id, project_id, agent_id, task_id, run_uuid,
         notice_type, title, content, visibility)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    execute_sql(
        sql,
        (
            notice_id,
            network_id,
            project_id,
            agent_id,
            task_id or None,
            run_uuid or None,
            notice_type,
            title or None,
            content,
            visibility,
        ),
        commit=True,
    )

    # sync Redis
    try:
        _sync_entity(
            "notice",
            notice_id,
            {
                "notice_id": notice_id,
                "network_id": network_id,
                "project_id": project_id,
                "agent_id": agent_id,
                "task_id": task_id or "",
                "run_uuid": run_uuid or "",
                "notice_type": notice_type,
                "title": title or "",
                "content": content[:200],  # Redis keeps only the digest
                "visibility": visibility,
                "active_status": "1",
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )
        r = get_redis_client()
        # agx:idx:notice:all is maintained as zset by _sync_entity; do NOT sadd here.
        r.sadd(f"agx:idx:notice:agent:{agent_id}", notice_id)
        r.sadd(f"agx:idx:notice:project:{project_id}", notice_id)
        if task_id:
            r.sadd(f"agx:idx:notice:task:{task_id}", notice_id)
        if run_uuid:
            r.sadd(f"agx:idx:notice:run:{run_uuid}", notice_id)
    except Exception as exc:
        log(f"agx_write_notice Redis index sync failed for {notice_id}: {exc}", "WARN")

    return json.dumps(
        {
            "status": "success",
            "notice_id": notice_id,
            "notice_type": notice_type,
            "visibility": visibility,
            "message": f"公告已发布: {notice_id}",
        },
        indent=2,
        ensure_ascii=False,
    )


@mcp.tool()
@require_level(0)
def agx_get_notice(caller_agent_id: str, notice_id: str) -> str:
    """
    [Get notice] Get the details of one notice (including the full content).

    Reads the Redis cache first; falls back to MySQL on miss.

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        notice_id: notice ID (e.g. note_xxx)

    Returns:
        notice details as JSON
    """
    # read Redis first
    try:
        r = get_redis_client()
        data = r.hgetall(f"agx:entity:notice:{notice_id}")
        if data:
            # Redis keeps only the digest; the full content is fetched from MySQL
            rows = execute_sql(
                "SELECT content FROM agx_agent_notice WHERE notice_id = %s",
                (notice_id,),
            )
            if rows:
                data["content"] = rows[0]["content"]
            return json.dumps(data, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"agx_get_notice({notice_id}) Redis lookup failed: {e}", "WARN")

    # fall back to MySQL
    rows = execute_sql(
        "SELECT * FROM agx_agent_notice WHERE notice_id = %s",
        (notice_id,),
    )
    if not rows:
        return json.dumps({"error": f"notice {notice_id} not found"}, indent=2)

    row = rows[0]
    row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S") if row.get("created_at") else ""
    return json.dumps(row, indent=2, ensure_ascii=False, default=str)


@mcp.tool()
@require_level(0)
def agx_query_notices(
    caller_agent_id: str,
    project_id: str = "",
    agent_id: str = "",
    notice_type: str = "",
    task_id: str = "",
    run_uuid: str = "",
    visibility: str = "",
    keyword: str = "",
    active_status: str = "1",
    limit: int = 50,
    offset: int = 0,
) -> str:
    """
    [Search notices] Filter the notice list by conditions; multi-dimensional combinations are supported.

    By default only active notices are returned (active_status=1); pass an empty string to query all (including archived).

    When to use:
    - view every error-type notice in a project (shared lessons)
    - trace the full work record of a task by task_id
    - search notices containing a keyword
    - view all decision records of an agent
    - periodic distillation: query the active logs of a period, summarize, then archive

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        project_id: project ID (most common; all notices in the project)
        agent_id: agent ID (one agent's notices)
        notice_type: type filter (log/decision/error/handoff/config/other)
        task_id: trace by task
        run_uuid: trace by execution instance
        visibility: visibility filter (private/project/network)
        keyword: title/body keyword search
        active_status: active-status filter, default "1"=active only, ""=all, "0"=archived only
        limit: items per page (default 50)
        offset: paging offset

    Returns:
        JSON: {total, notices: [...], limit, offset}
    """
    conditions = []
    params = []

    if project_id:
        conditions.append("project_id = %s")
        params.append(project_id)
    if agent_id:
        conditions.append("agent_id = %s")
        params.append(agent_id)
    if notice_type:
        conditions.append("notice_type = %s")
        params.append(notice_type)
    if task_id:
        conditions.append("task_id = %s")
        params.append(task_id)
    if run_uuid:
        conditions.append("run_uuid = %s")
        params.append(run_uuid)
    if visibility:
        conditions.append("visibility = %s")
        params.append(visibility)
    if active_status:
        conditions.append("active_status = %s")
        params.append(active_status)
    if keyword:
        conditions.append("(title LIKE %s OR content LIKE %s)")
        kw = f"%{keyword}%"
        params.append(kw)
        params.append(kw)

    where = " AND ".join(conditions) if conditions else "1=1"

    # count
    count_sql = f"SELECT COUNT(*) as total FROM agx_agent_notice WHERE {where}"
    count_rows = execute_sql(count_sql, tuple(params))
    total = count_rows[0]["total"] if count_rows else 0

    # query (do not return the full content to avoid oversized payloads; return a 200-character digest)
    list_sql = f"""
        SELECT notice_id, network_id, project_id, agent_id, task_id, run_uuid,
               notice_type, title, LEFT(content, 200) as summary, visibility, active_status, created_at
        FROM agx_agent_notice
        WHERE {where}
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
    """
    params.append(limit)
    params.append(offset)

    rows = execute_sql(list_sql, tuple(params))

    notices = []
    for row in rows:
        row["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S") if row.get("created_at") else ""
        notices.append(row)

    return json.dumps(
        {"total": total, "notices": notices, "limit": limit, "offset": offset},
        indent=2,
        ensure_ascii=False,
        default=str,
    )


@mcp.tool()
@require_level(0)
def agx_get_agent_group(caller_agent_id: str, group_id: str) -> str:
    """
    [Query team] Get team details.

    Reads the cache first; auto fallback to MySQL on miss.

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity and enables an audit trail. Read-only tools are no exception -- every call chain must be traceable.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).

        group_id (str): team ID.

    """
    data = _get_with_readthrough("agent_group", group_id, id_col="group_id")

    if "error" not in data:
        # query all member agents of this team (many-to-many)

        members = execute_sql(
            """
            SELECT ar.agent_id, ar.agent_name, ar.agent_role

            FROM agx_agent_group_member gm

            LEFT JOIN agx_agent_registry ar ON gm.agent_id = ar.agent_id

            WHERE gm.group_id = %s AND gm.active_status = 1 AND ar.active_status = 1

            """,
            (group_id,),
        )

        data["members"] = [m["agent_id"] for m in members] if members else []

        data["members_detail"] = members if members else []

    return json.dumps(data, indent=2, ensure_ascii=False)


@mcp.tool()
@require_level(0)
def agx_list_by_network(caller_agent_id: str, network_id: str, entity_type: str, agent_id: str = "") -> str:
    """
    [List entities by network] List every entity under the given network.

     MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity, enables an audit trail, and turns on task visibility filtering. Read-only tools are no
        exception -- every call chain must be traceable.

    Args:

        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).

        network_id (str): network ID.

        entity_type (str): entity type, one of:

            project / host / agent / agent_group / task / execution

        agent_id (str, optional): when entity_type="task", filter by agent_id.
            Omitted returns everything; passed returns only that agent's task list.
            Other entity types ignore this parameter.

    Returns:

        str: a JSON list.

    """
    valid_types = {
        "project": "agx_project",
        "host": "agx_host_inventory",
        "agent": "agx_agent_registry",
        "agent_group": "agx_agent_group",
        "task": "agx_task_base",
        "execution": "agx_task_execution",
    }

    if entity_type not in valid_types:
        return json.dumps({"error": f"Invalid entity_type: {entity_type}. Valid: {list(valid_types.keys())}"}, indent=2)

    try:
        table = valid_types[entity_type]

        # list field trimming: return only the core identity fields, excluding large fields such as x_desc/payload/_md
        if entity_type == "task":
            info = _get_caller_role_info(caller_agent_id)
            where_clause, params = _build_task_visibility_filter(caller_agent_id, info)
            cols = "task_name, task_labs, agent_id, owner_group, task_id, is_private, description, update_dt"
            # agent_id filter: when passed, query only that agent's tasks; when omitted, query all
            if agent_id:
                where_clause = f"{where_clause} AND agent_id = %s"
                params = params + (agent_id,)
            rows = execute_sql(
                f"SELECT {cols} FROM `{table}` WHERE network_id = %s AND ({where_clause})",
                (network_id,) + params,
            )
        else:
            cols_map = {
                "project": "pkid, network_id, project_id, project_name, project_abbr, platform, active_status",
                "host": "pkid, network_id, host_id, host_alias, endpoint_ip, os_platform, platforms, task_slots_limit, active_status, presence_status",
                "agent": "pkid, network_id, agent_id, agent_name, agent_role, agent_type, host_id, project_id, base_model, active_status, deploy_status",
                "agent_group": "pkid, network_id, group_id, group_name, active_status",
                "execution": "pkid, network_id, task_run_uuid, task_id, agent_id, host_id, project_id, run_status, send_agent_id, session_id, exec_name, prev_task, next_task, plan_id, started_at, finished_at, scheduled_at, updated_at, created_at",
            }
            cols = cols_map.get(entity_type, "*")  # fallback for unknown types
            rows = execute_sql(f"SELECT {cols} FROM `{table}` WHERE network_id = %s", (network_id,))

            # read-side visibility scope (shadow mode defaults to off -> behavior unchanged)
            _mode = _read_scope_mode()
            if _mode != "off" and caller_agent_id:
                _scope = _read_scope_of(caller_agent_id)
                if _scope != "all":
                    if _mode == "log":
                        log(f"[READ-SCOPE][log] agx_list_by_network caller={caller_agent_id} scope={_scope} entity={entity_type} would-filter", "WARN")
                    else:
                        _c_rows = execute_sql("SELECT project_id, host_id FROM agx_agent_registry WHERE agent_id = %s", (caller_agent_id,))
                        _cp = (_c_rows[0].get("project_id") or "") if _c_rows else ""
                        _ch = (_c_rows[0].get("host_id") or "") if _c_rows else ""

                        def _visible(_r):
                            if entity_type in ("agent", "execution"):
                                return _can_read_scope(caller_agent_id, _r.get("agent_id") or "", _r.get("project_id") or "", tool="agx_list_by_network")
                            if entity_type == "project":
                                if _scope == "project":
                                    return _caller_admins_project(caller_agent_id, _r.get("project_id") or "")
                                return (_r.get("project_id") or "") == _cp
                            if entity_type == "host":
                                return (_r.get("host_id") or "") == _ch
                            if entity_type == "agent_group":
                                return False
                            return True

                        rows = [r for r in rows if _visible(r)]

        # write the cache

        id_col = _ENTITY_ID_COL.get(entity_type)

        for row in rows:
            eid = row.get(id_col) or row.get("project_id", "") + ":" + row.get("agent_id", "")

            _sync_entity(entity_type, eid, row)

        # handle non-serializable types such as datetime

        def _serialize(obj):

            if hasattr(obj, "isoformat"):
                return obj.isoformat()

            return str(obj)

        return json.dumps(rows, indent=2, ensure_ascii=False, default=_serialize)

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_list_by_network", e)}, indent=2)


@mcp.tool()
@require_level(0)
def agx_list_agent_tasks(caller_agent_id: str, agent_id: str) -> str:
    """
    [Exact agent task list] Return all tasks of the given agent only, never truncated.

    Equivalent to agx_list_by_network(entity_type="task", agent_id=xxx),
    but the semantics are clearer: pin the agent first, then read its task directory.

    Typical use:
        # agentfather wants the host's agent_deploy task
        agent_id = "agx_host_xxx"  # with a host_id suffix on multi-host setups; agx_host on single-host
        tasks = agx_list_agent_tasks(caller_agent_id="agx_agentfather", agent_id=agent_id)
        # iterate tasks and match "agent_deploy" by task_name/first_line

    MCP tool safety rule:
        caller_agent_id is the first mandatory parameter of every MCP tool; it records the caller
        identity, enables an audit trail, and turns on task visibility filtering.

    Args:
        caller_agent_id (str): caller agent ID (required, for identity audit and permission checks).
        agent_id (str): target agent ID, e.g. "agx_host" or "agx_host_srv_a".

    Returns:
        str: a JSON array, each item with task_name, task_labs, agent_id, task_id, is_private.
    """
    try:
        table = "agx_task_base"
        info = _get_caller_role_info(caller_agent_id)
        where_clause, params = _build_task_visibility_filter(caller_agent_id, info)
        cols = "task_name, task_labs, agent_id, owner_group, task_id, is_private, description, update_dt"

        if agent_id:
            where_clause = f"{where_clause} AND agent_id = %s"
            params = params + (agent_id,)

        rows = execute_sql(
            f"SELECT {cols} FROM `{table}` WHERE active_status = 1 AND ({where_clause}) ORDER BY created_at ASC",
            params,
        )

        # write the cache
        for row in rows:
            _sync_entity("task", row.get("task_id", ""), row)

        def _serialize(obj):
            if hasattr(obj, "isoformat"):
                return obj.isoformat()
            return str(obj)

        return json.dumps(rows, indent=2, ensure_ascii=False, default=_serialize)

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_list_agent_tasks", e)}, indent=2)


@mcp.tool()
@require_level(0)
def agx_get_task_by_name(caller_agent_id: str, agent_id: str, task_name: str) -> str:
    """
    [Look up task ID by name] Precisely look up task_id by the stable task_name.

    task_id is a UUID that changes on every initialization; task_name is the stable identifier.
    This tool lets every agent look up a task by task_name instead of hard-coding task_id.

    Typical use:
        # agentfather dispatches agent_deploy to a host
        r = agx_get_task_by_name(
            caller_agent_id="agx_agentfather",
            agent_id="agx_host",
            task_name="agent_deploy"
        )
        task_id = r["task_id"]
        agx_dispatch_task(..., task_id=task_id)

    Match rules:
        1. exact match on agx_task_base.task_name (e.g. "agent deploy")
        2. exact match on agx_task_base.task_labs (e.g. "agent_deploy")
        3. task_name/task_labs are unique within one agent, so there is no ambiguity

    Args:
        caller_agent_id: caller agent ID.
        agent_id: target agent ID.
        task_name: stable task name (e.g. "agent_deploy", "agent_clone").

    Returns:
        str: a JSON object with task_id, task_name, task_labs, agent_id.
             Returns {"error": "task not found"} when not found.
    """
    try:
        table = "agx_task_base"
        info = _get_caller_role_info(caller_agent_id)
        where_clause, params = _build_task_visibility_filter(caller_agent_id, info)

        # exact task_name match
        sql = f"""
            SELECT task_id, task_name, task_labs, agent_id, description, SUBSTRING(x_desc, 1, 80) AS x_desc_preview
            FROM `{table}`
            WHERE active_status = 1
              AND agent_id = %s
              AND ({where_clause})
              AND (task_name = %s OR task_labs = %s)
            LIMIT 1
        """
        rows = execute_sql(
            sql,
            params + (agent_id, task_name, task_name),
        )
        if not rows:
            return json.dumps({"error": f"task '{task_name}' not found for agent '{agent_id}'"}, indent=2)

        return json.dumps(rows[0], indent=2, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_get_task_by_name", e)}, indent=2)


# ============================================================

# Tools: session registry queries (agx_session)

# ============================================================

# session type enum (kept consistent with agx_core.derive_session_type)
#   main     main session / long-lived conversation channel (agent:{aid}:main)
#   assistant assistant (a hand extended by the principal / an alter ego, session_id tail is asst_{slug})
#   (case was retired on 2026-10-22: measured only 2 sessions and 0 rows in agx_execution_link)
#   task    task session (a stable session attached to a task family)
#   oneshot one-off temporary session (the default fallback)
# * 2026-10-22: case retired; assistant added (it was missing before -- agx_update_session trying to set assistant
#   was rejected by the enum check). Four values = main / assistant / task / oneshot.
_SESSION_TYPES = {"main", "assistant", "task", "oneshot"}


@mcp.tool()
@require_level(0)
def agx_get_session(caller_agent_id: str, session_id: str) -> str:
    """
    [Query session] Get session identity information by session_id (name/purpose/owner/activity).

    Solves a long-standing problem: after receiving a task_run_uuid an agent can look up the execution to get
    session_id, then use this tool to look up the session_name of that session_id, and so know "what is this
    session of mine for".

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        session_id (str): session addressing ID, e.g. "agent:agentgeex_agent:main"
            (= agx_task_execution.session_id).

    Returns:
        str: JSON with session_id / session_name / session_type / x_desc / agent_id / project_id /
            task_labs / is_assistant / assistant_slug / creator_agent_id / run_count / last_run_uuid / last_active_at and more.
            * For an assistant session (is_assistant=1) it additionally carries assistant_files_root -- the assistant file
            root (synthesized dynamically; path = runtime base + relative structure; lets the assistant look up where its
            files live at runtime without persisting it in storage).
    """
    try:
        rows = execute_sql(
            "SELECT pkid, network_id, session_id, session_name, session_type, is_assistant, assistant_slug, x_desc, agent_id, project_id, "
            "task_labs, creator_agent_id, run_count, last_run_uuid, last_active_at, "
            "active_status, created_at, updated_at "
            "FROM agx_session WHERE session_id = %s",
            (session_id,),
        )
        if not rows:
            return json.dumps({"error": f"session '{session_id}' not found"}, indent=2, ensure_ascii=False)
        row = rows[0]
        # * 2026-10-10: for an assistant session -> synthesize and return the file root (assistant_files_root).
        #   path = code logic x runtime config (see assistant_files_root_for_slug), not persisted in the DB.
        #   attached only to sessions with is_assistant=1, not to ordinary sessions (avoids noise).
        try:
            if row.get("is_assistant"):
                _aid = (row.get("agent_id") or "").strip()
                _slug = (row.get("assistant_slug") or "").strip()
                if _aid and _slug:
                    row["assistant_files_root"] = assistant_files_root_for_slug(_aid, _slug)
        except Exception:
            pass
        return json.dumps(row, indent=2, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_get_session", e)}, indent=2)


# -- session-level model (decided by Lao Feng on 2026-10-15: the model follows the [session], not the agent) --
# architecture: MCP is a separate stdio subprocess, not inside the gateway process -> reading/writing the "framework (OpenClaw) session live model"
#       requires a WS round trip. It uses the [synchronous RPC dedicated key] channel, with the daemon not involved:
#         MCP rpush agx:ws:task:{host}(command=session_model_get/set, with rpc_id)
#           -> ws_client calls describe_session/patch_session_model
#           -> the result is written to agx:ws:rpc:{rpc_id} (a separate prefix, never touching the execution semantics of agx:ws:result)
#           -> MCP blpop agx:ws:rpc:{rpc_id} waiting for the reply (with timeout)
# the only WS owner = ws_client (the bridge is unique; plan B direct connection is not used).
def _ws_sync_rpc(command: str, payload: dict, timeout: int = 10, host_id: str = "") -> dict:
    """Synchronous RPC: MCP <-> ws_client (via Redis agx:ws:task -> agx:ws:rpc).

    Creates no execution and never touches agx:ws:result -- orthogonal to the execution delivery chain.
    Returns the dict written back by ws_client (with an error field on failure).

    * host_id (2026-11-04, multi-instance): which OpenClaw instance to send to.
      When omitted, config.host_id is used (the gateway process identity; equivalent in a single-instance setup).
      In a multi-instance setup you must pass [the host of the target session/agent]; otherwise the RPC goes to the
      primary instance's ws queue and the primary ws_client handles a session it cannot see -> guaranteed failure.
    """
    try:
        my_host_id = (host_id or "").strip() or config.get("host_id", "")
        if not my_host_id:
            return {"error": "无法获取 host_id,请检查 agx_config.json"}
        r = get_redis_client()
        rpc_id = f"rpc_{uuid.uuid4().hex[:16]}"
        ws_task = {
            "run_uuid": rpc_id,
            "rpc_id": rpc_id,
            "command": command,
            "agent_id": "",
            "instruction": json.dumps(payload, ensure_ascii=False),
            "timeout_seconds": timeout,
        }
        ws_queue_key = f"agx:ws:task:{my_host_id}"
        rpc_key = f"agx:ws:rpc:{rpc_id}"
        r.rpush(ws_queue_key, json.dumps(ws_task, ensure_ascii=False))
        # block waiting for the reply (ws_client side TTL=60s; the timeout here is a safety net)
        hit = r.blpop(rpc_key, timeout=timeout)
        if not hit:
            return {"error": f"WS 同步 RPC 超时({timeout}s) | command={command}"}
        _, raw = hit
        return json.loads(raw)
    except Exception as e:
        return {"error": _log_exception("_ws_sync_rpc", e)}


def _pin_session(session_key: str, pinned: bool = True) -> dict:
    """Pin or unpin a session (a one-way projection onto OpenClaw's sessions.patch pinned).

    * Decided by Lao Feng on 2026-10-18: assistant sessions are pinned by default, **without adding an AgentGeeX
      field** (no new "attention" column).
    Platform facts (OpenClaw 2026.8.35, scouted via agx_platform_openclaw and verified by testing):
      - there is no dedicated sessions.pin/unpin; pinning = the first-class field `pinned: bool` of sessions.patch
      - the server writes pinnedAt (SQLite pinned_at); when true it does `pinnedAt ??= now` (idempotent)
      - an archived session cannot be pinned (invalid); archiving clears pinnedAt automatically
      - permission operator.write; **WS JSON-RPC only**, there is no sessions route over HTTP

    Failure policy (decided by Lao Feng on 2026-10-18): **ignore**.
      Pinning is a side effect; it must not make the main "create assistant" flow fail.
      So this function never raises; on failure it only logs and returns {"error": ...}.

    Args:
        session_key: target session (e.g. agent:xxx:case_agx_dev).
        pinned: True=pin; False=unpin.

    Returns:
        the dict written back by ws_client (with pinned/pinnedAt on success); with error on failure.
    """
    try:
        if not session_key:
            return {"error": "empty session_key"}
        rpc = _ws_sync_rpc(
            "session_pin_set",
            {"session_key": session_key, "pinned": bool(pinned)},
            timeout=8,
            host_id=_host_of_session(session_key),
        )
        if rpc.get("error"):
            # failures are ignored: the main flow is not blocked, only logged.
            try:
                print(
                    f"[_pin_session] 置顶失败(已忽略) | key={session_key} | "
                    f"pinned={pinned} | {rpc.get('error')}",
                    file=sys.stderr,
                )
            except Exception:
                pass
        return rpc
    except Exception as e:
        # never raises (failures are ignored).
        try:
            _log_exception("_pin_session", e)
        except Exception:
            pass
        return {"error": str(e)}


def _check_session_model_permission(caller_agent_id: str, session_key: str) -> str | None:
    """Three permission tiers for reading/writing a session-level model (defined 2026-10-15, reusing the idea of _check_resource_ownership).

    Tier a) system group (workshop_manager / host_manager) -> allowed on everything;
    Tier b) ordinary agent -> session_key must start with "agent:{caller}:" (self-limited, same as agx_send_text);
    Tier c) project_manager -> the agent owning the target session belongs to a project the caller administers -> allowed.
    Returns None=allowed, str=reason for denial.
    """
    if not caller_agent_id or not session_key:
        return "参数缺失: caller_agent_id / session_key 均必填"
    role = _resolve_agent_role(caller_agent_id)
    # tier a) system group
    if role in ("host_manager", "workshop_manager"):
        return None
    # tier b) self-limited: session_key starts with agent:{caller}:
    if session_key.startswith(f"agent:{caller_agent_id}:"):
        return None
    # tier c) PM's own member: the agent owning the target session belongs to a project the caller administers
    if role == "project_manager":
        target_agent_id = session_key.split(":")[1] if session_key.startswith("agent:") and len(session_key.split(":")) >= 2 else ""
        if target_agent_id:
            rows = execute_sql(
                "SELECT a.project_id AS project_id, p.admin_agent_id AS admin_agent_id "
                "FROM agx_agent_registry a LEFT JOIN agx_project p ON a.project_id = p.project_id "
                "WHERE a.agent_id = %s AND a.active_status = 1",
                (target_agent_id,),
            )
            if rows and rows[0].get("admin_agent_id") == caller_agent_id:
                return None
        return (
            f"权限不足:'{caller_agent_id}'(project_manager) 只能操作自己 admin 项目下成员的会话模型, "
            f"当前目标 '{session_key}' 不在其项目内。"
        )
    return (
        f"权限不足:'{caller_agent_id}' 只能读写自己的会话模型(session_key 须以 "
        f"'agent:{caller_agent_id}:' 开头)。系统组(host_manager/workshop_manager)与 "
        "本项目 PM 可在授权范围内操作。"
    )


def _sync_session_model_to_db(session_key: str, model: str, provider: str, source: str) -> None:
    """Write the session's live model back into the 3 agx_session columns (fault tolerant: skip silently when the row is missing, log WARN)."""
    try:
        rows = execute_sql(
            "SELECT pkid FROM agx_session WHERE session_id = %s",
            (session_key,),
        )
        if not rows:
            log(f"[session_model] agx_session 无行, 跳过写回 | session={session_key}", "WARN")
            return
        execute_sql(
            "UPDATE agx_session SET session_model = %s, model_provider = %s, model_source = %s "
            "WHERE session_id = %s",
            (model, provider, source, session_key),
            commit=True,
        )
    except Exception as e:
        log(f"[session_model] 写回 agx_session 失败 | session={session_key} | {e}", "WARN")


@mcp.tool()
@require_level(1)
def agx_get_session_model(caller_agent_id: str, session_key: str) -> str:
    """
    [Read session model] Get the live model currently used by a framework (OpenClaw) session (the model follows the session, not the agent).

    Background (decided by Lao Feng on 2026-10-15): `agx_agent_registry.base_model` is deprecated -- models are no
    longer bound to agents but to [sessions]. This tool reads the session's live model / modelProvider / modelOverrideSource.

    Mechanism: MCP asks ws_client (sessions.describe) over a synchronous WS RPC for the live model.
    Side effect: once obtained, it is [written back into the 3 agx_session columns]
    (session_model/model_provider/model_source) so the DB side can aggregate offline
    (e.g. "which sessions use a given model").

    Three permission tiers (see _check_session_model_permission):
        - system group (host_manager/workshop_manager) -> any session
        - ordinary agent -> only its own (agent:{caller}:...)
        - project_manager -> sessions of members in a project it administers

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        session_key (str): session addressing key, e.g. "agent:agentgeex_agent:main".

    Returns:
        str: JSON with session_key / model / modelProvider / modelOverrideSource / agentRuntime;
             with error on failure.
    """
    try:
        deny = _check_session_model_permission(caller_agent_id, session_key)
        if deny:
            return json.dumps({"error": deny, "permission_denied": True}, ensure_ascii=False)
        rpc = _ws_sync_rpc(
            "session_model_get",
            {"session_key": session_key},
            timeout=10,
            host_id=_host_of_session(session_key),
        )
        if rpc.get("error"):
            return json.dumps(rpc, ensure_ascii=False)
        # sync back to agx_session (write back only when model is non-empty, to avoid clearing state by mistake)
        model = rpc.get("model") or ""
        provider = rpc.get("modelProvider") or ""
        source = rpc.get("modelOverrideSource") or "default"
        if model:
            _sync_session_model_to_db(session_key, model, provider, source)
        return json.dumps(rpc, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_get_session_model", e)}, ensure_ascii=False)


@mcp.tool()
@require_level(1)
def agx_set_session_model(caller_agent_id: str, session_key: str, model: str = "") -> str:
    """
    [Set session model] Set/clear the model of a framework (OpenClaw) session (session-level binding; other sessions of the same agent are unaffected).

    Mechanism: MCP calls ws_client (sessions.patch) over a synchronous WS RPC to write the override;
    on success it [writes back into the 3 agx_session columns].

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        session_key (str): session addressing key, e.g. "agent:agentgeex_agent:main".
        model (str): model identifier, format "<provider>/<model>" or "<model>" (e.g. "deepseek/deepseek-v4-flash").
            * an empty string -> clear the session override and fall back to the framework default.

    Three permission tiers: same as agx_get_session_model.

    Returns:
        str: JSON with session_key / model / modelProvider / modelOverrideSource / resolved;
             with error on failure.
    """
    try:
        deny = _check_session_model_permission(caller_agent_id, session_key)
        if deny:
            return json.dumps({"error": deny, "permission_denied": True}, ensure_ascii=False)
        rpc = _ws_sync_rpc(
            "session_model_set",
            {"session_key": session_key, "model": (model or None)},
            timeout=10,
            host_id=_host_of_session(session_key),
        )
        if rpc.get("error"):
            return json.dumps(rpc, ensure_ascii=False)
        # sync back to agx_session
        _model = rpc.get("model") or ""
        provider = rpc.get("modelProvider") or ""
        source = rpc.get("modelOverrideSource") or ("default" if not _model else "user")
        _sync_session_model_to_db(session_key, _model, provider, source)
        return json.dumps(rpc, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_set_session_model", e)}, ensure_ascii=False)


@mcp.tool()
@require_level(3)
def agx_assistant_profile(
    caller_agent_id: str,
    slug: str,
    assistant_name: str,
    responsibilities: str = "",
    collaborators: str = "",
    deliverables: str = "",
    extra_discipline: str = "",
    avatar: str = "",
    write: bool = False,
    mark_assistant: bool = True,
    create_dirs: bool = True,
) -> str:
    """
    [Assistant profile generator] Generate an assistant duty profile (x_desc) from a template; optionally write it to the DB.

    Design intent (confirmed with Lao Feng on 2026-10-09):
        The skeleton of an assistant profile is [fixed] -- "who I am / * where my files are / baseline work discipline"
        has nothing to do with the specific assistant (fully generated); only "duties/collaborators/deliverables/extra
        discipline" are variables.
        Hand-written SQL inevitably drifts (missing blocks / inconsistent formatting), so it is a generator:
        fill in the template, one consistent source.

    * Fixed parts (filled by the program; the human need not supply them):
        - header / who I am (including the defining line "I am a hand extended by the principal")
        - * where my files are (slug + file root + daily/notes/outputs) <- the core Lao Feng called out
        - the 3 baseline work-discipline items
    * Variable parts (supplied by the human, one per line):
        - responsibilities / collaborators / deliverables / extra_discipline

    Idempotency and safety:
        - write=False (default): only generate and return x_desc (preview); no DB write, no state change.
        - write=True: writes agx_session.x_desc; with mark_assistant=True it also sets
          is_assistant=1; with create_dirs=True it also creates the file-root skeleton (idempotent).
        - permission (L3 + resource-level ownership): may only write to your own case session (or project admin / global roles).

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        slug (str): assistant identifier (e.g. ui_design), limited to [a-z0-9_-].
        assistant_name (str): assistant name (e.g. "UI design assistant").
        responsibilities (str): duty list, one per line (newline separated).
        collaborators (str): collaborators, one per line.
        deliverables (str): deliverables, one per line.
        extra_discipline (str): assistant-specific discipline items, one per line (appended after the baseline).
        avatar (str): assistant avatar (URL or local path, no binary stored), optional; written into agx_session.avatar when non-empty.
        write (bool): whether to write to the DB (default False = preview only).
        mark_assistant (bool): whether to set is_assistant=1 when write=True (default True).
        create_dirs (bool): whether to create the directory skeleton when write=True (default True).

    Returns:
        str: JSON with slug / session_id / files_root / x_desc, plus the result of the write.
    """
    try:
        # * 2026-10-21: normalize the parameter (accepting asst_/case_/full session_id forms)
        slug = normalize_assistant_slug(slug)
        if not slug:
            return json.dumps({"error": "invalid slug (allowed: [a-z0-9_-], 可带 asst_/case_ 前缀)"},
                              indent=2, ensure_ascii=False)
        name = _sanitize_name_field(assistant_name) or slug

        def _split(s):
            return [ln.strip() for ln in (s or "").splitlines() if ln.strip()]

        # principal information (derived from the caller)
        _owner_row = execute_sql(
            "SELECT agent_name FROM agx_agent_registry WHERE agent_id = %s LIMIT 1",
            (caller_agent_id,),
        )
        owner_name = (_owner_row[0].get("agent_name") if _owner_row else "") or "AgentGeeX 维护者"

        # ** fixed 2026-10-22 (a structural problem Lao Feng called out):
        #   it used to "guess the prefix" -- concatenate asst_ and query, then concatenate case_ and query, two rounds of string guessing.
        #   This is the root cause of "switching the prefix from case to asst broke it":
        #   resolution depended on [the prefix spelling] rather than [the identity], so any prefix change, or a third prefix, lost the link immediately.
        #   Now it goes through _resolve_assistant_session: a DB lookup hitting the assistant_slug column,
        #   **whatever the prefix is makes no difference** (the same resolution as agx_assistant_send).
        session_id = _resolve_assistant_session(caller_agent_id, slug)
        files_root = assistant_files_root_for_slug(caller_agent_id, slug)

        x_desc = build_assistant_profile(
            slug=slug,
            assistant_name=name,
            responsibilities=_split(responsibilities),
            collaborators=_split(collaborators),
            deliverables=_split(deliverables),
            extra_discipline=_split(extra_discipline),
            owner_agent_id=caller_agent_id,
            owner_agent_name=owner_name,
            files_root=files_root,
            # * fixed 2026-10-22 (P1 reported by aifr_pm): the real session_id is already computed above
            #   (including the asst_ and legacy case_ compatibility decision), and it must be passed down here --
            #   otherwise the generator can only guess the prefix and an asst_ assistant's profile would be written as case_.
            session_id=session_id,
        )

        out = {
            "slug": slug,
            "assistant_name": name,
            "session_id": session_id,
            "files_root": files_root,
            "x_desc": x_desc,
            "x_desc_bytes": len(x_desc.encode("utf-8")),
            "written": False,
        }

        if not write:
            out["hint"] = "预览模式(write=False), 未写库。确认无误后传 write=True 落库。"
            return json.dumps(out, indent=2, ensure_ascii=False)

        # -- write the DB --
        # the session must exist (create it with agx_assistant_create first, then generate the profile)
        _sess_row = execute_sql(
            "SELECT session_id FROM agx_session WHERE session_id = %s LIMIT 1",
            (session_id,),
        )
        if not _sess_row:
            return json.dumps(
                {"error": f"session '{session_id}' 不存在; 请先用 agx_assistant_create(slug='{slug}') 建助理（建会话+档案一步到位）"},
                indent=2,
                ensure_ascii=False,
            )

        # resource-level ownership: your own session / project admin / global roles
        _own_row = execute_sql(
            "SELECT agent_id, project_id FROM agx_session WHERE session_id = %s LIMIT 1",
            (session_id,),
        )
        _deny = _check_resource_ownership(
            caller_agent_id,
            (_own_row[0].get("agent_id") or "") if _own_row else "",
            (_own_row[0].get("project_id") or "") if _own_row else "",
        )
        if _deny:
            return json.dumps({"error": _deny}, indent=2, ensure_ascii=False)

        if create_dirs:
            try:
                for _sub in ("daily", "notes", "outputs"):
                    os.makedirs(os.path.join(files_root, _sub), exist_ok=True)
                out["dirs_created"] = True
            except Exception:
                out["dirs_created"] = False

        _avatar = (avatar or "").strip()
        if mark_assistant:
            if _avatar:
                execute_sql(
                    "UPDATE agx_session SET x_desc = %s, is_assistant = 1, avatar = %s WHERE session_id = %s",
                    (x_desc, _avatar, session_id),
                    commit=True,
                )
            else:
                execute_sql(
                    "UPDATE agx_session SET x_desc = %s, is_assistant = 1 WHERE session_id = %s",
                    (x_desc, session_id),
                    commit=True,
                )
        else:
            if _avatar:
                execute_sql(
                    "UPDATE agx_session SET x_desc = %s, avatar = %s WHERE session_id = %s",
                    (x_desc, _avatar, session_id),
                    commit=True,
                )
            else:
                execute_sql(
                    "UPDATE agx_session SET x_desc = %s WHERE session_id = %s",
                    (x_desc, session_id),
                    commit=True,
                )
        out["avatar"] = _avatar
        out["written"] = True
        out["mark_assistant"] = bool(mark_assistant)

        # * decided by Lao Feng on 2026-10-18: assistant sessions are pinned by default (no AgentGeeX field, projected straight onto the platform).
        #   placed under the mark_assistant branch = only sessions that are [really marked as assistants] get pinned.
        #   So non-assistant sessions are unaffected (they do not go through mark_assistant=True).
        #   failures are [ignored]: pinning is a side effect and must not fail the profile write.
        if mark_assistant:
            _pin_res = _pin_session(session_id, True)
            _pin_ok = bool(_pin_res) and not _pin_res.get("error")
            out["pinned"] = _pin_ok
            if not _pin_ok:
                out["pin_error"] = _pin_res.get("error")

        out["hint"] = "档案已写入 x_desc。若助理尚未收到过身份提示, 下次派发时会自动注入并指示重读本档案。"
        return json.dumps(out, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_assistant_profile", e)}, indent=2)


# ===========================================================
# assistant facade -- the trio
# intent (decided by Lao Feng on 2026-10-09): an assistant is a [high-frequency specialized case] of agx_dispatch_task,
# and the generic method costs too much to explain -> a dedicated facade for the assistant family: self-explanatory names, arguments in assistant terms.
# All are [pure wrappers]: they reuse the already-decorated generic tools underneath, changing nothing and adding no mechanism.
#   agx_assistant_create -> create an assistant (create session + generate profile, one step)
#   agx_assistant_list   -> list my assistants
#   agx_assistant_send   -> message an assistant (= a dispatch wrapper, addressed by slug)
#   (agx_assistant_profile is kept: to fine-tune an existing assistant's profile afterwards)
# ===========================================================


_ASSIST_PREFIXES = ("asst_", "case_")


def normalize_assistant_slug(raw: str) -> str:
    """Normalize every accepted input form into a [pure slug] -- the assistant's unique identifier.

    * Decided by Lao Feng on 2026-10-21 (his words: "the assistant_slug field is doing too many jobs ...
      sometimes the agent also treats it as an ID, which constantly causes mistakes"):
      **slug is the single source of truth; the prefix decorates the id, it is not part of the identity.**

    Legacy baggage (why normalization is mandatory):
      - before 10-19 the assistant session prefix was case_, afterwards new ones use asst_
      - agents often pass "the prefixed id tail", or even "the full session_id", as the slug
      - the old implementation concatenated `asst_{input}` -> silently producing garbage ids:
          'case_agx_dev'                  -> asst_case_agx_dev
          'asst_writing_research'         -> asst_asst_writing_research
          'agent:x:case_agx_dev'          -> asst_agentagentgeex_agentcase_agx_dev
        No error, and an orphan session was created -- that is the "treat the slug as an id" trap.

    Accepted forms (all normalized to agx_dev):
        agx_dev / ASST_agx_dev / case_agx_dev / asst_agx_dev
        agent:aifr_pm:asst_agx_dev / agent:aifr_pm:case_agx_dev
        case_case_docs (historical double-prefix dirt) -> docs
    """
    s = (raw or "").strip().lower()
    if not s:
        return ""
    # full session_id -> take the tail segment (agent:xxx:{tail})
    if s.startswith("agent:") or ":" in s:
        s = s.split(":")[-1]
    # strip prefixes (the while loop handles the historical double prefix case_case_docs)
    changed = True
    while changed:
        changed = False
        for p in _ASSIST_PREFIXES:
            if s.startswith(p) and len(s) > len(p):
                s = s[len(p):]
                changed = True
    return "".join(ch for ch in s if ch.isalnum() or ch in "_-")[:48]


def _resolve_assistant_session(caller_agent_id: str, slug: str) -> str:
    """Translate an assistant slug (in any form) into a session addressing id.

    ** Refactor 2026-10-21: **stop guessing prefixes; look the slug up in the DB instead**.

    Root cause of the old implementation:
        `new_id = f"agent:{caller}:asst_{slug}"` then retry `case_{slug}`
        That was [deriving identity from the id shape]: two sources of truth (the assistant_slug column vs the id
        tail) were each derived independently, so any mismatch lost the link; and a wrong form silently produced garbage.

    New implementation (single source of truth = agx_session.assistant_slug):
        (1) normalize the input (strip any prefix / session_id wrapper)
        (2) DB lookup: WHERE agent_id=? AND assistant_slug=? AND active_status=1
            -> on hit, return the real session_id (**whatever the prefix is, it does not matter**)
        (3) on miss -> conclude "this assistant has not been created yet" and synthesize a candidate id with the
            current new prefix asst_ (for the session-creation flow of agx_assistant_create; send will clearly
            error out because it cannot find it)

    Benefits:
        - existing case_ and new asst_ are treated alike, no compatibility branch needed
        - an agent passing 'case_agx_dev' or a full id is resolved correctly, no more orphans
        - one extra DB query, but it saves the two round trips of "try asst_ then case_" (net neutral)
    """
    slug = normalize_assistant_slug(slug)
    if not slug:
        return ""
    # priority: the assistant type > asst_ prefix (new style) > activity count > stable id order
    # no LIKE + ESCAPE (backslash escaping easily breaks under pymysql % formatting),
    # SUBSTRING_INDEX is used to take the tail prefix instead, with no escaping issues.
    _ORDER = ("ORDER BY (session_type = 'assistant') DESC, "
              "(LEFT(SUBSTRING_INDEX(session_id, ':', -1), 5) = 'asst_') DESC, "
              "run_count DESC, session_id LIMIT 1")
    try:
        # (1) active ones first
        rows = execute_sql(
            "SELECT session_id FROM agx_session "
            "WHERE agent_id = %s AND assistant_slug = %s AND active_status = 1 "
            + _ORDER,
            (caller_agent_id, slug),
        )
        if rows:
            return str(rows[0].get("session_id") or "")
        # (2) archived ones must be recognized too -- an archived assistant may still be referenced by send/profile,
        #    and in that case **return its real id** (so the upper layer clearly reports "archived") rather than falling back to guessing
        #    and creating a non-existent candidate id (which would become a duplicated orphan).
        rows = execute_sql(
            "SELECT session_id FROM agx_session "
            "WHERE agent_id = %s AND assistant_slug = %s " + _ORDER,
            (caller_agent_id, slug),
        )
        if rows:
            return str(rows[0].get("session_id") or "")
    except Exception:
        pass
    # (3) genuinely absent from the DB -> return a candidate id.
    #    * only for the "this assistant has not been created yet" case (agx_assistant_create uses it to create).
    #    if profile/send receive one and cannot find the session, they error out clearly instead of silently creating an orphan.
    return f"agent:{caller_agent_id}:asst_{slug}"


@mcp.tool()
@require_level(3)
def agx_assistant_drift(
    caller_agent_id: str,
    slug: str,
    dry_run: bool = False,
) -> str:
    """
    [Assistant drift] Do only two things: confirm/create the session + fill in the directory skeleton. **Do not touch the profile, do not move files, do not pin.**

    * Why a separate method (decided by Lao Feng on 2026-10-27):
        An assistant = session + directories. When moving to another host (drift), what is actually missing is only
        "does this session exist on this host" and "does the assistants/{slug}/ skeleton exist" -- the profile should
        not be rewritten. But both existing entry points have extra side effects, so they cannot do just these two things:
          - agx_assistant_create: creates the session + **rewrites the x_desc profile** + pins in OpenClaw + creates directories
          - agx_assistant_profile: creates directories only when write=True, but then writes x_desc;
            write=False only previews and **does not create directories** (create_dirs sits inside the write branch)
        Lao Feng explicitly asked for "a separate method, do not add a mode parameter to create" --
        one method does one thing, so the semantics stay crisp.

    * Why files are not moved (decided by Lao Feng on 2026-10-27):
        The assistant directory lives inside the agent's own workspace
        ({work_dir}/agx_agents/{agent_id}/workspace/assistants/{slug}),
        and the workspace travels with agent deployment / the version repo, so nothing needs moving anyway.
        **And empty directories are not tracked by Git** -- so what must be filled in is exactly the skeleton:
        if it is not created, those three empty directories do not exist after a clone/deploy.

    * Responsibility boundary with agx_assistant_profile:
        This method **does not write x_desc / session_name / is_assistant / avatar / pin / model**.
        The profile always belongs to agx_assistant_profile. The `untouched` field in the return value is deliberate --
        it turns "what I did not change" into checkable evidence instead of asking for trust.

    Args:
        caller_agent_id (str): caller agent ID (the assistant's owner, required).
        slug (str): assistant identifier; accepts agx_dev / asst_agx_dev / case_agx_dev / a full session_id.
        dry_run (bool): True = only report "what would be done"; no DB write, no directory creation.

    Returns:
        str: JSON with session_existed / session_created / dirs_created /
             dirs_existing / dirs_failed / untouched.
    """
    subs = ("daily", "notes", "outputs")
    untouched = ["x_desc", "session_name", "is_assistant", "avatar", "pinned", "session_model"]
    try:
        slug = normalize_assistant_slug(slug)
        if not slug:
            return json.dumps(
                {"error": "invalid slug (allowed: [a-z0-9_-], 可带 asst_/case_ 前缀)"},
                indent=2, ensure_ascii=False)

        session_id = _resolve_assistant_session(caller_agent_id, slug)
        if not session_id:
            return json.dumps({"error": "cannot resolve assistant session id"},
                              indent=2, ensure_ascii=False)

        root = assistant_files_root_for_slug(caller_agent_id, slug)
        dirs_existing = [s for s in subs if os.path.isdir(os.path.join(root, s))]
        would_create = [s for s in subs if s not in dirs_existing]

        # -- whether the session already existed (do not rely on ensure_session's return value) --
        try:
            _rows = execute_sql(
                "SELECT session_id, is_assistant FROM agx_session WHERE session_id = %s LIMIT 1",
                (session_id,),
            )
            _existed = bool(_rows)
            # read-only evidence: the actual DB value (before creation it is necessarily False, meaning only "no such assistant row yet")
            _is_assistant = bool(_rows[0].get("is_assistant")) if _rows else False
            # * untouched semantics = which fields this call [does not write] (evidence, not a slogan).
            #   the create branch does set is_assistant=1 (a type marker, not profile content), so that branch does not list it
            #   under untouched -- otherwise the return value would be lying.
            if not _existed:
                untouched = [u for u in untouched if u != "is_assistant"]
        except Exception as _e:
            return json.dumps({"error": f"query agx_session failed: {_e}"},
                              indent=2, ensure_ascii=False)

        if dry_run:
            return json.dumps({
                "slug": slug,
                "session_id": session_id,
                "files_root": root,
                "session_existed": _existed,
                "session_is_assistant": _is_assistant,
                "session_created": False,
                "session_would_create": (not _existed),
                "dirs_created": [],
                "dirs_existing": dirs_existing,
                "would_create_dirs": would_create,
                "dirs_failed": [],
                "dry_run": True,
                "untouched": untouched,
                "hint": "dry_run: 未写库、未建目录。去掉 dry_run 才真正落地。",
            }, indent=2, ensure_ascii=False)

        # -- Step 1: the session (if it exists, **not a single field is changed**) --
        session_created = False
        if not _existed:
            ensure_session(
                session_id=session_id,
                name_hint=slug,
                agent_id=caller_agent_id,
                project_id=_resolve_project_id_from_agent(caller_agent_id),
                task_labs="",
                creator_agent_id=caller_agent_id,
                network_id=config.get("network_id", "") or "",
                x_desc=f"助理会话 · {slug}",
            )
            # * decided by Lao Feng on 2026-10-27: the drift create branch sets is_assistant
            #   Why it must: ensure_session's INSERT column list omits is_assistant (so it defaults to 0),
            #   while agx_assistant_list filters by session_type IN ('assistant','case') AND is_assistant=1.
            #   The drift semantics are "make this assistant exist on this machine" -- creating it with is_assistant=0 is half an assistant
            #   (it does not show in the list = created but as good as not created).
            #   Why it does not violate "do not touch the profile": is_assistant is a [type marker], not profile content (profile = x_desc).
            #   Why not change agx_core.ensure_session: that is a global function whose blast radius far exceeds this method -- patch it here only.
            _fix_err = ""
            try:
                execute_sql(
                    "UPDATE agx_session SET is_assistant = 1 WHERE session_id = %s",
                    (session_id,), commit=True,
                )
            except Exception as _e:
                _fix_err = str(_e)
            try:
                _after = execute_sql(
                    "SELECT session_id, is_assistant FROM agx_session WHERE session_id = %s LIMIT 1",
                    (session_id,),
                )
                session_created = bool(_after)
                # re-read the real value; do not take "I just issued the UPDATE" as the conclusion
                if _after:
                    _is_assistant = bool(_after[0].get("is_assistant"))
            except Exception:
                session_created = False

        # -- Step 2: the directory skeleton (distinguishing created / existing / failed) --
        dirs_created = []
        dirs_failed = []
        for _sub in subs:
            _p = os.path.join(root, _sub)
            if os.path.isdir(_p):
                continue  # already exists (already recorded in dirs_existing)
            try:
                os.makedirs(_p, exist_ok=True)
                if os.path.isdir(_p):
                    dirs_created.append(_sub)
                else:
                    dirs_failed.append(_sub)
            except Exception:
                dirs_failed.append(_sub)

        # -- hint: report, do not change --
        # the exists branch must never write is_assistant just to "look consistent" (contract: if it exists, not one field is UPDATEd).
        # if a legacy row has is_assistant=0, only expose it as evidence so the caller decides whether to run profile to fix it.
        _hint = "漂移已完成：会话就绪、目录骨架就绪。档案仍由 agx_assistant_profile 管理。"
        if _existed and not _is_assistant:
            _hint = ("注意：该会话 is_assistant=0（历史遗留），不会出现在 agx_assistant_list；"
                     "需要时用 agx_assistant_profile(write=True) 归位，本方法不擅自修改存量行。")
        elif not _existed and not _is_assistant:
            _hint = ("注意：新建后 is_assistant 仍为 0（补写未生效），该会话不会出现在 "
                     "agx_assistant_list；请重试或用 agx_assistant_profile(write=True) 归位。")
        if not _existed and _fix_err:
            _hint = _hint + f"（is_assistant 补写异常：{_fix_err}）"

        return json.dumps({
            "slug": slug,
            "session_id": session_id,
            "files_root": root,
            "session_existed": _existed,
            "session_is_assistant": _is_assistant,
            "session_created": session_created,
            "dirs_created": dirs_created,
            "dirs_existing": dirs_existing,
            "dirs_failed": dirs_failed,
            "dry_run": False,
            "untouched": untouched,
            "hint": _hint,
        }, indent=2, ensure_ascii=False)
    except Exception as _e:
        return json.dumps({"error": f"agx_assistant_drift failed: {_e}"},
                          indent=2, ensure_ascii=False)


@mcp.tool()
@require_level(3)
def agx_assistant_create(
    caller_agent_id: str,
    slug: str,
    assistant_name: str,
    responsibilities: str = "",
    collaborators: str = "",
    deliverables: str = "",
    extra_discipline: str = "",
    avatar: str = "",
) -> str:
    """
    [Create assistant] Create an assistant in one step -- create the session + generate the duty profile + mark is_assistant + create the file directories.

    * Why this method exists (decided by Lao Feng on 2026-10-09):
        An assistant = ensure_session (create session) + agx_assistant_profile (generate profile), two steps.
        Two steps force the caller to remember two steps, and each needs a long explanation -> high explanation cost.
        Assistants are a high-frequency specialized case, so they deserve a dedicated facade: "create assistant" in one call.

    * This tool = pure wrapper (no change to the underlying logic):
        Internally calls ensure_session (create session) then agx_assistant_profile(write=True),
        and handles idempotency (reuse the session when the assistant already exists, refresh the profile).

    * Post-hoc adjustment: to fine-tune an existing assistant's profile, call agx_assistant_profile(write=True).
        (This tool = creation; profile = modification. Clear separation of duties.)

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        slug (str): assistant identifier (e.g. ui_design), limited to [a-z0-9_-]. * Do not include the case_ prefix.
        assistant_name (str): assistant name (e.g. "UI design assistant").
        responsibilities (str): duty list, one per line (newline separated).
        collaborators (str): collaborators, one per line.
        deliverables (str): deliverables, one per line.
        extra_discipline (str): assistant-specific discipline items, one per line (appended after the baseline).
        avatar (str): assistant avatar (URL or local path, no binary stored), optional; passed through to agx_assistant_profile to write into agx_session.avatar.

    Returns:
        str: JSON with slug / session_id / files_root / reused / profile_written.
    """
    try:
        # * 2026-10-21: normalize the parameter (accepting asst_/case_/full session_id forms)
        slug = normalize_assistant_slug(slug)
        if not slug:
            return json.dumps({"error": "invalid slug (allowed: [a-z0-9_-], 可带 asst_/case_ 前缀)"},
                              indent=2, ensure_ascii=False)
        name = _sanitize_name_field(assistant_name)
        if not name:
            return json.dumps({"error": "assistant_name is required"}, indent=2, ensure_ascii=False)

        # ** 2026-10-22: removed the old case_ fallback branch.
        #   it [wrote the prefix guessing a second time] outside _resolve_assistant_session --
        #   two implementations of one thing = two sources of truth, fix one and miss the other.
        #   the resolver already looks up the DB by assistant_slug: a legacy case_ assistant still hits its real id,
        #   so there is no need to concatenate case_ out here to compare.
        session_id = _resolve_assistant_session(caller_agent_id, slug)

        # determine whether it already exists (for the reused marker)
        _exist = execute_sql(
            "SELECT session_id FROM agx_session WHERE session_id = %s LIMIT 1",
            (session_id,),
        )
        reused = bool(_exist)

        # -- Step 1: create the session --
        # ** refactor 2026-10-22 (Lao Feng called it out):
        #   it used to go through a since-deleted intermediate function whose way of creating a session was
        #   **dispatching a fake "declare item" execution**, letting dispatch incidentally write the session row.
        #   Simply to "create a session" it ran a whole LLM execution, and 27 meaningless "declare item"
        #   executions have accumulated in the DB because of it. That was a historical hack.
        #
        #   Now it calls the underlying ensure_session directly (agx_core.py:987):
        #   an idempotent INSERT / when it exists only the counter is updated, dispatching no execution.
        #   the directory skeleton used to be created incidentally by that intermediate function; it is added explicitly here
        #   (profile's create_dirs creates it too, double safety and both idempotent).
        #
        #   * this also fixes the double-open bug: ensure_session is idempotent by session_id,
        #     and the session_id comes from the resolver (the real id), no longer decided by concatenating a prefix.
        _declared_exist = execute_sql(
            "SELECT session_id, session_name FROM agx_session WHERE session_id = %s LIMIT 1",
            (session_id,),
        )
        if not _declared_exist:
            try:
                for _sub in ("daily", "notes", "outputs"):
                    os.makedirs(os.path.join(assistant_files_root_for_slug(caller_agent_id, slug), _sub),
                                exist_ok=True)
            except Exception:
                pass  # a failed directory creation does not block (profile's create_dirs will try once more)
            ensure_session(
                session_id=session_id,
                name_hint=name,
                agent_id=caller_agent_id,
                project_id=_resolve_project_id_from_agent(caller_agent_id),
                task_labs="",
                creator_agent_id=caller_agent_id,
                network_id=config.get("network_id", "") or "",
                x_desc=f"助理会话 · {name}",
            )

        # Step 2: generate the duty profile (write DB + mark is_assistant + create directories)
        _prof_res = agx_assistant_profile(
            caller_agent_id=caller_agent_id,
            slug=slug,
            assistant_name=name,
            responsibilities=responsibilities,
            collaborators=collaborators,
            deliverables=deliverables,
            extra_discipline=extra_discipline,
            avatar=avatar,
            write=True,
            mark_assistant=True,
            create_dirs=True,
        )
        try:
            _prof_json = json.loads(_prof_res)
        except Exception:
            _prof_json = {"raw": _prof_res}
        if "error" in _prof_json:
            return json.dumps({"error": f"生成档案失败: {_prof_json['error']}"}, indent=2, ensure_ascii=False)

        # Step 3: * decided by Lao Feng on 2026-10-18 -- assistant sessions are pinned by default.
        #   No AgentGeeX field is added (no new "attention slot" column); it is projected one-way onto OpenClaw's
        #   sessions.patch {pinned:true}. Failures are [ignored] (pinning is a side effect; it must not fail assistant creation).
        _pin_res = _pin_session(session_id, True)
        _pinned_ok = bool(_pin_res) and not _pin_res.get("error")

        return json.dumps(
            {
                "slug": slug,
                "assistant_name": name,
                "session_id": session_id,
                "files_root": _prof_json.get("files_root", ""),
                "reused": reused,
                "profile_written": True,
                "pinned": _pinned_ok,
                "pin_error": (None if _pinned_ok else _pin_res.get("error")),
                "hint": (
                    "助理已就绪（会话已默认置顶）。后续用 agx_assistant_send(slug=..., instruction=...) 给它派活；"
                    "要微调档案用 agx_assistant_profile(write=True)。"
                ),
            },
            indent=2,
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_assistant_create", e)}, indent=2)


@mcp.tool()
@require_level(0)
def agx_assistant_list(
    caller_agent_id: str,
    active_status: str = "1",
    limit: int = 50,
    offset: int = 0,
) -> str:
    """
    [List assistants] List every assistant under my name.

    Intent (decided by Lao Feng on 2026-10-09): "how many assistants do I (the principal) have" -- the most
    frequently asked question, deserving its own facade.

    * Difference from agx_my_sessions:
        agx_my_sessions is the [generic] session list (including main/task/oneshot and all others);
        this tool is [specialized] = only "assistants": forced filtering by caller + only the distinct marker
        (session_type IN ('assistant','case') AND is_assistant=1), so other sessions do not interfere.

        * 2026-10-21: the assistant type became its own value 'assistant' (it used to be 'case', mixed up with "items").

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        active_status (str): active status, default "1"=active only; ""=all; "0"=archived only.
        limit (int): items per page, default 50.
        offset (int): paging offset, default 0.

    Returns:
        str: JSON with agent_id / total / the assistants list (slug/name/session/last active).
    """
    try:
        # * changed 2026-10-21 (aifr_pm P1-5): the assistant type is independent of 'case', now 'assistant'.
        #   During the compatibility period both are accepted: legacy case_ assistant sessions without the backfill are still listed.
        #   Once the backfill is done only 'assistant' can remain.
        where = ["agent_id = %s",
                 "session_type IN ('assistant', 'case')",
                 "is_assistant = 1"]
        params = [caller_agent_id]
        if active_status != "":
            where.append("active_status = %s")
            params.append(int(active_status))
        where_sql = "WHERE " + " AND ".join(where)

        total_rows = execute_sql(f"SELECT COUNT(*) AS c FROM agx_session {where_sql}", tuple(params))
        total = int(total_rows[0]["c"]) if total_rows else 0

        rows = execute_sql(
            f"SELECT session_id, assistant_slug, session_name, avatar, run_count, last_active_at "
            f"FROM agx_session {where_sql} ORDER BY last_active_at DESC LIMIT %s OFFSET %s",
            tuple(params + [int(limit), int(offset)]),
        )
        assistants = [
            {
                "slug": r.get("assistant_slug"),
                "assistant_name": r.get("session_name"),
                "avatar": r.get("avatar"),
                "session_id": r.get("session_id"),
                "run_count": r.get("run_count"),
                "last_active_at": str(r.get("last_active_at")) if r.get("last_active_at") else None,
            }
            for r in (rows or [])
        ]
        return json.dumps(
            {"agent_id": caller_agent_id, "total": total, "assistants": assistants},
            indent=2,
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_assistant_list", e)}, indent=2)


@mcp.tool()
@require_level(3)
def agx_assistant_send(
    caller_agent_id: str,
    slug: str,
    instruction: str,
    needs_reply: int = 1,
    timeout_seconds: int = None,
    exec_name: str = "",
    exec_instruction: str = "",
) -> str:
    """
    [Message an assistant] Dispatch an instruction to the given assistant (addressed by slug).

    Intent (decided by Lao Feng on 2026-10-09):
        Dispatching to an assistant = agx_dispatch_task(agent_id=self, session_key=<that assistant's real session_id>).
        But the caller would have to know what session_key looks like -- that is the explanation cost of the generic
        method. This tool specializes the intent "dispatch to an assistant": give only the slug, session_key is
        resolved automatically.

    * This tool = a pure wrapper over agx_dispatch_task:
        agent_id is hard-coded to the caller itself (an assistant is an alter ego, no privilege crossing);
        session_key is resolved from the slug to [that assistant's real session] (DB lookup by assistant_slug,
        so the asst_/case_ prefix does not matter -- no more prefix concatenation).
        After dispatch an [assistant identity hint] is injected automatically (the assistant must re-read its identity).

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        slug (str): target assistant identifier (e.g. ui_design).
        instruction (str): the instruction content for the assistant.
        needs_reply (int): receipt requirement. 0-not needed 1-reply needed (default 1).
        timeout_seconds (int): watchdog timeout (seconds); follows the default when omitted.
        exec_name (str): short execution title (Chinese, optional).
        exec_instruction (str): full execution instruction body (optional; when passed it overrides the template merge).

    Returns:
        str: the result of agx_dispatch_task (including run_uuid).
    """
    try:
        session_id = _resolve_assistant_session(caller_agent_id, slug)
        if not session_id:
            return json.dumps({"error": "invalid slug (allowed: [a-z0-9_-])"}, indent=2, ensure_ascii=False)

        # validate that the assistant exists (under the caller's name + is a case assistant)
        _row = execute_sql(
            "SELECT session_id, session_name, is_assistant, active_status "
            "FROM agx_session WHERE session_id = %s LIMIT 1",
            (session_id,),
        )
        if not _row:
            return json.dumps(
                {"error": f"助理 '{slug}' 不存在(session={session_id}); 请先 agx_assistant_create 建它"},
                indent=2,
                ensure_ascii=False,
            )
        if not _row[0].get("is_assistant"):
            return json.dumps(
                {"error": f"会话 '{session_id}' 不是助理(未标 is_assistant); 请用 agx_assistant_create(slug=...) 建助理"},
                indent=2,
                ensure_ascii=False,
            )

        _kw = dict(
            caller_agent_id=caller_agent_id,
            instruction=instruction,
            agent_id=caller_agent_id,   # an assistant is an alter ego: dispatch to self
            session_key=session_id,     # the deterministically resolved assistant session
            needs_reply=needs_reply,
        )
        if timeout_seconds is not None:
            _kw["timeout_seconds"] = timeout_seconds
        if exec_name:
            _kw["exec_name"] = exec_name
        if exec_instruction:
            _kw["exec_instruction"] = exec_instruction

        return agx_dispatch_task(**_kw)
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_assistant_send", e)}, indent=2)


@mcp.tool()
@require_level(3)
def agx_link_execution(
    caller_agent_id: str,
    execution_id: str,
    case_session_id: str,
    link_role: str = "reference",
    reason: str = "",
) -> str:
    """
    [Link execution] Reference an original execution into a target session (a case item).

    Intent (confirmed with Lao Feng on 2026-10-05):
        What is linked is a [pointer], not a move -- the original execution row is untouched (session_id/summary are
        never modified); only a reference edge is added. So one execution can be referenced by several sessions
        (multiplexing): "the artifact of one job is needed by two later threads" -- both cases can read it.

    Granularity = execution level (not session level): precise down to "which run", and it can be cherry-picked.

    Idempotent: linking the same (execution_id, case_session_id) again only refreshes role/reason, no error.

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        execution_id (str): the referenced original execution's task_run_uuid (e.g. "run_xxx"). Required.
        case_session_id (str): the target session referencing it (e.g. "agent:{aid}:case_{slug}"). Required.
        link_role (str): reference role, enum reference/source/merged, default reference.
        reason (str): why it is linked (categorization/reuse rationale, optional).

    Returns:
        str: JSON with execution_id / case_session_id / link_role / created (whether a new edge was created).
    """
    try:
        eid = (execution_id or "").strip()
        tid = (case_session_id or "").strip()
        if not eid or not tid:
            return json.dumps({"error": "execution_id and case_session_id are required"}, indent=2, ensure_ascii=False)

        role = (link_role or "reference").strip()
        if role not in ("reference", "source", "merged"):
            return json.dumps({"error": f"invalid link_role '{role}' (allowed: reference/source/merged)"}, indent=2, ensure_ascii=False)

        # validate that the original execution exists (it is not modified, only validated for traceability)
        ex = execute_sql("SELECT task_run_uuid FROM agx_task_execution WHERE task_run_uuid = %s", (eid,))
        if not ex:
            return json.dumps({"error": f"execution '{eid}' not found"}, indent=2, ensure_ascii=False)

        # validate that the target session exists
        sess = execute_sql("SELECT session_id FROM agx_session WHERE session_id = %s", (tid,))
        if not sess:
            return json.dumps({"error": f"target session '{tid}' not found"}, indent=2, ensure_ascii=False)

        existing = execute_sql(
            "SELECT pkid FROM agx_execution_link WHERE execution_id = %s AND case_session_id = %s",
            (eid, tid),
        )
        created = not existing
        if created:
            execute_sql(
                "INSERT INTO agx_execution_link "
                "(execution_id, case_session_id, link_role, linked_by, reason, active_status) "
                "VALUES (%s, %s, %s, %s, %s, 1)",
                (eid, tid, role, caller_agent_id, _sanitize_name_field(reason, 512) or ""),
                commit=True,
            )
        else:
            execute_sql(
                "UPDATE agx_execution_link SET link_role = %s, reason = %s, active_status = 1 "
                "WHERE execution_id = %s AND case_session_id = %s",
                (role, _sanitize_name_field(reason, 512) or "", eid, tid),
                commit=True,
            )

        return json.dumps(
            {
                "execution_id": eid,
                "case_session_id": tid,
                "link_role": role,
                "created": created,
                "note": "原始 execution 未被修改(只加引用边), 可被多个 session 复用",
            },
            indent=2,
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_link_execution", e)}, indent=2)


@mcp.tool()
@require_level(0)
def agx_list_execution_links(
    caller_agent_id: str,
    case_session_id: str = "",
    execution_id: str = "",
    active_status: str = "1",
    limit: int = 100,
) -> str:
    """
    [Query links] Query the link edges between executions and sessions (read-only).

    Two directions:
        - pass case_session_id -> see "which historical executions this long-lived session aggregates"
        - pass execution_id    -> see "which sessions referenced this execution" (who used me)

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        case_session_id (str): filter by target session (optional).
        execution_id (str): filter by referenced execution (optional).
        active_status (str): active status, default "1"; "" queries all.
        limit (int): result cap, default 100.
    """
    try:
        where = []
        params = []
        if case_session_id.strip():
            where.append("case_session_id = %s")
            params.append(case_session_id.strip())
        if execution_id.strip():
            where.append("execution_id = %s")
            params.append(execution_id.strip())
        if active_status != "":
            where.append("active_status = %s")
            params.append(int(active_status))
        ws = ("WHERE " + " AND ".join(where)) if where else ""
        params.append(int(limit))
        rows = execute_sql(
            f"SELECT pkid, execution_id, case_session_id, link_role, linked_at, linked_by, reason, active_status "
            f"FROM agx_execution_link {ws} ORDER BY linked_at DESC LIMIT %s",
            tuple(params),
        )
        return json.dumps({"total": len(rows), "links": rows}, indent=2, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_list_execution_links", e)}, indent=2)


# -- skill sharing (agx_skill) --
# design (confirmed with Lao Feng on 2026-10-05/2026-10-06, requirement doc docs/agent_docs/2026-10-05-skill-sharing-table-requirements.md):
#   a skill = reusable, actionable "how to"; "what I did" logs always go into notices (agx_agent_notice).
#   open writes (any agent can contribute), no manual review, use_count/helpful_count let the signal float the good ones up.


def _is_skill_like_notice(content: str) -> bool:
    """Lightweight heuristic: decide whether the content reads like a "work log" rather than a "reusable skill" (a soft reminder for discipline 1, not a hard block).

    A skill should carry "followable" traits (commands/code/steps/parameters); if it is entirely first-person
    narrative about "what I did today" with no operational trace, emit a warning about the boundary but do not
    reject it (open writes; the boundary relies on self-discipline plus reminders).
    """
    c = content or ""
    if not c.strip():
        return True
    operational = ("`" in c) or ("```" in c) or ("$" in c) or ("def " in c) or ("--" in c)
    if operational:
        return False
    first_person = ("我今天" in c) or ("我完成了" in c) or ("我做了" in c)
    return bool(first_person)


def _skill_desc_fallback(content: str, limit: int = 200) -> str:
    """Fallback when description is missing: extract one sentence from the first line/first heading of the body.

    Background (2026-10-07): description is the first piece of information on the skill list page; when empty, an
    entry "does not show why it is worth clicking". But the low-friction discipline makes description optional ->
    when a contributor only pastes the body it silently stays empty (in fact a design flaw).
    Root fix: when empty, extract the first non-empty, non-heading line from content.
    """
    in_fence = False
    for raw in (content or "").splitlines():
        line = raw.strip()
        # code fence open/close toggle; content inside a fence is never considered
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not line:
            continue
        # strip markdown heading/quote/list markers, keeping only the text
        text = line.lstrip("#>*-` \t").strip()
        if not text:
            continue
        return text[:limit]
    return ""


@mcp.tool()
@require_level(1)
def agx_skill_contribute(
    caller_agent_id: str,
    skill_name: str,
    content: str,
    category: str = "other",
    description: str = "",
    tags: str = "",
    skill_id: str = "",
    appkey: str = "",
) -> str:
    """
    [Contribute skill] Distill a reusable skill you worked out into the shared library (public to everyone).

    * Boundary discipline (hard line against the notice table agx_agent_notice):
        A skill = reusable, actionable "how to" (commands/usage/pitfalls/examples).
        "What I did" work logs always go into notices, never here.
        Criterion: if reading it lets you do the thing -> skill library; if reading it only tells you "who did what" -> notice.

    Design: open writes (any agent can contribute), no manual review -- good skills float up naturally through
    use_count/helpful_count signals. Low friction first: only skill_name + content are required.

    Idempotent: passing the same skill_name (without a skill_id) updates the existing skill (same skill recurring ->
    refresh content), otherwise creates a new one. An explicit skill_id updates/creates by that ID.

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit).
        skill_name (str): skill name (required, e.g. "playwright headless browser").
        content (str): skill body (required): commands/usage/pitfalls/examples, followable.
        category (str): category (e.g. browser/github/huggingface/search/cli/mcp/other), default other.
        description (str): one-line explanation (what problem this skill solves).
        tags (str): tags (comma-separated free text).
        skill_id (str): optional; when given, update that skill; otherwise match by skill_name or create.
        appkey (str): optional. The appkey this skill's server/service needs (stored in plaintext;
            quick availability first, no masking). * Semantics: **written/overwritten only when non-empty**;
            omitting it or passing an empty string **keeps the existing value unchanged** (avoids accidental clearing).

    Returns:
        str: JSON with skill_id / created (created or updated) / warning (optional boundary reminder).
    """
    try:
        name = _sanitize_name_field(skill_name, 256)
        if not name or not name.strip():
            return json.dumps({"error": "skill_name 不能为空"}, indent=2, ensure_ascii=False)
        body = (content or "").strip()
        if not body:
            return json.dumps({"error": "content 不能为空"}, indent=2, ensure_ascii=False)

        cat = (category or "other").strip() or "other"
        desc = _sanitize_name_field(description, 1024) or ""
        tg = _sanitize_name_field(tags, 512) or ""
        ak = _sanitize_name_field(appkey, 128) or ""
        network_id = config.get("network_id", "") or ""

        # description fallback (2026-10-07): in the list, description is the one-line explanation and the first information;
        # when empty the entry "does not show why it is worth clicking". Low friction makes it optional -> when empty, extract it from the first body line automatically.
        desc_fallback_used = False
        if not desc:
            desc = _skill_desc_fallback(body, 200)
            if desc:
                desc_fallback_used = True

        # locate an existing skill: an explicit skill_id wins, otherwise match idempotently by skill_name + contributor
        existing = None
        sid = (skill_id or "").strip()
        if sid:
            rows = execute_sql("SELECT skill_id FROM agx_skill WHERE skill_id = %s", (sid,))
            existing = rows[0]["skill_id"] if rows else None
        else:
            rows = execute_sql(
                "SELECT skill_id FROM agx_skill WHERE skill_name = %s AND source_agent_id = %s AND active_status = 1 "
                "ORDER BY updated_at DESC LIMIT 1",
                (name, caller_agent_id),
            )
            existing = rows[0]["skill_id"] if rows else None

        warning = None
        if _is_skill_like_notice(body):
            warning = (
                "内容疑似工作流水/缺少可照做的操作细节。技能库只收可复用的'怎么做'; "
                "工作流水请走 agx_write_notice。"
            )
        if existing:
            sid = existing
            # appkey semantics: written/overwritten only when non-empty; omitted or empty -> keep the existing value unchanged (avoids accidental clearing).
            if ak:
                execute_sql(
                    "UPDATE agx_skill SET skill_name=%s, category=%s, description=%s, content=%s, tags=%s, "
                    "appkey=%s, source_agent_id=%s, active_status=1 WHERE skill_id=%s",
                    (name, cat, desc, body, tg, ak, caller_agent_id, sid),
                    commit=True,
                )
            else:
                execute_sql(
                    "UPDATE agx_skill SET skill_name=%s, category=%s, description=%s, content=%s, tags=%s, "
                    "source_agent_id=%s, active_status=1 WHERE skill_id=%s",
                    (name, cat, desc, body, tg, caller_agent_id, sid),
                    commit=True,
                )
            created = False
        else:
            sid = (skill_id or "").strip() or agx_generate_uuid(prefix="skill")
            execute_sql(
                "INSERT INTO agx_skill "
                "(skill_id, network_id, skill_name, category, description, content, tags, source_agent_id, appkey) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (sid, network_id, name, cat, desc, body, tg, caller_agent_id, ak),
                commit=True,
            )
            created = True

        # sync the Redis cache (content holds only a digest; the full body falls back to MySQL)
        try:
            row = execute_sql(
                "SELECT pkid, skill_id, network_id, skill_name, category, description, tags, "
                "source_agent_id, use_count, helpful_count, active_status, created_at, updated_at, appkey "
                "FROM agx_skill WHERE skill_id = %s",
                (sid,),
            )
            if row:
                data = dict(row[0])
                data["content"] = body[:200]
                _sync_entity("skill", sid, data)
                r = get_redis_client()
                r.sadd(f"agx:idx:skill:category:{cat}", sid)
                r.sadd(f"agx:idx:skill:source:{caller_agent_id}", sid)
        except Exception as exc:
            log(f"agx_skill_contribute Redis sync failed for {sid}: {exc}", "WARN")

        payload = {
            "status": "success",
            "skill_id": sid,
            "created": created,
            "message": ("技能已创建" if created else "技能已更新") + f": {sid}",
        }
        if desc_fallback_used:
            payload["description_fallback"] = (
                "未提供 description, 已从正文首行自动兜底: " + desc
            )
        if warning:
            payload["warning"] = warning
        return json.dumps(payload, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_skill_contribute", e)}, indent=2)


@mcp.tool()
@require_level(0)
def agx_skill_query(
    caller_agent_id: str,
    keyword: str = "",
    category: str = "",
    tags: str = "",
    source_agent_id: str = "",
    active_status: str = "1",
    limit: int = 50,
    offset: int = 0,
) -> str:
    """
    [Search skills] Search the shared skill library by keyword/category/tag/contributor (public to everyone).

    The skill library is public to all, no secrets -- deposited once, shared everywhere, no repeated exploration.
    Sorted by [weighted heat score] descending by default (signal first + freshness bonus), so both good skills and
    new skills can surface.

    * Ranking formula (changed 2026-10-13, replacing the old three-column ordering helpful/use/updated):
        score = helpful_count * 5                       -- peer recognition, highest weight
              + use_count                               -- accumulated actual use
              + GREATEST(0, 30 - DATEDIFF(NOW(), updated_at)) * 0.3   -- freshness bonus (linear decay over 30 days)
      Motivation: in the old ordering updated_at was the third level and was blocked by use_count -> new skills
      (use_count naturally 0) always sank to the bottom, while old skills dominated through accumulated volume (the
      Matthew effect). With a weighted score: a freshly landed, recognized new skill (5 + 0 + ~9 = 14) can immediately
      outrank a purely accumulated old one; an old skill that is still used still keeps a floor, so it does not "die
      on expiry"; a zombie skill (long unupdated, unrecognized, rarely used) sinks naturally.

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit).
        keyword (str): keyword (fuzzy match in skill_name/description/content/tags).
        category (str): category filter (e.g. browser/github/search...).
        tags (str): tag filter (fuzzy match on the tags field).
        source_agent_id (str): filter by contributor.
        active_status (str): active status, default "1"; ""=all; "0"=archived only.
        limit (int): items per page, default 50.
        offset (int): paging offset, default 0.

    Returns:
        str: JSON with total + the skills list (each with a content digest; use agx_skill_get for the full body).
    """
    try:
        conditions = []
        params = []
        if keyword:
            conditions.append("(skill_name LIKE %s OR description LIKE %s OR content LIKE %s OR tags LIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw, kw])
        if category:
            conditions.append("category = %s")
            params.append(category)
        if tags:
            conditions.append("tags LIKE %s")
            params.append(f"%{tags}%")
        if source_agent_id:
            conditions.append("source_agent_id = %s")
            params.append(source_agent_id)
        if active_status != "":
            conditions.append("active_status = %s")
            params.append(int(active_status))
        where = " AND ".join(conditions) if conditions else "1=1"

        count_rows = execute_sql(f"SELECT COUNT(*) as total FROM agx_skill WHERE {where}", tuple(params))
        total = count_rows[0]["total"] if count_rows else 0

        rows = execute_sql(
            f"SELECT skill_id, network_id, skill_name, category, description, tags, source_agent_id, "
            f"use_count, helpful_count, active_status, created_at, updated_at, appkey, LEFT(content, 200) as summary "
            f"FROM agx_skill WHERE {where} "
            f"ORDER BY (helpful_count * 5 + use_count "
            f"+ GREATEST(0, 30 - DATEDIFF(NOW(), updated_at)) * 0.3) DESC, "
            f"updated_at DESC LIMIT %s OFFSET %s",
            tuple(params + [limit, offset]),
        )
        skills = []
        for row in rows:
            r = dict(row)
            for k in ("created_at", "updated_at"):
                if r.get(k):
                    r[k] = r[k].strftime("%Y-%m-%d %H:%M:%S")
            skills.append(r)
        return json.dumps(
            {"total": total, "skills": skills, "limit": limit, "offset": offset},
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_skill_query", e)}, indent=2)


@mcp.tool()
@require_level(0)
def agx_skill_get(caller_agent_id: str, skill_id: str = "", skill_name: str = "") -> str:
    """
    [Read skill] Get the full body of one skill (commands/usage/pitfalls/examples).

    Reads the Redis cache first, falls back to MySQL on miss. Reading automatically increments use_count (a usage signal).
    The skill library is public to everyone -- any agent may read it, there are no secrets.

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit).
        skill_id (str): skill ID (e.g. skill_xxx). Use either this or skill_name.
        skill_name (str): skill name (takes the latest row by that name); used when skill_id is empty.

    Returns:
        str: JSON with the skill's full fields (including the full content).
    """
    try:
        sid = (skill_id or "").strip()
        if not sid:
            nm = (skill_name or "").strip()
            if not nm:
                return json.dumps({"error": "skill_id 与 skill_name 至少提供一个"}, indent=2, ensure_ascii=False)
            rows = execute_sql(
                "SELECT skill_id FROM agx_skill WHERE skill_name = %s AND active_status = 1 "
                "ORDER BY helpful_count DESC, updated_at DESC LIMIT 1",
                (nm,),
            )
            if not rows:
                return json.dumps({"error": f"skill not found by name: {nm}"}, indent=2, ensure_ascii=False)
            sid = rows[0]["skill_id"]

        rows = execute_sql("SELECT * FROM agx_skill WHERE skill_id = %s", (sid,))
        if not rows:
            return json.dumps({"error": f"skill {sid} not found"}, indent=2, ensure_ascii=False)

        # usage signal: increment use_count (best effort, a failure does not affect the return)
        try:
            execute_sql("UPDATE agx_skill SET use_count = use_count + 1 WHERE skill_id = %s", (sid,), commit=True)
            get_redis_client().hincrby(f"{CACHE_PREFIX['skill']}{sid}", "use_count", 1)
        except Exception as exc:
            log(f"agx_skill_get use_count incr failed for {sid}: {exc}", "WARN")

        data = dict(rows[0])
        data["use_count"] = int(data.get("use_count") or 0) + 1
        for k in ("created_at", "updated_at"):
            if data.get(k):
                data[k] = data[k].strftime("%Y-%m-%d %H:%M:%S")
        return json.dumps(data, indent=2, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_skill_get", e)}, indent=2)


@mcp.tool()
@require_level(1)
def agx_skill_feedback(
    caller_agent_id: str,
    skill_id: str,
    helpful: int = 1,
) -> str:
    """
    [Skill feedback] Cast a "useful" vote on a skill so the signal ranking lifts good skills up (discipline 2: filter by signal, no manual review).

    There is no manual review flow -- open writes + usage/recognition signals, letting the mechanism filter instead of
    people. Any agent may give feedback (self-promotion is softly averted: a contributor may give feedback on its own
    skill, but repeated votes from the same agent are simply accumulated by the implementation, with no dedup table --
    good enough for the MVP stage).

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit).
        skill_id (str): skill ID (required).
        helpful (int): 1=useful (default) / -1=not useful (decrement, never below 0).

    Returns:
        str: JSON with skill_id / helpful_count.
    """
    try:
        sid = (skill_id or "").strip()
        if not sid:
            return json.dumps({"error": "skill_id 不能为空"}, indent=2, ensure_ascii=False)
        rows = execute_sql("SELECT skill_id, helpful_count FROM agx_skill WHERE skill_id = %s", (sid,))
        if not rows:
            return json.dumps({"error": f"skill {sid} not found"}, indent=2, ensure_ascii=False)

        delta = 1 if int(helpful) >= 0 else -1
        # avoid going negative on decrement
        if delta < 0:
            execute_sql(
                "UPDATE agx_skill SET helpful_count = GREATEST(helpful_count - 1, 0) WHERE skill_id = %s",
                (sid,),
                commit=True,
            )
        else:
            execute_sql(
                "UPDATE agx_skill SET helpful_count = helpful_count + 1 WHERE skill_id = %s",
                (sid,),
                commit=True,
            )
        new_rows = execute_sql("SELECT helpful_count FROM agx_skill WHERE skill_id = %s", (sid,))
        hc = new_rows[0]["helpful_count"] if new_rows else 0
        try:
            r = get_redis_client()
            r.hset(f"{CACHE_PREFIX['skill']}{sid}", "helpful_count", str(hc))
        except Exception as exc:
            log(f"agx_skill_feedback Redis sync failed for {sid}: {exc}", "WARN")
        return json.dumps(
            {"status": "success", "skill_id": sid, "helpful_count": int(hc), "delta": delta},
            indent=2,
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_skill_feedback", e)}, indent=2)


# -- skill archive / delete (* approved by Lao Feng on 2026-11-19: fill in soft and hard delete for agx_skill) --
#   Gap: previously agx_skill only had contribute / query / get / feedback.
#         Disabling a skill required raw SQL -> bypassing _sync_entity / _remove_from_indexes,
#         leaving phantoms in the hash and in the three indexes skill:all / category / source.
#   Pattern copied from agx_archive_task / agx_delete_task: soft delete first, hard delete separate and guarded.


def _skill_perm_check(caller_agent_id: str, row: dict):
    """Ownership decision for a skill -- aligned with the tiers of agx_set_agent_skills, nothing invented.

    The skill table **has no project_id**, so tier (2) PM does not apply (there is no "own project" to go by).
    Only two tiers remain:
      (1) resource ownership: caller == source_agent_id
      (3) super role: agent_manager / workshop_manager

    Returns:
        (allowed, is_super, reason)
    """
    caller = (caller_agent_id or "").strip()
    role = _lookup_agent_role(caller) or "unknown"
    is_super = role in ("agent_manager", "workshop_manager")
    owner = (row.get("source_agent_id") or "").strip()
    if is_super:
        return True, True, f"global_role({role})"
    if owner and caller == owner:
        return True, False, "owner"
    return False, False, f"caller={caller}(role={role}) owner={owner or '空'}"


@mcp.tool()
@require_level(1)
def agx_skill_archive(caller_agent_id: str, skill_id: str) -> str:
    """
    [Archive skill] Set the given skill to active_status=0 (soft delete, recoverable).

    * Difference from agx_skill_delete (prefer this tool):
        - archive: soft delete, keeps the row and all history (use_count / helpful_count untouched), only removes it from active lists
        - delete : physical delete, unrecoverable

    * Why the indexes must be cleared: a skill has 4 derived views -- the entity hash,
      agx:idx:skill:all, agx:idx:skill:category:{cat}, agx:idx:skill:source:{aid}.
      Only UPDATE-ing the DB leaves them stale -> skill:all keeps listing a disabled skill and audit reports an inconsistency.
      So: first _sync_entity to flush the hash to active_status=0 (hash stays complete, no stale value),
          then _remove_from_indexes to drop it from the three indexes.

    * Permission (aligned with agx_set_agent_skills; skills have no project ownership, so only two tiers):
        (1) resource ownership: caller == source_agent_id
        (3) super role: agent_manager / workshop_manager

    Idempotent: archiving an already-archived skill -> success + "already disabled".

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        skill_id (str): skill ID (required, e.g. skill_xxx).

    Returns:
        str: JSON {status, skill_id, action, message, permission}
    """
    try:
        sid = (skill_id or "").strip()
        if not sid:
            return json.dumps({"status": "error", "message": "skill_id 不能为空"}, indent=2, ensure_ascii=False)

        rows = execute_sql("SELECT * FROM agx_skill WHERE skill_id = %s", (sid,))
        if not rows:
            return json.dumps(
                {"status": "error", "message": f"skill '{sid}' not found"}, indent=2, ensure_ascii=False
            )
        row = dict(rows[0])

        allowed, _is_super, reason = _skill_perm_check(caller_agent_id, row)
        if not allowed:
            return json.dumps(
                {
                    "status": "denied",
                    "message": (
                        f"权限不足：{reason}。仅 [技能贡献者本人 / agent_manager / workshop_manager] "
                        f"可归档技能。"
                    ),
                    "skill_id": sid,
                    "source_agent_id": row.get("source_agent_id"),
                },
                indent=2,
                ensure_ascii=False,
            )

        if int(row.get("active_status") or 0) == 0:
            return json.dumps(
                {
                    "status": "success",
                    "skill_id": sid,
                    "action": "noop",
                    "permission": reason,
                    "message": "已是停用状态(active_status=0)，无需重复归档。",
                },
                indent=2,
                ensure_ascii=False,
            )

        execute_sql(
            "UPDATE agx_skill SET active_status = 0, updated_at = NOW() WHERE skill_id = %s",
            (sid,),
            commit=True,
        )

        # -- cache: refresh the hash first (keep fields complete, content keeps only a digest, same rule as contribute) --
        try:
            _data = {
                k: row.get(k)
                for k in (
                    "pkid", "skill_id", "network_id", "skill_name", "category", "description",
                    "tags", "source_agent_id", "use_count", "helpful_count",
                    "created_at", "updated_at", "appkey",
                )
                if k in row
            }
            _data["content"] = (row.get("content") or "")[:200]
            _data["active_status"] = "0"
            _sync_entity("skill", sid, _data)
        except Exception as exc:
            log(f"agx_skill_archive _sync_entity({sid}) failed: {exc}", "WARN")

        # -- indexes: drop it from skill:all / category / source (_sync_entity zadd's it back) --
        _remove_from_indexes("skill", sid, row)

        return json.dumps(
            {
                "status": "success",
                "skill_id": sid,
                "action": "archived",
                "permission": reason,
                "message": f"技能已归档(active_status=0): {sid}",
            },
            indent=2,
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_skill_archive", e)}, indent=2)


@mcp.tool()
@require_level(1)
def agx_skill_delete(caller_agent_id: str, skill_id: str) -> str:
    """
    [Physically delete skill] Delete a skill from agx_skill for good (unrecoverable).

    !! Prefer agx_skill_archive (soft delete); this tool is only for cleaning genuinely dirty data.

    * Permission: same as agx_skill_archive (the contributor itself / agent_manager / workshop_manager).

    * Extra protection (decided by Lao Feng on 2026-11-19):
        If the skill a non-global role wants to delete already has use_count>0 or helpful_count>0
        (= someone used it / someone recognized it), it is always refused with a hint to use archive.
        Reason: deleting something others are already benefiting from has irreversible impact; archive is reversible and
        keeps all history.
        * This protection **applies to the contributor itself too** -- "I wrote it" does not mean "deleting it harms nobody".

    * Cleanup: after deleting the DB row, call _invalidate_entity (delete the entity hash + clear all index memberships),
      the same approach as agx_delete_task.

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        skill_id (str): skill ID (required, e.g. skill_xxx).

    Returns:
        str: JSON {status, skill_id, action, message, permission}
    """
    try:
        sid = (skill_id or "").strip()
        if not sid:
            return json.dumps({"status": "error", "message": "skill_id 不能为空"}, indent=2, ensure_ascii=False)

        rows = execute_sql("SELECT * FROM agx_skill WHERE skill_id = %s", (sid,))
        if not rows:
            return json.dumps(
                {"status": "error", "message": f"skill '{sid}' not found"}, indent=2, ensure_ascii=False
            )
        row = dict(rows[0])

        allowed, is_super, reason = _skill_perm_check(caller_agent_id, row)
        if not allowed:
            return json.dumps(
                {
                    "status": "denied",
                    "message": (
                        f"权限不足：{reason}。仅 [技能贡献者本人 / agent_manager / workshop_manager] "
                        f"可删除技能。"
                    ),
                    "skill_id": sid,
                    "source_agent_id": row.get("source_agent_id"),
                },
                indent=2,
                ensure_ascii=False,
            )

        use_count = int(row.get("use_count") or 0)
        helpful_count = int(row.get("helpful_count") or 0)
        if not is_super and (use_count > 0 or helpful_count > 0):
            return json.dumps(
                {
                    "status": "refused",
                    "skill_id": sid,
                    "use_count": use_count,
                    "helpful_count": helpful_count,
                    "message": (
                        f"拒绝物理删除：该技能已有 use_count={use_count} / helpful_count={helpful_count}，"
                        f"说明有人取用过或认可过，删除不可逆。"
                        f"请改用 agx_skill_archive 归档（可恢复、保留全部历史）。"
                        f"确需硬删请联系 agent_manager / workshop_manager。"
                    ),
                },
                indent=2,
                ensure_ascii=False,
            )

        execute_sql("DELETE FROM agx_skill WHERE skill_id = %s", (sid,), commit=True)
        try:
            _invalidate_entity("skill", sid, row)
        except Exception as exc:
            log(f"agx_skill_delete _invalidate_entity({sid}) failed: {exc}", "WARN")

        return json.dumps(
            {
                "status": "success",
                "skill_id": sid,
                "action": "deleted",
                "permission": reason,
                "message": f"技能已物理删除: {sid}",
            },
            indent=2,
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_skill_delete", e)}, indent=2)


@mcp.tool()
@require_level(0)
def agx_list_sessions(
    caller_agent_id: str,
    agent_id: str = "",
    project_id: str = "",
    session_type: str = "",
    active_status: str = "1",
    keyword: str = "",
    limit: int = 50,
    offset: int = 0,
) -> str:
    """
    [List sessions] Query the session list; filter by agent / project / session type / active status / keyword.

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        agent_id (str): filter by owning agent (optional).
        project_id (str): filter by project (optional).
        session_type (str): filter by session type (optional): main / task / oneshot. Empty = unlimited.
        active_status (str): active-status filter, default "1"=active only; ""=all; "0"=archived only.
        keyword (str): fuzzy search on session name/note (optional).
        limit (int): items per page, default 50.
        offset (int): paging offset, default 0.

    Returns:
        str: JSON with total + the sessions list.
    """
    try:
        where = []
        params = []
        if agent_id:
            where.append("agent_id = %s")
            params.append(agent_id)
        if project_id:
            where.append("project_id = %s")
            params.append(project_id)
        if session_type:
            where.append("session_type = %s")
            params.append(session_type)
        if active_status != "":
            where.append("active_status = %s")
            params.append(int(active_status))
        if keyword:
            where.append("(session_name LIKE %s OR x_desc LIKE %s OR session_id LIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])

        # read-side visibility scope (shadow mode defaults to off -> behavior unchanged)
        _mode = _read_scope_mode()
        if _mode != "off" and caller_agent_id:
            _scope = _read_scope_of(caller_agent_id)
            if _scope == "self":
                if _mode == "log":
                    log(f"[READ-SCOPE][log] agx_list_sessions caller={caller_agent_id} scope=self would-restrict to self", "WARN")
                else:
                    where.append("agent_id = %s")
                    params.append(caller_agent_id)
            elif _scope == "project":
                if _mode == "log":
                    log(f"[READ-SCOPE][log] agx_list_sessions caller={caller_agent_id} scope=project would-restrict to own-project members", "WARN")
                else:
                    where.append("agent_id IN (SELECT ar.agent_id FROM agx_agent_registry ar JOIN agx_project p ON ar.project_id = p.project_id WHERE p.admin_agent_id = %s)")
                    params.append(caller_agent_id)

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""

        total_rows = execute_sql(f"SELECT COUNT(*) AS c FROM agx_session {where_sql}", tuple(params))
        total = total_rows[0]["c"] if total_rows else 0

        rows = execute_sql(
            f"SELECT session_id, session_name, session_type, is_assistant, assistant_slug, agent_id, project_id, task_labs, "
            f"run_count, last_run_uuid, last_active_at, active_status, created_at "
            f"FROM agx_session {where_sql} "
            f"ORDER BY last_active_at DESC, pkid DESC LIMIT %s OFFSET %s",
            tuple(params) + (int(limit), int(offset)),
        )
        return json.dumps(
            {"total": total, "limit": limit, "offset": offset, "sessions": rows or []},
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_list_sessions", e)}, indent=2)


@mcp.tool()
@require_level(0)
def agx_my_sessions(
    caller_agent_id: str,
    task_labs: str = "",
    session_type: str = "",
    keyword: str = "",
    active_status: str = "1",
    limit: int = 50,
    offset: int = 0,
) -> str:
    """
    [My sessions] Query the session list under the calling agent itself.

    Difference from agx_list_sessions: this tool **forces filtering by caller** and neither needs (nor allows)
    an agent_id, so it never sees anyone else's sessions. Used by an agent to check "which sessions do I have,
    what is each one for, which was active most recently", and to reuse a session via the session_key parameter.

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        task_labs (str): filter by task label (optional), e.g. "universal" / "general" / "plan_execute".
        session_type (str): filter by session type (optional): main / task / oneshot. Empty = unlimited.
        keyword (str): fuzzy search on session name/note/ID (optional).
        active_status (str): active-status filter, default "1"=active only; ""=all; "0"=archived only.
        limit (int): items per page, default 50.
        offset (int): paging offset, default 0.

    Returns:
        str: JSON with agent_id / total / the sessions list (most recently active first).
    """
    try:
        where = ["agent_id = %s"]
        params = [caller_agent_id]
        if task_labs:
            where.append("task_labs = %s")
            params.append(task_labs)
        if session_type:
            where.append("session_type = %s")
            params.append(session_type)
        if active_status != "":
            where.append("active_status = %s")
            params.append(int(active_status))
        if keyword:
            where.append("(session_name LIKE %s OR x_desc LIKE %s OR session_id LIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])
        where_sql = "WHERE " + " AND ".join(where)

        total_rows = execute_sql(f"SELECT COUNT(*) AS c FROM agx_session {where_sql}", tuple(params))
        total = total_rows[0]["c"] if total_rows else 0

        rows = execute_sql(
            f"SELECT session_id, session_name, session_type, x_desc, project_id, task_labs, "
            f"is_assistant, assistant_slug, "
            f"run_count, last_run_uuid, last_active_at, active_status, created_at "
            f"FROM agx_session {where_sql} "
            f"ORDER BY last_active_at DESC, pkid DESC LIMIT %s OFFSET %s",
            tuple(params) + (int(limit), int(offset)),
        )
        return json.dumps(
            {
                "agent_id": caller_agent_id,
                "total": total,
                "limit": limit,
                "offset": offset,
                "sessions": rows or [],
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_my_sessions", e)}, indent=2)


@mcp.tool()
@require_level(3)
def agx_note_main_session(
    caller_agent_id: str,
    instruction: str = "",
    timeout_seconds: int = None,
    exec_name: str = "",
) -> str:
    """
    [Note in the main session] Deliver a "take notes" execution instance into the calling agent's own main session.

    Intent (confirmed with Lao Feng on 2026-09-14):
        The main session (agent:{aid}:main) is the conversation channel between the PM and the human partner, and also
        where important decisions are made, but it has no execution boundary by nature -- its content is a continuous
        conversation stream that cannot enter the "execution.summary -> log aggregation" pipeline.
        This tool turns "take notes" into an execution using the general task:
        it sends an execution into its own main session whose task manual is "record the current main session content";
        the executor writes "what happened in the main session just now" into execution.summary.

    Semantics: **snapshot-style recording** (not retroactive backfill)
        - when it is sent is when the main session content is captured
        - send often -> fine-grained snapshots; send rarely -> coarse-grained snapshots
        - at the end of the day the aggregation (log) task consolidates all snapshots into prose

    Constraints:
        - can only send to yourself (no specifying others), to avoid writing into someone else's main session
        - always lands in the caller's main session: session_key = agent:{caller_agent_id}:main
        - does not carry the task template manual (to avoid polluting the main session context)

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        instruction (str, optional): supplementary note (e.g. "focus on the plan just decided").
            Uses the default note instruction when omitted.
        timeout_seconds (int, optional): timeout in seconds; follows config.task_timeout by default.
        exec_name (str, optional): short execution title (Chinese), default "note in the main session".

    Returns:
        str: JSON with run_uuid / msg_id / queue / session_key.
    """
    agent_id = caller_agent_id
    try:
        # -- 1. validate the agent's deploy state + owning host --
        agent_row = execute_sql(
            "SELECT host_id, deploy_status, default_session_id FROM agx_agent_registry "
            "WHERE agent_id = %s AND active_status = 1",
            (agent_id,),
        )
        if not agent_row:
            return json.dumps({"error": f"agent '{agent_id}' 不存在或已停用"}, indent=2)
        if agent_row[0].get("deploy_status") != "deployed":
            return json.dumps(
                _deploy_gate_msg(agent_id, agent_row[0].get("deploy_status")),
                indent=2,
            )
        host_id = agent_row[0].get("host_id", "")
        if not host_id:
            return json.dumps({"error": f"cannot determine host_id for agent '{agent_id}'"}, indent=2)

        # -- 2. find the general task (every agent's default task template) --
        tasks = execute_sql(
            "SELECT task_id, network_id FROM agx_task_base "
            "WHERE agent_id = %s AND task_labs = 'general' AND active_status = 1 LIMIT 1",
            (agent_id,),
        )
        if not tasks:
            return json.dumps(
                {"error": f"no default task (task_labs='general') found for agent '{agent_id}'"}, indent=2
            )
        task_id = tasks[0]["task_id"]
        network_id = tasks[0]["network_id"]

        # -- 3. read the "take notes" task manual (the canonical source: tasks/main_session_notes.md) --
        #     design principle (Lao Feng, 2026-09-14): task descriptions must live in the tasks/ directory for extension and i18n,
        #     and hard-coding them in MCP code is forbidden. This reads the same file source as task_base.x_desc.
        note_desc = _load_task_desc("main_session_notes")
        if not note_desc:
            return json.dumps(
                {
                    "error": (
                        "任务说明书 tasks/main_session_notes.md 未找到或为空,"
                        "请先创建该文件(任务描述必须来自 tasks/ 目录)"
                    )
                },
                indent=2,
                ensure_ascii=False,
            )
        final_instruction = note_desc
        if instruction and instruction.strip():
            final_instruction += f"\n\n【本次补充说明】\n{instruction.strip()}"

        resolved_project_id = _resolve_project_id_from_agent(agent_id)
        timeout = int(timeout_seconds) if timeout_seconds else int(config.task_timeout)
        session_key = f"agent:{agent_id}:main"

        result = _ao_dispatch_task(
            caller_agent_id=caller_agent_id,
            instruction=final_instruction,
            agent_id=agent_id,
            task_id=task_id,
            network_id=network_id,
            project_id=resolved_project_id,
            host_id=host_id,
            sender_id="",
            timeout_seconds=timeout,
            session_key=session_key,
            send_session_id=f"agent:{caller_agent_id}:main",
            needs_reply=0,
            session_name="主会话",
            exec_name=exec_name or "主会话做笔记",
        )

        if "error" in result:
            return json.dumps(result, indent=2)

        result["note_of"] = agent_id
        result["session_key"] = session_key
        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_note_main_session", e)}, indent=2)


@mcp.tool()
@require_level(3)
def agx_send_text(
    caller_agent_id: str,
    text: str,
    session_key: str = "",
) -> str:
    """
    [Send text directly] Send a plain-text message to the given session (fire-and-forget, no execution created).

    Intent (confirmed with Lao Feng on 2026-10-05):
        Unlike agx_dispatch_task, this tool **dispatches no task, creates no execution and assembles no task template**;
        it delivers a piece of plain text to the given session straight through the underlying sessions.send.
        Semantics = "speaking", not "assigning work".

    * Added 2026-11-19: when the target is an [assistant session] (is_assistant=1), an
        [assistant identity hint] is injected before the body (same criterion as _assistant_hint, a pointer not the
        full text, idempotent). The method's intent is unchanged: still no execution, still no task wrapping;
        it simply gives the assistant an identity anchor on the "speaking" path too (previously only
        dispatch/continue/schedule were covered).

    Mechanism (minimal bypass, the existing execute_task/continue_task paths are untouched):
        MCP assembles a slim {command:"send_text"} envelope -> RPUSH to the agent queue
        -> the daemon sees command=="send_text" -> passes it through (does not smash struct_msg) -> RPUSH to the WS queue
        -> ws_client sees mode=="send_text" -> calls sessions.send({key, message: text}) directly
        * An assistant session's body gains an identity hint (injected by the kernel, not supplied by the caller).

    Safety (honouring the platform philosophy red line: agents must not chat with each other):
        - can only send to yourself: session_key must equal agent:{caller}:main or start with agent:{caller}:
        - session_key must pass the character-safety check (_is_safe_session_key)
        - defaults to the caller's main session agent:{caller}:main

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        text (str): the plain text to send (required, non-empty).
        session_key (str, optional): target session addressing key. Defaults to agent:{caller}:main.
            Only allowed to point at the caller's own sessions.

    Returns:
        str: JSON with msg_id / queue / session_key / delivered.
    """
    agent_id = caller_agent_id
    try:
        # -- 1. parameter validation --
        if not text or not text.strip():
            return json.dumps({"error": "text 不能为空"}, indent=2, ensure_ascii=False)
        if not session_key:
            session_key = f"agent:{agent_id}:main"

        # -- 2. character safety check --
        if not _is_safe_session_key(session_key):
            return json.dumps(
                {"error": f"session_key 非法(含不安全字符): {session_key!r}"}, indent=2, ensure_ascii=False
            )

        # -- 3. ownership check: may only send to yourself --
        own_prefix = f"agent:{agent_id}:"
        if not (session_key == f"agent:{agent_id}:main" or session_key.startswith(own_prefix)):
            return json.dumps(
                {"error": f"无权向该会话发送(只能发给自己): {session_key!r}"}, indent=2, ensure_ascii=False
            )

        # -- 4. resolve host_id --
        agent_row = execute_sql(
            "SELECT host_id, deploy_status FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
            (agent_id,),
        )
        if not agent_row:
            return json.dumps({"error": f"agent '{agent_id}' 不存在或已停用"}, indent=2)
        if agent_row[0].get("deploy_status") != "deployed":
            return json.dumps(
                _deploy_gate_msg(agent_id, agent_row[0].get("deploy_status")),
                indent=2,
            )
        host_id = agent_row[0].get("host_id", "")
        if not host_id:
            return json.dumps({"error": f"cannot determine host_id for agent '{agent_id}'"}, indent=2)

        # -- 5. session registration (register the key on first sight, reusing the existing logic) --
        resolved_project_id = _resolve_project_id_from_agent(agent_id)
        ensure_session(
            session_id=session_key,
            name_hint="",
            agent_id=agent_id,
            project_id=resolved_project_id,
            task_labs="",
            run_uuid="",
            creator_agent_id=caller_agent_id,
            network_id=config.get("network_id", ""),
        )

        # -- 5.5) assistant identity hint (* decided by Lao Feng on 2026-11-19) --
        #   Exactly the same as _ao_dispatch_task / _ao_continue_task / _ao_schedule_task:
        #   if the target session has is_assistant=1, an [assistant identity hint] pointer is injected before the content.
        #   Why it must be done in this method:
        #     (1) all four delivery paths must share one rule -- miss one and the assistant "loses its memory" on that path;
        #        measured gap: Win3 "session page -> send message -> text mode" goes exactly down this path,
        #        and the assistant received plain text with no identity positioning at all.
        #     (2) the single source of truth for the hint text is agx_core._inject_assistant_hint (the file root,
        #        the discipline items will change later); making the BFF/front end copy it would inevitably drift.
        #     (3) this method can only send to sessions under your own name (already validated in step 3), and the assistant
        #        sessions agent:{caller}:case_* / asst_* are already in reach -> no permission surface change.
        #   * a non-assistant session is returned as-is (the function checks is_assistant internally, idempotent).
        _text_to_send = _inject_assistant_hint(session_key, text)

        # -- 6. assemble the slim command envelope (no task lookup, no template rendering) --
        msg_id = agx_generate_uuid(prefix="msg")
        now_ts = int(datetime.now().timestamp())
        message = {
            "proto_ver": "1.0",
            "msg_id": msg_id,
            "timestamp": now_ts,
            "target_id": agent_id,
            "sender_id": "",
            "send_agent": agent_id,
            "msg_type": "command",
            "payload": {
                "command": "send_text",
                "instruction": _text_to_send,
                "session_key": session_key,
            },
        }

        queue_key = f"agx:queue:{host_id}:agent:{agent_id}"
        r = get_redis_client()
        r.rpush(queue_key, json.dumps(message, ensure_ascii=False))
        queue_len = r.llen(queue_key)

        return json.dumps(
            {
                "msg_id": msg_id,
                "queue": queue_key,
                "queue_length": queue_len,
                "session_key": session_key,
                "delivered": True,
                # * decided by Lao Feng on 2026-11-19: "whatever you send to OpenClaw is what the history shows".
                #   text mode creates no execution and the UI has nowhere to read it back -> only the layer that actually sends can report it.
                #   What is reported back is the [result] (the final body including the assistant identity hint), not the [rule] --
                #   the BFF/front end must not render a hint of its own, otherwise the text drifts (same conclusion as the 2026-11-17 skill block).
                #   For a non-assistant session text_full == the input text, identical to the old behavior.
                "text_full": _text_to_send,
                "text_len": len(_text_to_send),
                "note": "fire-and-forget: 已投递,不建 execution、不等回答",
            },
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_send_text", e)}, indent=2)


@mcp.tool()
@require_level(5)
def agx_nudge_execution(
    caller_agent_id: str,
    task_run_uuid: str,
    kind: str = "auto",
    note: str = "",
) -> str:
    """[Manual intervention] Nudge a stuck task -- creates no execution, changes no run_status.

    * What problem it solves (Lao Feng, 2026-10-31)
      All three automatic watchdog mechanisms have limits, and after they are used up the task is still running:
        wakeups at 70/80/90% fire only once each / alert_count hits the ceiling / resend send_resend_max=2
      The UI shows "still running", but in reality nobody will push it again. A human must be able to push once more.

    * Direction rule (copy the watchdog, do not invent)
      urge       -> the executor agent_id     (same as _try_wake: make it work)
      ack_remind -> the initiator send_agent_id (same as _notify_owner: make it acknowledge)
      Reversing them = nagging someone who already handed in the homework to do the homework.

    * Three things it does not do (otherwise the problem only grows)
      1. does not create an execution -- that would be dispatching another task, which would time out in turn
      2. does not change run_status -- a protected field, only agx_finish_task may end it
      3. does not change needs_reply -- nudging is not acknowledging

    * Rate limit: the same run and kind at most once per 60 seconds (prevents accidental double clicks).
      Manual intervention means "I judge the system is stuck", not an action to spam.

    Args:
        caller_agent_id: caller agent ID (whoever intervenes; written to the event's from_agent)
        task_run_uuid: the execution instance ID to nudge
        kind: auto (default, chosen from the state) / urge (nudge to work) / ack_remind (nudge to acknowledge)
        note: extra explanation, written to the event's reason

    Returns:
        JSON: {ok, kind, toAgent, deliveredTo, eventId, state, hint}
              or {error, code}: NOT_FOUND / STATE_MISMATCH / BAD_KIND /
                 RATE_LIMITED / NO_TARGET / DELIVERY_FAILED
    """
    import json as _json
    from datetime import datetime as _dt

    def _err(code: str, msg: str, **extra) -> str:
        out = {"error": msg, "code": code}
        out.update(extra)
        return _json.dumps(out, indent=2, ensure_ascii=False)

    run_uuid = (task_run_uuid or "").strip()
    if not run_uuid:
        return _err("BAD_ARGS", "task_run_uuid 必填")

    try:
        rows = execute_sql(
            "SELECT task_run_uuid, agent_id, send_agent_id, run_status, session_id, "
            "       dispatched_at, timeout_seconds, alert_count, "
            "       reply_sent_at, reply_read, exec_name "
            "FROM agx_task_execution WHERE task_run_uuid = %s",
            (run_uuid,),
            commit=False,
        )
        if not rows:
            return _err("NOT_FOUND", f"执行记录不存在：{run_uuid}")
        ex = rows[0]

        status = (ex.get("run_status") or "").strip()
        if status in ("succeeded", "failed", "cancelled"):
            return _err(
                "STATE_MISMATCH",
                f"该任务已结束（run_status={status}），无需催办。",
                run_status=status,
                finished=True,
            )
        if status not in ("running", "pending", "awaiting_confirm"):
            return _err("STATE_MISMATCH", f"该任务当前状态 {status} 没有可催的动作。", run_status=status)

        k = (kind or "auto").strip().lower()
        if k not in ("auto", "urge", "ack_remind"):
            return _err("BAD_KIND", f"kind 只能是 auto / urge / ack_remind，收到 {kind!r}")

        # -- availability: the two axes are orthogonal --
        unread = bool(
            ex.get("reply_sent_at")
            and str(ex.get("reply_sent_at")).strip()
            and not int(ex.get("reply_read") or 0)
        )
        can_urge = status == "running"
        can_ack = unread

        if k == "auto":
            # an unsigned receipt is the clearer pending item, so take priority; otherwise nudge the executor
            if can_ack:
                k = "ack_remind"
            elif can_urge:
                k = "urge"
            else:
                return _err(
                    "STATE_MISMATCH",
                    f"当前状态（{status}）既无可催的执行方，也没有待签收的回执。",
                    run_status=status,
                )

        if k == "urge" and not can_urge:
            return _err(
                "STATE_MISMATCH",
                f"只能对 running 的任务催办，当前 run_status={status}。",
                run_status=status,
            )
        if k == "ack_remind" and not can_ack:
            return _err(
                "STATE_MISMATCH",
                "该任务没有「已发未签」的回执，催签收没有对象。",
                run_status=status,
            )

        # -- target: the direction decides whom to send to --
        agent_id = (ex.get("agent_id") or "").strip()
        send_agent_id = (ex.get("send_agent_id") or "").strip()
        target = agent_id if k == "urge" else send_agent_id
        if not target:
            return _err(
                "NO_TARGET",
                f"该任务没有可催的对象（{k} 需要 {'agent_id' if k == 'urge' else 'send_agent_id'}）。",
            )

        # -- rate limit: the same run and kind once per 60 seconds --
        recent = execute_sql(
            "SELECT event_id, created_at FROM agx_task_execution_event "
            "WHERE task_run_uuid = %s AND event_type = 'manual_nudge' "
            "  AND reason LIKE %s AND created_at >= NOW() - INTERVAL 60 SECOND "
            "ORDER BY created_at DESC LIMIT 1",
            (run_uuid, k + "%"),
            commit=False,
        )
        if recent:
            return _err(
                "RATE_LIMITED",
                f"同一任务 60 秒内只能催一次（上次 {recent[0].get('created_at')}）。",
                last_event_id=recent[0].get("event_id"),
            )

        # -- body: different wording per state, including "what to do next" --
        timeout = int(ex.get("timeout_seconds") or 0) or 1800
        alerts = 0
        try:
            alerts = int(ex.get("alert_count") or 0)
        except (TypeError, ValueError):
            alerts = 0
        age = None
        try:
            if ex.get("dispatched_at"):
                age = int((_dt.now() - ex["dispatched_at"]).total_seconds())
        except Exception:
            age = None
        age_s = f"{age}s" if age is not None else "未知"
        pct = f"{age / timeout:.0%}" if (age is not None and timeout > 0) else "?"

        if k == "urge":
            if age is not None and age >= timeout:
                head = (
                    f"【催办·已超时】任务已超时 {age - timeout}s"
                    f"（累计 {age_s} / 预计 {timeout}s）"
                )
            elif alerts >= 1:
                head = (
                    f"【催办·已告警 {alerts} 次】系统已不再自动唤醒，任务仍停在 running"
                    f"（已运行 {age_s} / 预计 {timeout}s）"
                )
            else:
                head = f"【催办】任务已运行 {age_s} / 预计 {timeout}s（{pct}），接近超时"
            text = (
                f"{head}\n\n"
                f"任务: {run_uuid}\n"
                f"标题: {(ex.get('exec_name') or '-')}\n"
                f"环境: 看门狗的自动唤醒/告警/重发都已用尽或到顶，人已手动催办。\n\n"
                "请做两件事:\n"
                "1) 继续推进并汇报当前进展；或\n"
                "2) 判定无法完成，直接结案并说明卡点\n"
                f"   agx_finish_task(caller_agent_id=\"{target}\", "
                f"task_run_uuid=\"{run_uuid}\", status=\"failed\", summary=\"<卡点>\")\n\n"
                f"（本条由 {caller_agent_id} 人工发出，不是系统唤醒）"
            )
        else:
            text = (
                "【催签收】你有一条回执未签收\n\n"
                f"任务: {run_uuid}\n"
                f"标题: {(ex.get('exec_name') or '-')}\n"
                f"执行方: {agent_id}\n"
                f"回执发出: {ex.get('reply_sent_at')}\n\n"
                "看门狗按「签收」判定这件事办完了；不签收会继续重发。\n"
                "请签收:\n"
                f"  agx_ack_receipt(caller_agent_id=\"{target}\", task_run_uuid=\"{run_uuid}\")\n\n"
                f"（本条由 {caller_agent_id} 人工发出）"
            )
        if (note or "").strip():
            text += f"\n\n干预备注: {note.strip()[:200]}"

        # -- write the event first (leave a trace, then deliver; even a failed delivery can show "a human nudged it") --
        from agx_mcp.agx_core import _ao_record_event as _rec

        reason_txt = (f"{k}: {note.strip()}" if (note or "").strip() else k)[:255]
        event_id = _rec(
            task_run_uuid=run_uuid,
            event_type="manual_nudge",
            level=0,
            from_agent=caller_agent_id,
            to_agent=target,
            to_session=f"agent:{target}:main",
            channel="queue",
            reason=reason_txt,
            delivery_state="pending",
        )

        # -- delivery: the send_text envelope (same channel as agx_send_text, only the target is someone else)
        agent_row = execute_sql(
            "SELECT host_id, deploy_status FROM agx_agent_registry WHERE agent_id = %s AND active_status = 1",
            (target,),
            commit=False,
        )
        if not agent_row:
            return _err(
                "DELIVERY_FAILED",
                f"目标 agent '{target}' 不存在或已停用；事件已记录（{event_id}）但消息未投递。",
                eventId=event_id,
            )
        host_id = agent_row[0].get("host_id", "")
        if not host_id:
            return _err(
                "DELIVERY_FAILED",
                f"无法确定 '{target}' 的 host_id；事件已记录（{event_id}）但消息未投递。",
                eventId=event_id,
            )

        session_key = f"agent:{target}:main"
        msg_id = agx_generate_uuid(prefix="msg")
        message = {
            "proto_ver": "1.0",
            "msg_id": msg_id,
            "timestamp": int(_dt.now().timestamp()),
            "target_id": target,
            "sender_id": "",
            "send_agent": caller_agent_id,
            "msg_type": "command",
            "payload": {
                "command": "send_text",
                "instruction": text,
                "session_key": session_key,
            },
        }
        queue_key = f"agx:queue:{host_id}:agent:{target}"
        r = get_redis_client()
        r.rpush(queue_key, _json.dumps(message, ensure_ascii=False))

        try:
            execute_sql(
                "UPDATE agx_task_execution_event SET delivery_state='sent', delivered_at=NOW() "
                "WHERE event_id = %s",
                (event_id,),
                commit=True,
            )
        except Exception:
            pass

        return _json.dumps(
            {
                "ok": True,
                "kind": k,
                "toAgent": target,
                "deliveredTo": session_key,
                "queue": queue_key,
                "eventId": event_id,
                "msgId": msg_id,
                "state": {
                    "runStatus": status,
                    "ageSeconds": age,
                    "timeoutSeconds": timeout,
                    "alertCount": alerts,
                    "replySentAt": str(ex.get("reply_sent_at") or ""),
                    "replyRead": int(ex.get("reply_read") or 0),
                },
                "note": "已投递；不建 execution、不改 run_status。60 秒内同一任务同一动作只能一次。",
            },
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_nudge_execution", e)}, indent=2)


# ═══════════════════════════════════════════════════════════════════
# unified input-bar facade (decided by Lao Feng on 2026-10-19: P1 forced either-or + thin facade)
# ═══════════════════════════════════════════════════════════════════
#
# * intent: a thin facade, not a merge.
#   the three modes text / task / quickcmd are [fundamentally different]:
#     text      produces no execution | can only send to yourself | L3
#     task      produces an execution | may create a session | L5
#     quickcmd  produces an execution | may create a session | L5
#   merging them into one giant branch would make any change to any mode touch the same method, enlarging the regression surface.
#   So: the three underlying method signatures and behaviors are [completely unchanged], and this facade does only 5 things --
#     (1) parameter normalization (from_session / to_agent / to_session)
#     (2) receipt normalization (reply: "yes"|"no" -> needs_reply + force_no_reply, the patch does not leak out)
#     (3) idempotency pass-through (client_request_id)
#     (4) permission by mode (text->L3 / task|quickcmd->L5)
#     (5) structurally identical JSON return
#
# * why reply must be required and may only be yes/no (pointed out by agx_ui_lead on 2026-10-19):
#   if reply had a default (e.g. "inherit"), then "no middle ground" would hold only in the UI --
#   a BFF that forgets to pass it would silently take the default and bypass the whole constraint. Hence no default + explicit validation.
#
# * why force_no_reply is not exposed:
#   it is a patch caused by agx_dispatch_task using needs_reply: int = 0 as a default
#   (it cannot distinguish "explicitly passed 0" from "not passed"). The facade absorbs it, so it does not leak into the front-end contract.
# ═══════════════════════════════════════════════════════════════════

_UNIVERSAL_MODES = ("text", "task", "quickcmd")
# modes that need L5 (they produce an execution / can dispatch across agents)
_UNIVERSAL_L5_MODES = ("task", "quickcmd")


def _safe_json(raw: str) -> dict:
    """The underlying method returns a JSON string; parse it safely here (on failure wrap it as an error instead of crashing)."""
    if isinstance(raw, dict):
        return raw
    try:
        _v = json.loads(raw)
        return _v if isinstance(_v, dict) else {"value": _v}
    except Exception:
        return {"error": f"底层返回非 JSON: {str(raw)[:200]}"}


def _universal_deny(caller_agent_id: str, mode: str) -> str | None:
    """Validate permission according to the mode.

    Returns None = allowed; str = reason for denial.
    Structurally the same as _check_pipeline_caller (detailed checks in the function body), no new decorator level.

    * 2026-10-19 fix for a blocker (this exemption must stay, otherwise it is a regression):
      agent_manager (agentfather) / host_manager already have the DISPATCH_EXEMPT_ROLES exemption for dispatch
      tools (permissions.py:150-153); even without L5 they can call agx_dispatch_task / agx_quick_cmd_send.
      If the facade did not allow them explicitly it would be stricter than the underlying method it wraps ->
      the regression of "it used to dispatch, and after switching to the facade it cannot".
      So the same exemption constant is reused here instead of a separate rule.
    """
    import permissions as _perm

    try:
        _role = _perm._resolve_agent_role(caller_agent_id)
        allowed = _perm._get_agent_allowed_levels(caller_agent_id)
    except Exception:
        return "权限校验失败: 无法解析调用方角色"

    if mode == "text":
        need = 3
    else:
        need = 5
        # dispatch exemption: consistent with the underlying agx_dispatch_task / agx_quick_cmd_send
        if _role in getattr(_perm, "DISPATCH_EXEMPT_ROLES", set()):
            return None

    if need not in allowed:
        return (
            f"权限不足: Agent '{caller_agent_id}' 无权以 mode='{mode}' 发送"
            f"(需要 level {need}, 实际 {sorted(allowed)})。"
            + (
                "文本模式仅需 L3; 派发任务/快捷指令需 L5(项目管理员)。"
                if mode == "text"
                else "请通过 agx_dispatch_task 将任务分派给有权限的 Agent 执行。"
            )
        )
    return None


@mcp.tool()
@require_level(3)
def agx_universal_send(
    caller_agent_id: str,
    mode: str,
    reply: str,
    from_session: str = "",
    to_agent: str = "",
    to_session: str = "",
    text: str = "",
    task_id: str = "",
    cmd_id: str = "",
    params: str = "{}",
    timeout_seconds: int = None,
    exec_name: str = "",
    instruction_suffix: str = "",
    client_request_id: str = "",
    session_name: str = "",
    needs_confirm: int = 0,
) -> str:
    """
    [Unified send facade] One sentence, one entry point: send text / dispatch a task / send a quick command.

    * This method is a [thin facade] -- the signatures and behavior of the underlying agx_send_text /
      agx_dispatch_task / agx_quick_cmd_send are completely unchanged; only parameter and receipt normalization
      happens here.

    * mode is one of three (fundamentally different, not three spellings of the same thing):
        "text"      -> say one thing in [the caller's own] session, as the caller.
                       Produces no execution, cannot cross agents, needs only L3.
        "task"      -> dispatch a task to some agent and run it in its session. Produces an execution, needs L5.
        "quickcmd"  -> send a predefined quick command. Produces an execution, needs L5.

    * reply is required and may only be "yes"/"no" (no default value, cannot be omitted):
        This is a hard constraint -- having a default would leave a "middle ground" that the API could bypass.
        It is meaningless in text mode (which produces no execution), where an empty string is allowed.

    Args:
        caller_agent_id (str): caller agent ID (the sender identity, also the audit subject).
        mode (str): "text" | "task" | "quickcmd".
        reply (str): "yes" (want a receipt) | "no" (no receipt). Required for task/quickcmd.
        from_session (str): the sender's own session id (decides where the receipt lands).
            Defaults to agent:{caller}:main when omitted.
        to_agent (str): target agent ID. In text mode it must equal the caller (or be omitted).
        to_session (str): target session key.
            * Semantics differ per mode:
              text      -> may only point at the caller's own session (omitted = main session)
              task/quickcmd -> omitted = the kernel derives it (usually creates a one-off session)
        text (str): the body when mode="text", required and non-empty.
        task_id (str): the task template ID when mode="task".
        cmd_id (str): the quick command ID when mode="quickcmd".
        params (str): the parameter JSON string when mode="quickcmd".
        timeout_seconds (int): watchdog timeout (seconds). Follows the kernel default when omitted.
            * the front end is advised to leave it empty by default -- otherwise it overrides the config plan_timeout.
        exec_name (str): short execution title (Chinese).
        instruction_suffix (str): supplementary text appended to the end of the instruction (e.g. a skill reference block).
        client_request_id (str): idempotency key. Reuse the same value when retrying the same intent -> returns the
            first result and does not dispatch twice. * This is the only guarantee that "network retries produce no
            duplicate side effects".
        session_name (str): session name hint (used when task mode creates a session).
        needs_confirm (int): *2026-11-06 Lao Feng. Task-level property: 0=not needed (default)
            1=needs human release -- the executor does **not write succeeded** when done, it lands on awaiting_confirm,
            and only counts as complete after a human calls agx_confirm_task (downstream chaining does not advance,
            the watchdog does not time it). **Only supported for mode='task'**; text produces no execution and
            quickcmd goes through agx_quick_cmd_send (the kernel has no such parameter), so passing 1 raises
            NEEDS_CONFIRM_UNSUPPORTED explicitly -- better an error than silently dropping it ("the UI selected it
            but it did not take effect" is worse than not having it).

    Returns:
        str: JSON. The three modes return structurally identical fields:
            {"mode":..., "ok":true, "run_uuid":..., "session_key":..., ...}
            text mode has no run_uuid (no execution is produced) and returns msg_id.
            An idempotent hit adds "idempotent": true.
    """
    _mode = (mode or "").strip().lower()

    # -- (1) mode validity --
    if _mode not in _UNIVERSAL_MODES:
        return json.dumps(
            {
                "error": "MODE_INVALID",
                "detail": f"mode 必须是 {' | '.join(_UNIVERSAL_MODES)} 之一，收到 {mode!r}",
            },
            ensure_ascii=False,
        )

    # -- (2) receipt format validation (* must come before the permission check) --
    #  Reason (pointed out by agx_ui_lead on 2026-10-19, and correct):
    #    a wrong reply is a [parameter format problem], unrelated to the caller identity. If permission were checked first,
    #    an L3 worker writing reply="inherit" would receive PERMISSION_DENIED
    #    and wrongly conclude "I need to ask someone for more permission", while the real problem is a bad parameter.
    #    So: report format errors first, permission errors afterwards.
    _reply = (reply or "").strip().lower()
    if _mode in _UNIVERSAL_L5_MODES:
        if _reply not in ("yes", "no"):
            return json.dumps(
                {
                    "error": "REPLY_REQUIRED",
                    "detail": "reply 必须是 'yes' 或 'no'，不支持省略、空串或 'inherit'。"
                    "（存在默认值等于中间地带可从 API 绕过，故强制必填）",
                },
                ensure_ascii=False,
            )

    # -- (3) permission by mode (before any real action) --
    _deny = _universal_deny(caller_agent_id, _mode)
    if _deny:
        return json.dumps({"error": "PERMISSION_DENIED", "detail": _deny}, ensure_ascii=False)

    # -- (4) receipt normalization (the patch is absorbed here, never leaked) --
    _needs_reply = 1 if _reply == "yes" else 0
    _force_no_reply = _reply == "no"          # <- the patch appears only here

    # -- (5) sender session (where the receipt lands) --
    _from = (from_session or "").strip() or f"agent:{caller_agent_id}:main"

    # -- (6) instruction_suffix applied uniformly (* consistent across the three modes) --
    #  Corrected 2026-10-19 (pointed out by agx_ui_lead, and correct): the original implementation passed suffix only in the quickcmd branch,
    #  so task / text missed it -> the same parameter behaved differently per mode and callers could not predict it.
    #
    #  Plan A (do not touch the kernel): the facade renders it itself. Reason:
    #    agx_dispatch_task has no instruction_suffix parameter, and supporting it would mean changing the kernel signature + the template merge logic;
    #    while a skill block is essentially "part of the instruction body", so having the facade append it is semantically correct.
    #
    #  * quickcmd is the exception: it has its own instruction_suffix parameter (appended after _render_quick_cmd),
    #    with established behavior (2026-10-18). To avoid double appending, the quickcmd branch still passes it to the kernel.
    _suffix = (instruction_suffix or "").strip()
    _text_with_suffix = text
    if _suffix and _mode in ("text", "task"):
        _text_with_suffix = f"{text}\n\n{_suffix}" if text else _suffix

    # -- (7) needs_confirm normalization + mode guard --
    #  * why not simply "ignore it" in text/quickcmd:
    #    if the UI puts the switch on an entry point that does not support it, ignoring it = the user thinks it took effect when it did not.
    #    An explicit error exposes immediately that "this entry point does not support it" instead of leaving the result quietly wrong.
    try:
        _nc = 1 if int(needs_confirm or 0) == 1 else 0
    except (TypeError, ValueError):
        return json.dumps(
            {"error": "NEEDS_CONFIRM_INVALID", "detail": f"needs_confirm 必须是 0/1，收到 {needs_confirm!r}"},
            ensure_ascii=False,
        )
    if _nc and _mode != "task":
        return json.dumps(
            {
                "error": "NEEDS_CONFIRM_UNSUPPORTED",
                "detail": f"needs_confirm 只支持 mode='task'，收到 mode={_mode!r}。"
                "（quickcmd 走 agx_quick_cmd_send，内核没有该参数；text 不产生执行）",
            },
            ensure_ascii=False,
        )

    try:
        # ============== text mode ==============
        if _mode == "text":
            if not text or not text.strip():
                return json.dumps(
                    {"error": "TEXT_EMPTY", "detail": "mode='text' 时 text 必填非空"},
                    ensure_ascii=False,
                )
            # * semantics are fixed: text = "speak as the caller, in the caller's own session"
            #   the kernel agx_send_text has a self-only hard red line of its own; a friendlier error is produced here up front.
            _to = (to_agent or "").strip()
            if _to and _to != caller_agent_id:
                return json.dumps(
                    {
                        "error": "TEXT_MODE_SELF_ONLY",
                        "detail": f"文本模式只能以自己身份发给自己（caller={caller_agent_id}, to_agent={_to}）。"
                        "要给别人发消息请用 mode='task' 派任务，或让对方自己发言。",
                    },
                    ensure_ascii=False,
                )
            _sess = (to_session or "").strip() or f"agent:{caller_agent_id}:main"
            _raw = agx_send_text(
                caller_agent_id=caller_agent_id,
                text=_text_with_suffix,
                session_key=_sess,
            )
            _res = _safe_json(_raw)
            if _res.get("error"):
                return json.dumps(
                    {"error": "TEXT_SEND_FAILED", "detail": _res.get("error"), "raw": _res},
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "mode": "text",
                    "ok": True,
                    "session_key": _res.get("session_key", _sess),
                    "msg_id": _res.get("msg_id", ""),
                    "queue": _res.get("queue", ""),
                    # * decided by Lao Feng on 2026-11-19: pass through the final body returned by the kernel.
                    #   this branch used to return only session_key/msg_id/queue -> the BFF could not obtain what was actually sent,
                    #   so the UI history could only show the raw user input, inconsistent with what OpenClaw actually received.
                    #   (task mode has no such problem -- it creates an execution, and reading exec_instruction back is inherently faithful.)
                    "text_full": _res.get("text_full", ""),
                    "text_len": _res.get("text_len", 0),
                    "reply_applied": False,   # text produces no execution, so reply is meaningless
                },
                ensure_ascii=False,
            )

        # ============== quickcmd mode ==============
        if _mode == "quickcmd":
            if not (cmd_id or "").strip():
                return json.dumps(
                    {"error": "CMD_ID_REQUIRED", "detail": "mode='quickcmd' 时 cmd_id 必填"},
                    ensure_ascii=False,
                )
            _raw = agx_quick_cmd_send(
                caller_agent_id=caller_agent_id,
                cmd_id=cmd_id.strip(),
                params=params or "{}",
                self_session_id=_from,
                target_session_key=(to_session or "").strip(),
                timeout_seconds=timeout_seconds,
                exec_name=exec_name,
                needs_reply=_needs_reply,
                force_no_reply=_force_no_reply,
                instruction_suffix=instruction_suffix,
                # * fixed 2026-11-19: this branch has read `_res["idempotent"]` since 2026-10-17,
                #   but neither premise was fulfilled -- (1) quick_cmd_send did not accept client_request_id;
                #   (2) it was not passed down here either. Result: the promise in the universal_send docs
                #   "network retries produce no duplicate side effects" was always false in quickcmd mode.
                #   The kernel now supplies (1) and this supplies (2), closing the loop.
                client_request_id=client_request_id,
            )
            _res = _safe_json(_raw)
            if _res.get("error"):
                return json.dumps(
                    {"error": "QUICKCMD_SEND_FAILED", "detail": _res.get("error"), "raw": _res},
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "mode": "quickcmd",
                    "ok": True,
                    # * the kernel's returned value must be used: quickcmd actually lands in a oneshot session,
                    #   not :main (a real BUG fixed on 2026-10-17; do not render it yourself)
                    "run_uuid": _res.get("run_uuid", ""),
                    "session_key": _res.get("session_key", ""),
                    "msg_id": _res.get("msg_id", ""),
                    "queue": _res.get("queue", ""),
                    "idempotent": bool(_res.get("idempotent")),
                    "reply_applied": True,
                    "needs_reply": _needs_reply,
                },
                ensure_ascii=False,
            )

        # ============== task mode ==============
        if not (to_agent or "").strip():
            return json.dumps(
                {"error": "TO_AGENT_REQUIRED", "detail": "mode='task' 时 to_agent 必填"},
                ensure_ascii=False,
            )
        if not (task_id or "").strip():
            return json.dumps(
                {"error": "TASK_ID_REQUIRED", "detail": "mode='task' 时 task_id 必填"},
                ensure_ascii=False,
            )
        if not (text or "").strip():
            return json.dumps(
                {"error": "INSTRUCTION_REQUIRED", "detail": "mode='task' 时 text 作为指令正文，必填非空"},
                ensure_ascii=False,
            )
        _raw = agx_dispatch_task(
            caller_agent_id=caller_agent_id,
            instruction=_text_with_suffix,
            agent_id=to_agent.strip(),
            task_id=task_id.strip(),
            sender_id=caller_agent_id,
            timeout_seconds=timeout_seconds,
            needs_reply=_needs_reply,
            force_no_reply=_force_no_reply,
            session_key=(to_session or "").strip(),
            self_session_id=_from,
            session_name=session_name,
            exec_name=exec_name,
            client_request_id=client_request_id,
            needs_confirm=_nc,
        )
        _res = _safe_json(_raw)
        if _res.get("error"):
            return json.dumps(
                {"error": "TASK_SEND_FAILED", "detail": _res.get("error"), "raw": _res},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "mode": "task",
                "ok": True,
                "run_uuid": _res.get("run_uuid", ""),
                "session_key": _res.get("session_key", ""),
                "msg_id": _res.get("msg_id", ""),
                "queue": _res.get("queue", ""),
                "idempotent": bool(_res.get("idempotent")),
                "reply_applied": True,
                "needs_reply": _needs_reply,
                # for the caller to verify: whether choosing "wait for human release" actually landed in the DB
                "needs_confirm": _nc,
            },
            ensure_ascii=False,
        )

    except Exception as e:
        return json.dumps(
            {"error": "UNIVERSAL_SEND_FAILED", "detail": _log_exception("agx_universal_send", e)},
            ensure_ascii=False,
        )


@mcp.tool()
@require_level(3)
def agx_self_dispatch(
    caller_agent_id: str,
    instruction: str,
    exec_instruction: str = "",
    timeout_seconds: int = None,
    exec_name: str = "",
    self_session_id: str = "",
    needs_reply: int = 0,
    force_no_reply: bool = False,
) -> str:
    """
    [Self dispatch] An agent dispatches a temporary/follow-up task to itself, running in its own dedicated session.

    Intent (confirmed with Lao Feng on 2026-10-01):
        dispatch is the ability to [command others]; raised to L5, only PMs / system core roles can use it.
        But roles without L5 (workers etc.) also have a legitimate need to [queue follow-up actions for themselves]
        (e.g. "after A finishes here, do B", a self-assigned ad-hoc inspection/analysis/snapshot).
        These are two different things:
          - sending to others -> commanding, L5, must never be opened up (otherwise agents chatting = out of control)
          - sending to yourself -> self-management, the same class as agx_note_main_session (sending to your own main
                         session), and should be the worker's outlet
        This tool adds that [worker-only self-dispatch outlet].

    Core constraints (against privilege abuse):
        - agent_id is hard-coded = caller_agent_id; no parameter can target anyone else
        - the landing point is [your own] universal channel (private per agent, dedicated session, concurrent)
        - the universal privacy check is a second safety net inside _ao_dispatch_task

    Semantics:
        - every call creates a dedicated session (agent:{aid}:universal:{run_xxx}) and can run in parallel
        - instruction is the core instruction of this execution (required); exec_instruction may carry the full body
        - consistent with other executions: goes into summary, is traceable, and is aggregated by the logs

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        instruction (str): the dynamic instruction for this self-assignment (required).
        exec_instruction (str, optional): the full execution instruction body. When passed it is stored directly as
            exec_instruction (the universal task supports skipping the template merge).
        timeout_seconds (int, optional): timeout in seconds; follows config.task_timeout by default.
        exec_name (str, optional): short execution title (Chinese).
        self_session_id (str, optional): * [the sender's own session id] (added 2026-10-09).
            The session id you are currently in (stated in the [session context] block at the top of exec_instruction).
            When passed, this self-dispatched execution's send_session_id uses it; when omitted it falls back to
            agent:{caller}:main.
            * Why self-dispatch needs it too: the execution's message still carries self_session_id (the daemon
            direct-delivery path and the watchdog receipt both read it); an empty value makes the receipt land in the
            wrong session (main), see the 2026-10-09 fix note.
        needs_reply (int, optional): receipt requirement. 0-not needed 1-reply needed (watchdog: 1->2->3).
            * Added 2026-10-13 (fixing the "self-dispatch always loses the receipt" bug): when not passed explicitly
            it is derived from the universal task's reply_mode (result/confirm -> 1), aligned with agx_dispatch_task.
            Before the fix, self-dispatch hard-coded needs_reply=0 -> the result silently never returned to the initiator.
        force_no_reply (bool, optional): explicitly disable the receipt (default False), outranks the template derivation.
            Pass True for "self-dispatch but stay silent" (rather than only needs_reply=0).

    Returns:
        str: JSON with run_uuid / msg_id / queue / session_key.

    Example:
        agx_self_dispatch(
            instruction="inspect the queue backlog of every agent in this project and output a snapshot",
            exec_name="queue inspection snapshot"
        )
    """
    agent_id = caller_agent_id
    if not instruction or not instruction.strip():
        return json.dumps({"error": "instruction is required"}, indent=2)
    try:
        # -- 1. validate the agent's deploy state + owning host --
        agent_row = execute_sql(
            "SELECT host_id, deploy_status FROM agx_agent_registry "
            "WHERE agent_id = %s AND active_status = 1",
            (agent_id,),
        )
        if not agent_row:
            return json.dumps({"error": f"agent '{agent_id}' 不存在或已停用"}, indent=2)
        if agent_row[0].get("deploy_status") != "deployed":
            return json.dumps(
                _deploy_gate_msg(agent_id, agent_row[0].get("deploy_status")),
                indent=2,
            )
        host_id = agent_row[0].get("host_id", "")
        if not host_id:
            return json.dumps({"error": f"cannot determine host_id for agent '{agent_id}'"}, indent=2)

        # -- 2. find this agent's universal task (a private standing channel) --
        tasks = execute_sql(
            "SELECT task_id, network_id, reply_mode FROM agx_task_base "
            "WHERE agent_id = %s AND task_labs = 'universal' AND active_status = 1 LIMIT 1",
            (agent_id,),
        )
        if not tasks:
            return json.dumps(
                {"error": f"no universal task (task_labs='universal') found for agent '{agent_id}'"},
                indent=2,
            )
        task_id = tasks[0]["task_id"]
        network_id = tasks[0]["network_id"]

        # -- 2.5 needs_reply derivation (2026-10-13, fixing "self-dispatch always loses the receipt") --
        #   the universal task template's reply_mode is usually 'confirm' => a receipt is expected.
        #   Rule aligned with agx_dispatch_task: force_no_reply wins; otherwise derive from the template when no receipt was requested explicitly.
        #   (needs_reply is an int defaulting to 0, so it cannot distinguish "explicitly passed 0" from "not passed" -> the boolean handles that)
        reply_mode_val = tasks[0].get("reply_mode", "result")
        if force_no_reply:
            needs_reply = 0
        elif not needs_reply and reply_mode_val in ("result", "confirm"):
            needs_reply = 1

        resolved_project_id = _resolve_project_id_from_agent(agent_id)
        timeout = int(timeout_seconds) if timeout_seconds else int(config.task_timeout)

        # a universal task lets the caller pass a full exec_instruction as the body;
        # otherwise instruction is authoritative (_ao_dispatch_task writes instruction into the exec_instruction column).
        final_instruction = exec_instruction.strip() if (exec_instruction and exec_instruction.strip()) else instruction

        # -- 3. the landing point is its own universal channel: a dedicated session each time, concurrent --
        #     consistent with the universal derivation inside _ao_dispatch_task.
        _short_id = agx_generate_uuid(prefix="run", infix="universal")
        session_key = f"agent:{agent_id}:universal:{_short_id}"

        result = _ao_dispatch_task(
            caller_agent_id=caller_agent_id,
            instruction=final_instruction,
            agent_id=agent_id,
            task_id=task_id,
            network_id=network_id,
            project_id=resolved_project_id,
            host_id=host_id,
            sender_id="",
            timeout_seconds=timeout,
            session_key=session_key,
            send_session_id=(self_session_id if _is_safe_session_key(self_session_id) else f"agent:{caller_agent_id}:main"),
            needs_reply=needs_reply,
            session_name="",
            exec_name=exec_name or "自我调度",
        )

        if "error" in result:
            return json.dumps(result, indent=2)

        result["self_dispatch_of"] = agent_id
        result["session_key"] = session_key
        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_self_dispatch", e)}, indent=2)


@mcp.tool()
@require_level(3)
def agx_daily_log(
    caller_agent_id: str,
    date: str = "",
    activate: bool = True,
    instruction: str = "",
    timeout_seconds: int = None,
    self_session_id: str = "",
) -> str:
    """
    [Daily log pipeline] One-click orchestration of the two steps "main session snapshot + log aggregation".

    Intent (confirmed with Lao Feng on 2026-09-14):
        After adopting the dispatch/plan model, logs are no longer written by the PM alone -- the work sites are scattered
        across the execution.summary of each session. A complete daily log = the two chains combined:
          step1  main_session_notes  -> capture the "main session site" (a snapshot landing in the caller's main session)
          step2  log_organize        -> aggregate the whole project group's execution.summary into prose

        The two have a strict ordering: log_organize must run after notes, otherwise that day's main session site has not
        yet entered execution.summary and the aggregation misses it -- hence the two-step serial plan via prev_task.

    Why MCP builds the plan directly (instead of handing it to a plan-creation task):
        The orchestration structure is completely fixed (2 steps, fixed order, zero freedom), and deterministic work
        should not be left for an LLM to take apart on the fly.

    Abstraction: only one intent action is exposed to the caller, "log today", and the internal 2 steps are transparent.

    Constraints:
        - **PM only** (log_organize itself is PM-only); a non-PM is rejected outright, with no degradation.
        - a plan can only be created for the caller itself, and the aggregation scope = the caller's project group.
        - step1 lands in the caller's main session (session_key=agent:{caller}:main) and carries no task template, to avoid pollution.

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        date (str, optional): target date YYYY-MM-DD, default today (decides the daily file name and the aggregation window).
        activate (bool): whether to hand activation to plan_execute (default True). False only creates a draft plan and returns its plan_id for human review.
        instruction (str, optional): supplementary note appended to the step1 snapshot (e.g. "focus on the plan just decided").
        timeout_seconds (int, optional): timeout per step (seconds), follows config.task_timeout by default.
        self_session_id (str, optional): * [the sender's own session id] (added 2026-10-12).
            Not passed from a main-session call -> fall back to agent:{caller}:main; pass it explicitly from a dedicated
            session (the platform's [session context] already provides it).
            Passed through to the internal dispatch of plan_execute, deciding where its receipt lands.

    Returns:
        str: JSON with plan_id / steps / plan_status / handed_off.

    * Revised 2026-10-12: step 6 no longer inlines agx_activate_plan (which made the initiator's session activate it by
        hand -> step1 was delivered back into the same session -> the initiating turn and the step1 execution turn
        deadlocked each other). Instead it dispatches the plan_execute task, which does activation + lifecycle
        management in a [dedicated session]. The initiator stops at deploy.
    """
    import pathlib
    import tempfile

    try:
        agent_id = caller_agent_id

        # -- 0. validate that the caller is a PM (log_organize is PM-only; intercept early so it cannot reach step 2 and fail) --
        agent_row = execute_sql(
            "SELECT agent_role, project_id, host_id, deploy_status FROM agx_agent_registry "
            "WHERE agent_id = %s AND active_status = 1",
            (agent_id,),
        )
        if not agent_row:
            return json.dumps({"error": f"agent '{agent_id}' 不存在或已停用"}, indent=2, ensure_ascii=False)
        caller_role = (agent_row[0].get("agent_role") or "").strip()
        if caller_role != "project_manager":
            return json.dumps(
                {
                    "error": (
                        f"agx_daily_log 仅限 PM(role=project_manager)调用,"
                        f"当前 caller '{agent_id}' 角色为 '{caller_role}',已拒绝"
                    ),
                    "permission_denied": True,
                },
                indent=2,
                ensure_ascii=False,
            )
        if agent_row[0].get("deploy_status") != "deployed":
            return json.dumps(
                _deploy_gate_msg(agent_id, agent_row[0].get("deploy_status")),
                indent=2,
                ensure_ascii=False,
            )

        project_id = agent_row[0].get("project_id") or ""
        if not project_id or project_id == "system_project":
            return json.dumps(
                {"error": f"caller '{agent_id}' 未绑定业务项目 (project_id={project_id}),无法汇总日志"},
                indent=2,
                ensure_ascii=False,
            )

        # -- 1. parse the date --
        date_str = (date or "").strip()
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            return json.dumps({"error": f"date 格式非法: '{date_str}',应为 YYYY-MM-DD"}, indent=2, ensure_ascii=False)
        window = [f"{date_str} 00:00:00", f"{date_str} 23:59:59"]

        # -- 2. locate the two carrier tasks (both belong to the caller) --
        tasks = execute_sql(
            "SELECT task_id, task_labs, network_id, task_type FROM agx_task_base "
            "WHERE agent_id = %s AND active_status = 1 AND task_labs IN ('general', 'log_organize', 'plan_execute')",
            (agent_id,),
        )
        task_by_labs = {t["task_labs"]: t for t in (tasks or [])}
        if "general" not in task_by_labs:
            return json.dumps({"error": f"agent '{agent_id}' 缺少 general 任务模板(step1 载体)"}, indent=2, ensure_ascii=False)
        if "log_organize" not in task_by_labs:
            return json.dumps(
                {"error": f"agent '{agent_id}' 缺少 log_organize 任务模板(step2 载体)"},
                indent=2,
                ensure_ascii=False,
            )
        # * 2026-10-12: the activation carrier (only needed when activate=True)
        if activate and "plan_execute" not in task_by_labs:
            return json.dumps(
                {"error": f"agent '{agent_id}' 缺少 plan_execute 任务模板(激活载体)"},
                indent=2,
                ensure_ascii=False,
            )
        general_task = task_by_labs["general"]
        log_task = task_by_labs["log_organize"]
        pe_task = task_by_labs.get("plan_execute")

        # -- 3. read the task manuals (the canonical source, tasks/*.md) --
        note_desc = _load_task_desc("main_session_notes")
        if not note_desc:
            return json.dumps(
                {"error": "任务说明书 tasks/main_session_notes.md 未找到或为空"}, indent=2, ensure_ascii=False
            )
        log_desc = _load_task_desc("log_organize")
        if not log_desc:
            return json.dumps(
                {"error": "任务说明书 tasks/log_organize.md 未找到或为空"}, indent=2, ensure_ascii=False
            )

        step1_instruction = note_desc
        if instruction and instruction.strip():
            step1_instruction += f"\n\n【本次补充说明】\n{instruction.strip()}"
        step2_instruction = log_desc + (
            "\n\n---\n【本次执行参数】\n"
            + json.dumps(
                {
                    "date": date_str,
                    "project_id": project_id,
                    "window": window,
                    "scope": "project",
                    "filter_mode": "auto",
                    "output": "daily",
                },
                ensure_ascii=False,
            )
        )

        # -- 4. generate the plan JSON --
        timeout = int(timeout_seconds) if timeout_seconds else int(config.task_timeout)
        uniq = uuid.uuid4().hex[:12]
        plan_id = f"plan_daily_log_{uniq}"
        run1 = f"run_daily_log_notes_{uuid.uuid4().hex[:12]}"
        run2 = f"run_daily_log_org_{uuid.uuid4().hex[:12]}"

        plan = {
            "plan_id": plan_id,
            "project_id": project_id,
            "description": f"当日日志管线({date_str}): 主会话快照 → 日志汇总",
            "steps": [
                {
                    "run_uuid": run1,
                    "task_id": general_task["task_id"],
                    "agent_id": agent_id,
                    "instruction": step1_instruction,
                    "session_key": f"agent:{agent_id}:main",
                    "exec_name": f"主会话快照({date_str})",
                    "timeout_seconds": timeout,
                },
                {
                    "run_uuid": run2,
                    "task_id": log_task["task_id"],
                    "agent_id": agent_id,
                    "instruction": step2_instruction,
                    "prev_task": run1,
                    "exec_name": f"日志汇总({date_str})",
                    "timeout_seconds": timeout,
                },
            ],
        }

        # write a temp JSON -> _ao_plan_create (reusing the existing validation/persistence logic; the private version, not limited by the caller's L5)
        tmp_fd, tmp_path = tempfile.mkstemp(prefix="agx_daily_log_", suffix=".json")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)
            create_res = json.loads(_ao_plan_create(caller_agent_id, tmp_path))
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        if "error" in create_res:
            return json.dumps({"error": f"plan_create 失败: {create_res['error']}"}, indent=2, ensure_ascii=False)

        # -- 5. deploy --
        # * fixed 2026-10-12 (decided by Lao Feng): pass the initiator's session straight through as the steps' send_session_id.
        #   Historical defect: it was not passed -> _deploy_executions could not obtain it -> step.send_session_id=NULL
        #   -> the message's self_session_id was empty (measured run_daily_log_notes_601302fa2e57).
        #   Fallback chain: self_session_id not passed (main-session call) -> agent:{caller}:main.
        _deploy_self_sid = (self_session_id or "").strip() or f"agent:{agent_id}:main"
        deploy_res = json.loads(
            _ao_plan_deploy(caller_agent_id, plan_id=plan_id, send_session_id=_deploy_self_sid)
        )
        if "error" in deploy_res:
            return json.dumps({"error": f"plan_deploy 失败: {deploy_res['error']}", "plan_id": plan_id}, indent=2, ensure_ascii=False)

        # -- 6. optional activation --
        # * fixed 2026-10-12 (decided by Lao Feng): no longer inlines agx_activate_plan -- the initiator's session (usually the main session)
        #   activating it by hand delivers step1 back into [the same session] immediately -> the initiating turn and the step1 execution turn deadlock
        #   (measured: run_daily_log_notes_45796fe8abb4 stuck running forever; step2 never started because of prev_task).
        #   The right answer: activation + lifecycle (collecting step receipts / handling failures / finishing) is the job of the plan_execute task,
        #   which runs it in a [dedicated session] -> the initiator stops at deploy and is not occupied.
        #   self_session_id: not passed from a main-session call -> fall back to agent:{caller}:main;
        #                    passed explicitly from a dedicated session (the platform's [session context] already provides it).
        handed_off = False
        if activate:
            disp_res = json.loads(agx_dispatch_task(
                caller_agent_id=caller_agent_id,
                agent_id=agent_id,
                task_id=pe_task["task_id"],
                instruction=json.dumps({"plan_id": plan_id}, ensure_ascii=False),
                sender_id=agent_id,
                needs_reply=1,
                self_session_id=self_session_id,
                timeout_seconds=(int(timeout) * 2 if timeout else 7200),
                exec_name=f"日志计划执行({date_str})",
            ))
            if "error" in disp_res:
                return json.dumps(
                    {"error": f"plan_execute 派发失败: {disp_res['error']}", "plan_id": plan_id, "deployed": True},
                    indent=2,
                    ensure_ascii=False,
                )
            handed_off = True

        return json.dumps(
            {
                "ok": True,
                "plan_id": plan_id,
                "date": date_str,
                "project_id": project_id,
                "steps": [
                    {"run_uuid": run1, "role": "main_session_notes", "task_labs": "general", "session_key": f"agent:{agent_id}:main"},
                    {"run_uuid": run2, "role": "log_organize", "task_labs": "log_organize", "prev_task": run1},
                ],
                "plan_status": "dispatched" if handed_off else "draft",
                "handed_off": handed_off,
            },
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_daily_log", e)}, indent=2)


@mcp.tool()
@require_level(0)
def agx_create_project_pipeline(
    caller_agent_id: str,
    instruction: str,
    activate: bool = True,
    timeout_seconds: int = None,
    self_session_id: str = "",
) -> str:
    """
    [Create project pipeline] One-click, deterministic plan orchestration of "create project + clone PM + create worker + deploy + bind".

    Intent (confirmed with Lao Feng on 2026-10-13):
        Turn "create a project" from "hopping link by link" (many external dispatches; any break in the middle breaks the
        chain) into [declarative plan orchestration] -- one step to the end; done means wholly done. The structure is
        completely fixed with zero freedom, and deterministic work should not be left for an LLM to take apart, so the
        plan is orchestrated directly inside the MCP method.

    Structurally identical to agx_daily_log: the method only does "build the plan + deploy + hand off activation", and
    agx_core (in the name of the system PM) activates and drives it in a [dedicated session] with plan_execute; the
    initiator stops at dispatch (approach X: the receipt returns to the caller per self_session_id).

    * Step chain (each step dispatched to the specialized agent; "whoever should handle it, handles it"):
        step1  create the project    -> agx_projectmother   produces {project_id}
        step2  clone the project PM  -> agx_agentfather     reads step1.result for project_id
        step2.5 deploy the project PM -> host_manager       materializes the PM on the filesystem
        step3  (optional) clone worker -> agx_agentfather   reads step1/step2.result
        step4  (optional) deploy worker -> host_manager     reads step3.result
        step5  bind + accept         -> agx_projectmother

    * 2026-10-13: agent_create is abolished. Both the PM and the workers of this project go through clone
        (PM <- super_pm; worker <- agent_template, or its dedicated template when it has one).

    * Cross-step data passing (approach 3, decided by Lao Feng on 2026-10-13):
        A plan step's instruction is frozen at deploy time and does not contain earlier results. Hence the convention:
        **each step writes its structured data into the `result` field (JSON) of agx_finish_task when it finishes, and
        the downstream step reads it via agx_get_execution(prev_task).result.**
        This convention is written into the step instructions the method generates (the task templates are unchanged).

    Permission (added 2026-10-13; the method enforces it = the entrance is locked down):
        * only agx_projectmother (the dedicated project-creation duty) + global roles (workshop_manager /
        host_manager) may call it. Every other agent is rejected -- creating a project must go through projectmother.

    Args:
        caller_agent_id (str): caller agent ID (identity audit).
        instruction (str): JSON string, project creation parameters:
            {
              "project_name": "project name (required)",
              "description": "project description (required)",
              "project_abbr": "project abbreviation (required, all lowercase letters)",
              "platform": "openclaw (optional, default openclaw)",
              "source_dir": "source directory (optional)",
              "worker": [                        # optional: the list of workers to create
                 {"agent_id": "xxx_worker", "agent_name": "display name", "template_agent_id": "template", "role": "worker"}
              ]
            }
        activate (bool): whether to hand activation to plan_execute (default True). False only creates a draft plan.
        timeout_seconds (int): timeout per step (seconds), follows config.task_timeout by default.
        self_session_id (str): the caller's session id, passed through to plan_execute to decide where the receipt lands.
            Falls back to agent:{caller}:main when not passed.

    Returns:
        str: JSON with plan_id / steps / plan_status / handed_off.
    """
    import pathlib
    import tempfile

    try:
        # -- 0. validate the caller --
        agent_row = execute_sql(
            "SELECT agent_role, project_id, host_id, deploy_status FROM agx_agent_registry "
            "WHERE agent_id = %s AND active_status = 1",
            (caller_agent_id,),
        )
        if not agent_row:
            return json.dumps({"error": f"agent '{caller_agent_id}' 不存在或已停用"}, indent=2, ensure_ascii=False)
        caller_row = agent_row[0]
        caller_project = caller_row.get("project_id") or ""
        caller_host = caller_row.get("host_id") or ""
        caller_role = caller_row.get("agent_role") or ""

        # -- 0.5 permission check (Lao Feng, 2026-10-13: the method enforces it = the entrance is locked down) --
        #   creating a project is projectmother's dedicated duty. Only projectmother itself + global roles
        #   (workshop_manager / host_manager) may call it; everything else is rejected.
        _proj_deny = _check_pipeline_caller(caller_agent_id, caller_role, mode="project")
        if _proj_deny:
            return json.dumps({"error": _proj_deny, "permission_denied": True}, indent=2, ensure_ascii=False)

        # -- 1. parse the input JSON --
        try:
            params = json.loads(instruction) if isinstance(instruction, str) else (instruction or {})
        except (ValueError, TypeError):
            return json.dumps({"error": "instruction 不是合法 JSON"}, indent=2, ensure_ascii=False)
        if not isinstance(params, dict):
            return json.dumps({"error": "instruction 必须是 JSON 对象"}, indent=2, ensure_ascii=False)

        project_name = (params.get("project_name") or "").strip()
        description = (params.get("description") or "").strip()
        abbr = (params.get("project_abbr") or "").strip()
        platform = (params.get("platform") or "openclaw").strip()
        source_dir = (params.get("source_dir") or "").strip()
        custom_project_id = (params.get("project_id") or "").strip()  # * 2026-10-13: custom project ID
        workers = params.get("worker") or []
        if not isinstance(workers, list):
            workers = []

        if not project_name or not description or not abbr:
            return json.dumps(
                {"error": "缺少必填参数: project_name / description / project_abbr"},
                indent=2,
                ensure_ascii=False,
            )
        if not re.match(r"^[a-z][a-z0-9_]*$", abbr):
            return json.dumps(
                {"error": f"project_abbr 必须全小写字母/数字/下划线且以字母开头: '{abbr}'"},
                indent=2,
                ensure_ascii=False,
            )
        # * 2026-10-13: pre-validate the custom project_id (format + duplicate check) -- block it before orchestration,
        #   so the plan does not run to step1 only to discover a name clash (fail early, report early).
        if custom_project_id:
            _pid_valid, _pid_err = _validate_project_id(custom_project_id)
            if not _pid_valid:
                return json.dumps({"error": _pid_err, "invalid_project_id": True}, indent=2, ensure_ascii=False)
            _pid_dup = execute_sql(
                "SELECT COUNT(*) AS cnt FROM agx_project WHERE project_id = %s",
                (custom_project_id,),
            )
            if _pid_dup and _pid_dup[0].get("cnt", 0) > 0:
                return json.dumps(
                    {"error": f"project_id '{custom_project_id}' already exists, choose a different project_id",
                     "duplicate_project_id": True},
                    indent=2,
                    ensure_ascii=False,
                )

        # -- 2. locate the carrier tasks (dynamically, hard-coding forbidden) --
        def _find_task(agent_id, task_labs):
            _cands = _sys_names(agent_id) if agent_id in SYSTEM_AGENTS else (agent_id,)
            for _a in _cands:
                rows = execute_sql(
                    "SELECT task_id, network_id, task_type FROM agx_task_base "
                    "WHERE agent_id = %s AND task_labs = %s AND active_status = 1 LIMIT 1",
                    (_a, task_labs),
                )
                if rows:
                    return rows[0]
            return None

        core_task = _find_task(SYS_CORE, "plan_execute")
        pm_create_task = _find_task(SYS_PROJECTMOTHER, "project_create")
        clone_task = _find_task(SYS_AGENTFATHER, "agent_clone")
        # * 2026-10-13: agent_create is abolished; workers and PM both go through clone (no agent_create task needed any more).
        host_manager = _resolve_host_manager(caller_host or "test_host_mac")
        hm_agent_id = host_manager.get("agent_id") if host_manager else ""
        deploy_task = _find_task(hm_agent_id, "agent_deploy") if hm_agent_id else None

        missing = []
        if not core_task:
            missing.append(f"{SYS_CORE}.plan_execute")
        if not pm_create_task:
            missing.append(f"{SYS_PROJECTMOTHER}.project_create")
        if not clone_task:
            missing.append(f"{SYS_AGENTFATHER}.agent_clone")
        if workers:
            if not clone_task:
                missing.append(f"{SYS_AGENTFATHER}.agent_clone")
            if not deploy_task:
                missing.append(f"{hm_agent_id}.agent_deploy")
        if missing:
            return json.dumps({"error": f"缺少载体 task 模板: {missing}"}, indent=2, ensure_ascii=False)

        # -- 3. generate the run_uuid + plan skeleton --
        timeout = int(timeout_seconds) if timeout_seconds else int(config.task_timeout)
        uniq = uuid.uuid4().hex[:12]
        plan_id = f"plan_create_project_{uniq}"
        run_create = f"run_cproj_create_{uuid.uuid4().hex[:12]}"
        run_clone_pm = f"run_cproj_clonepm_{uuid.uuid4().hex[:12]}"
        run_deploy_pm = f"run_cproj_deploypm_{uuid.uuid4().hex[:12]}"

        steps = []

        # step1: create the project (projectmother)
        # * 2026-10-13: if the caller passed project_id, pass it through to step1 (agx_create_project validates + dedupes);
        #   if not passed, the field is omitted -> agx_create_project falls back to auto-generating proj_<hex>.
        _step1_params = {
            "project_name": project_name,
            "description": description,
            "project_abbr": abbr,
            "platform": platform,
            "source_dir": source_dir,
            "admin_agent_id": "",
        }
        if custom_project_id:
            _step1_params["project_id"] = custom_project_id
        step1_instr = (
            "【建项目管线 · step1/创建项目】\n"
            "调用 agx_create_project 创建新项目。参数:\n"
            + json.dumps(
                _step1_params,
                ensure_ascii=False,
                indent=2,
            )
            + "\n\n★ 结案约定: 把 agx_create_project 的返回(至少含 project_id)作为结构化数据写入 "
            "agx_finish_task 的 result 字段(JSON 字符串), 供 step2 读取。\n"
            "例如 result={\"project_id\":\"proj_xxx\",\"project_abbr\":\"" + abbr + "\"}\n"
            "summary 写人类可读结论。"
        )
        steps.append(
            {
                "run_uuid": run_create,
                "task_id": pm_create_task["task_id"],
                "agent_id": SYS_PROJECTMOTHER,
                "instruction": step1_instr,
                "exec_name": f"建项目·创建项目({abbr})",
                "timeout_seconds": timeout,
            }
        )

        # step2: clone the project PM (agentfather)
        step2_instr = (
            "【建项目管线 · step2/克隆项目PM】\n"
            "先用 agx_get_execution(\"" + run_create + "\") 读取 step1 的 result 字段, "
            "从中解析出新项目的 project_id。\n"
            "然后调用 agx_clone_agent 克隆项目专属 PM (模板 super_pm):\n"
            "  caller_agent_id=\"agx_agentfather\"\n"
            "  host_id=\"" + (caller_host or "test_host_mac") + "\"\n"
            "  template_agent_id=\"super_pm\"\n"
            "  target_project_id=<step1 拿到的 project_id>\n"
            "  new_agent_id=\"" + abbr + "_pm\"\n"
            "  agent_name=\"" + project_name + " 项目经理\"\n"
            "★ 幂等: 若 " + abbr + "_pm 已存在且 active → succeed 返回已有。\n\n"
            "★ 结案约定: 把克隆结果写入 agx_finish_task 的 result 字段(JSON), 至少含:\n"
            "  {\"project_id\":\"...\",\"pm_agent_id\":\"" + abbr + "_pm\",\"host_id\":\"...\"}\n"
            "供 step3/step4 读取。"
        )
        steps.append(
            {
                "run_uuid": run_clone_pm,
                "task_id": clone_task["task_id"],
                "agent_id": SYS_AGENTFATHER,
                "instruction": step2_instr,
                "prev_task": run_create,
                "exec_name": f"建项目·克隆PM({abbr}_pm)",
                "timeout_seconds": timeout,
            }
        )

        # step2.5: deploy the project PM (host_manager.agent_deploy)
        # * iron rule: the complete project-creation chain = create -> clone PM -> **deploy PM** -> clone worker -> deploy worker -> bind.
        # missing deploy PM leaves the PM only in the DB, not materialized on the filesystem (deploy_status stays pending).
        step_deploy_pm_instr = (
            "【建项目管线 · step2.5/部署项目PM】\n"
            "先用 agx_get_execution(\"" + run_clone_pm + "\") 读 step2.result 拿 pm_agent_id / host_id。\n"
            "然后执行 agent_deploy 把项目 PM 物化到文件系统并注册到 OpenClaw:\n"
            "  agent_id=\"" + abbr + "_pm\"\n\n"
            "★ 部署是异步的: 本 step 只完成文件落地(deploy_status=deploying)。\n"
            "  OpenClaw 注册由 ws_client 异步完成, 完成后回调到本会话(agx_deploy_callback),\n"
            "  此时再 agx_update_agent(deploy_status='deployed') 并结案(正常流程见 agent_deploy task 说明书)。\n\n"
            "★ 结案约定: result 写入 {\"project_id\":\"...\",\"pm_agent_id\":\"" + abbr + "_pm\",\"host_id\":\"...\"}"
        )
        steps.append(
            {
                "run_uuid": run_deploy_pm,
                "task_id": deploy_task["task_id"],
                "agent_id": hm_agent_id,
                "instruction": step_deploy_pm_instr,
                "prev_task": run_clone_pm,
                "exec_name": f"建项目·部署PM({abbr}_pm)",
                "timeout_seconds": timeout,
            }
        )

        # step3+: one step per worker (agentfather.agent_clone) + deploy (host.agent_deploy)
        # * 2026-10-13: agent_create abolished; workers go through clone (default template agent_template, or their dedicated one).
        prev = run_deploy_pm
        worker_runs = []
        for i, w in enumerate(workers, 1):
            if not isinstance(w, dict):
                continue
            w_id = (w.get("agent_id") or "").strip()
            w_name = (w.get("agent_name") or w_id).strip()
            w_tpl = (w.get("template_agent_id") or "").strip()
            w_role = (w.get("role") or "worker").strip()
            if not w_id:
                continue
            if not re.match(r"^[a-z][a-z0-9_]*$", w_id):
                return json.dumps(
                    {"error": f"worker agent_id 必须全小写 snake_case: '{w_id}'"},
                    indent=2,
                    ensure_ascii=False,
                )
            run_w = f"run_cproj_worker{i}_{uuid.uuid4().hex[:12]}"
            run_deploy = f"run_cproj_deploy{i}_{uuid.uuid4().hex[:12]}"
            w_instr = (
                f"【建项目管线 · step3.{i}/克隆Worker】\n"
                "先用 agx_get_execution(\"" + run_create + "\") 读 step1.result 拿 project_id。\n"
                "本步骤职责: 按你的 agent_clone 任务克隆 Worker（执行流程与约束见任务说明书 / AGENTS.md）。参数:\n"
                "  caller_agent_id=\"agx_agentfather\"\n"
                "  host_id=\"" + (caller_host or "test_host_mac") + "\"\n"
                "  template_agent_id=\"" + (w_tpl or "agent_template") + "\"\n"
                "  target_project_id=<step1 的 project_id>\n"
                "  new_agent_id=\"" + w_id + "\"\n"
                "  agent_name=\"" + w_name + "\"\n"
                "★ worker 模板: 通用 worker 用 \"agent_template\"; 有专属模板(如 book_writer)则用专属。\n"
                "★ 幂等: 已存在且 active → succeed。\n\n"
                "★ 结案约定: result 写入 {\"agent_id\":\"" + w_id + "\",\"project_id\":\"...\",\"host_id\":\"...\"}"
            )
            steps.append(
                {
                    "run_uuid": run_w,
                    "task_id": clone_task["task_id"],
                    "agent_id": SYS_AGENTFATHER,
                    "instruction": w_instr,
                    "prev_task": prev,
                    "exec_name": f"建项目·克隆Worker({w_id})",
                    "timeout_seconds": timeout,
                }
            )
            d_instr = (
                f"【建项目管线 · step4.{i}/部署Worker】\n"
                "先用 agx_get_execution(\"" + run_w + "\") 读 step3." + str(i) + ".result 拿 agent_id/host_id。\n"
                "然后执行 agent_deploy 把 Worker 物化到文件系统并注册到 OpenClaw。\n\n"
                "★ 部署是异步的: 本 step 只完成文件落地(deploy_status=deploying)。\n"
                "  OpenClaw 注册由 ws_client 异步完成, 完成后回调到本会话(agx_deploy_callback),\n"
                "  此时再 agx_update_agent(deploy_status='deployed') 并结案(正常流程见 agent_deploy task 说明书)。\n\n"
                "★ 结案约定: result 写入 {\"agent_id\":\"" + w_id + "\",\"deploy_status\":\"deploying(待回调置 deployed)\"}"
            )
            steps.append(
                {
                    "run_uuid": run_deploy,
                    "task_id": deploy_task["task_id"],
                    "agent_id": hm_agent_id,
                    "instruction": d_instr,
                    "prev_task": run_w,
                    "exec_name": f"建项目·部署Worker({w_id})",
                    "timeout_seconds": timeout,
                }
            )
            prev = run_deploy
            worker_runs.append(w_id)

        # stepN: bind + accept (projectmother)
        run_bind = f"run_cproj_bind_{uuid.uuid4().hex[:12]}"
        bind_instr = (
            "【建项目管线 · stepN/绑定与验收】\n"
            "先用 agx_get_execution(\"" + run_clone_pm + "\") 读 step2.result 拿 project_id / pm_agent_id。\n"
            "本步骤职责: 核对新项目下 Agent 绑定情况并完成验收（执行流程与约束见任务说明书 / AGENTS.md）。\n"
            "1. 核对项目下 Agent 绑定情况(agx_list_project_agents(project_id))。\n"
            "2. 如有 Agent 未绑定到新项目，则将其绑定（新 PM / Worker）。\n"
            "3. 验收: 确认项目存在、PM 已克隆、Worker(如有)已部署。\n\n"
            "★ 结案约定: result 写入 {\"project_id\":\"...\",\"pm_agent_id\":\"...\","
            "\"workers\":[...],\"verified\":true}"
        )
        steps.append(
            {
                "run_uuid": run_bind,
                "task_id": pm_create_task["task_id"],
                "agent_id": SYS_PROJECTMOTHER,
                "instruction": bind_instr,
                "prev_task": prev,
                "exec_name": f"建项目·绑定验收({abbr})",
                "timeout_seconds": timeout,
            }
        )

        # ── 4. plan JSON ──
        # project_id semantics = "the project this plan serves". The subject of the project-creation pipeline (the new project) only gets an ID at runtime,
        # so it is unknown at deploy time -> **the caller's project must not be filled in** (otherwise core reads it from agx_plan_query and writes it into the receipt as the new project ID).
        # The project-creation plan is orchestrated by the system PM (agx_core) -> belongs to system_project (the orchestrator's view); the real ownership is decided when each step executes.
        plan = {
            "plan_id": plan_id,
            "project_id": "system_project",
            "description": f"建项目管线: {project_name}({abbr}) —— 创建+克隆PM+Worker+部署+绑定",
            "steps": steps,
        }

        tmp_fd, tmp_path = tempfile.mkstemp(prefix="agx_create_project_pipeline_", suffix=".json")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)
            # * 2026-10-29: this pipeline's activator is fixed to agx_core (see the plan_execute dispatch below),
            #   so plan.host_id must follow the [activator], not the caller. Otherwise, when the caller is not on core's host,
            #   the creating watchdog wins the notification right but cannot reach core's session -> plan_completed is lost forever.
            create_res = json.loads(
                _ao_plan_create(
                    caller_agent_id, tmp_path, host_id=_resolve_send_host_id(SYS_CORE)
                )
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        if "error" in create_res:
            return json.dumps({"error": f"plan_create 失败: {create_res['error']}"}, indent=2, ensure_ascii=False)

        # -- 5. deploy --
        _deploy_self_sid = (self_session_id or "").strip() or f"agent:{caller_agent_id}:main"
        deploy_res = json.loads(
            _ao_plan_deploy(caller_agent_id, plan_id=plan_id, send_session_id=_deploy_self_sid)
        )
        if "error" in deploy_res:
            return json.dumps({"error": f"plan_deploy 失败: {deploy_res['error']}", "plan_id": plan_id}, indent=2, ensure_ascii=False)

        # -- 6. hand off activation (approach X: dispatch to core's plan_execute, the receipt returns to the caller) --
        handed_off = False
        if activate:
            disp_res = json.loads(agx_dispatch_task(
                caller_agent_id=caller_agent_id,
                agent_id=SYS_CORE,
                task_id=core_task["task_id"],
                instruction=json.dumps({"plan_id": plan_id}, ensure_ascii=False),
                sender_id=caller_agent_id,
                needs_reply=1,
                self_session_id=self_session_id,
                timeout_seconds=(int(timeout) * (len(steps) + 1) if timeout else 7200),
                exec_name=f"建项目计划执行({abbr})",
            ))
            if "error" in disp_res:
                return json.dumps(
                    {"error": f"plan_execute 派发失败: {disp_res['error']}", "plan_id": plan_id, "deployed": True},
                    indent=2,
                    ensure_ascii=False,
                )
            handed_off = True

        return json.dumps(
            {
                "ok": True,
                "plan_id": plan_id,
                "project_abbr": abbr,
                "steps": [
                    {"run_uuid": s["run_uuid"], "agent_id": s["agent_id"], "exec_name": s.get("exec_name", "")}
                    for s in steps
                ],
                "workers": worker_runs,
                "plan_status": "dispatched" if handed_off else "draft",
                "handed_off": handed_off,
            },
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_create_project_pipeline", e)}, indent=2)


@mcp.tool()
@require_level(0)
def agx_create_agent_pipeline(
    caller_agent_id: str,
    instruction: str,
    activate: bool = True,
    timeout_seconds: int = None,
    self_session_id: str = "",
) -> str:
    """
    [Create agent pipeline] One-click, deterministic plan orchestration of "clone + configure + deploy".

    Intent (confirmed with Lao Feng on 2026-10-13):
        Turn "create an agent" from "hopping link by link" into [declarative plan orchestration], structurally identical
        to agx_create_project_pipeline. The method only builds the plan + deploys + hands off activation; agx_core drives
        it entirely in a dedicated session with plan_execute, and the initiator stops at dispatch.

    * 2026-10-13: agent_create is abolished. "Create an agent" always goes through clone.
        Default template: PM -> super_pm, everything else -> agent_template; template_agent_id can override explicitly.

    * Step chain (each step dispatched to the specialized agent; "whoever should handle it, handles it"):
        step1 clone (agentfather.agent_clone, template_agent_id default as above) -> deploy (host.agent_deploy)
        - (optional) configure: step1.5 configure (agentfather.agent_configure)

    * Cross-step data passing (approach 3): a step writes its structured data into the result of agx_finish_task when it
        finishes, and the downstream step reads it via agx_get_execution(prev_task).result.

    Permission (added 2026-10-13; the method enforces it = the entrance is locked down):
        * externally visible -- only agx_agentfather (the dedicated agent-creation duty) + project_manager (a PM may
        request creation/cloning of a worker for its own project) + global roles (workshop_manager / host_manager) may
        call it. Every other agent is rejected.

    Args:
        caller_agent_id (str): caller agent ID.
        instruction (str): JSON string:
            {
              "new_agent_id": "new agent_id (optional, all lowercase; omitted -> defaults to {template}_{hex(6)})",
              "agent_name": "display name (required)",
              "target_project_id": "target project (required)",
              "host_id": "host (optional, defaults to the caller's host)",
              "template_agent_id": "template (optional, PM->super_pm / others->agent_template by default)",
              "agent_role": "worker|project_manager (optional, decides the default template)",
              "agent_type": "openclaw (optional)",
              "configure": {"base_model": "...", "system_prompt": "..."}  # optional: configure before deploy
            }
            * new_agent_id semantics (decided by Lao Feng on 2026-10-13, aligned with agx_clone_agent):
              passed  -> validate the format + explicit duplicate check (duplicates explicitly rejected);
              omitted -> defaults to {template_agent_id}_{random_hex(6)}.
              (Legacy names agent_id / create_agent_id / clone_agent_id are also accepted.)
        activate (bool): default True.
        timeout_seconds (int): timeout per step (seconds).
        self_session_id (str): the caller's session id, passed through to decide where the receipt lands.

    Returns:
        str: JSON with plan_id / steps / plan_status / handed_off.
    """
    import pathlib
    import tempfile

    try:
        agent_row = execute_sql(
            "SELECT agent_role, project_id, host_id, deploy_status FROM agx_agent_registry "
            "WHERE agent_id = %s AND active_status = 1",
            (caller_agent_id,),
        )
        if not agent_row:
            return json.dumps({"error": f"agent '{caller_agent_id}' 不存在或已停用"}, indent=2, ensure_ascii=False)
        caller_host = agent_row[0].get("host_id") or ""
        caller_role = agent_row[0].get("agent_role") or ""
        caller_project = agent_row[0].get("project_id") or ""

        # -- 0.5 permission check (Lao Feng, 2026-10-13: the method enforces it = the entrance is locked down) --
        #   creating an agent is agentfather's dedicated duty; but it must be externally visible -- a PM (project_manager)
        #   may request creation/cloning of a worker for its own project. Only agentfather itself + PMs + global roles may call it.
        _agent_deny = _check_pipeline_caller(caller_agent_id, caller_role, mode="agent")
        if _agent_deny:
            return json.dumps({"error": _agent_deny, "permission_denied": True}, indent=2, ensure_ascii=False)

        try:
            params = json.loads(instruction) if isinstance(instruction, str) else (instruction or {})
        except (ValueError, TypeError):
            return json.dumps({"error": "instruction 不是合法 JSON"}, indent=2, ensure_ascii=False)
        if not isinstance(params, dict):
            return json.dumps({"error": "instruction 必须是 JSON 对象"}, indent=2, ensure_ascii=False)

        # * 2026-10-13: agent_create is abolished. "Create an agent" always goes through clone.
        #   Default template: PM -> super_pm, others -> agent_template; template_agent_id can override explicitly.
        # * 2026-10-13 (part 2): semantic parameter names -- no more bare "agent_id".
        #   The official parameter name = new_agent_id; legacy names agent_id / create_agent_id / clone_agent_id are accepted.
        #   Semantics aligned with agx_clone_agent.new_agent_id: passed -> validate + dedupe (duplicates rejected); omitted -> default generation.
        new_agent_id = (
            params.get("new_agent_id")
            or params.get("create_agent_id")
            or params.get("clone_agent_id")
            or params.get("agent_id")
            or ""
        ).strip()
        agent_name = (params.get("agent_name") or "").strip()
        target_project = (params.get("target_project_id") or "").strip()
        host_id = (params.get("host_id") or caller_host or "test_host_mac").strip()
        agent_role = (params.get("agent_role") or "worker").strip()
        agent_type = (params.get("agent_type") or "openclaw").strip()
        configure = params.get("configure") or {}
        _default_tpl = "super_pm" if agent_role == "project_manager" else "agent_template"
        template_id = (params.get("template_agent_id") or "").strip() or _default_tpl

        # agent_name / target_project remain required; new_agent_id became optional (omitted -> default generation).
        if not agent_name or not target_project:
            return json.dumps(
                {"error": "缺少必填参数: agent_name / target_project_id"},
                indent=2,
                ensure_ascii=False,
            )

        # * new_agent_id semantics (decided by Lao Feng on 2026-10-13, aligned with agx_clone_agent):
        #   passed -> validate the format + explicit duplicate check (duplicates explicitly rejected);
        #   omitted -> defaults to {template_id}_{random_hex(6)} (identical to agx_clone_agent).
        #   Note: the pipeline layer must generate the default ID here -- because the step1 instruction is frozen at deploy time,
        #       the downstream clone cannot generate it itself (it has no access to the "empty" semantic).
        if not new_agent_id:
            _suffix = os.urandom(3).hex()  # 3 bytes -> 6 hex, consistent with _clone_agent_from_template
            new_agent_id = f"{template_id}_{_suffix}"
        elif not re.match(r"^[a-z][a-z0-9_]*$", new_agent_id):
            return json.dumps(
                {"error": f"new_agent_id 必须全小写 snake_case 且以字母开头: '{new_agent_id}'"},
                indent=2,
                ensure_ascii=False,
            )

        # explicit duplicate check: when a custom ID is passed, pre-check it (fail early, so the plan does not reach step1 before noticing a clash).
        _dup = execute_sql(
            "SELECT COUNT(*) AS cnt FROM agx_agent_registry WHERE agent_id = %s",
            (new_agent_id,),
        )
        if _dup and int(_dup[0].get("cnt") or 0) > 0:
            return json.dumps(
                {
                    "error": f"agent_id '{new_agent_id}' already exists, choose a different new_agent_id",
                    "duplicate_agent_id": True,
                },
                indent=2,
                ensure_ascii=False,
            )

        # -- 1.5 PM scope validation (2026-10-13): a PM may only create workers for [its own project] --
        #   decided by Lao Feng: creating an agent is externally visible, but a PM's permission boundary is its own project.
        #   (agentfather is not subject to this: it may create agents for any project; global roles are also unrestricted.)
        if caller_role == "project_manager":
            if target_project != caller_project:
                return json.dumps(
                    {
                        "error": (
                            f"权限不足:PM '{caller_agent_id}' 只能建【自己项目】({caller_project}) 的 worker, "
                            f"不能建其他项目({target_project}) 的 Agent。跨项目/系统级 Agent 请 dispatch 给 agx_agentfather。"
                        ),
                        "permission_denied": True,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            if agent_role == "project_manager":
                return json.dumps(
                    {
                        "error": (
                            f"权限不足:PM '{caller_agent_id}' 只能建本项目 worker, 不能建 project_manager 级 Agent。"
                            "建 PM 请 dispatch 给 agx_agentfather。"
                        ),
                        "permission_denied": True,
                    },
                    indent=2,
                    ensure_ascii=False,
                )

        if not template_id:
            return json.dumps({"error": "缺少克隆模板: 请提供 template_agent_id(PM 用 super_pm, worker 用 agent_template)"}, indent=2, ensure_ascii=False)

        # -- locate the carrier tasks --
        def _find_task(agent_id, task_labs):
            # * for a system agent, fall back to the old name automatically when the new name is given (see the same-name closure note above)
            _cands = _sys_names(agent_id) if agent_id in SYSTEM_AGENTS else (agent_id,)
            for _a in _cands:
                rows = execute_sql(
                    "SELECT task_id, network_id FROM agx_task_base "
                    "WHERE agent_id = %s AND task_labs = %s AND active_status = 1 LIMIT 1",
                    (_a, task_labs),
                )
                if rows:
                    return rows[0]
            return None

        core_task = _find_task(SYS_CORE, "plan_execute")
        father_task = _find_task(SYS_AGENTFATHER, "agent_clone")
        cfg_task = _find_task(SYS_AGENTFATHER, "agent_configure") if configure else None
        host_manager = _resolve_host_manager(host_id)
        hm_agent_id = host_manager.get("agent_id") if host_manager else ""
        deploy_task = _find_task(hm_agent_id, "agent_deploy") if hm_agent_id else None

        missing = []
        if not core_task:
            missing.append(f"{SYS_CORE}.plan_execute")
        if not father_task:
            missing.append(f"{SYS_AGENTFATHER}.agent_clone")
        if configure and not cfg_task:
            missing.append(f"{SYS_AGENTFATHER}.agent_configure")
        if not deploy_task:
            missing.append(f"{hm_agent_id}.agent_deploy")
        if missing:
            return json.dumps({"error": f"缺少载体 task 模板: {missing}"}, indent=2, ensure_ascii=False)

        # -- plan skeleton --
        timeout = int(timeout_seconds) if timeout_seconds else int(config.task_timeout)
        uniq = uuid.uuid4().hex[:12]
        plan_id = f"plan_create_agent_{uniq}"
        run_create = f"run_cagent_clone_{uuid.uuid4().hex[:12]}"

        steps = []
        s1_instr = (
            "【建Agent管线 · step1/克隆Agent】\n"
            "调用 agx_clone_agent 克隆新 Agent:\n"
            "  caller_agent_id=\"agx_agentfather\"\n"
            "  host_id=\"" + host_id + "\"\n"
            "  template_agent_id=\"" + template_id + "\"\n"
            "  target_project_id=\"" + target_project + "\"\n"
            "  new_agent_id=\"" + new_agent_id + "\"\n"
            "  agent_name=\"" + agent_name + "\"\n"
            "★ 幂等: 已存在且 active → succeed。\n\n"
            "★ 结案约定: result 写入 {\"agent_id\":\"" + new_agent_id + "\","
            "\"target_project_id\":\"" + target_project + "\",\"host_id\":\"" + host_id + "\"}"
        )
        steps.append(
            {
                "run_uuid": run_create,
                "task_id": father_task["task_id"],
                "agent_id": SYS_AGENTFATHER,
                "instruction": s1_instr,
                "exec_name": f"建Agent·克隆({new_agent_id})",
                "timeout_seconds": timeout,
            }
        )

        prev = run_create

        # optional: the configure step
        if configure and cfg_task:
            run_cfg = f"run_cagent_cfg_{uuid.uuid4().hex[:12]}"
            cfg_instr = (
                "【建Agent管线 · step2/配置Agent】\n"
                "用 agx_get_execution(\"" + run_create + "\") 读前置 result 确认 agent_id。\n"
                "调用 agx_update_agent 配置:\n"
                "  agent_id=\"" + new_agent_id + "\"\n"
                "  updates=" + json.dumps(configure, ensure_ascii=False) + "\n\n"
                "★ 结案约定: result 写入 {\"agent_id\":\"" + new_agent_id + "\",\"configured\":true}"
            )
            steps.append(
                {
                    "run_uuid": run_cfg,
                    "task_id": cfg_task["task_id"],
                    "agent_id": SYS_AGENTFATHER,
                    "instruction": cfg_instr,
                    "prev_task": prev,
                    "exec_name": f"建Agent·配置({new_agent_id})",
                    "timeout_seconds": timeout,
                }
            )
            prev = run_cfg

        # the deploy step
        run_deploy = f"run_cagent_deploy_{uuid.uuid4().hex[:12]}"
        dep_instr = (
            "【建Agent管线 · stepN/部署Agent】\n"
            "用 agx_get_execution(\"" + prev + "\") 读前置 result 拿 agent_id/host_id。\n"
            "执行 agent_deploy 把 Agent 物化到文件系统并注册到 OpenClaw:\n"
            "  agent_id=\"" + new_agent_id + "\"\n"
            "★ 部署是异步的: 本 step 只完成文件落地(deploy_status=deploying)。\n"
            "  OpenClaw 注册由 ws_client 异步完成, 完成后回调到本会话(agx_deploy_callback),\n"
            "  此时再 agx_update_agent(deploy_status='deployed') 并结案(正常流程见 agent_deploy task 说明书)。\n\n"
            "★ 结案约定: result 写入 {\"agent_id\":\"" + new_agent_id + "\",\"deploy_status\":\"deploying(待回调置 deployed)\"}"
        )
        steps.append(
            {
                "run_uuid": run_deploy,
                "task_id": deploy_task["task_id"],
                "agent_id": hm_agent_id,
                "instruction": dep_instr,
                "prev_task": prev,
                "exec_name": f"建Agent·部署({new_agent_id})",
                "timeout_seconds": timeout,
            }
        )

        plan = {
            "plan_id": plan_id,
            "project_id": target_project,
            "description": f"建Agent管线: {agent_name}({new_agent_id}) ← {template_id} → 目标项目 {target_project}",
            "steps": steps,
        }

        tmp_fd, tmp_path = tempfile.mkstemp(prefix="agx_create_agent_pipeline_", suffix=".json")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(plan, f, ensure_ascii=False, indent=2)
            # * 2026-10-29: this pipeline's activator is fixed to agx_core (see the plan_execute dispatch below),
            #   so plan.host_id must follow the [activator], not the caller. Otherwise, when the caller is not on core's host,
            #   the creating watchdog wins the notification right but cannot reach core's session -> plan_completed is lost forever.
            create_res = json.loads(
                _ao_plan_create(
                    caller_agent_id, tmp_path, host_id=_resolve_send_host_id(SYS_CORE)
                )
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        if "error" in create_res:
            return json.dumps({"error": f"plan_create 失败: {create_res['error']}"}, indent=2, ensure_ascii=False)

        _deploy_self_sid = (self_session_id or "").strip() or f"agent:{caller_agent_id}:main"
        deploy_res = json.loads(
            _ao_plan_deploy(caller_agent_id, plan_id=plan_id, send_session_id=_deploy_self_sid)
        )
        if "error" in deploy_res:
            return json.dumps({"error": f"plan_deploy 失败: {deploy_res['error']}", "plan_id": plan_id}, indent=2, ensure_ascii=False)

        handed_off = False
        if activate:
            disp_res = json.loads(agx_dispatch_task(
                caller_agent_id=caller_agent_id,
                agent_id=SYS_CORE,
                task_id=core_task["task_id"],
                instruction=json.dumps({"plan_id": plan_id}, ensure_ascii=False),
                sender_id=caller_agent_id,
                needs_reply=1,
                self_session_id=self_session_id,
                timeout_seconds=(int(timeout) * (len(steps) + 1) if timeout else 7200),
                exec_name=f"建Agent计划执行({new_agent_id})",
            ))
            if "error" in disp_res:
                return json.dumps(
                    {"error": f"plan_execute 派发失败: {disp_res['error']}", "plan_id": plan_id, "deployed": True},
                    indent=2,
                    ensure_ascii=False,
                )
            handed_off = True

        return json.dumps(
            {
                "ok": True,
                "plan_id": plan_id,
                "agent_id": new_agent_id,
                "template_agent_id": template_id,
                "steps": [
                    {"run_uuid": s["run_uuid"], "agent_id": s["agent_id"], "exec_name": s.get("exec_name", "")}
                    for s in steps
                ],
                "plan_status": "dispatched" if handed_off else "draft",
                "handed_off": handed_off,
            },
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        return json.dumps({"error": _log_exception("agx_create_agent_pipeline", e)}, indent=2)


@mcp.tool()

@require_level(0)
def agx_project_sessions(
    caller_agent_id: str,
    project_id: str = "",
    member_agent_id: str = "",
    task_labs: str = "",
    session_type: str = "",
    keyword: str = "",
    active_status: str = "1",
    limit: int = 100,
    offset: int = 0,
) -> str:
    """
    [Project sessions] Query the session list of a project's members (PM / admin view).

    Permission model (resource-level ownership check):
        - project admin (caller == project.admin_agent_id) -> may view every member session of this project
        - global roles (host_manager / workshop_manager)     -> may view
        - any other caller -> **safe degradation**: only its own sessions within the project are returned (with a note)

    When project_id is empty it is taken from the caller's own bound project_id.

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        project_id (str): target project ID (optional; when empty the caller's own project_id is used).
        member_agent_id (str): only show one member's sessions (optional).
        task_labs (str): filter by task label (optional).
        session_type (str): filter by session type (optional): main / task / oneshot. Empty = unlimited.
        keyword (str): fuzzy search on session name/note/ID (optional).
        active_status (str): active-status filter, default "1"=active only; ""=all; "0"=archived only.
        limit (int): items per page, default 100.
        offset (int): paging offset, default 0.

    Returns:
        str: JSON with project_id / scope / total / the sessions list.
    """
    try:
        resolved_project_id = project_id or _resolve_project_id_from_agent(caller_agent_id) or ""
        if not resolved_project_id:
            return json.dumps(
                {"error": f"无法确定项目:caller '{caller_agent_id}' 未绑定 project_id,请显式传入 project_id"},
                indent=2,
                ensure_ascii=False,
            )

        # -- permission check: may it view every member session of this project --
        scope = "project"
        note = ""
        _deny = _check_resource_ownership(caller_agent_id, "", resolved_project_id)
        if _deny:
            # safe degradation: return only its own sessions within this project
            scope = "self_only"
            note = f"权限降级:非项目管理员,仅可查看自己的会话。({_deny})"

        where = ["project_id = %s"]
        params = [resolved_project_id]
        if scope == "self_only":
            where.append("agent_id = %s")
            params.append(caller_agent_id)
        elif member_agent_id:
            where.append("agent_id = %s")
            params.append(member_agent_id)
        if task_labs:
            where.append("task_labs = %s")
            params.append(task_labs)
        if session_type:
            where.append("session_type = %s")
            params.append(session_type)
        if active_status != "":
            where.append("active_status = %s")
            params.append(int(active_status))
        if keyword:
            where.append("(session_name LIKE %s OR x_desc LIKE %s OR session_id LIKE %s)")
            kw = f"%{keyword}%"
            params.extend([kw, kw, kw])
        where_sql = "WHERE " + " AND ".join(where)

        total_rows = execute_sql(f"SELECT COUNT(*) AS c FROM agx_session {where_sql}", tuple(params))
        total = total_rows[0]["c"] if total_rows else 0

        rows = execute_sql(
            f"SELECT session_id, session_name, session_type, x_desc, agent_id, task_labs, "
            f"is_assistant, assistant_slug, "
            f"run_count, last_run_uuid, last_active_at, active_status, created_at "
            f"FROM agx_session {where_sql} "
            f"ORDER BY last_active_at DESC, pkid DESC LIMIT %s OFFSET %s",
            tuple(params) + (int(limit), int(offset)),
        )

        # attach a per-member summary (so a PM can see how many sessions each person has)
        summary_rows = execute_sql(
            f"SELECT agent_id, COUNT(*) AS session_count, SUM(run_count) AS run_total "
            f"FROM agx_session {where_sql} GROUP BY agent_id ORDER BY session_count DESC",
            tuple(params),
        )

        return json.dumps(
            {
                "project_id": resolved_project_id,
                "caller_agent_id": caller_agent_id,
                "scope": scope,
                "note": note,
                "total": total,
                "limit": limit,
                "offset": offset,
                "by_agent": summary_rows or [],
                "sessions": rows or [],
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_project_sessions", e)}, indent=2)


@mcp.tool()
@require_level(2)
def agx_update_session(
    caller_agent_id: str,
    session_id: str,
    updates: dict,
) -> str:
    """
    [Update session] Dynamically update selected fields of agx_session (main use: manually rename/archive/retype a session).

    Args:
        caller_agent_id (str): caller agent ID (required, identity audit and permission checks).
        session_id (str): session addressing ID.
        updates (dict): the field dictionary to update.
            Updatable fields: session_name, x_desc, session_type, active_status, avatar.
            session_type enum: main (main/long-lived) / task (task session) / oneshot (one-off temporary).
            Example: {"session_name": "marketing situation awareness - weekly report session"}
                  {"session_type": "main"}  (mark a session as the main/long-lived session)
                  {"active_status": 0}  (archive)

    Returns:
        str: success -> "Session <id> updated: [field1, field2]"
    """
    try:
        allowed = {"session_name", "x_desc", "session_type", "active_status", "avatar"}
        payload = {k: v for k, v in (updates or {}).items() if k in allowed}
        if not payload:
            return json.dumps(
                {
                    "error": (
                        "no updatable field provided "
                        "(allowed: session_name, x_desc, session_type, active_status, avatar)"
                    )
                },
                indent=2,
            )

        # session_type enum validation
        if "session_type" in payload:
            st = str(payload["session_type"] or "").strip()
            if st not in _SESSION_TYPES:
                return json.dumps(
                    {
                        "error": (
                            f"invalid session_type '{payload['session_type']}'; "
                            f"allowed: {sorted(_SESSION_TYPES)}"
                        )
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            payload["session_type"] = st

        exists = execute_sql(
            "SELECT session_id, agent_id, project_id FROM agx_session WHERE session_id = %s", (session_id,)
        )
        if not exists:
            return json.dumps({"error": f"session '{session_id}' not found"}, indent=2, ensure_ascii=False)

        # -- resource-level ownership check: a session belongs to agx_session.agent_id + project_id --
        _deny = _check_resource_ownership(
            caller_agent_id, exists[0].get("agent_id", ""), exists[0].get("project_id", "")
        )
        if _deny:
            return json.dumps({"error": _deny, "permission_denied": True}, indent=2, ensure_ascii=False)

        if "session_name" in payload:
            payload["session_name"] = _sanitize_name_field(payload["session_name"]) or "未命名会话"

        set_sql = ", ".join(f"{k} = %s" for k in payload)
        execute_sql(
            f"UPDATE agx_session SET {set_sql} WHERE session_id = %s",
            tuple(payload.values()) + (session_id,),
            commit=True,
        )
        return f"Session {session_id} updated: {list(payload.keys())}"
    except Exception as e:
        return json.dumps({"error": _log_exception("agx_update_session", e)}, indent=2)


# ============================================================

# Resource: asset overview

# ============================================================


@mcp.resource("agx://inventory/all")
def resource_inventory() -> str:
    """View an asset overview of AgentGeeX (read from the Redis cache)"""
    try:
        r = get_redis_client()

        # prefer the active entity list from the cache indexes

        def get_active_ids(idx_key: str) -> list:

            try:
                idx_type = r.type(idx_key)

                if idx_type == "zset":
                    return [item[0] for item in r.zrevrange(idx_key, 0, -1, withscores=True)]

                elif idx_type == "set":
                    return list(r.smembers(idx_key))

                return []

            except Exception as exc:
                log(f"agx_query_notices get_active_ids failed: {exc}", "WARN")
                return []

        result = {}

        # Projects - from the all index

        proj_ids = get_active_ids(IDX_PREFIX["project_all"])

        result["projects"] = []

        for pid in proj_ids[:10]:  # show at most 10
            data = _get_entity("project", pid)

            if data:
                result["projects"].append(
                    {"project_id": data.get("project_id"), "project_name": data.get("project_name")}
                )

            else:
                result["projects"].append(pid)

        # Hosts

        host_ids = get_active_ids(IDX_PREFIX["host_all"])

        result["hosts"] = []

        for hid in host_ids[:10]:
            data = _get_entity("host", hid)

            if data:
                result["hosts"].append(
                    {
                        "host_id": data.get("host_id"),
                        "host_alias": data.get("host_alias"),
                        "presence_status": data.get("presence_status"),
                    }
                )

            else:
                result["hosts"].append({"host_id": hid, "from_cache": False})

        # Agents

        agent_ids = list(r.smembers(IDX_PREFIX["agent_active"]))

        result["agents"] = []

        for aid in agent_ids[:10]:
            data = _get_entity("agent", aid)

            if data:
                result["agents"].append(
                    {
                        "agent_id": data.get("agent_id"),
                        "agent_name": data.get("agent_name"),
                        "agent_role": data.get("agent_role"),
                    }
                )

            else:
                result["agents"].append({"agent_id": aid, "from_cache": False})

        # Running tasks

        running_ids = [item[0] for item in r.zrevrange(f"{EXEC_STATUS_IDX}running", 0, -1, withscores=True)]

        result["running_executions"] = []

        for rid in running_ids[:10]:
            data = _get_entity("execution", rid)

            if data:
                result["running_executions"].append(
                    {
                        "task_run_uuid": data.get("task_run_uuid"),
                        "task_id": data.get("task_id"),
                        "run_status": data.get("run_status"),
                        "started_at": data.get("started_at"),
                    }
                )

        # Failed tasks

        failed_ids = [item[0] for item in r.zrevrange(f"{EXEC_STATUS_IDX}failed", 0, -1, withscores=True)]

        result["failed_executions"] = []

        for fid in failed_ids[:10]:
            data = _get_entity("execution", fid)

            if data:
                result["failed_executions"].append(
                    {
                        "task_run_uuid": data.get("task_run_uuid"),
                        "run_status": data.get("run_status"),
                    }
                )

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        return _log_exception("resource_inventory", e)


def watch_stdin_sync():
    """Watch stdin state synchronously in a dedicated thread, preventing the child process from becoming a zombie when the parent dies on Windows.

    The internal is_parent_alive structure is kept, using pure in-memory/handle state checks on Windows, completely eliminating pipe deadlocks.
    """
    import os
    import sys

    ppid = os.getppid()
    log(f"Started watch_stdin_sync... PID={os.getpid()}, PPID={ppid}")

    def is_parent_alive():
        if sys.platform == "win32":
            # --- Windows ultimate harmless keep-alive probe ---
            # if the parent PID has changed (e.g. it became 1 or another adopter process), the original parent is dead
            if os.getppid() != ppid:
                return False

            # further use a pure in-memory handle probe on Windows (never spawns an external process, zero pipe cost)
            try:
                import ctypes

                # OpenProcess access constant: PROCESS_QUERY_LIMITED_INFORMATION (0x1000)
                # an extremely lightweight, safe query right that does not compete for the stdio pipe
                handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, ppid)
                if handle == 0:
                    return False  # cannot get a handle, so the process no longer exists

                # check the process exit code
                exit_code = ctypes.c_ulong()
                ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                ctypes.windll.kernel32.CloseHandle(handle)

                # 259 means STILL_ACTIVE (the process is still running)
                return exit_code.value == 259
            except Exception as exc:
                # ctypes exception, degrade to the basic stdin state heuristic
                log(f"daemon_is_parent_alive ctypes failed: {exc}", "WARN")
                return not (sys.stdin is None or sys.stdin.closed)
        else:
            # --- Linux / macOS logic unchanged ---
            return os.getppid() == ppid

    try:
        while True:
            time.sleep(2)  # inspect once every 2 seconds
            if not is_parent_alive():
                log("WINDOWS MCP: Parent process detected as DEAD. Exiting...", "ERROR")
                break
    except Exception as e:
        log(f"watch_stdin_sync encountered error: {e}", "WARN")
    finally:
        # once the parent is judged dead, kill itself cleanly
        log("WINDOWS MCP RETURNED / EXITING NOW...", "ERROR")
        os._exit(0)


def _sse_endpoint() -> tuple[str, int]:
    """SSE listen address (host, port).

    Reads the mcp section of the main config agx_config.json:
        { "mcp": { "sse_host": "127.0.0.1", "sse_port": 31888 } }
    Falls back to the defaults (127.0.0.1:31888) when missing, keeping backward compatibility.
    The port can also be overridden temporarily with the MCP_SSE_PORT / MCP_SSE_HOST environment variables (handy for multiple instances).
    """
    host = "127.0.0.1"
    port = 31888
    try:
        mcp_cfg = getattr(config, "mcp", None)
        if mcp_cfg is not None:
            host = str(mcp_cfg.get("sse_host", host) or host)
            port = int(mcp_cfg.get("sse_port", port) or port)
    except Exception as e:  # noqa: BLE001
        log(f"read mcp sse config failed, fallback to {host}:{port}: {e}", "WARN")
    host = os.environ.get("MCP_SSE_HOST", host)
    port = int(os.environ.get("MCP_SSE_PORT", port))
    return host, port


def _http_endpoint() -> tuple[str, int]:
    """Streamable HTTP listen address (host, port).

    Reads the mcp section of the main config agx_config.json:
        { "mcp": { "http_host": "127.0.0.1", "http_port": 31891 } }
    Falls back to the defaults (127.0.0.1:31891) when missing.
    The port can also be overridden temporarily with the MCP_HTTP_HOST / MCP_HTTP_PORT environment variables (handy for multiple instances).

    * Why sse_port (31888) is not reused:
      31888 is already taken by webtools, so the shared MCP must listen on its own port (stated by Lao Feng on 2026-11-04).
    """
    host = "127.0.0.1"
    port = 31891
    try:
        mcp_cfg = getattr(config, "mcp", None)
        if mcp_cfg is not None:
            host = str(mcp_cfg.get("http_host", host) or host)
            port = int(mcp_cfg.get("http_port", port) or port)
    except Exception as e:  # noqa: BLE001
        log(f"read mcp http config failed, fallback to {host}:{port}: {e}", "WARN")
    host = os.environ.get("MCP_HTTP_HOST", host)
    port = int(os.environ.get("MCP_HTTP_PORT", port))
    return host, port


async def main(transport: str = "stdio"):
    """Start the MCP service.

    Args:
        transport: "stdio" (default) / "sse" (old) / "http" (Streamable HTTP, recommended).

    * Positioning of the three modes (verified against the MCP spec 2025-06-18 on 2026-11-04):
      stdio  one process per client -> N concurrent = N processes + N connection pools. Default, behavior unchanged.
      sse    the old HTTP+SSE, superseded by Streamable HTTP, kept only for compatibility.
      http   the officially recommended multi-client way; in stateless mode it supports multiple workers with no session affinity.
    """
    # start the guard thread (daemon=True, does not block main-thread exit)
    # * enabled only in stdio mode (fixed 2026-11-04):
    #   this watchdog compares getppid() with the ppid it started with and kills itself when the parent is gone.
    #   That is the correct semantics for stdio mode (we are a child of the gateway and should exit when the gateway dies).
    #   But server modes (http/sse) are the opposite: they are meant to be supervised by launchd/systemd and
    #   to outlive the shell that started them. If it stayed enabled, running under nohup would kill itself within
    #   five seconds with "Parent process detected as DEAD" (measured 2026-11-04 00:48).
    if transport == "stdio":
        monitor_thread = threading.Thread(target=watch_stdin_sync, daemon=True)
        monitor_thread.start()
    else:
        log(f"server mode ({transport}): stdin watchdog disabled "
            f"(process outlives its launcher by design)")

    if transport in ("http", "streamable-http"):
        host, port = _http_endpoint()
        # ★ stateless_http=True:
        #   each request gets its own transport context -> no session is held -> --workers N multiprocess works,
        #   and a client disconnect cannot leak a session. Every tool in this service identifies the caller through the
        #   caller_agent_id parameter (zero places read the process environment), with no process-level session state, so stateless is safe.
        #   * known side effect: _TASK_DESC_CACHE is a module-level cache, so each worker keeps its own copy and
        #     may read a stale value across workers -- handle that before going multi-worker (see docs section 10.4).
        log(f"Starting MCP with Streamable HTTP transport on {host}:{port} "
            f"(stateless_http=True, path=/mcp)")
        await mcp.run_async(transport="http", host=host, port=port,
                            stateless_http=True)
    elif transport == "sse":
        host, port = _sse_endpoint()
        log(f"Starting MCP with SSE transport on {host}:{port}")
        await mcp.run_async(transport="sse", host=host, port=port)
    else:
        log("Starting MCP with stdio transport")
        await mcp.run_async(transport="stdio")


if __name__ == "__main__":
    # parse the command-line argument: --transport=stdio|sse|http (default stdio, behavior unchanged)
    # also supports --port=NNNN / --host=ADDR to override the listen address (handy for temporarily running multiple instances)
    transport = "stdio"
    _cli_port = ""
    _cli_host = ""
    for arg in sys.argv[1:]:
        if arg.startswith("--transport="):
            transport = arg.split("=", 1)[1].strip()
        elif arg.startswith("--port="):
            _cli_port = arg.split("=", 1)[1].strip()
        elif arg.startswith("--host="):
            _cli_host = arg.split("=", 1)[1].strip()
    if _cli_port:
        os.environ["MCP_HTTP_PORT"] = _cli_port
        os.environ["MCP_SSE_PORT"] = _cli_port
    if _cli_host:
        os.environ["MCP_HTTP_HOST"] = _cli_host
        os.environ["MCP_SSE_HOST"] = _cli_host

    log("Running startup diagnostics...")
    try:
        diag = agx_check_services("MCP_STARTUP")
        mysql_info = diag.get("mysql_check_info", {})
        redis_info = diag.get("redis_check_info", {})
        log(
            f"MySQL {mysql_info.get('status', '?')}: {mysql_info.get('message', '?')} [{mysql_info.get('endpoint', '?')}]"
        )
        log(
            f"Redis {redis_info.get('status', '?')}: {redis_info.get('message', '?')} [{redis_info.get('endpoint', '?')}]"
        )
    except Exception as e:
        log(f"Startup diagnostics FAILED: {e}", "ERROR")
    asyncio.run(main(transport=transport))
