"""SQLite auth store: users (scrypt hashes), roles, tenants, expiring revocable sessions.

Contract:
- Passwords are stored as `scrypt$n$r$p$salt$hash`. A legacy unsalted SHA-256 hash still verifies once
  and is upgraded on that successful login.
- Session tokens are random and returned once. Only their SHA-256 is stored, so a leaked database
  does not leak live sessions. Sessions expire (`ORION_SESSION_TTL`, default 12h) and stop working
  the moment the user is deactivated.
- Nothing runs at import time. The schema and the default admin are created on first connection
  to a given database path (`ORION_AUTH_DB` overrides the path, which tests use for a temp file).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from harness import tenancy

ROLE_SUPER_USER = "super_user"
ROLE_LIMITED = "limited"
ROLES = (ROLE_SUPER_USER, ROLE_LIMITED)
DEFAULT_TENANT = "default"

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
_DUMMY_HASH = ""
_ready: set[str] = set()
_ready_lock = threading.RLock()


def db_path() -> Path:
    raw = os.getenv("ORION_AUTH_DB", "").strip()
    return Path(raw) if raw else Path(__file__).parent.parent / "orion_auth.db"


def session_ttl_s() -> float:
    try:
        return max(60.0, float(os.getenv("ORION_SESSION_TTL", "43200")))
    except ValueError:
        return 43200.0


def _raw_connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_connection() -> sqlite3.Connection:
    """Connection to the auth DB. Creates the schema and default admin on first use of a path."""
    path = db_path()
    key = str(path.resolve())
    if key not in _ready:
        with _ready_lock:
            if key not in _ready:
                _ready.add(key)  # add first: setup_default_user reconnects
                try:
                    init_database()
                    setup_default_user()
                except Exception:
                    _ready.discard(key)
                    raise
    return _raw_connection(path)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_database() -> None:
    """Create tables and run additive migrations. Safe to call repeatedly."""
    conn = _raw_connection(db_path())
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'limited',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                role TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        user_cols = _columns(conn, "users")
        if "role" not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'limited'")
        if "tenant" not in user_cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN tenant TEXT DEFAULT '{DEFAULT_TENANT}'")
        session_cols = _columns(conn, "sessions")
        if "expires_at" not in session_cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN expires_at REAL")
        if "tenant" not in session_cols:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN tenant TEXT DEFAULT '{DEFAULT_TENANT}'")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- passwords


def hash_password(password: str, *, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, maxmem=64 * 1024 * 1024
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def _legacy_hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


def verify_password(stored: str, password: str) -> tuple[bool, bool]:
    """Return (ok, needs_rehash). Legacy SHA-256 hashes verify but need an upgrade."""
    if stored.startswith("scrypt$"):
        try:
            _, n, r, p, salt_hex, hash_hex = stored.split("$")
            digest = hashlib.scrypt(
                password.encode("utf-8"),
                salt=bytes.fromhex(salt_hex),
                n=int(n),
                r=int(r),
                p=int(p),
                maxmem=64 * 1024 * 1024,
            )
            return hmac.compare_digest(digest.hex(), hash_hex), False
        except (ValueError, TypeError):
            return False, False
    return hmac.compare_digest(stored, _legacy_hash(password)), True


def _dummy() -> str:
    global _DUMMY_HASH
    if not _DUMMY_HASH:
        _DUMMY_HASH = hash_password("orion-timing-equaliser")
    return _DUMMY_HASH


def validate_password_strength(password: str) -> Optional[str]:
    if len(password or "") < 8:
        return "Password must be at least 8 characters"
    return None


# ---------------------------------------------------------------- users


def create_user(username: str, password: str, role: str = ROLE_LIMITED, tenant: str = DEFAULT_TENANT) -> bool:
    """Create a user. False if the role is unknown or the username is taken; `TenantError` for a bad tenant id."""
    if role not in ROLES:
        return False
    tenant = tenancy.slug(tenant)  # tenant ids become directory and collection names, so they are validated here
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, tenant) VALUES (?, ?, ?, ?)",
            (username, hash_password(password), role, tenant),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


#: Password hash of accounts provisioned by an identity provider. It is not a valid scrypt string and can
#: never equal a SHA-256 digest, so no password verifies against it. It also marks the account as
#: IdP-managed, so a local account with the same name is never taken over by a token.
EXTERNAL_HASH = "!external"


def get_user_row(username: str) -> Optional[dict]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT username, role, tenant, is_active, password_hash FROM users WHERE username = ?", (username,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def create_external_user(username: str, role: str, tenant: str) -> bool:
    """Provision an IdP-managed account (no usable password). False if the name is taken."""
    if role not in ROLES:
        return False
    tenant = tenancy.slug(tenant)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role, tenant) VALUES (?, ?, ?, ?)",
            (username, EXTERNAL_HASH, role, tenant),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def verify_user(username: str, password: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE username = ? AND is_active = 1", (username,)
        ).fetchone()
        if row is None:
            verify_password(_dummy(), password)  # keep timing similar for unknown users
            return False
        ok, rehash = verify_password(row["password_hash"], password)
        if ok and rehash:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE username = ?", (hash_password(password), username)
            )
            conn.commit()
        return ok
    finally:
        conn.close()


def get_user_role(username: str) -> Optional[str]:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT role FROM users WHERE username = ? AND is_active = 1", (username,)
        ).fetchone()
    finally:
        conn.close()
    return (row["role"] or ROLE_LIMITED) if row else None


def get_user_tenant(username: str) -> str:
    conn = get_connection()
    try:
        row = conn.execute("SELECT tenant FROM users WHERE username = ?", (username,)).fetchone()
    finally:
        conn.close()
    return (row["tenant"] if row and row["tenant"] else DEFAULT_TENANT)


def set_user_role(username: str, role: str) -> bool:
    if role not in ROLES:
        return False
    conn = get_connection()
    try:
        cur = conn.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def is_super_user(username: str) -> bool:
    return get_user_role(username) == ROLE_SUPER_USER


def get_all_users() -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, username, role, tenant, created_at, is_active FROM users ORDER BY username"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def user_exists(username: str) -> bool:
    conn = get_connection()
    try:
        return conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone() is not None
    finally:
        conn.close()


def set_user_password(username: str, password: str) -> bool:
    conn = get_connection()
    try:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?", (hash_password(password), username)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_user_active(username: str, active: bool) -> bool:
    """Deactivate or reactivate a user. Deactivation also revokes every session."""
    conn = get_connection()
    try:
        cur = conn.execute("UPDATE users SET is_active = ? WHERE username = ?", (1 if active else 0, username))
        if not active:
            conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------- sessions


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(username: str, role: str, tenant: str = DEFAULT_TENANT, ttl_s: Optional[float] = None) -> str:
    token = secrets.token_urlsafe(32)
    expires = time.time() + (ttl_s if ttl_s is not None else session_ttl_s())
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO sessions (token, username, role, tenant, expires_at) VALUES (?, ?, ?, ?, ?)",
            (_token_hash(token), username, role, tenant, expires),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def get_session(token: str) -> Optional[dict]:
    """Identity for a live token, else None. Expired sessions and inactive users are rejected and purged."""
    if not token:
        return None
    hashed = _token_hash(token)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT s.username, s.tenant AS stenant, s.expires_at, u.role AS urole, u.is_active, u.tenant AS utenant "
            "FROM sessions s LEFT JOIN users u ON u.username = s.username WHERE s.token = ?",
            (hashed,),
        ).fetchone()
        if row is None:
            return None
        expired = row["expires_at"] is not None and row["expires_at"] < time.time()
        gone = row["urole"] is None or not row["is_active"]
        if expired or gone:
            conn.execute("DELETE FROM sessions WHERE token = ?", (hashed,))
            conn.commit()
            return None
        return {
            "username": row["username"],
            "role": row["urole"] or ROLE_LIMITED,
            "tenant": row["utenant"] or row["stenant"] or DEFAULT_TENANT,
        }
    finally:
        conn.close()


def delete_session(token: str) -> None:
    if not token:
        return
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sessions WHERE token = ?", (_token_hash(token),))
        conn.commit()
    finally:
        conn.close()


def purge_expired_sessions() -> int:
    conn = get_connection()
    try:
        cur = conn.execute("DELETE FROM sessions WHERE expires_at IS NOT NULL AND expires_at < ?", (time.time(),))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ---------------------------------------------------------------- bootstrap


def setup_default_user() -> None:
    """Create or refresh the admin account from ORION_ADMIN_PASSWORD.

    In development the password defaults to `admin`. With `ORION_ENV=production` the variable is
    mandatory, so a deployment can never start with a guessable admin password.
    """
    configured = os.getenv("ORION_ADMIN_PASSWORD", "")
    if os.getenv("ORION_ENV", "").strip().lower() == "production" and not configured:
        raise RuntimeError("ORION_ADMIN_PASSWORD must be set when ORION_ENV=production")
    password = configured or "admin"
    if not user_exists("admin"):
        create_user("admin", password, ROLE_SUPER_USER)
        return
    if configured:
        set_user_password("admin", password)
    if get_user_role("admin") != ROLE_SUPER_USER:
        set_user_role("admin", ROLE_SUPER_USER)
