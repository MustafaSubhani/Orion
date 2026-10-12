"""API key and login-session gate. Empty ORION_API_KEY keeps local scripts open."""

from __future__ import annotations

import hmac
import os
from typing import Optional


def expected_api_key() -> str:
    return os.getenv("ORION_API_KEY", "").strip()


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    return authorization.replace("Bearer ", "").strip()


def api_key_ok(authorization: str | None) -> bool:
    expected = expected_api_key()
    if not expected:
        return True
    token = bearer_token(authorization)
    if not token:
        return False
    if hmac.compare_digest(token, expected):
        return True
    try:
        from backend.database import get_session

        return get_session(token) is not None
    except Exception:
        return False


def identity_from(authorization: str | None) -> Optional[dict]:
    token = bearer_token(authorization)
    if not token:
        return None
    expected = expected_api_key()
    if expected and hmac.compare_digest(token, expected):
        return {"username": "api", "role": None}
    try:
        from backend.database import get_session

        return get_session(token)
    except Exception:
        return None
