"""Login, signup, logout and identity."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, Request

from backend import database as db
from backend.deps import Identity, current_identity
from backend.errors import ApiError
from backend.ratelimit import login_throttle
from backend.schemas import AuthRequest, IdentityOut, SessionOut
from harness.auth import bearer_token
from harness.settings_store import effective

router = APIRouter(prefix="/auth", tags=["Auth"])


def _client_key(request: Request, username: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{username.lower()}@{host}"


@router.post("/login", response_model=SessionOut)
def login(body: AuthRequest, request: Request) -> SessionOut:
    username = body.username.strip()
    key = _client_key(request, username)
    login_throttle.check(key)
    if not username or not db.verify_user(username, body.password):
        login_throttle.fail(key)
        raise ApiError(401, "invalid_credentials", "Invalid username or password")
    login_throttle.success(key)
    role = db.get_user_role(username) or db.ROLE_LIMITED
    tenant = db.get_user_tenant(username)
    token = db.create_session(username, role, tenant)
    return SessionOut(token=token, username=username, role=role, tenant=tenant)


@router.post("/signup", response_model=SessionOut)
def signup(body: AuthRequest) -> SessionOut:
    if not effective("auth.open_signup"):
        raise ApiError(403, "signup_disabled", "Self-service signup is disabled. Ask an administrator for an account.")
    username = body.username.strip()
    problem = db.validate_password_strength(body.password)
    if len(username) < 2 or problem:
        raise ApiError(400, "weak_credentials", problem or "Username is too short")
    if not db.create_user(username, body.password, db.ROLE_LIMITED):
        raise ApiError(409, "username_taken", "Username already exists")
    token = db.create_session(username, db.ROLE_LIMITED, db.DEFAULT_TENANT)
    return SessionOut(token=token, username=username, role=db.ROLE_LIMITED, tenant=db.DEFAULT_TENANT)


@router.get("/me", response_model=IdentityOut)
def me(identity: Identity = Depends(current_identity)) -> IdentityOut:
    return IdentityOut(username=identity.username, role=identity.role, tenant=identity.tenant)


@router.post("/logout")
def logout(authorization: Optional[str] = Header(default=None)) -> dict:
    db.delete_session(bearer_token(authorization))
    return {"ok": True}
