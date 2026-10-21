"""MCP client against a real stdio server: register, call, timeout, circuit, guard, approvals, search."""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import guard, mcp_client
from harness import settings_store as ss
from harness.context import new_context
from harness.errors import ToolRuntimeError
from harness.mcp_client import McpBridge, ServerConfig, verb_name
from harness.tool_registry import ToolRegistry
from harness.types import SideEffect

FIXTURE = str(Path(__file__).parent / "fixtures" / "echo_mcp_server.py")


def echo_config(name: str = "echo", **overrides) -> ServerConfig:
    data = {"name": name, "transport": "stdio", "command": sys.executable, "args": [FIXTURE], "timeout_s": 30}
    data.update(overrides)
    return ServerConfig.from_dict(data)


class ConfigTests(unittest.TestCase):
    def test_server_name_rules(self) -> None:
        for bad in ("", "has space", "dou__ble", "dot.name"):
            with self.assertRaises(ValueError, msg=bad):
                ServerConfig.from_dict({"name": bad, "command": "x"})
        self.assertEqual(ServerConfig.from_dict({"name": "ok-name_1", "command": "x"}).name, "ok-name_1")

    def test_transport_requirements_and_side_effect_choice(self) -> None:
        with self.assertRaises(ValueError):
            ServerConfig.from_dict({"name": "a", "transport": "stdio"})
        with self.assertRaises(ValueError):
            ServerConfig.from_dict({"name": "a", "transport": "streamable_http"})
        with self.assertRaises(ValueError):
            ServerConfig.from_dict({"name": "a", "transport": "carrier-pigeon", "command": "x"})
        with self.assertRaises(ValueError):
            ServerConfig.from_dict({"name": "a", "command": "x", "default_side_effect": "maybe"})

    def test_env_references_are_interpolated_and_missing_ones_fail(self) -> None:
        with patch.dict(os.environ, {"ORION_TEST_TOKEN": "tok-123"}):
            cfg = ServerConfig.from_dict(
                {"name": "a", "transport": "streamable_http", "url": "https://x", "headers": {"Authorization": "Bearer ${ORION_TEST_TOKEN}"}}
            )
        self.assertEqual(cfg.headers["Authorization"], "Bearer tok-123")
        with self.assertRaises(ValueError) as err:
            ServerConfig.from_dict({"name": "a", "command": "x", "env": {"K": "${ORION_DEFINITELY_UNSET}"}})
        self.assertIn("ORION_DEFINITELY_UNSET", str(err.exception))

    def test_load_configs_skips_bad_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.json"
            path.write_text(
                '{"servers": [{"name": "good", "command": "x"}, {"name": "bad name", "command": "x"}, {"command": "y"}]}', encoding="utf-8"
            )
            configs, errors = mcp_client.load_configs(path)
            self.assertEqual([c.name for c in configs], ["good"])
            self.assertEqual(len(errors), 2)
            self.assertEqual(mcp_client.load_configs(Path(tmp) / "none.json"), ([], []))

    def test_verb_names_are_provider_safe_and_stable(self) -> None:
        verb = verb_name("srv", "my.tool name")
        self.assertRegex(verb, r"^[A-Za-z0-9_-]{1,64}$")
        self.assertTrue(verb.startswith("mcp__srv__"))
        long = verb_name("srv", "t" * 200)
        self.assertLessEqual(len(long), 64)
        self.assertEqual(long, verb_name("srv", "t" * 200))
        self.assertNotEqual(long, verb_name("srv", "t" * 199 + "u"))


class LiveServerCase(unittest.TestCase):
    """One real stdio server shared by the class; each test gets a fresh registry and temp state."""

    bridge: McpBridge
    status: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.bridge = McpBridge()
        cls.status = cls.bridge.connect(echo_config("echo", agents=[]))
        cls._patch = patch.object(mcp_client, "_BRIDGE", cls.bridge)
        cls._patch.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._patch.stop()
        cls.bridge.close_all()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"ORION_STATE_DB": str(Path(self._tmp.name) / "s.db")})
        self._env.start()
        ss.clear_cache()
        guard.reset()
        self.tools = ToolRegistry()
        mcp_client.sync_registry(self.tools)

    def tearDown(self) -> None:
        guard.set_classifier(None)
        self._env.stop()
        ss.clear_cache()
        self._tmp.cleanup()

    def ctx(self, **kw):
        ctx = new_context(**kw)
        ctx.extra["mode"] = "product"
        ctx.extra["approvals"] = []
        return ctx


class ConnectionTests(LiveServerCase):
    def test_connects_and_lists_tools(self) -> None:
        self.assertTrue(self.status["connected"], self.status)
        self.assertEqual(self.status["tools"], 7)
        self.assertIsNone(self.status["error"])

    def test_tools_are_registered_with_safe_verbs(self) -> None:
        verbs = [v for v in self.tools.verbs() if v.startswith("mcp__")]
        self.assertIn("mcp__echo__echo", verbs)
        self.assertEqual(len(verbs), 7)
        for verb in verbs:
            self.assertRegex(verb, r"^[A-Za-z0-9_-]{1,64}$")
        schema = self.tools.manifest("mcp__echo__add").schema
        self.assertEqual(set(schema["properties"]), {"a", "b"})

    def test_side_effects_follow_annotations_and_the_server_default(self) -> None:
        effect = lambda name: self.tools.manifest(verb_name("echo", name)).side_effect  # noqa: E731
        self.assertEqual(effect("echo"), SideEffect.READ)
        self.assertEqual(effect("write_note"), SideEffect.WRITE)
        self.assertEqual(effect("unannotated"), SideEffect.WRITE)  # safe default
        trusting = McpBridge()
        try:
            trusting.connect(echo_config("trust", default_side_effect="read"))
            with patch.object(mcp_client, "_BRIDGE", trusting):
                registry = ToolRegistry()
                mcp_client.sync_registry(registry)
                self.assertEqual(registry.manifest(verb_name("trust", "unannotated")).side_effect, SideEffect.READ)
                self.assertEqual(registry.manifest(verb_name("trust", "write_note")).side_effect, SideEffect.WRITE)
        finally:
            trusting.close_all()

    def test_allow_list_limits_exposed_tools(self) -> None:
        narrow = McpBridge()
        try:
            narrow.connect(echo_config("narrow", allow=["echo", "add"]))
            with patch.object(mcp_client, "_BRIDGE", narrow):
                registry = ToolRegistry()
                self.assertEqual(sorted(mcp_client.sync_registry(registry)), ["mcp__narrow__add", "mcp__narrow__echo"])
        finally:
            narrow.close_all()

    def test_a_bad_command_reports_an_error_without_raising(self) -> None:
        broken = McpBridge()
        try:
            status = broken.connect(ServerConfig.from_dict({"name": "broken", "command": "orion-no-such-binary", "timeout_s": 5}))
            self.assertFalse(status["connected"])
            self.assertTrue(status["error"])
        finally:
            broken.close_all()

    def test_disconnect_removes_the_tools_on_sync(self) -> None:
        other = McpBridge()
        other.connect(echo_config("temp"))
        with patch.object(mcp_client, "_BRIDGE", other):
            registry = ToolRegistry()
            self.assertIn("mcp__temp__echo", mcp_client.sync_registry(registry))
            other.disconnect("temp")
            self.assertEqual(mcp_client.sync_registry(registry), [])
            self.assertNotIn("mcp__temp__echo", registry.verbs())
        other.close_all()


class CallTests(LiveServerCase):
    def test_call_through_the_registry(self) -> None:
        res = self.tools.call("mcp__echo__echo", {"text": "hi"}, self.ctx())
        self.assertTrue(res.ok, res.error)
        self.assertIn("echo: hi", res.preview)
        added = self.tools.call("mcp__echo__add", {"a": 2, "b": 5}, self.ctx())
        self.assertEqual(added.data["text"], "7")
        self.assertEqual(added.data["structured"], {"result": 7})

    def test_eval_mode_and_disabled_flag_deny_every_call(self) -> None:
        res = self.tools.call("mcp__echo__echo", {"text": "x"}, new_context())
        self.assertEqual(res.error_class, "PolicyDenied")
        ss.set_value("mcp.enabled", False, "test")
        res = self.tools.call("mcp__echo__echo", {"text": "x"}, self.ctx())
        self.assertEqual(res.error_class, "PolicyDenied")

    def test_remote_errors_are_typed_and_trip_the_circuit(self) -> None:
        ctx = self.ctx()
        classes = [self.tools.call("mcp__echo__echo", {}, ctx).error_class for _ in range(4)]  # missing required arg
        self.assertEqual(classes[:3], ["ToolRuntime"] * 3)
        self.assertEqual(classes[3], "CircuitOpen")

    def test_a_slow_tool_times_out_with_a_typed_error(self) -> None:
        with self.assertRaises(ToolRuntimeError) as err:
            self.bridge.call("echo", "slow", {"seconds": 3}, timeout_s=1)
        self.assertIn("timed out", str(err.exception))

    def test_calling_a_disconnected_server_is_a_typed_error(self) -> None:
        with self.assertRaises(ToolRuntimeError):
            self.bridge.call("ghost", "echo", {})

    def test_calls_are_traced_with_the_remote_preview(self) -> None:
        from harness.trace import get_tracer

        ctx = self.ctx()
        self.tools.call("mcp__echo__echo", {"text": "trace-me"}, ctx)
        spans = [s for s in get_tracer().spans_for(ctx.request_id) if s.name == "tool.mcp__echo__echo"]
        self.assertTrue(spans)
        self.assertIn("trace-me", spans[0].extra["preview"])


class GuardAndApprovalTests(LiveServerCase):
    def test_poisoned_remote_output_is_stubbed_by_the_guard(self) -> None:
        guard.set_classifier(lambda texts: [0.97 if "SECRET-INSTRUCTION" in t else 0.01 for t in texts])
        ss.set_value("guard.enabled", True, "test")
        res = self.tools.call("mcp__echo__poisoned_page", {}, self.ctx())
        self.assertTrue(res.ok)
        self.assertIn("withheld", res.preview)
        self.assertNotIn("SECRET-INSTRUCTION", res.preview)
        ss.set_value("guard.enabled", False, "test")
        raw = self.tools.call("mcp__echo__poisoned_page", {}, self.ctx())
        self.assertIn("SECRET-INSTRUCTION", raw.preview)

    def test_write_tools_need_approval_and_run_after_it_is_granted(self) -> None:
        from harness import approvals

        ctx = self.ctx(user="carol")
        blocked = self.tools.call("mcp__echo__write_note", {"text": "hello"}, ctx)
        self.assertEqual(blocked.error_class, "ApprovalRequired")
        pending = approvals.list_for(tenant="default", status="pending")[0]
        approvals.decide(pending["id"], approver="boss", tenant="default", approve=True)
        done = self.tools.call("mcp__echo__write_note", {"text": "hello"}, self.ctx(user="carol"))
        self.assertTrue(done.ok, done.error)
        self.assertIn("saved: hello", done.preview)

    def test_read_only_tools_never_need_approval(self) -> None:
        self.assertTrue(self.tools.call("mcp__echo__echo", {"text": "x"}, self.ctx()).ok)


class WildcardTests(LiveServerCase):
    def test_card_wildcards_expand_to_the_server_tools(self) -> None:
        names = mcp_client.expand_tool_names(["retrieve", "mcp:echo"], "any")
        self.assertIn("retrieve", names)
        self.assertIn("mcp__echo__echo", names)
        self.assertNotIn("mcp:echo", names)
        self.assertEqual(mcp_client.expand_tool_names(["retrieve"], "any"), ["retrieve"])
        self.assertIn("mcp__echo__add", mcp_client.expand_tool_names(["mcp:*"], "any"))
        self.assertEqual(mcp_client.expand_tool_names(["mcp:other"], "any"), [])

    def test_server_level_agent_grants(self) -> None:
        granted = McpBridge()
        try:
            granted.connect(echo_config("granted", agents=["analyst"]))
            with patch.object(mcp_client, "_BRIDGE", granted):
                self.assertIn("mcp__granted__echo", mcp_client.expand_tool_names(["retrieve"], "analyst"))
                self.assertNotIn("mcp__granted__echo", mcp_client.expand_tool_names(["retrieve"], "someone_else"))
        finally:
            granted.close_all()


class SearchBackendTests(LiveServerCase):
    def test_web_search_can_use_an_mcp_server(self) -> None:
        searching = McpBridge()
        try:
            searching.connect(echo_config("srch", search_tool="web_search_results"))
            with patch.object(mcp_client, "_BRIDGE", searching):
                from harness import web_search

                out = web_search.search("apples", 5, backend="mcp:srch")
            self.assertEqual([r["url"] for r in out["results"]], ["https://example.org/a", "https://example.org/b"])
            self.assertEqual(out["results"][0]["title"], "About apples")
            self.assertIn("[1] About apples - https://example.org/a", out["preview"])
        finally:
            searching.close_all()

    def test_search_without_a_connected_server_is_a_typed_error(self) -> None:
        with self.assertRaises(ToolRuntimeError):
            mcp_client.search("ghost", "q", 3)

    def test_parse_search_handles_json_structured_and_plain_text(self) -> None:
        self.assertEqual(mcp_client._parse_search('[{"title":"T","link":"https://a.b"}]', None, 5)[0]["url"], "https://a.b")
        self.assertEqual(mcp_client._parse_search("", {"results": [{"url": "https://c.d", "description": "x"}]}, 5)[0]["snippet"], "x")
        plain = mcp_client._parse_search("see https://e.f/page and https://g.h", None, 1)
        self.assertEqual([r["url"] for r in plain], ["https://e.f/page"])


class StatusTests(LiveServerCase):
    def test_bridge_status_for_health(self) -> None:
        info = mcp_client.bridge_status()
        self.assertTrue(info["available"])
        self.assertEqual(info["servers"][0]["name"], "echo")
        self.assertTrue(info["servers"][0]["connected"])

    def test_connect_all_respects_the_enabled_flag(self) -> None:
        ss.set_value("mcp.enabled", False, "test")
        with patch.object(mcp_client, "_BRIDGE", McpBridge()):
            self.assertEqual(mcp_client.connect_all(Path(self._tmp.name) / "none.json", self.tools), {"enabled": False, "servers": [], "errors": []})


if __name__ == "__main__":
    unittest.main()
