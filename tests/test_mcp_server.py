"""MCP server: protocol negotiation, spec-shaped results, role enforcement, HTTP transport rules."""

from __future__ import annotations

import json
import unittest

from harness.mcp_server import LATEST_PROTOCOL, SUPPORTED_PROTOCOLS, handle_mcp, negotiate
from tests.apitest import ApiTestCase


def rpc(method: str, params: dict | None = None, req_id: int | None = 1, **kw):
    body = {"jsonrpc": "2.0", "method": method}
    if req_id is not None:
        body["id"] = req_id
    if params is not None:
        body["params"] = params
    return handle_mcp(body, **kw)


class ProtocolTests(unittest.TestCase):
    def test_initialize_negotiates_the_requested_revision(self) -> None:
        for version in SUPPORTED_PROTOCOLS:
            res = rpc("initialize", {"protocolVersion": version})
            self.assertEqual(res["result"]["protocolVersion"], version)
        self.assertEqual(rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"], LATEST_PROTOCOL)
        self.assertEqual(negotiate(None), LATEST_PROTOCOL)

    def test_initialize_declares_capabilities_and_server_info(self) -> None:
        result = rpc("initialize", {})["result"]
        self.assertEqual(result["serverInfo"]["name"], "orion-harness")
        self.assertEqual(set(result["capabilities"]), {"tools", "resources", "prompts"})

    def test_ping_returns_an_empty_result(self) -> None:
        self.assertEqual(rpc("ping"), {"jsonrpc": "2.0", "id": 1, "result": {}})

    def test_notifications_get_no_response(self) -> None:
        self.assertIsNone(rpc("notifications/initialized", req_id=None))
        self.assertIsNone(rpc("notifications/cancelled", {"requestId": 3}, req_id=None))

    def test_unknown_method_and_invalid_request_are_json_rpc_errors(self) -> None:
        self.assertEqual(rpc("nope/nothing")["error"]["code"], -32601)
        self.assertEqual(handle_mcp({"id": 4, "method": "ping"})["error"]["code"], -32600)  # missing jsonrpc

    def test_the_same_id_is_echoed(self) -> None:
        self.assertEqual(rpc("ping", req_id="abc")["id"], "abc")


class ToolTests(unittest.TestCase):
    def test_tools_list_has_standard_fields(self) -> None:
        tools = rpc("tools/list")["result"]["tools"]
        self.assertTrue(tools)
        for tool in tools:
            self.assertTrue({"name", "description", "inputSchema", "annotations"} <= set(tool))
            self.assertIn("readOnlyHint", tool["annotations"])
        names = {t["name"] for t in tools}
        self.assertIn("sql_spec", names)
        self.assertNotIn("web_search", names)  # disabled by default

    def test_limited_role_does_not_see_tools_it_cannot_call(self) -> None:
        admin = {t["name"] for t in rpc("tools/list", role=None)["result"]["tools"]}
        limited = {t["name"] for t in rpc("tools/list", role="limited")["result"]["tools"]}
        self.assertIn("forecast", admin)
        self.assertNotIn("forecast", limited)
        self.assertLess(limited, admin)

    def test_call_result_is_a_standard_call_tool_result(self) -> None:
        res = rpc("tools/call", {"name": "join_check", "arguments": {"entities": "MNC,MSFT"}})["result"]
        self.assertFalse(res["isError"])
        self.assertEqual(res["content"][0]["type"], "text")
        self.assertIn("ILLEGAL", res["content"][0]["text"])
        self.assertTrue(res["structuredContent"]["ok"])
        self.assertTrue(res["ok"])  # legacy field kept

    def test_tool_failure_is_is_error_not_a_protocol_error(self) -> None:
        res = rpc("tools/call", {"name": "join_check", "arguments": {}})["result"]
        self.assertTrue(res["isError"])
        self.assertEqual(res["structuredContent"]["error_class"], "SchemaInvalid")
        self.assertEqual(res["error_class"], "SchemaInvalid")

    def test_unknown_tool_is_invalid_params(self) -> None:
        self.assertEqual(rpc("tools/call", {"name": "no_such_tool"})["error"]["code"], -32602)

    def test_non_object_arguments_are_invalid_params(self) -> None:
        self.assertEqual(rpc("tools/call", {"name": "join_check", "arguments": [1]})["error"]["code"], -32602)

    def test_role_policy_applies_to_calls(self) -> None:
        res = rpc("tools/call", {"name": "forecast", "arguments": {"table": "applfin", "column": "NetSales_Total"}}, role="limited")["result"]
        self.assertTrue(res["isError"])
        self.assertEqual(res["error_class"], "PolicyDenied")


class ResourceAndPromptTests(unittest.TestCase):
    def test_resources_list_tables_with_entities(self) -> None:
        resources = rpc("resources/list")["result"]["resources"]
        self.assertIn("table://hr", {r["uri"] for r in resources})

    def test_limited_role_never_sees_salary_in_a_resource(self) -> None:
        full = json.loads(rpc("resources/read", {"uri": "table://hr"}, role=None)["result"]["contents"][0]["text"])
        limited = json.loads(rpc("resources/read", {"uri": "table://hr"}, role="limited")["result"]["contents"][0]["text"])
        self.assertIn("Salary_INR", full["columns"])
        self.assertNotIn("Salary_INR", limited["columns"])
        self.assertTrue(all("Salary_INR" not in row for row in limited["sample"]))

    def test_unknown_resources_are_invalid_params(self) -> None:
        self.assertEqual(rpc("resources/read", {"uri": "file:///etc/passwd"})["error"]["code"], -32602)
        self.assertEqual(rpc("resources/read", {"uri": "table://nope"})["error"]["code"], -32602)

    def test_prompts_use_only_spec_roles(self) -> None:
        prompts = rpc("prompts/list")["result"]["prompts"]
        self.assertTrue(prompts)
        got = rpc("prompts/get", {"name": prompts[0]["name"], "arguments": {"task": "count people"}})["result"]
        self.assertTrue(all(m["role"] in {"user", "assistant"} for m in got["messages"]))
        self.assertEqual(rpc("prompts/get", {"name": "ghost"})["error"]["code"], -32602)


class HttpTransportTests(ApiTestCase):
    def post(self, body: dict, headers=None):
        return self.client.post("/mcp", json=body, headers=headers or self.admin())

    def test_notification_is_202_with_no_body(self) -> None:
        res = self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(res.status_code, 202)
        self.assertEqual(res.content, b"")

    def test_request_is_a_json_response(self) -> None:
        res = self.post({"jsonrpc": "2.0", "id": 9, "method": "ping"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"jsonrpc": "2.0", "id": 9, "result": {}})

    def test_role_comes_from_the_session(self) -> None:
        limited = self.make_user("mcp-limited")
        names = {t["name"] for t in self.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, limited).json()["result"]["tools"]}
        self.assertNotIn("forecast", names)

    def test_anonymous_callers_are_rejected(self) -> None:
        self.assertEqual(self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
