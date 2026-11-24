# MCP Permission System

## Overview

A decorator-based L0–L7 tiered permission model that validates the caller's identity at the entry point of each MCP tool function, preventing Agents from operating outside their authority.

## Files

| File | Responsibility |
|------|------|
| `agx_mcp/permissions.py` | Permission matrix definition + the `@require_level(N)` decorator + the `set_lookup_fn()` injection hook |
| `agx_mcp/mcp_server.py` | **87 MCP tools** (`@mcp.tool()`) + **88 `@require_level`** decorators |

> **Correction 2026-10-24**: the directory used to be named `mcp/` and is now `agx_mcp/` (to avoid clashing with the `mcp` SDK package from pip).
> The tool count grew from 53 in the first version to **87** (counting method: actual `@mcp.tool()` declarations, excluding commented-out lines).
> Note: `@require_level` occurs 1 more time than the tool count — one internal function (not an MCP tool) also carries a level decorator.

## L0-L7 permission levels

> ⚠️ **Major correction 2026-10-24**: the "typical operations" column of the original table **badly disagreed with the code**
> (e.g. `dispatch_task` was listed as L2 but is actually L5; `create_task` was listed as L5 but is actually L2).
> The table below has been recalibrated against the code (`grep -oE '@require_level\([0-9]+\)' agx_mcp/mcp_server.py`).

| Level | Tools | Actual meaning | Typical tools (measured from code) |
|:----:|:---:|------|----------|
| L0 | 36 | Read-only | `agx_check_services`, `agx_generate_uuid`, `agx_get_*`, `agx_list_*` |
| L1 | 5 | Basic writes | `agx_write_notice`, `agx_skill_contribute`, `agx_skill_feedback`, `agx_get/set_session_model` |
| L2 | 3 | **Task definition** | `agx_create_task`, `agx_update_task`, `agx_update_session` |
| L3 | 15 | **Execution scheduling** | `agx_schedule_task`, `agx_activate_task`, `agx_continue_task`, `agx_finish_task`, `agx_assistant_create/profile` |
| L4 | 6 | **Agent lifecycle** | `agx_upsert_agent`, `agx_clone_agent`, `agx_create/update_agent_group` |
| L5 | 18 | **Projects + cross-Agent dispatch** | `agx_create_project`, `agx_dispatch_task`, `agx_plan_create`, `agx_bind_agent_to_project` |
| L6 | 3 | Host management | `agx_register_host`, `agx_update_host`, `agx_deploy_agent` |
| L7 | 2 | System management | `agx_cache_clear`, `agx_cache_rebuild` |

(88 `@require_level` in total; per-level counts measured on 2026-10-24)

### Key differences from the old document (easy to misread)

| Tool | Old doc said | Actual |
|------|:---:|:---:|
| `agx_dispatch_task` | L2 | **L5** |
| `agx_create_task` | L5 | **L2** |
| `agx_update_task` | L5 | **L2** |
| `agx_schedule_task` | L2 | **L3** |

**The rule: L2 governs "task template definition", L5 governs "commanding other Agents".**
If the command does not leave the Agent, L2 suffices; as soon as you need to direct someone else's work (dispatch/plan), it must be L5.

## Role allow-sets

The levels each role may access are defined explicitly, and are **not derived from the numeric ordering**:

| Role | Allowed Levels | Key restriction |
|------|:---:|------|
| `workshop_manager` | {0,1,2,3,4,5,6,7} | full permissions |
| `agent_manager` | {0,1,2,3,4} |  no L5+ |
| `project_manager` | {0,1,2,3,5} |  **no L4** (cannot create/clone Agents) |
| `host_manager` | {0,1,2,3,6} |  no L4, L5 |
| `worker` | {0,1,2,3} | read/write + scheduling only |
| `unknown` | {0} | unidentified caller, read-only |

### Key design decision: project_manager ≠ L4

`project_manager` (e.g. `agx_projectmother`) has the numeric level 5, but explicitly excludes L4. Deriving permissions from the numeric ordering would let 5 ≥ 4 through. Hence **explicit allow-sets** instead of numeric comparison.

## Permission lookup precedence

Lookup order in `_get_agent_allowed_levels(agent_id)`:

1. **`AGENT_ROLE_OVERRIDE`** — exact override (for testing/debugging)
2. **`_SYSTEM_AGENT_ROLES`** — hardcoded fallback for the 4 core Agents
3. **`_lookup_fn(agent_id)`** — externally injected lookup (Redis → MySQL on miss)
4. **default `"unknown"`** → L0 only

### Hardcoded fallback for the 4 core Agents

```python
_SYSTEM_AGENT_ROLES = {
    "agx_core":          "workshop_manager",
    "agx_agentfather":   "agent_manager",
    "agx_projectmother": "project_manager",
    "agx_host":          "host_manager",
}
```

**Why hardcode?** The previous version of `permissions.py` queried MySQL directly through `executeSql`, but in the child process (the MCP process launched by mcporter) the module import path was wrong and the query failed. After switching to injection, the Redis cache may miss on a cold start; hardcoding guarantees the 4 core Agents are always identified correctly, independent of external-service availability.

## Decorator pattern

```python
@mcp.tool()
@require_level(4)  # L4: Agent lifecycle
def agx_upsert_agent(caller_agent_id: str, ...):
    ...
```

- `@require_level(N)` extracts `caller_agent_id` from `args[0]`
- `caller_agent_id="main_session"` is allowed straight through (a human calling via MCP)
- An unauthorised call returns a JSON error string rather than raising, keeping MCP protocol compatibility

> ⚠️ **The decorator order cannot be reversed**: `@mcp.tool()` must be on top and `@require_level(N)` below it.
> Reversed, FastMCP registers it as an ordinary function instead of a tool.

## Dependency injection

`permissions.py` does not depend on MySQL/Redis directly; it receives an injected lookup function through `set_lookup_fn()`:

```python
# in mcp_server.py at startup
from permissions import set_lookup_fn   # note: imported directly inside agx_mcp, without the package prefix
set_lookup_fn(_lookup_agent_role)
```

The reason: module import paths are unstable in a child process, so the lookup function is injected by `mcp_server.py` (which already has the correct path).

## Legacy ALLOWLIST cleanup

All 6 legacy hardcoded permission checks in `mcp_server.py` have been removed:

```python
#  removed
if caller_agent_id not in AGENT_CLONE_ALLOWLIST:
    raise PermissionError(...)

#  replaced by
@require_level(4)
def agx_clone_agent(caller_agent_id, ...):
```

## Internal call paths

| Caller | Callee | Mechanism | Status |
|--------|--------|------|:--:|
| Watchdog | `_ao_dispatch_task` / `_ao_activate_task` | `agx_core.py` (bypasses the decorators) |  safe |
| Daemon | `query_mysql` | plain SQL, does not call MCP functions |  safe |
| Inside MCP | `agx_generate_uuid()` | 3 sites, an L0 tool function |  no risk |

## Change history

| Date | Change |
|------|------|
| 2026-06-04 | First version: L0-L7 matrix + decorators + 53 functions injected + all legacy ALLOWLIST checks removed |
| 2026-10-24 | **Recalibrated the L0-L7 table against the code** (the original table had L2/L3/L5 meanings completely wrong);
directory `mcp/` → `agx_mcp/`; tool count 53 → **87 MCP tools / 88 require_level** |
