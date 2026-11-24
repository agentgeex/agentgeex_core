"""Authentication: JWT issue/verify + dependency injection (corresponding to contract /auth/*).

This is currently a skeleton: the user table is not wired in and login uses placeholder validation.
Integration point: replace `authenticate()` with a lookup against the AgentGeeX user table / an external IdP.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from fastapi import Depends, Header, HTTPException

from .config import settings

# ── minimal JWT (HS256), zero third-party dependencies ────────────────────


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(msg: bytes) -> str:
    sig = hmac.new(settings.jwt_secret.encode(), msg, hashlib.sha256).digest()
    return _b64e(sig)


def create_token(claims: dict[str, Any]) -> str:
    header = {"alg": settings.jwt_alg, "typ": "JWT"}
    payload = dict(claims)
    payload.setdefault("exp", int(time.time()) + settings.jwt_expire_minutes * 60)
    seg = f"{_b64e(json.dumps(header).encode())}.{_b64e(json.dumps(payload).encode())}"
    return f"{seg}.{_sign(seg.encode())}"


def decode_token(token: str) -> dict[str, Any]:
    try:
        h, p, s = token.split(".")
    except ValueError as e:
        raise HTTPException(401, {"error": {"code": "UNAUTHENTICATED", "message": "token 格式非法"}}) from e
    if not hmac.compare_digest(_sign(f"{h}.{p}".encode()), s):
        raise HTTPException(401, {"error": {"code": "UNAUTHENTICATED", "message": "token 签名无效"}})
    claims = json.loads(_b64d(p))
    if claims.get("exp", 0) < int(time.time()):
        raise HTTPException(401, {"error": {"code": "UNAUTHENTICATED", "message": "登录已过期，请重新登录"}})
    return claims


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """Validate credentials -> user information (a **person**).

    TODO: wire in the real user source. Currently a placeholder: any non-empty username/password passes and
          returns a demo user, to ease frontend integration.

    [2026-10-15 Feng's correction: an account = a person, not bound to an agent (important)]
        The web page is **for people**. AgentGeeX = super-individual: one person commands all virtual agents.
        The login account is a **person** (operator). Once inside, the person may interact with **every agent**,
        but the **[sender agent identity] is chosen per operation** and is never bound at login.

        (The old implementation wrote username into the agent_id claim as if it were an agent identity = backwards:
         it made "a person work for the agents". Corrected now.)

        The login name is only the **[audit subject actor]** (for the trace), no longer a default for any agent identity.
"""
    if not username or not password:
        return None
    return {
        "id": f"u_{username}",
        "name": username,      # the person (login account) -- audit subject only
        "role": "operator",    # the person's system role (super terminal: may operate every agent)
        "tenant_id": "tenant_demo",
        # ★ agent_id is no longer written: a person is not an agent. The sender agent identity is passed explicitly **per action**.
    }


def current_user(authorization: str = Header(default="")) -> dict[str, Any]:
    """Dependency: parse the current user from Authorization: Bearer <JWT>."""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, {"error": {"code": "UNAUTHENTICATED", "message": "缺少 Bearer token"}})
    return decode_token(authorization.removeprefix("Bearer ").strip())


CurrentUser = Depends(current_user)


def caller_agent_id_of(user: dict[str, Any]) -> str:
    """[Semantics retired] Old: take the **[sender agent identity]** default from the logged-in user.

    [2026-10-15 Feng's correction] an account = a person, not bound to an agent.
    The sender agent identity should be passed explicitly by the caller
    (e.g. the caller_agent_id parameter of actions.run_task / quick_cmd.run).

    This function is kept only for old call sites and always returns an empty string (= "no sender agent specified").
    New code should use actor_of(user) to get the **[audit subject (person)]**.
"""
    _ = user
    return ""


def actor_of(user: dict[str, Any]) -> str:
    """Extract the **[audit subject actor]** from the current user (the logged-in person; trace only, **never an authorisation binding**)."""
    return str(user.get("name") or user.get("sub") or "")
