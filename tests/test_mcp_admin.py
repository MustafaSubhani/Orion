"""Admin endpoints for MCP, and the web_search `mcp:` backend through the HTTP API surface."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import mcp_client
from harness.mcp_client import McpBridge
from tests.apitest import ApiTestCase

FIXTURE = str(Path(__file__).parent / "fixtures" / "echo_mcp_server.py")


class McpAdminTests(ApiTestCase):
    def setUp(self) -> None:
        self.bridge = McpBridge()
        self._patch = patch.object(mcp_client, "_BRIDGE", self.bridge)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()
        self.bridge.close_all()

    def test_admin_only(self) -> None:
        user = self.make_user("mcp-viewer")
        self.assertEqual(self.client.get("/admin/mcp", headers=user).status_code, 403)
        self.assertEqual(self.client.post("/admin/mcp/reload", headers=user).status_code, 403)
        self.assertEqual(self.client.get("/admin/mcp").status_code, 401)

    def test_reload_connects_configured_servers_and_status_lists_their_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "servers.json"
            config.write_text(
                json.dumps({"servers": [{"name": "echo", "command": sys.executable, "args": [FIXTURE]}, {"name": "bad name", "command": "x"}]}),
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"ORION_MCP_SERVERS": str(config)}):
                reloaded = self.client.post("/admin/mcp/reload", headers=self.admin()).json()
                self.assertTrue(reloaded["enabled"])
                self.assertTrue(reloaded["servers"][0]["connected"])
                self.assertEqual(len(reloaded["errors"]), 1)
                status = self.client.get("/admin/mcp", headers=self.admin()).json()
        self.assertEqual(status["servers"][0]["name"], "echo")
        self.assertIn("mcp__echo__echo", status["servers"][0]["tool_names"])

    def test_health_reports_mcp_state(self) -> None:
        body = self.client.get("/health").json()
        self.assertTrue(body["mcp"]["available"])
        self.assertEqual(body["mcp"]["servers"], [])


if __name__ == "__main__":
    unittest.main()
