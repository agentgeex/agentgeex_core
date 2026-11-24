"""Win3 BFF -- FastAPI application entry point.

Start:
    cd agentgeex/webtools/server
    ../../.venv/bin/python -m uvicorn app.main:app --reload --port 8090

Or (dependencies already installed into the repo-root .venv):
    ../../.venv/bin/uvicorn app.main:app --reload --port 8090

Architecture: browser SPA -> [this BFF] -> MCP(streamable-http, 127.0.0.1:31890/agentgeex/mcp) -> MySQL / Redis
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import asr
from .config import settings
from .routers import actions, audio, auth, resources, system
from .routers import mcp as mcp_router
from .routers import quick_cmd as quick_cmd_router
from .routers import quick_entry as quick_entry_router
from .routers import settings as settings_router

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("win3.bff")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Process lifecycle: start the background ASR probe and shut it down on exit.

    ★ The probe is **resident** and does not follow requests: only by always probing can "the service is back"
      be discovered by the system first, rather than by someone clicking once more and failing.
"""
    task = asyncio.create_task(asr.health_loop(), name="asr-health")
    log.info("BFF 就绪；ASR 探活间隔 %.0fs，主机 %s", settings.asr_health_interval,
             ", ".join(h["id"] for h in asr.service_settings.hosts()))
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


app = FastAPI(
    lifespan=lifespan,
    title="Win3 BFF",
    version="0.1.0",
    description="Win3 商业控制台后端聚合层（Backend-for-Frontend）",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Total-Count"],
)


@app.exception_handler(HTTPException := __import__("fastapi").HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Unified error body: { error: { code, message, details } } (contract section 0)."""
    detail = exc.detail
    if isinstance(detail, dict) and "error" in detail:
        body = detail
    else:
        body = {"error": {"code": f"HTTP_{exc.status_code}", "message": str(detail), "details": {}}}
    return JSONResponse(status_code=exc.status_code, content=body, headers=getattr(exc, "headers", None))


# contract version prefix
API_PREFIX = "/v1"
app.include_router(system.router)
app.include_router(auth.router, prefix=API_PREFIX)
app.include_router(mcp_router.router, prefix=API_PREFIX)
app.include_router(resources.router, prefix=API_PREFIX)
app.include_router(actions.router, prefix=API_PREFIX)
app.include_router(quick_cmd_router.router, prefix=API_PREFIX)
app.include_router(quick_entry_router.router, prefix=API_PREFIX)
app.include_router(audio.router, prefix=API_PREFIX)
app.include_router(settings_router.router, prefix=API_PREFIX)


@app.get("/")
def root() -> dict:
    return {"service": "win3-bff", "version": "0.1.0", "docs": "/docs", "api": API_PREFIX}
