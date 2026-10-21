"""Passwords, sessions, auth-by-default, signup policy and upload identity."""

from __future__ import annotations

import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.apitest import ADMIN_PASSWORD, USER_PASSWORD, ApiTestCase


class PasswordTests(unittest.TestCase):
    def test_scrypt_hash_is_salted_and_verifies(self) -> None:
        from backend import database as db

        a, b = db.hash_password("correct horse"), db.hash_password("correct horse")
        self.assertTrue(a.startswith("scrypt$"))
        self.assertNotEqual(a, b)  # per-user salt
        self.assertEqual(db.verify_password(a, "correct horse"), (True, False))
        self.assertEqual(db.verify_password(a, "wrong"), (False, False))

    def test_legacy_sha256_verifies_and_asks_for_rehash(self) -> None:
        from backend import database as db

        legacy = db._legacy_hash("old-password")
        self.assertEqual(db.verify_password(legacy, "old-password"), (True, True))
        self.assertEqual(db.verify_password(legacy, "nope"), (False, True))

    def test_malformed_stored_hash_never_verifies(self) -> None:
        from backend import database as db

        self.assertEqual(db.verify_password("scrypt$broken", "x"), (False, False))

    def test_password_strength_rule(self) -> None:
        from backend import database as db

        self.assertIsNotNone(db.validate_password_strength("short"))
        self.assertIsNone(db.validate_password_strength("long-enough-1"))


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            os.environ,
            {"ORION_AUTH_DB": str(Path(self._tmp.name) / "a.db"), "ORION_ADMIN_PASSWORD": ADMIN_PASSWORD},
        )
        self._env.start()
        os.environ.pop("ORION_ENV", None)

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_session_token_is_stored_hashed(self) -> None:
        from backend import database as db

        token = db.create_session("admin", "super_user")
        conn = sqlite3.connect(os.environ["ORION_AUTH_DB"])
        stored = [r[0] for r in conn.execute("SELECT token FROM sessions")]
        conn.close()
        self.assertNotIn(token, stored)
        self.assertEqual(db.get_session(token)["username"], "admin")

    def test_expired_session_is_rejected_and_purged(self) -> None:
        from backend import database as db

        token = db.create_session("admin", "super_user", ttl_s=-5)
        self.assertIsNone(db.get_session(token))
        conn = sqlite3.connect(os.environ["ORION_AUTH_DB"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)
        conn.close()

    def test_deactivated_user_loses_sessions(self) -> None:
        from backend import database as db

        db.create_user("temp-user", USER_PASSWORD, db.ROLE_LIMITED)
        token = db.create_session("temp-user", db.ROLE_LIMITED)
        self.assertIsNotNone(db.get_session(token))
        db.set_user_active("temp-user", False)
        self.assertIsNone(db.get_session(token))
        self.assertFalse(db.verify_user("temp-user", USER_PASSWORD))

    def test_role_change_applies_to_existing_sessions(self) -> None:
        from backend import database as db

        db.create_user("promote-me", USER_PASSWORD, db.ROLE_LIMITED)
        token = db.create_session("promote-me", db.ROLE_LIMITED)
        db.set_user_role("promote-me", db.ROLE_SUPER_USER)
        self.assertEqual(db.get_session(token)["role"], db.ROLE_SUPER_USER)

    def test_legacy_hash_is_upgraded_on_login(self) -> None:
        from backend import database as db

        db.get_connection().close()
        conn = sqlite3.connect(os.environ["ORION_AUTH_DB"])
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'limited')",
            ("legacy", db._legacy_hash("legacy-pass-1")),
        )
        conn.commit()
        conn.close()
        self.assertTrue(db.verify_user("legacy", "legacy-pass-1"))
        conn = sqlite3.connect(os.environ["ORION_AUTH_DB"])
        stored = conn.execute("SELECT password_hash FROM users WHERE username='legacy'").fetchone()[0]
        conn.close()
        self.assertTrue(stored.startswith("scrypt$"))
        self.assertTrue(db.verify_user("legacy", "legacy-pass-1"))

    def test_production_requires_an_admin_password(self) -> None:
        from backend import database as db

        with patch.dict(os.environ, {"ORION_ENV": "production", "ORION_ADMIN_PASSWORD": ""}):
            with self.assertRaises(RuntimeError):
                db.setup_default_user()

    def test_nothing_runs_at_import_time(self) -> None:
        import importlib

        from backend import database as db

        importlib.reload(db)
        self.assertFalse(Path(os.environ["ORION_AUTH_DB"]).exists())


class LoginApiTests(ApiTestCase):
    def test_public_routes_need_no_credentials(self) -> None:
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_protected_routes_reject_anonymous_callers(self) -> None:
        for method, path in [("get", "/agents"), ("post", "/chat"), ("get", "/kb"), ("get", "/traces"), ("post", "/mcp")]:
            res = getattr(self.client, method)(path)
            self.assertEqual(res.status_code, 401, path)
            self.assertEqual(res.json()["error"]["code"], "unauthenticated")

    def test_login_me_logout_roundtrip(self) -> None:
        headers = self.login()
        me = self.client.get("/auth/me", headers=headers)
        self.assertEqual(me.json()["username"], "admin")
        self.assertEqual(me.json()["role"], "super_user")
        self.client.post("/auth/logout", headers=headers)
        self.assertEqual(self.client.get("/auth/me", headers=headers).status_code, 401)

    def test_wrong_password_is_401_and_rate_limited(self) -> None:
        from backend.ratelimit import login_throttle

        login_throttle.reset()
        for _ in range(5):
            res = self.client.post("/auth/login", json={"username": "admin", "password": "wrong"})
            self.assertEqual(res.status_code, 401)
        blocked = self.client.post("/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
        self.assertEqual(blocked.status_code, 429)
        login_throttle.reset()
        self.assertEqual(
            self.client.post("/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD}).status_code, 200
        )

    def test_login_error_does_not_reveal_whether_the_user_exists(self) -> None:
        from backend.ratelimit import login_throttle

        login_throttle.reset()
        a = self.client.post("/auth/login", json={"username": "admin", "password": "x"}).json()
        b = self.client.post("/auth/login", json={"username": "nobody", "password": "x"}).json()
        self.assertEqual(a["error"]["message"], b["error"]["message"])
        login_throttle.reset()

    def test_api_key_is_accepted_as_a_bearer(self) -> None:
        with patch.dict(os.environ, {"ORION_API_KEY": "service-key-xyz"}):
            res = self.client.get("/auth/me", headers={"Authorization": "Bearer service-key-xyz"})
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["username"], "api")
            self.assertEqual(
                self.client.get("/auth/me", headers={"Authorization": "Bearer wrong-key"}).status_code, 401
            )

    def test_signup_is_disabled_by_default_and_admin_can_enable_it(self) -> None:
        body = {"username": "newcomer", "password": USER_PASSWORD}
        self.assertEqual(self.client.post("/auth/signup", json=body).status_code, 403)
        self.client.put("/admin/settings/auth.open_signup", headers=self.admin(), json={"value": True})
        try:
            self.assertEqual(self.client.post("/auth/signup", json={"username": "newcomer", "password": "short"}).status_code, 400)
            ok = self.client.post("/auth/signup", json=body)
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual(ok.json()["role"], "limited")
            self.assertEqual(self.client.post("/auth/signup", json=body).status_code, 409)
        finally:
            self.client.delete("/admin/settings/auth.open_signup", headers=self.admin())

    def test_upload_identity_comes_from_the_session_not_the_form(self) -> None:
        headers = self.make_user("uploader")
        seen: dict = {}

        def fake_ingest(name, data, *, uploaded_by="chat"):
            seen["by"] = uploaded_by
            return {"id": "res_1", "filename": name}

        with patch("backend.routes.kb.ingest_bytes", fake_ingest):
            res = self.client.post(
                "/ingest",
                headers=headers,
                data={"user": "admin"},  # spoof attempt
                files=[("files", ("a.txt", io.BytesIO(b"hello"), "text/plain"))],
            )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(seen["by"], "uploader")

    def test_oversized_upload_is_rejected(self) -> None:
        headers = self.make_user("uploader2")
        with patch("backend.routes.kb.MAX_BYTES", 10):
            res = self.client.post(
                "/ingest", headers=headers, files=[("files", ("big.txt", io.BytesIO(b"x" * 100), "text/plain"))]
            )
        self.assertEqual(res.status_code, 413)


class AuthDisabledTests(ApiTestCase):
    extra_env = {"ORION_AUTH_REQUIRED": "false"}

    def test_anonymous_caller_is_the_local_admin_when_auth_is_off(self) -> None:
        res = self.client.get("/auth/me")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["role"], "super_user")


if __name__ == "__main__":
    unittest.main()
