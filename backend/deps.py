"""FastAPI dependencies: who is calling, and are they allowed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header

from backend.errors import ApiError
from harness import tenancy
from harness.auth import auth_required, identity_from


@dataclass(frozen=True)
class Identity:
    username: str
    role: str
    tenant: str = "default"

    @property
    def is_admin(self) -> bool:
        return self.role == "super_user"

    @property
    def is_platform_admin(self) -> bool:
        """An administrator of the operator tenant. Only they manage deployment-wide resources and other tenants."""
        return self.is_admin and tenancy.is_platform(self.tenant)

    @property
    def harness_role(self) -> Optional[str]:
        """Role passed to the harness. Only `limited` narrows tool and column access."""
        return "limited" if self.role == "limited" else None


LOCAL_IDENTITY = Identity(username="local", role="super_user")


def current_identity(authorization: Optional[str] = Header(default=None)) -> Identity:
    """The authenticated caller. With `ORION_AUTH_REQUIRED=false` an anonymous caller is the local admin."""
    ident = identity_from(authorization)
    if ident is not None:
        return Identity(
            username=str(ident.get("username") or "unknown"),
            role=str(ident.get("role") or "limited"),
            tenant=str(ident.get("tenant") or "default"),
        )
    if not auth_required():
        return LOCAL_IDENTITY
    raise ApiError(401, "unauthenticated", "Missing or invalid credentials")


def require_admin(identity: Identity = Depends(current_identity)) -> Identity:
    if not identity.is_admin:
        raise ApiError(403, "forbidden", "Administrator access required")
    return identity


def require_platform_admin(identity: Identity = Depends(current_identity)) -> Identity:
    """Deployment-wide resources (MCP servers, connectors, other tenants' users) are not a tenant admin's to touch."""
    if not identity.is_platform_admin:
        raise ApiError(403, "forbidden", "Platform administrator access required")
    return identity


def can_see_agent(identity: Identity, card: object) -> bool:
    """Built-in agents are shared. A custom agent belongs to one tenant; only the platform admin sees across."""
    return identity.is_platform_admin or tenancy.card_visible(card, identity.tenant)


def in_scope(identity: Identity, tenant: str) -> bool:
    """Whether `identity` may see or manage a resource owned by `tenant`."""
    return identity.is_platform_admin or identity.tenant == tenant
