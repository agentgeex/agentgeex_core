"""Health checks + service self-check (no authentication required)."""

from __future__ import annotations

from fastapi import APIRouter

from .. import kernel
from ..config import settings

router = APIRouter(tags=["system"])


@router.get("/healthz")
def healthz() -> dict:
    """Lightweight liveness: 200 as long as the process is alive."""
    return {"status": "ok", "service": "win3-bff"}


@router.get("/readyz")
def readyz() -> dict:
    """Readiness probe: checks AgentGeeX kernel (MySQL + Redis) connectivity."""
    k = kernel.ping()
    return {
        "status": "ok" if (k.get("mysql") and k.get("redis")) else "degraded",
        "kernel": k,
        "config_path": kernel.config_path(),
        "cors_origins": settings.cors_origins,
    }
