"""Administrator endpoints: runtime settings and users. Every route requires `super_user`."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend import database as db
from backend.deps import Identity, require_admin
from backend.errors import ApiError
from backend.schemas import SettingOut, SettingValue, UserCreate, UserOut, UserRoleChange
from harness import settings_store

router = APIRouter(prefix="/admin", tags=["Admin"])


@router.get("/settings", response_model=list[SettingOut])
def list_settings(admin: Identity = Depends(require_admin)) -> list[dict[str, Any]]:
    return settings_store.describe(admin.tenant)


@router.put("/settings/{key}", response_model=SettingOut)
def put_setting(key: str, body: SettingValue, admin: Identity = Depends(require_admin)) -> dict[str, Any]:
    try:
        settings_store.set_value(key, body.value, admin.username, admin.tenant)
    except settings_store.SettingsError as exc:
        raise ApiError(400 if "unknown setting" not in str(exc) else 404, "invalid_setting", str(exc)) from exc
    return next(r for r in settings_store.describe(admin.tenant) if r["key"] == key)


@router.delete("/settings/{key}", response_model=SettingOut)
def reset_setting(key: str, admin: Identity = Depends(require_admin)) -> dict[str, Any]:
    try:
        settings_store.reset(key, admin.username, admin.tenant)
    except settings_store.SettingsError as exc:
        raise ApiError(404, "invalid_setting", str(exc)) from exc
    return next(r for r in settings_store.describe(admin.tenant) if r["key"] == key)


@router.get("/connectors")
def list_connectors(_: Identity = Depends(require_admin)) -> list[dict[str, Any]]:
    from harness.connectors import registry

    return [c.describe() for c in registry().all()]


@router.post("/connectors/reload")
def reload_connectors(_: Identity = Depends(require_admin)) -> dict[str, Any]:
    from harness import connectors

    return {"loaded": connectors.reload()}


@router.post("/connectors/{connector_id}/test")
def test_connector(connector_id: str, _: Identity = Depends(require_admin)) -> dict[str, Any]:
    from harness.connectors import registry

    connector = registry().get(connector_id)
    if connector is None:
        raise ApiError(404, "not_found", f"Unknown connector '{connector_id}'")
    return connector.test()


def _user_out(row: dict[str, Any]) -> UserOut:
    return UserOut(
        username=row["username"],
        role=row.get("role") or db.ROLE_LIMITED,
        tenant=row.get("tenant") or db.DEFAULT_TENANT,
        is_active=bool(row.get("is_active", 1)),
        created_at=str(row.get("created_at") or ""),
    )


@router.get("/users", response_model=list[UserOut])
def list_users(admin: Identity = Depends(require_admin)) -> list[UserOut]:
    return [_user_out(u) for u in db.get_all_users() if (u.get("tenant") or db.DEFAULT_TENANT) == admin.tenant]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, admin: Identity = Depends(require_admin)) -> UserOut:
    problem = db.validate_password_strength(body.password)
    if problem:
        raise ApiError(400, "weak_credentials", problem)
    if not db.create_user(body.username, body.password, body.role, admin.tenant):
        raise ApiError(409, "username_taken", "Username already exists")
    return _user_out(next(u for u in db.get_all_users() if u["username"] == body.username))


@router.put("/users/{username}/role", response_model=UserOut)
def change_role(username: str, body: UserRoleChange, admin: Identity = Depends(require_admin)) -> UserOut:
    if username == admin.username and body.role != db.ROLE_SUPER_USER:
        raise ApiError(409, "self_demotion", "You cannot remove your own administrator role")
    if not db.set_user_role(username, body.role):
        raise ApiError(404, "not_found", "Unknown user")
    return _user_out(next(u for u in db.get_all_users() if u["username"] == username))


@router.delete("/users/{username}")
def deactivate_user(username: str, admin: Identity = Depends(require_admin)) -> dict[str, Any]:
    """Deactivate (not delete) a user and revoke their sessions, keeping the audit trail intact."""
    if username == admin.username:
        raise ApiError(409, "self_deactivation", "You cannot deactivate your own account")
    if not db.set_user_active(username, False):
        raise ApiError(404, "not_found", "Unknown user")
    return {"ok": True, "username": username}
