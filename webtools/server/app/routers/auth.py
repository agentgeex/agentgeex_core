"""Auth routes -- correspond to contract section 3.1 /auth/*."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ..auth import authenticate, create_token, decode_token

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str
    agent_id: str | None = None  # [2026-10-05] the audit identity passed in by the frontend (defaults to username)


@router.post("/login")
def login(body: LoginBody) -> dict:
    user = authenticate(body.username, body.password)
    if not user:
        raise HTTPException(
            401,
            {"error": {"code": "INVALID_CREDENTIALS", "message": "用户名或密码错误"}},
        )
    # [2026-10-05 Feng: audit identity is left to the frontend] the frontend may pass agent_id explicitly; defaults to username
    if body.agent_id:
        user = {**user, "agent_id": body.agent_id}
    claims = {
        "sub": user["id"],
        "name": user["name"],
        "role": user["role"],
        "tenant_id": user["tenant_id"],
        "agent_id": user.get("agent_id"),
    }
    return {"token": create_token(claims), "user": user}


@router.post("/logout", status_code=204)
def logout() -> None:
    """Stateless JWT: logout is implemented by the frontend discarding the token."""
    return None


@router.get("/me")
def me(authorization: str = Header(default="")) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, {"error": {"code": "UNAUTHENTICATED", "message": "缺少 Bearer token"}})
    c = decode_token(authorization.removeprefix("Bearer ").strip())
    return {
        "id": c["sub"],
        "name": c.get("name"),
        "role": c.get("role"),
        "tenant_id": c.get("tenant_id"),
        "agent_id": c.get("agent_id"),
    }
