"""Optional authentication: login and current role."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from echos.api import auth as A
from echos.api.models import AuthOut, LoginIn

from echos.api.deps import ERR

router = APIRouter(prefix="/api", responses=ERR)


@router.post("/auth/login", response_model=AuthOut, tags=["auth"])
def login(request: Request, body: LoginIn):
    """Exchange a token for a role. The token is then sent as `Authorization: Bearer <token>`. Failed attempts are rate-limited."""
    who = request.client.host if request.client else "?"
    if not request.app.state.auth_on:
        return {"auth_enabled": False, "role": "operator"}
    if request.app.state.limiter.blocked(who):
        raise HTTPException(429, "too many failed logins: wait a minute")
    role = A.role_of(body.token)
    if role is None:
        request.app.state.limiter.fail(who)
        raise HTTPException(401, "wrong token")
    return {"auth_enabled": True, "role": role}

@router.get("/auth/me", response_model=AuthOut, tags=["auth"])
def me(request: Request):
    """Is authentication on, and which role does this request have? (role is null when not logged in)"""
    if not request.app.state.auth_on:
        return {"auth_enabled": False, "role": "operator"}
    tok = request.headers.get("authorization", "")
    return {"auth_enabled": True, "role": A.role_of(tok[7:] if tok.lower().startswith("bearer ") else None)}
