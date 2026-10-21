"""Administrator endpoints: runtime settings and users. Every route requires `super_user`."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend import database as db
from backend.deps import Identity, in_scope, require_admin, require_platform_admin
from backend.errors import ApiError
from backend.schemas import SettingOut, SettingValue, UserCreate, UserOut, UserRoleChange
from harness import settings_store, tenancy

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


class QueueDecision(BaseModel):
    action: str = Field(pattern="^(dismiss|repair)$")


@router.get("/quality/queue")
def quality_queue(
    status: Optional[str] = Query(default="pending", pattern="^(pending|dismissed|resolved)$"), admin: Identity = Depends(require_admin)
) -> list[dict[str, Any]]:
    """Answers the background judge found weak. Review them; nothing is patched without a decision here."""
    from harness import quality

    return quality.list_queue(tenant=admin.tenant, status=status)


@router.post("/quality/queue/{item_id}")
def quality_decide(item_id: str, body: QueueDecision, admin: Identity = Depends(require_admin)) -> dict[str, Any]:
    """`dismiss` closes the item. `repair` asks the Repair Agent to patch the Lab agent, behind the contract guard."""
    from harness import quality

    if body.action == "dismiss":
        if not quality.decide(item_id, tenant=admin.tenant, status="dismissed", actor=admin.username):
            raise ApiError(404, "not_found", "Unknown or already decided queue item")
        return {"id": item_id, "status": "dismissed"}
    try:
        return quality.apply_repair(item_id, tenant=admin.tenant, actor=admin.username)
    except LookupError as exc:
        raise ApiError(404, "not_found", str(exc)) from exc
    except PermissionError as exc:
        raise ApiError(409, "not_repairable", str(exc)) from exc


@router.get("/mcp")
def mcp_status(_: Identity = Depends(require_platform_admin)) -> dict[str, Any]:
    """Configured MCP servers, their connection state and the tools they expose."""
    from harness import mcp_client

    info = mcp_client.bridge_status()
    for server in mcp_client.bridge().servers():
        row = next((s for s in info["servers"] if s["name"] == server.config.name), None)
        if row is not None:
            row["tool_names"] = [mcp_client.verb_name(server.config.name, t.name) for t in server.exposed_tools()]
    return info


@router.post("/mcp/reload")
def mcp_reload(_: Identity = Depends(require_platform_admin)) -> dict[str, Any]:
    """Re-read config/mcp_servers.json, reconnect every server and refresh the registered tools."""
    from harness import mcp_client

    return mcp_client.connect_all()


@router.get("/connectors")
def list_connectors(_: Identity = Depends(require_platform_admin)) -> list[dict[str, Any]]:
    from harness.connectors import registry

    return [c.describe() for c in registry().all()]


@router.post("/connectors/reload")
def reload_connectors(_: Identity = Depends(require_platform_admin)) -> dict[str, Any]:
    from harness import connectors

    return {"loaded": connectors.reload()}


@router.post("/connectors/{connector_id}/test")
def test_connector(connector_id: str, _: Identity = Depends(require_platform_admin)) -> dict[str, Any]:
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


def _tenant_of(row: dict[str, Any]) -> str:
    return row.get("tenant") or db.DEFAULT_TENANT


def _managed_user(username: str, admin: Identity) -> dict[str, Any]:
    """The user row, or 404. A user in another tenant is indistinguishable from one that does not exist."""
    row = next((u for u in db.get_all_users() if u["username"] == username), None)
    if row is None or not in_scope(admin, _tenant_of(row)):
        raise ApiError(404, "not_found", "Unknown user")
    return row


@router.get("/users", response_model=list[UserOut])
def list_users(admin: Identity = Depends(require_admin)) -> list[UserOut]:
    """A tenant administrator sees their own tenant; a platform administrator sees every tenant."""
    return [_user_out(u) for u in db.get_all_users() if in_scope(admin, _tenant_of(u))]


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, admin: Identity = Depends(require_admin)) -> UserOut:
    tenant = body.tenant or admin.tenant
    if not in_scope(admin, tenant):
        raise ApiError(403, "forbidden", "Only a platform administrator can create users in another tenant")
    problem = db.validate_password_strength(body.password)
    if problem:
        raise ApiError(400, "weak_credentials", problem)
    try:
        created = db.create_user(body.username, body.password, body.role, tenant)
    except tenancy.TenantError as exc:
        raise ApiError(400, "invalid_tenant", str(exc)) from exc
    if not created:
        raise ApiError(409, "username_taken", "Username already exists")
    return _user_out(next(u for u in db.get_all_users() if u["username"] == body.username))


@router.put("/users/{username}/role", response_model=UserOut)
def change_role(username: str, body: UserRoleChange, admin: Identity = Depends(require_admin)) -> UserOut:
    _managed_user(username, admin)
    if username == admin.username and body.role != db.ROLE_SUPER_USER:
        raise ApiError(409, "self_demotion", "You cannot remove your own administrator role")
    if not db.set_user_role(username, body.role):
        raise ApiError(404, "not_found", "Unknown user")
    return _user_out(next(u for u in db.get_all_users() if u["username"] == username))


@router.delete("/users/{username}")
def deactivate_user(username: str, admin: Identity = Depends(require_admin)) -> dict[str, Any]:
    """Deactivate (not delete) a user and revoke their sessions, keeping the audit trail intact."""
    _managed_user(username, admin)
    if username == admin.username:
        raise ApiError(409, "self_deactivation", "You cannot deactivate your own account")
    if not db.set_user_active(username, False):
        raise ApiError(404, "not_found", "Unknown user")
    return {"ok": True, "username": username}
