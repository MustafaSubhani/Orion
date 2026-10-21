"""Request authentication: a login session token or the service API key.

Contract: authentication is required unless `ORION_AUTH_REQUIRED` is explicitly false (local
development only). `identity_from` never raises and returns None for anything it cannot prove.
"""

from __future__ import annotations

import hmac
import os
from typing import Optional

SERVICE_ROLE = "super_user"  # the API key is an administrator credential


def expected_api_key() -> str:
    return os.getenv("ORION_API_KEY", "").strip()


def auth_required() -> bool:
    return os.getenv("ORION_AUTH_REQUIRED", "true").strip().lower() not in {"0", "false", "no", "off"}


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    value = authorization.strip()
    if value.lower().startswith("bearer "):
        value = value[7:]
    return value.strip()


def identity_from(authorization: str | None) -> Optional[dict]:
    """Identity dict `{username, role, tenant}` for a valid token or API key, else None."""
    token = bearer_token(authorization)
    if not token:
        return None
    expected = expected_api_key()
    if expected and hmac.compare_digest(token, expected):
        return {"username": "api", "role": SERVICE_ROLE, "tenant": "default"}
    try:
        from backend.database import get_session

        return get_session(token)
    except Exception:  # a broken auth store must fail closed, not crash the request
        return None


def api_key_ok(authorization: str | None) -> bool:
    """True when the caller may use the API. With auth disabled every caller is allowed."""
    if not auth_required():
        return True
    return identity_from(authorization) is not None
