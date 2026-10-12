"""
SQLite Database for User Authentication
Handles user credentials with hashed passwords and role-based access control
"""

import sqlite3
import hashlib
import os
import secrets
from pathlib import Path
from typing import Optional

# Database path
DB_PATH = Path(__file__).parent.parent / "orion_auth.db"

# Role constants
ROLE_SUPER_USER = "super_user"
ROLE_LIMITED = "limited"


def get_connection():
    """Get SQLite database connection"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    """Initialize the database with users table including role column"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Create users table with role column
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'limited',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT 1
        )
    """)
    
    # Migration: Add role column if it doesn't exist (for existing databases)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'limited'")
        print("[DATABASE] Added role column to users table")
    except sqlite3.OperationalError:
        # Column already exists
        pass
    
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            role TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()
    print(f"[DATABASE] Initialized at {DB_PATH}")


def hash_password(password: str) -> str:
    """Hash password using SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()


def create_user(username: str, password: str, role: str = ROLE_LIMITED) -> bool:
    """Create a new user with hashed password and role"""
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        password_hash = hash_password(password)
        cursor.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, password_hash, role)
        )
        conn.commit()
        print(f"[DATABASE] User '{username}' created with role '{role}'")
        return True
    except sqlite3.IntegrityError:
        print(f"[DATABASE] User '{username}' already exists")
        return False
    finally:
        conn.close()


def verify_user(username: str, password: str) -> bool:
    """Verify user credentials"""
    conn = get_connection()
    cursor = conn.cursor()
    
    password_hash = hash_password(password)
    cursor.execute(
        "SELECT * FROM users WHERE username = ? AND password_hash = ? AND is_active = 1",
        (username, password_hash)
    )
    
    user = cursor.fetchone()
    conn.close()
    
    return user is not None


def get_user_role(username: str) -> Optional[str]:
    """Get the role for a user"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT role FROM users WHERE username = ? AND is_active = 1", (username,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return result["role"] or ROLE_LIMITED
    return None


def set_user_role(username: str, role: str) -> bool:
    """Set the role for a user (admin function)"""
    if role not in [ROLE_SUPER_USER, ROLE_LIMITED]:
        return False
    
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    
    if affected > 0:
        print(f"[DATABASE] User '{username}' role updated to '{role}'")
        return True
    return False


def is_super_user(username: str) -> bool:
    """Check if a user has super_user role"""
    return get_user_role(username) == ROLE_SUPER_USER


def get_all_users() -> list:
    """Get list of all users (for admin purposes)"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, username, role, created_at, is_active FROM users")
    users = [dict(row) for row in cursor.fetchall()]
    
    conn.close()
    return users


def user_exists(username: str) -> bool:
    """Check if a user exists"""
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT 1 FROM users WHERE username = ?", (username,))
    exists = cursor.fetchone() is not None
    
    conn.close()
    return exists


def set_user_password(username: str, password: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (hash_password(password), username),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def create_session(username: str, role: str) -> str:
    token = secrets.token_urlsafe(32)
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO sessions (token, username, role) VALUES (?, ?, ?)",
            (token, username, role),
        )
        conn.commit()
        return token
    finally:
        conn.close()


def get_session(token: str) -> Optional[dict]:
    if not token:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT username, role FROM sessions WHERE token = ?", (token,))
        row = cursor.fetchone()
        if not row:
            return None
        return {"username": row["username"], "role": row["role"] or ROLE_LIMITED}
    finally:
        conn.close()


def delete_session(token: str) -> None:
    if not token:
        return
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


def setup_default_user():
    """Create default admin from ORION_ADMIN_PASSWORD (dev default: admin)."""
    password = os.getenv("ORION_ADMIN_PASSWORD", "admin")
    if not user_exists("admin"):
        create_user("admin", password, ROLE_SUPER_USER)
        print("[DATABASE] Default admin user created with super_user role")
    else:
        if os.getenv("ORION_ADMIN_PASSWORD"):
            set_user_password("admin", password)
        if get_user_role("admin") != ROLE_SUPER_USER:
            set_user_role("admin", ROLE_SUPER_USER)
            print("[DATABASE] Admin user role updated to super_user")


# Initialize database on module import
init_database()
setup_default_user()


if __name__ == "__main__":
    # Test the database
    print("\n--- Database Test ---")
    print(f"DB Path: {DB_PATH}")
    print(f"Admin exists: {user_exists('admin')}")
    print(f"Admin role: {get_user_role('admin')}")
    print(f"Is admin super_user: {is_super_user('admin')}")
    print(f"Verify admin/admin: {verify_user('admin', 'admin')}")
    print(f"All users: {get_all_users()}")
