"""FastAPI dependencies: who is calling, and are they allowed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header

from backend.errors import ApiError
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
