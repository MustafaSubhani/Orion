"""Shared harness for API tests: temp auth and state databases, a TestClient, and login helpers."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

ADMIN_PASSWORD = "admin-pass-123"
USER_PASSWORD = "user-pass-1234"


def fake_stream(text: str = "42 employees", agents: tuple[str, ...] = ("hr",)):
    """A stand-in for `coordinate_stream` that never calls a model."""

    def _stream(query, allowed=None, user="system", user_role=None, pin_agent=None, **kwargs) -> Iterator[dict]:
        rid = "ORION-TEST-0001"
        yield {"type": "init", "message": "Analyzing your query...", "request_id": rid}
        yield {"type": "task_start", "task_id": "n1", "agent": agents[0], "request_id": rid}
        yield {
            "type": "final",
            "response": text,
            "tasks": [{"id": "n1", "description": query, "agent": agents[0], "status": "completed", "result": text}],
            "agents_used": list(agents),
            "success": True,
            "request_id": rid,
            "graph": {"nodes": [], "edges": []},
            "backtracks": 0,
            "tokens_used": 7,
            "_seen": {"user": user, "role": user_role, "pin": pin_agent, "kwargs": kwargs},
        }

    return _stream


class FakeGateway:
    """Records every completion request and replays scripted replies. Never touches the network."""

    def __init__(self, replies=None, error=None):
        from harness.types import LLMResponse

        from harness.config import get_settings

        self.settings = get_settings()
        self._make = lambda text: LLMResponse(text=text, model="fake", tokens_in=10, tokens_out=5)
        self.replies = list(replies or [])
        self.error = error
        self.calls: list[dict] = []

    def complete(self, *, ctx, messages, purpose="worker", name="", max_tokens=0, **kw):
        self.calls.append({"purpose": purpose, "name": name, "messages": messages, "max_tokens": max_tokens})
        if self.error is not None:
            raise self.error
        ctx.charge_tokens(15)
        return self._make(self.replies.pop(0) if self.replies else "ok")


def installed_runtime(gateway, *, agents=None, tools=None, healer=None):
    """Context manager: make `get_runtime()` return a runtime with these parts, then restore it."""
    from contextlib import contextmanager

    from harness import context as ctxmod

    @contextmanager
    def cm():
        previous = ctxmod._RUNTIME
        ctxmod.set_runtime(ctxmod.Runtime(gateway=gateway, tools=tools, agents=agents, tracer=None, healer=healer))
        try:
            yield gateway
        finally:
            ctxmod._RUNTIME = previous

    return cm()


class ApiTestCase(unittest.TestCase):
    """Boots the real app against temp databases. Subclasses may extend `extra_env`."""

    extra_env: dict[str, str] = {}

    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient

        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        env = {
            "ORION_AUTH_DB": str(root / "auth.db"),
            "ORION_STATE_DB": str(root / "state.db"),
            "ORION_ADMIN_PASSWORD": ADMIN_PASSWORD,
            "ORION_AUTH_REQUIRED": "true",
            "ORION_KB_DIR": str(root / "kb"),
            **cls.extra_env,
        }
        cls._env = patch.dict(os.environ, env)
        cls._env.start()
        os.environ.pop("ORION_API_KEY", None)
        os.environ.pop("ORION_ENV", None)
        from harness import settings_store

        settings_store.clear_cache()
        from backend.api import app
        from backend.ratelimit import login_throttle

        login_throttle.reset()
        cls.app = app
        cls.client = TestClient(app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._env.stop()
        cls._tmp.cleanup()
        from harness import settings_store

        settings_store.clear_cache()

    def login(self, username: str = "admin", password: str = ADMIN_PASSWORD) -> dict[str, str]:
        res = self.client.post("/auth/login", json={"username": username, "password": password})
        self.assertEqual(res.status_code, 200, res.text)
        return {"Authorization": f"Bearer {res.json()['token']}"}

    def admin(self) -> dict[str, str]:
        return self.login()

    def make_user(self, username: str, role: str = "limited") -> dict[str, str]:
        """Create (or reuse) a user through the admin API and return its auth headers."""
        res = self.client.post(
            "/admin/users",
            headers=self.admin(),
            json={"username": username, "password": USER_PASSWORD, "role": role},
        )
        self.assertIn(res.status_code, (201, 409), res.text)
        return self.login(username, USER_PASSWORD)
