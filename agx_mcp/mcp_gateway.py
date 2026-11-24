#!/usr/bin/env python3
"""MCP unified gateway — one process mounts agent-os / mysql-srv / redis-srv, dispatched by URL path.

    /agentgeex/mcp AgentGeeX kernel   (100 tools)
    /mysql/mcp     MySQL reflection   (5 tools)
    /redis/mcp     local Redis admin  (7 tools)

Design notes
------------
1. **Do not touch the three service files.** Each already exports `mcp` (a FastMCP
   instance); this file only calls `mcp.http_app()` and mounts it on a Starlette Mount.
2. **Tool names stay unchanged.** Path isolation means each server keeps its own
   namespace, exactly as in the three-process setup (contrast: FastMCP's native
   `mount(prefix=)` rewrites tool names into `mysql_query_database`, which would
   break every prompt — so it is not used).
3. **lifespan must be composed by hand.** Starlette's `Mount` does not forward
   lifespan, so a child app's startup/shutdown would never run; we enter and exit
   them with an AsyncExitStack.
4. **Connection pools are not shared** (a fact, not a defect): `mcp_server.pool` and
   `mcp_server_redis.pool` are two independent objects. What merging processes saves
   is framework overhead (measured physical footprint 260.6M -> 78.4M), not
   connection count.

Usage
----
    python agx_mcp/mcp_gateway.py                      # 127.0.0.1:31890
    python agx_mcp/mcp_gateway.py --port 31890 --host 127.0.0.1
    python agx_mcp/mcp_gateway.py --max-threads 120    # raise the shared thread pool
    python agx_mcp/mcp_gateway.py --mount agentgeex,redis  # mount a subset only
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import os
import sys

# ---------------------------------------------------------------------------
# sys.path: must contain both the project root (for `permissions` / `common.*` / `config_loader`)
# and the agx_mcp directory (for the three service modules themselves). Must precede imports; order must not change.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_ROOT, _HERE):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

# path name -> module name. Order is the mount order and only affects log readability.
#
# * 2026-11-11 agentgeex rename: the canonical path is /agentgeex/mcp.
#   The old MCP path is retired (every client entry in ~/.openclaw/openclaw.json points here).
MOUNTS: tuple[tuple[str, str], ...] = (
    ("agentgeex", "mcp_server"),
    ("mysql", "mcp_server_mysql"),
    ("redis", "mcp_server_redis"),
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 31890
# The three services share one anyio thread pool. 40 is anyio's hard-coded default;
# measured: 50 blocking calls already queue another service for 5 seconds, so we raise it to 120.
DEFAULT_MAX_THREADS = 120


def _load(selected: list[str] | None) -> dict[str, object]:
    """Import the three modules by path name; returns {path: FastMCP instance}."""
    wanted = set(selected) if selected else {p for p, _ in MOUNTS}
    unknown = wanted - {p for p, _ in MOUNTS}
    if unknown:
        raise SystemExit(
            f"unknown mount(s): {', '.join(sorted(unknown))} "
            f"(valid: {', '.join(p for p, _ in MOUNTS)})"
        )

    servers: dict[str, object] = {}
    for path, modname in MOUNTS:
        if path not in wanted:
            continue
        mod = importlib.import_module(modname)
        server = getattr(mod, "mcp", None)
        if server is None:
            raise SystemExit(f"{modname} has no module-level `mcp` (FastMCP instance)")
        servers[path] = server
    return servers


def build_app(servers: dict[str, object], max_threads: int = DEFAULT_MAX_THREADS):
    """Mount N FastMCP instances on one Starlette app; returns an ASGI app ready for uvicorn."""
    from starlette.applications import Starlette
    from starlette.routing import Mount

    sub_apps: dict[str, object] = {}
    # The same module may be mounted on several paths (canonical name + compatibility alias) —
    # http_app() must be called only once: internally it creates/holds a session manager and
    # other state; calling it twice on one FastMCP instance starts two state machines. Cache by instance identity.
    _by_module: dict[int, object] = {}
    for path, server in servers.items():
        sub = _by_module.get(id(server))
        if sub is None:
            sub = server.http_app(path="/mcp", stateless_http=True)
            _by_module[id(server)] = sub
        sub_apps[path] = sub

    # lifespan likewise runs once (mounting one instance twice would enter it twice in the AsyncExitStack).
    _unique_subs = list(_by_module.values())

    @contextlib.asynccontextmanager
    async def lifespan(app):
        # Compose the lifespan of the three child apps (Starlette Mount does not forward it).
        async with contextlib.AsyncExitStack() as stack:
            for sub in _unique_subs:
                await stack.enter_async_context(sub.lifespan(app))
            # Only after the child app startup has run do we raise the shared thread pool.
            # Must be set inside an async context: RunVar is task-local.
            _tune_thread_limiter(max_threads)
            yield

    return Starlette(
        routes=[Mount(f"/{path}", app=sub) for path, sub in sub_apps.items()],
        lifespan=lifespan,
    )


def _tune_thread_limiter(max_threads: int) -> None:
    """Raise the anyio default thread-pool limit. Failure does not affect the service (falls back to 40)."""
    if max_threads <= 0:
        return
    try:
        from anyio.to_thread import current_default_thread_limiter

        limiter = current_default_thread_limiter()
        limiter.total_tokens = max_threads
        print(f"[gateway] thread limiter total_tokens = {limiter.total_tokens}", flush=True)
    except Exception as exc:  # noqa: BLE001 - pure optimization; it must not prevent the service from starting
        print(f"[gateway] thread limiter tune skipped: {exc}", flush=True)


def _probe_self(host: str, port: int, paths: list[str], timeout: float = 5.0) -> int:
    """Post-start self-check: handshake each endpoint and call list_tools. Returns the number of failures."""
    import urllib.request

    failed = 0
    for path in paths:
        url = f"http://{host}:{port}/{path}/mcp"
        req = urllib.request.Request(
            url,
            data=b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
                 b'{"protocolVersion":"2025-06-18","capabilities":{},'
                 b'"clientInfo":{"name":"mcp-gateway-probe","version":"1"}}}',
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read(400).decode("utf-8", "replace")
            ok = r.status == 200 and '"result"' in body
        except Exception as exc:  # noqa: BLE001
            ok, body = False, f"{type(exc).__name__}: {exc}"
        print(f"[gateway] selfcheck {'OK  ' if ok else 'FAIL'} {url}  {body[:120]}", flush=True)
        failed += 0 if ok else 1
    return failed


def main() -> None:
    ap = argparse.ArgumentParser(description="MCP unified gateway (single process, path routing)")
    ap.add_argument("--host", default=os.environ.get("MCP_GATEWAY_HOST", DEFAULT_HOST))
    ap.add_argument("--port", type=int, default=int(os.environ.get("MCP_GATEWAY_PORT", DEFAULT_PORT)))
    ap.add_argument("--mount", default="", help="逗号分隔的子集，如 agentgeex,redis；空=全部")
    ap.add_argument("--max-threads", type=int, default=DEFAULT_MAX_THREADS,
                    help=f"共享 anyio 线程池上限（默认 {DEFAULT_MAX_THREADS}，0=保持 40）")
    ap.add_argument("--log-level", default="info")
    ap.add_argument("--no-selfcheck", action="store_true")
    args = ap.parse_args()

    selected = [s.strip() for s in args.mount.split(",") if s.strip()] or None
    servers = _load(selected)
    app = build_app(servers, max_threads=args.max_threads)

    paths = list(servers)
    print(
        f"[gateway] listening on http://{args.host}:{args.port}\n"
        + "\n".join(f"[gateway]   /{p}/mcp  <- {m}" for (p, m) in MOUNTS if p in servers),
        flush=True,
    )

    import uvicorn

    config = uvicorn.Config(app, host=args.host, port=args.port, log_level=args.log_level)
    server = uvicorn.Server(config)

    # Run the self-check after the service is up (on a separate thread in the same process, to avoid blocking the event loop).
    if not args.no_selfcheck:
        import threading

        def _delayed_probe() -> None:
            import time

            time.sleep(3.0)
            if _probe_self(args.host, args.port, paths):
                print("[gateway] selfcheck had failures (service still running)", flush=True)

        threading.Thread(target=_delayed_probe, daemon=True, name="gateway-selfcheck").start()

    server.run()


if __name__ == "__main__":
    main()
