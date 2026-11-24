"""mcp_client.py -- BFF -> AgentGeeX MCP protocol client (both SSE / Streamable HTTP transports).

[2026-10-05 Feng's decision: the BFF speaks the MCP protocol, fully same-origin with MCP]
The original implementation (in-process `from agx_mcp import agx_core / mcp_server` in kernel.py) had a
legacy problem: `agx_core.py` and `mcp_server.py` **each defined a duplicate set of cache functions
(get_redis_client / _sync_entity / _publish_event)**, so there were two Redis connection pools and two
camp of cache logic maintained separately, and changing one place made the same data get written
inconsistently along two paths.

So the BFF no longer imports the kernel in-process; it uses the **official mcp SDK** to connect to a
standalone MCP instance and calls the tool functions registered by `mcp_server` over JSON-RPC --
permission / cache / connection pool are **fully same-origin** with MCP, side-stepping the legacy duplication.

★ 2026-11-04 (Feng: unify MCP as a shared service):
  the endpoint changed from "the SSE instance the BFF started (31888)" to "the shared gateway
  `http://127.0.0.1:31890/agentgeex/mcp` (PM2-managed, streamable-http)".
  transport is **derived from the URL** (trailing /sse -> SSE, otherwise streamable-http);
  no separate switch -- one fact should not have two fields that can contradict each other.

Design notes:
- **Async**: the official SDK is async while BFF routes are sync, so a resident event loop plus
  `run_coroutine_threadsafe` provides a synchronous facade (`call_tool`).
- **Long connection**: one resident, reused SSE session (no reconnect per request); it reconnects
  automatically when the connection dies.
- **Audit identity**: `caller_agent_id` is **passed in by the frontend** (Feng, 2026-10-05: leave it to the
  frontend) and the BFF passes it through as the MCP tool's first argument. Falls back to the system identity.
- **Result parsing**: MCP tools return `TextContent` (usually a JSON string or "Error: ..."), parsed
  uniformly into a Python object; an `"Error: ..."` prefix becomes `McpToolError`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import threading
from typing import Any

from .config import settings

log = logging.getLogger("win3.bff.mcp")

# Default caller identity (used when the caller passes no caller_agent_id).
#
# [2026-10-31 Feng] the old value was `"win3_bff"`, which **on inspection is a dead value**:
#   `SELECT ... FROM agx_agent_registry WHERE agent_id='win3_bff'` finds nobody
#   (it is not among the 90 agents in the DB). If the fallback ever triggered, the kernel would treat it as an unknown identity -> insufficient permission,
#   surfacing as "an inexplicable PERMISSION_DENIED" instead of "a parameter was missing".
#
# Unified to `agx_core` (workshop_manager, L0-L7) -- the same identity as
# `realdata.EDITOR_AGENT_ID`, i.e. the established submit identity for all console writes.
# A literal is written here rather than an import: realdata -> kernel -> mcp_client would form a cycle.
#
# Impact: only calls that omit caller_agent_id use it. All live call_tool sites
#   (actions / quick_cmd / realdata / routers.mcp) pass caller explicitly, so behaviour is unchanged;
#   this only decides whose name is used "if one is ever missed" -- from "nobody" to "the console identity".
# ★ 2026-11-11: the built-in core agent now carries the agx_ prefix -> default agx_core.
#   To switch submit identity (e.g. the core agent is not called agx_core on some machine),
#   just override AGX_EDITOR_AGENT_ID; no code change or re-release needed.
DEFAULT_CALLER = (os.environ.get("AGX_EDITOR_AGENT_ID") or "agx_core").strip()


class McpToolError(RuntimeError):
    """Raised when an MCP tool returns "Error: ..." (including insufficient permission)."""


class McpUnavailable(RuntimeError):
    """MCP service unreachable / connection failed."""


# ══════════════════════════════════════════════════════════════════
# Async core: resident event loop + persistent SSE session
# ══════════════════════════════════════════════════════════════════
class _McpClient:
    """The official mcp SDK client (async), managing one resident session; reconnects automatically on disconnect.

    The two transports differ only in "how the connection is made"; once established both are
    `(read, write)` streams + `ClientSession`, and callers above are identical.
"""

    def __init__(self, url: str, transport: str = "streamable-http") -> None:
        self._url = url
        self._transport = transport
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: Any = None
        self._ready = threading.Event()
        self._startup_error: str = ""
        self._lock = threading.Lock()

    # ---- connect (the only transport-specific part) --------------------------
    @contextlib.asynccontextmanager
    async def _connect(self):
        """Build a session by transport and yield it for callers above.

        SSE:             sse_client(url)            -> (read, write)
        Streamable HTTP: streamablehttp_client(url) -> (read, write, get_session_id)

        The latter has an extra session_id getter which we do not use (stateless; every call is an
        independent POST), but all three return values must be received or unpacking fails.
"""
        from mcp import ClientSession

        if self._transport == "sse":
            from mcp.client.sse import sse_client

            ctx = sse_client(self._url)
        else:
            from mcp.client.streamable_http import streamablehttp_client

            ctx = streamablehttp_client(self._url)

        async with ctx as conn:
            read, write = conn[0], conn[1]
            async with ClientSession(read, write) as session:
                yield session

    # ---- event loop thread -------------------------------------------
    def start(self) -> None:
        """Start the resident event loop in a background thread and establish the session (idempotent)."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._ready.clear()
            self._startup_error = ""
            self._thread = threading.Thread(target=self._run_loop, name="mcp-sse", daemon=True)
            self._thread.start()
        # Wait for the connection to be ready (10s max)
        self._ready.wait(timeout=10)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except BaseException as e:  # noqa: BLE001
            # BaseException: anyio TaskGroup may raise CancelledError wrapped in ExceptionGroup
            self._session = None
            self._startup_error = str(e)
            self._ready.set()
            log.error("mcp loop exited (%s): %r", self._transport, e)
        finally:
            with contextlib.suppress(Exception):
                loop.close()

    async def _serve(self) -> None:
        """Establish the session and keep it alive; reconnect on disconnect (never exit)."""
        while True:
            try:
                async with self._connect() as session:
                    await session.initialize()
                    self._session = session
                    self._startup_error = ""
                    self._ready.set()
                    log.info("mcp connected (%s): %s", self._transport, self._url)
                    # Keepalive: idle-wait until the peer closes / the connection dies.
                    # Note: connection death is raised at the next await (including anyio ExceptionGroup),
                    # caught by the outer except BaseException and then reconnected.
                    while True:
                        await asyncio.sleep(5)
                        if self._session is None:
                            raise RuntimeError("session invalidated")
            except BaseException as e:  # noqa: BLE001
                # Whatever the exception (including CancelledError/ExceptionGroup), reconnect and never exit
                self._session = None
                self._ready.clear()  # clear the ready flag: makes call() block waiting for the reconnect
                if not self._startup_error:
                    self._startup_error = str(e)
                log.warning("mcp session lost (%s), retrying in 3s: %r", self._transport, e)
                await asyncio.sleep(3)

    # ---- synchronous facade ------------------------------------------
    @property
    def ready(self) -> bool:
        # judged by "do we hold a session right now" (True again once a reconnect succeeds)
        return self._session is not None

    def call(self, tool: str, arguments: dict[str, Any], timeout: float = 60.0) -> Any:
        """Call an MCP tool synchronously -> the parsed Python object."""
        self.start()
        if not self._session or not self._loop:
            # not ready: wait for the reconnect (3s backoff + handshake) and try once more
            self._ready.wait(timeout=15)
        if not self._session or not self._loop:
            raise McpUnavailable(f"mcp unavailable ({self._transport} {self._url}): {self._startup_error or 'not ready'}")

        async def _do() -> Any:
            res = await self._session.call_tool(tool, arguments)
            return _parse_tool_result(tool, res)

        fut = asyncio.run_coroutine_threadsafe(_do(), self._loop)
        try:
            return fut.result(timeout=timeout)
        except TimeoutError as e:
            fut.cancel()
            raise McpUnavailable(f"mcp tool {tool} timeout after {timeout}s") from e
        except Exception as e:  # noqa: BLE001
            # connection-class exception -> mark the session lost, triggering a background reconnect; the next call retries automatically
            if not isinstance(e, McpToolError) and "Error:" not in str(e):
                self._session = None
            raise

    def list_tools(self, timeout: float = 20.0) -> list[str]:
        self.start()
        if not self._session or not self._loop:
            self._ready.wait(timeout=15)
        if not self._session or not self._loop:
            raise McpUnavailable(f"mcp session not ready ({self._transport} {self._url})")

        async def _do() -> list[str]:
            res = await self._session.list_tools()
            return [t.name for t in res.tools]

        fut = asyncio.run_coroutine_threadsafe(_do(), self._loop)
        try:
            return fut.result(timeout=timeout)
        except Exception:  # noqa: BLE001
            self._session = None
            raise


def _parse_tool_result(tool: str, res: Any) -> Any:
    """Parse an MCP CallToolResult into a Python object.

    - take the first TextContent.text;
    - if it has an "Error: ..." prefix -> raise McpToolError (including insufficient permission);
    - try json.loads; on failure return the string as-is.
"""
    texts: list[str] = []
    for c in getattr(res, "content", []) or []:
        t = getattr(c, "text", None)
        if t is not None:
            texts.append(t)
    raw = "\n".join(texts).strip()
    if raw.startswith("Error:"):
        raise McpToolError(raw)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


# ══════════════════════════════════════════════════════════════════
# singleton facade
# ══════════════════════════════════════════════════════════════════
_client: _McpClient | None = None
_client_lock = threading.Lock()


def _get_client() -> _McpClient:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = _McpClient(settings.mcp_url, settings.mcp_transport)
    return _client


def call_tool(tool: str, caller_agent_id: str | None = None, **arguments: Any) -> Any:
    """Call an MCP tool synchronously (the unified entry point).

    Args:
        tool: MCP tool name (e.g. "agx_list_sessions").
        caller_agent_id: caller identity (passed through by the frontend; defaults to DEFAULT_CALLER).
            Injected as the tool's first parameter `caller_agent_id` (required by the MCP security spec).
        **arguments: the tool's other parameters.

    Returns:
        The parsed Python object (dict / list / str).

    Raises:
        McpToolError: the tool returned "Error: ..." (including insufficient permission).
        McpUnavailable: the SSE instance is unreachable.
"""
    if not caller_agent_id:
        # The fallback must not be silent: a missing identity is a code defect and must be visible in the log,
        # otherwise it runs silently under a super identity and nobody notices the parameter was omitted.
        log.warning("call_tool(%s) 未传 caller_agent_id，回退 %s", tool, DEFAULT_CALLER)
    args = {"caller_agent_id": caller_agent_id or DEFAULT_CALLER, **arguments}
    return _get_client().call(tool, args)


def list_tools() -> list[str]:
    """List all tools registered on the SSE MCP instance (capability self-check)."""
    return _get_client().list_tools()


def ping() -> dict[str, Any]:
    """Liveness probe: MCP connectivity (the key name mcp_sse is kept for old callers)."""
    out: dict[str, Any] = {
        "mcp_sse": False,
        "url": settings.mcp_url,
        "transport": settings.mcp_transport,
    }
    try:
        names = list_tools()
        out["mcp_sse"] = True
        out["tool_count"] = len(names)
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)
    return out
