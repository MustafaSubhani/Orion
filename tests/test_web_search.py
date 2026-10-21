"""web_search: backend choice, OpenAI citation parsing, DDG mapping, redaction, filters, eval hiding."""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from harness import settings_store as ss
from harness import web_search as ws
from harness.config import Settings
from harness.context import new_context
from harness.errors import PolicyDeniedError, SchemaInvalidError, ToolRuntimeError
from harness.tool_registry import ToolRegistry

RESPONSES_PAYLOAD = {
    "output": [
        {"type": "web_search_call", "id": "ws_1", "status": "completed", "action": {"type": "search", "query": "q"}},
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": "Apple reported record services revenue. Analysts expect growth.",
                    "annotations": [
                        {"type": "url_citation", "url": "https://example.com/a", "title": "Apple results", "start_index": 0, "end_index": 38},
                        {"type": "url_citation", "url": "https://example.com/a", "title": "dup", "start_index": 0, "end_index": 5},
                        {"type": "url_citation", "url": "https://news.example.org/b", "title": "Outlook", "start_index": 39, "end_index": 64},
                    ],
                }
            ],
        },
    ]
}


class WebCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"ORION_STATE_DB": str(Path(self._tmp.name) / "s.db")})
        self._env.start()
        ss.clear_cache()

    def tearDown(self) -> None:
        self._env.stop()
        ss.clear_cache()
        self._tmp.cleanup()

    def product(self):
        ctx = new_context()
        ctx.extra["mode"] = "product"
        return ctx


class OpenAIParsingTests(WebCase):
    def test_answer_and_unique_citations_are_extracted(self) -> None:
        out = ws.parse_openai_response(RESPONSES_PAYLOAD, 5)
        self.assertIn("record services revenue", out["answer"])
        self.assertEqual([r["url"] for r in out["results"]], ["https://example.com/a", "https://news.example.org/b"])
        self.assertEqual(out["results"][0]["title"], "Apple results")
        self.assertTrue(out["results"][0]["snippet"].startswith("Apple reported"))

    def test_limit_applies_and_empty_payloads_are_safe(self) -> None:
        self.assertEqual(len(ws.parse_openai_response(RESPONSES_PAYLOAD, 1)["results"]), 1)
        self.assertEqual(ws.parse_openai_response({}, 5), {"answer": "", "results": []})

    def test_request_shape_uses_the_responses_api_with_the_hosted_tool(self) -> None:
        settings = Settings(worker_provider="openai", openai_api_key="sk-test")
        reply = MagicMock(status_code=200)
        reply.json.return_value = RESPONSES_PAYLOAD
        with patch("harness.web_search.get_settings", return_value=settings), patch(
            "harness.web_search.httpx.post", return_value=reply
        ) as post:
            ss.set_value("web_search.openai_model", "model-x", "test")
            ss.set_value("web_search.allowed_domains", ["example.com"], "test")
            out = ws.search("apple revenue", 5, backend="openai")
        url = post.call_args.args[0]
        body = post.call_args.kwargs["json"]
        self.assertTrue(url.endswith("/responses"))
        self.assertEqual(body["model"], "model-x")
        self.assertEqual(body["tools"], [{"type": "web_search", "filters": {"allowed_domains": ["example.com"]}}])
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(out["backend"], "openai")
        self.assertIn("[1] Apple results - https://example.com/a", out["preview"])

    def test_missing_key_or_model_and_http_errors_are_typed(self) -> None:
        with patch("harness.web_search.get_settings", return_value=Settings(worker_provider="openai")):
            with self.assertRaises(ToolRuntimeError):
                ws.search("q", 3, backend="openai")
        with patch("harness.web_search.get_settings", return_value=Settings(worker_provider="mistral", openai_api_key="k")):
            with self.assertRaises(ToolRuntimeError):
                ws.search("q", 3, backend="openai")  # no model to use
        settings = Settings(worker_provider="openai", openai_api_key="k")
        settings.tiers = type(settings.tiers)(router="r", worker="gpt-x", judge="j")
        with patch("harness.web_search.get_settings", return_value=settings), patch(
            "harness.web_search.httpx.post", return_value=MagicMock(status_code=429)
        ):
            with self.assertRaises(ToolRuntimeError) as err:
                ws.search("q", 3, backend="openai")
        self.assertIn("429", str(err.exception))
        self.assertNotIn("sk-", str(err.exception))


class DuckDuckGoTests(WebCase):
    def fake_ddgs(self, rows):
        module = types.ModuleType("ddgs")

        class DDGS:
            def text(self, query, max_results=5):
                self.query, self.max = query, max_results
                return rows

        module.DDGS = DDGS
        return patch.dict(sys.modules, {"ddgs": module})

    def test_results_are_mapped_with_citations(self) -> None:
        rows = [
            {"title": "A", "href": "https://a.example/x", "body": "alpha"},
            {"title": "B", "href": "https://b.example/y", "body": "beta"},
        ]
        with self.fake_ddgs(rows):
            out = ws.search("something", 5, backend="ddg")
        self.assertEqual([r["url"] for r in out["results"]], ["https://a.example/x", "https://b.example/y"])
        self.assertIn("[2] B - https://b.example/y", out["preview"])

    def test_allowed_domains_filter_results(self) -> None:
        rows = [{"title": "A", "href": "https://sub.good.com/x", "body": ""}, {"title": "B", "href": "https://evil.net/y", "body": ""}]
        ss.set_value("web_search.allowed_domains", ["good.com"], "test")
        with self.fake_ddgs(rows):
            out = ws.search("q", 5, backend="ddg")
        self.assertEqual([r["url"] for r in out["results"]], ["https://sub.good.com/x"])

    def test_backend_failure_is_a_typed_tool_error(self) -> None:
        module = types.ModuleType("ddgs")
        module.DDGS = lambda: (_ for _ in ()).throw(RuntimeError("rate limited"))
        with patch.dict(sys.modules, {"ddgs": module}):
            with self.assertRaises(ToolRuntimeError):
                ws.search("q", 3, backend="ddg")

    def test_outgoing_query_is_redacted(self) -> None:
        seen = {}
        module = types.ModuleType("ddgs")

        class DDGS:
            def text(self, query, max_results=5):
                seen["q"] = query
                return []

        module.DDGS = DDGS
        with patch.dict(sys.modules, {"ddgs": module}):
            out = ws.search("contact jane.doe@corp.com or 555-123-4567 about apple", 3, backend="ddg")
        self.assertNotIn("jane.doe@corp.com", seen["q"])
        self.assertNotIn("555-123-4567", seen["q"])
        self.assertIn("apple", seen["q"])
        self.assertIn("(no results)", out["preview"])


class BackendChoiceTests(WebCase):
    def test_auto_prefers_openai_only_for_the_openai_provider(self) -> None:
        with patch("harness.web_search.get_settings", return_value=Settings(worker_provider="openai")):
            self.assertEqual(ws.choose_backend(), "openai")
        for provider in ("mistral", "openai_compat"):
            with patch("harness.web_search.get_settings", return_value=Settings(worker_provider=provider)):
                self.assertEqual(ws.choose_backend(), "ddg")

    def test_explicit_backend_wins_over_auto(self) -> None:
        ss.set_value("web_search.backend", "mcp:brave", "test")
        self.assertEqual(ws.choose_backend(), "mcp:brave")
        ss.set_value("web_search.backend", "ddg", "test")
        with patch("harness.web_search.get_settings", return_value=Settings(worker_provider="openai")):
            self.assertEqual(ws.choose_backend(), "ddg")

    def test_mcp_backend_delegates_and_filters(self) -> None:
        fake = types.ModuleType("harness.mcp_client")
        fake.search = lambda server, query, limit: [{"title": "T", "url": "https://x.example/p", "snippet": "s"}]
        with patch.dict(sys.modules, {"harness.mcp_client": fake}), patch("harness.web_search.registry_unused", create=True):
            import harness

            with patch.object(harness, "mcp_client", fake, create=True):
                out = ws.search("q", 3, backend="mcp:brave")
        self.assertEqual(out["results"][0]["url"], "https://x.example/p")
        self.assertEqual(out["backend"], "mcp:brave")

    def test_unknown_backend_is_a_schema_error(self) -> None:
        with self.assertRaises(SchemaInvalidError):
            ws.search("q", 3, backend="altavista")


class ToolGatingTests(WebCase):
    def test_disabled_by_default_even_in_product_mode(self) -> None:
        with self.assertRaises(PolicyDeniedError):
            ws.handle({"query": "apple"}, self.product())

    def test_eval_mode_can_never_search_even_when_enabled(self) -> None:
        ss.set_value("web_search.enabled", True, "test")
        with self.assertRaises(PolicyDeniedError):
            ws.handle({"query": "apple"}, new_context())
        res = ToolRegistry().call("web_search", {"query": "apple"}, new_context())
        self.assertFalse(res.ok)
        self.assertEqual(res.error_class, "PolicyDenied")

    def test_enabled_product_call_returns_citations_through_the_registry(self) -> None:
        ss.set_value("web_search.enabled", True, "test")
        with patch(
            "harness.web_search.search",
            return_value={"results": [{"title": "T", "url": "https://x.example"}], "preview": "[1] T - https://x.example", "answer": ""},
        ) as spy:
            res = ToolRegistry().call("web_search", {"query": "apple news", "max_results": 3}, self.product())
        self.assertTrue(res.ok, res.error)
        self.assertIn("https://x.example", res.preview)
        self.assertEqual(spy.call_args.args[1], 3)

    def test_limited_role_may_search_and_blank_queries_are_rejected(self) -> None:
        ss.set_value("web_search.enabled", True, "test")
        ctx = self.product()
        ctx.role = "limited"
        res = ToolRegistry().call("web_search", {"query": "   "}, ctx)
        self.assertEqual(res.error_class, "SchemaInvalid")  # reached the handler, so the role allowed it

    def test_max_results_is_capped_by_the_admin_setting(self) -> None:
        ss.set_value("web_search.enabled", True, "test")
        ss.set_value("web_search.max_results", 2, "test")
        with patch("harness.web_search.search", return_value={"results": [], "preview": "", "answer": ""}) as spy:
            ws.handle({"query": "x", "max_results": 10}, self.product())
        self.assertEqual(spy.call_args.args[1], 2)

    def test_worker_gets_the_tool_only_when_enabled_in_product_mode(self) -> None:
        ctx = self.product()
        self.assertFalse(ws.is_enabled(ctx))
        ss.set_value("web_search.enabled", True, "test")
        self.assertTrue(ws.is_enabled(ctx))
        self.assertFalse(ws.is_enabled(new_context()))


if __name__ == "__main__":
    unittest.main()
