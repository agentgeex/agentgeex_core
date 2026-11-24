"""AgentGeeX kernel adapter layer -- the single funnel for BFF -> kernel access.

[2026-10-05 Feng's decision: speak the MCP protocol (SSE), fully same-origin with MCP]
This module **no longer imports the kernel source in-process** (the former `from agx_mcp import agx_core /
mcp_server` approach is retired). Reason: `agx_core.py` and `mcp_server.py` each defined a duplicate set of
cache functions (get_redis_client / _sync_entity / _publish_event), i.e. two Redis connection pools and two
copies of cache logic -> guaranteed inconsistency.

It now goes through `app.mcp_client` to the shared MCP gateway (since 2026-11-04
127.0.0.1:31890/agentgeex/mcp, streamable-http) and calls the tool functions registered by `mcp_server` over
JSON-RPC -- permission / cache / connection pool fully same-origin with MCP.

Division of responsibility:
    frontend -> speaks REST only to this BFF;
    BFF      -> reaches MCP (streamable-http) via this module / mcp_client -> MySQL / Redis.
"""

from __future__ import annotations

import logging
from typing import Any

from . import mcp_client
from .config import AGX_ROOT, mysql_settings, redis_settings, settings
from .mcp_client import McpToolError, McpUnavailable

log = logging.getLogger("win3.bff.kernel")

__all__ = [
    "AGX_ROOT",
    "McpToolError",
    "McpUnavailable",
    "call_tool",
    "execute",
    "has_mcp_tool",
    "list_tools",
    "query",
    "reset_mysql_cfg",
    "redis_client",
    "ping",
    "config_path",
    "settings",
]

# ══════════════════════════════════════════════════════════════════
# Read channel: direct MySQL / Redis (Feng's decision of 2026-10-05)
#
#   MCP is positioned as the [operation] channel (writing data / dispatching / cache sync).
#   Pure [reads] need not detour through MCP: direct MySQL/Redis is faster, has more fields and allows free aggregation,
#   and reads have no side effects, so there is no write-consistency issue with the MCP cache.
#
#   Therefore: the BFF builds its own independent MySQL connection pool (read-only), separate from MCP's pool.
#   Warning: writes / operations must still go through `call_tool` (MCP SSE), to reuse its cache write chain and permissions.
# ══════════════════════════════════════════════════════════════════
import pymysql  # noqa: E402
from pymysql.cursors import DictCursor  # noqa: E402

_mysql_cfg: dict[str, Any] | None = None


def _load_mysql_cfg() -> dict[str, Any]:
    """MySQL connection parameters -- the single source is `app.config.mysql_settings()`.

    [2026-11-13 Feng: "env first, json as fallback"]
    This used to open(AGX_ROOT/"agx_config.json") directly, take the mysql section and cache it forever.
    After credentials moved to `~/.agx/agx_creds.env` (the json no longer stores accounts/passwords):
      - with the json moved away -> fell back to the default root/empty password -> the whole site returned 500
        (Access denied; this actually happened);
      - a uvicorn `--reload` restart clears the cache, so the failure appears suddenly at the next reload.
    It now goes through config.mysql_settings(): inject the credentials file -> read the json -> override with
    environment variables, using **the same environment variable names** as the kernel's `config_loader.py`,
    so there is no second reading logic.

    ★ The in-process cache is kept (no repeated file reads per request), but `reset_mysql_cfg()` is provided
      so a config change takes effect without a restart -- the original had only a module-level variable, so
      a change still required a restart.
"""
    global _mysql_cfg  # noqa: PLW0603
    if _mysql_cfg is not None:
        return _mysql_cfg
    try:
        cfg = dict(mysql_settings())
    except Exception as e:  # noqa: BLE001
        log.warning("load mysql cfg failed: %s", e)
        cfg = {}
    _mysql_cfg = cfg
    if not cfg.get("user"):
        log.warning(
            "MySQL 账号为空 —— 凭据文件可能没配（~/.agx/agx_creds.env 的 AGX_DB_USER/AGX_DB_PASS）"
        )
    return _mysql_cfg


def reset_mysql_cfg() -> None:
    """Discard the cached connection parameters (call after changing credentials/config; no process restart needed)."""
    global _mysql_cfg  # noqa: PLW0603
    _mysql_cfg = None


def query(sql: str, params: tuple | None = None) -> list[dict[str, Any]]:
    """Read-only SQL query -> list[dict] (direct MySQL, short connection, read-only).

    SELECT only; BFF-side writes go through MCP (call_tool) and must not write through here.
"""
    cfg = _load_mysql_cfg()
    conn = pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset=cfg.get("charset") or "utf8mb4",
        cursorclass=DictCursor,
        connect_timeout=5,
        read_timeout=30,
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(r) for r in (cur.fetchall() or [])]
    finally:
        conn.close()


def execute(sql: str, params: tuple | None = None) -> int:
    """Write SQL (INSERT/UPDATE/DELETE) -> affected row count (direct MySQL, short connection).

    Warning: this is an exception channel (2026-10-14). The BFF's normal writes/operations go through MCP
    (call_tool) to reuse its cache chain and permissions. But **a few BFF-owned support tables** (such as the
    quick-command table agx_quick_cmd, for which no MCP tool is registered today) have no MCP channel; to keep
    the feature closed-loop this provides a direct write entry. BFF-owned tables only; core entities
    (agent/task/execution ...) must still go through call_tool.

    Returns lastrowid (INSERT) or rowcount (UPDATE/DELETE).
"""
    cfg = _load_mysql_cfg()
    conn = pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset=cfg.get("charset") or "utf8mb4",
        cursorclass=DictCursor,
        connect_timeout=5,
        read_timeout=30,
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return int(cur.lastrowid or cur.rowcount or 0)
    finally:
        conn.close()


def redis_client():
    """Direct Redis (for reading cache; cache writes are still handled by MCP's _sync_entity)."""
    import redis as _redis

    # [2026-11-13] same source as MySQL: credentials file -> json -> environment variables (AGX_REDIS_*).
    # The original read the json only; after the credentials moved it silently connected to a "passwordless Redis", showing an empty cache rather than an error.
    try:
        cfg = dict(redis_settings())
    except Exception as e:  # noqa: BLE001
        log.warning("load redis cfg failed: %s", e)
        cfg = {}
    return _redis.Redis(
        host=cfg.get("host", "127.0.0.1"),
        port=int(cfg.get("port", 6379)),
        db=int(cfg.get("db", 0)),
        username=cfg.get("username") or None,
        password=cfg.get("password") or None,
        decode_responses=True,
        socket_connect_timeout=5,
    )

# ══════════════════════════════════════════════════════════════════
# MCP tool calls (the single kernel exit point, over the SSE protocol)
# ══════════════════════════════════════════════════════════════════
def call_tool(tool: str, caller_agent_id: str | None = None, **arguments: Any) -> Any:
    """Call an MCP tool (over the SSE protocol, including its @require_level permission and cache chain).

    Whenever the BFF needs to read data / write data / send a message, it passes the parameters through this
    function to a tool already implemented in MCP (such as `agx_list_sessions` / `agx_upsert_agent` /
    `agx_dispatch_task`).

    Args:
        tool: MCP tool name.
        caller_agent_id: caller identity (passed through by the frontend; defaults to the system identity).
        **arguments: the tool's other parameters.

    Raises:
        McpToolError: the tool returned "Error: ..." (including insufficient permission).
        McpUnavailable: the SSE MCP instance is unreachable.
"""
    return mcp_client.call_tool(tool, caller_agent_id=caller_agent_id, **arguments)


def list_tools() -> list[str]:
    """List all tools registered on the SSE MCP instance (startup capability self-check)."""
    return mcp_client.list_tools()


def has_mcp_tool(tool_name: str) -> bool:
    """Whether MCP has registered that tool."""
    try:
        return tool_name in list_tools()
    except Exception:  # noqa: BLE001
        return False


# ══════════════════════════════════════════════════════════════════
# Liveness / metadata
# ══════════════════════════════════════════════════════════════════
def ping() -> dict[str, Any]:
    """Kernel liveness probe: MCP (SSE) + the MySQL/Redis behind it (through agx_check_services).

    Return structure:
        {
          "mcp_sse": bool,            # whether the MCP channel is up (key name is historical)
          "url": "...",               # SSE endpoint
          "tool_count": int,          # number of registered tools (when up)
          "mysql": bool, "redis": bool,  # kernel self-check results (through the MCP tool)
          "error": "..."              # failure reason (optional)
        }
"""
    # the key name mcp_sse is kept for old callers (/readyz and the frontend's MCP self-check dialog both read it)
    out: dict[str, Any] = {
        "mcp_sse": False,
        "url": settings.mcp_url,
        "transport": settings.mcp_transport,
    }
    try:
        out["tool_count"] = len(list_tools())
        out["mcp_sse"] = True
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)

    # direct MySQL liveness probe (read channel)
    try:
        rows = query("SELECT 1 AS ok")
        out["mysql"] = bool(rows and rows[0].get("ok") == 1)
    except Exception as e:  # noqa: BLE001
        out["mysql"] = False
        out["mysql_error"] = str(e)

    # direct Redis liveness probe (read channel)
    try:
        out["redis"] = bool(redis_client().ping())
    except Exception as e:  # noqa: BLE001
        out["redis"] = False
        out["redis_error"] = str(e)

    return out


def config_path() -> str:
    """Path of the kernel's main config file (for audit).

    ★ 2026-11-24: report the file actually in use (the install root on a standard deploy), not the
      naive `AGX_ROOT/agx_config.json` -- the old answer pointed at a file that does not exist and
      sent anyone debugging an empty console to the wrong place.
    """
    from .config import agx_config_path

    hit = agx_config_path()
    return str(hit) if hit is not None else str(AGX_ROOT / "agx_config.json")
