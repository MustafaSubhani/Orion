"""Optional OpenID Connect sign-in: exchange an IdP-issued ID token for an Orion session.

The browser (or any client) completes the provider's login flow and posts the resulting ID token to
`POST /auth/oidc`. This module verifies it and maps its claims to a local identity. It is off unless
`ORION_OIDC_ISSUER` and `ORION_OIDC_CLIENT_ID` are set.

Contract:
- Only asymmetric signatures are accepted (RS/PS/ES/EdDSA). `none` and HMAC algorithms are refused, so a
  token can never be forged with a shared secret or without a signature.
- `iss`, `aud`, `exp` and `iat`-style standard checks are enforced. `sub` is required.
- Keys come from the provider's JWKS (`ORION_OIDC_JWKS_URL`, else OIDC discovery).
- The token's tenant claim must be a valid tenant slug; an invalid value rejects the login, it is never
  silently mapped to the default tenant.
- Accounts are provisioned just in time with no usable password. A token can never sign in as, or
  change, a local password account, and it can never move an account to another tenant.
- Roles come from the IdP on every login: the admin value in the roles claim means `super_user`,
  anything else is `limited`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from harness import tenancy

ASYMMETRIC = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA")
_USERNAME = re.compile(r"^[A-Za-z0-9_.@-]{2,64}$")


class OidcError(Exception):
    """A sign-in problem that is safe to show the caller (never contains the token or key material)."""

    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.safe_message = message
        self.status = status


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    audience: str
    jwks_url: str = ""
    username_claim: str = "preferred_username"
    tenant_claim: str = "tenant"
    role_claim: str = "roles"
    admin_values: tuple[str, ...] = ("orion-admin",)
    default_tenant: str = tenancy.DEFAULT_TENANT
    auto_provision: bool = True
    leeway_s: int = 30
    algorithms: tuple[str, ...] = field(default=ASYMMETRIC)


def config() -> Optional[OidcConfig]:
    """The configuration from the environment, or None when OIDC is not enabled."""
    issuer = os.getenv("ORION_OIDC_ISSUER", "").strip()
    audience = os.getenv("ORION_OIDC_CLIENT_ID", "").strip()
    if not issuer or not audience:
        return None
    admin = tuple(v.strip() for v in os.getenv("ORION_OIDC_ADMIN_VALUES", "orion-admin").split(",") if v.strip())
    return OidcConfig(
        issuer=issuer.rstrip("/"),
        audience=audience,
        jwks_url=os.getenv("ORION_OIDC_JWKS_URL", "").strip(),
        username_claim=os.getenv("ORION_OIDC_USERNAME_CLAIM", "preferred_username").strip() or "preferred_username",
        tenant_claim=os.getenv("ORION_OIDC_TENANT_CLAIM", "tenant").strip() or "tenant",
        role_claim=os.getenv("ORION_OIDC_ROLE_CLAIM", "roles").strip() or "roles",
        admin_values=admin or ("orion-admin",),
        default_tenant=os.getenv("ORION_OIDC_DEFAULT_TENANT", tenancy.DEFAULT_TENANT).strip() or tenancy.DEFAULT_TENANT,
        auto_provision=os.getenv("ORION_OIDC_AUTO_PROVISION", "true").strip().lower() not in {"0", "false", "no", "off"},
    )


KeyResolver = Callable[[str], Any]
_clients: dict[str, Any] = {}


def _discover_jwks(cfg: OidcConfig) -> str:
    import httpx

    try:
        response = httpx.get(f"{cfg.issuer}/.well-known/openid-configuration", timeout=10.0)
        response.raise_for_status()
        return str(response.json()["jwks_uri"])
    except Exception as exc:
        raise OidcError("the identity provider could not be reached", status=502) from exc


def default_key_resolver(cfg: OidcConfig) -> KeyResolver:
    """Resolve the signing key for a token from the provider's JWKS (cached by PyJWT)."""
    import jwt

    url = cfg.jwks_url or _discover_jwks(cfg)
    client = _clients.get(url)
    if client is None:
        client = _clients[url] = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600)

    def resolve(token: str) -> Any:
        try:
            return client.get_signing_key_from_jwt(token).key
        except Exception as exc:
            raise OidcError("the token's signing key is not trusted") from exc

    return resolve


def verify(token: str, cfg: OidcConfig, resolver: Optional[KeyResolver] = None) -> dict[str, Any]:
    """Validate the token and return its claims, or raise `OidcError`."""
    import jwt

    token = (token or "").strip()
    if not token or token.count(".") != 2:
        raise OidcError("malformed token")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise OidcError("malformed token") from exc
    algorithm = str(header.get("alg") or "")
    if algorithm not in cfg.algorithms:
        raise OidcError("unsupported token algorithm")
    key = (resolver or default_key_resolver(cfg))(token)
    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=list(cfg.algorithms),
            audience=cfg.audience,
            issuer=cfg.issuer,
            leeway=cfg.leeway_s,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise OidcError("the token has expired") from exc
    except jwt.PyJWTError as exc:
        raise OidcError("the token is not valid") from exc
    return dict(claims)


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v for v in re.split(r"[,\s]+", value) if v]
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    return [str(value)]


def identity_from_claims(claims: dict[str, Any], cfg: OidcConfig) -> tuple[str, str, str]:
    """`(username, role, tenant)` for a verified token."""
    raw_name = claims.get(cfg.username_claim) or claims.get("email") or claims.get("sub")
    username = str(raw_name or "").strip()
    if not _USERNAME.fullmatch(username):
        raise OidcError("the token has no usable username claim", status=403)
    raw_tenant = claims.get(cfg.tenant_claim)
    tenant_value = str(raw_tenant).strip().lower() if raw_tenant not in (None, "") else cfg.default_tenant
    try:
        tenant = tenancy.slug(tenant_value)
    except tenancy.TenantError as exc:
        raise OidcError("the token's tenant is not valid", status=403) from exc
    roles = {r.lower() for r in _as_list(claims.get(cfg.role_claim))}
    admin = any(v.lower() in roles for v in cfg.admin_values)
    return username, ("super_user" if admin else "limited"), tenant


def login(token: str, cfg: Optional[OidcConfig] = None, resolver: Optional[KeyResolver] = None) -> dict[str, str]:
    """Verify `token`, provision or update the account and open a session. Returns the session fields."""
    from backend import database as db

    cfg = cfg or config()
    if cfg is None:
        raise OidcError("OIDC sign-in is not enabled", status=404)
    username, role, tenant = identity_from_claims(verify(token, cfg, resolver), cfg)
    row = db.get_user_row(username)
    if row is None:
        if not cfg.auto_provision:
            raise OidcError("no account exists for this identity", status=403)
        if not db.create_external_user(username, role, tenant):
            raise OidcError("could not create the account", status=409)
    else:
        if row["password_hash"] != db.EXTERNAL_HASH:
            raise OidcError("this username belongs to a local account", status=403)
        if not row["is_active"]:
            raise OidcError("this account is disabled", status=403)
        if (row["tenant"] or db.DEFAULT_TENANT) != tenant:
            raise OidcError("this account belongs to another tenant", status=403)
        if row["role"] != role:
            db.set_user_role(username, role)  # the IdP is the source of truth for roles
    session = db.create_session(username, role, tenant)
    return {"token": session, "username": username, "role": role, "tenant": tenant}
