"""Provider resolution (mistral parity, openai, openai_compat) and the OpenAI-specific wire details."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from harness import gateway as gw
from harness.config import ModelTiers, Settings, get_settings
from harness.context import new_context
from harness.errors import ToolRuntimeError


def settings(**kw) -> Settings:
    base = dict(
        mistral_api_key="m-key",
        worker_provider="mistral",
        router_provider="mistral",
        judge_provider="mistral",
        worker_api_key="m-key",
        judge_api_key="m-key",
        tiers=ModelTiers(router="r-model", worker="w-model", judge="j-model"),
    )
    base.update(kw)
    return Settings(**base)


class ResolveEndpointTests(unittest.TestCase):
    def test_mistral_resolution_is_unchanged(self) -> None:
        s = settings()
        for purpose, model in [("worker", "w-model"), ("router", "r-model"), ("judge", "j-model"), ("repair", "w-model"), ("lab", "w-model")]:
            provider, url, key, chosen = gw._resolve_endpoint(s, purpose, None)
            self.assertEqual((provider, url, key, chosen), ("mistral", "https://api.mistral.ai/v1/chat/completions", "m-key", model))

    def test_summarize_uses_the_router_tier(self) -> None:
        self.assertEqual(gw._resolve_endpoint(settings(), "summarize", None)[3], "r-model")

    def test_openai_resolution(self) -> None:
        s = settings(worker_provider="openai", worker_api_key="sk-w", openai_api_key="sk-o")
        provider, url, key, model = gw._resolve_endpoint(s, "worker", "gpt-x")
        self.assertEqual((provider, url, key, model), ("openai", "https://api.openai.com/v1/chat/completions", "sk-w", "gpt-x"))
        s = settings(judge_provider="openai", judge_api_key="", openai_api_key="sk-o")
        self.assertEqual(gw._resolve_endpoint(s, "judge", "gpt-j")[2], "sk-o")

    def test_openai_base_url_override(self) -> None:
        s = settings(worker_provider="openai", openai_api_key="k", openai_base_url="https://proxy.corp/v1/")
        self.assertEqual(gw._resolve_endpoint(s, "worker", "m")[1], "https://proxy.corp/v1/chat/completions")

    def test_compat_resolution(self) -> None:
        s = settings(worker_provider="openai_compat", worker_base_url="http://127.0.0.1:11434/v1", worker_api_key="")
        provider, url, key, _ = gw._resolve_endpoint(s, "worker", None)
        self.assertEqual((provider, url, key), ("openai_compat", "http://127.0.0.1:11434/v1/chat/completions", "local"))

    def test_mixed_providers_per_purpose(self) -> None:
        s = settings(worker_provider="openai", openai_api_key="sk-o", worker_api_key="sk-o", judge_provider="mistral")
        self.assertEqual(gw._resolve_endpoint(s, "worker", "gpt-x")[0], "openai")
        self.assertEqual(gw._resolve_endpoint(s, "judge", None)[0], "mistral")


class SettingsEnvTests(unittest.TestCase):
    def test_openai_key_is_used_when_the_provider_is_openai(self) -> None:
        env = {"ORION_WORKER_PROVIDER": "openai", "OPENAI_API_KEY": "sk-env", "MISTRAL_API_KEY": "m-env"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("ORION_WORKER_API_KEY", None)
            get_settings.cache_clear()
            try:
                s = get_settings()
                self.assertEqual((s.worker_provider, s.worker_api_key, s.openai_api_key), ("openai", "sk-env", "sk-env"))
            finally:
                get_settings.cache_clear()

    def test_mistral_default_is_unchanged(self) -> None:
        with patch.dict(os.environ, {"MISTRAL_API_KEY": "m-env"}, clear=False):
            for name in ("ORION_WORKER_PROVIDER", "ORION_WORKER_API_KEY", "ORION_JUDGE_PROVIDER"):
                os.environ.pop(name, None)
            get_settings.cache_clear()
            try:
                s = get_settings()
                self.assertEqual((s.worker_provider, s.worker_api_key, s.judge_api_key), ("mistral", "m-env", "m-env"))
            finally:
                get_settings.cache_clear()

    def test_previously_ignored_env_vars_are_now_read(self) -> None:
        env = {
            "ORION_CIRCUIT_THRESHOLD": "7", "ORION_ROUTER_TOPK": "5", "ORION_RETRIEVE_K": "11",
            "ORION_PARALLEL_WORKERS": "6", "ORION_NODE_TIMEOUT": "42", "ORION_CORS_ORIGINS": "https://a.example, https://b.example",
        }
        with patch.dict(os.environ, env):
            get_settings.cache_clear()
            try:
                s = get_settings()
                self.assertEqual((s.circuit_fail_threshold, s.router_topk, s.retrieve_k, s.parallel_workers, s.node_timeout_s), (7, 5, 11, 6, 42.0))
                self.assertEqual(s.cors_origins, ("https://a.example", "https://b.example"))
            finally:
                get_settings.cache_clear()


def reply(status: int = 200, body: dict | None = None) -> MagicMock:
    response = MagicMock(status_code=status, text=str(body or ""))
    response.json.return_value = body or {"choices": [{"message": {"content": "ok"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
    response.raise_for_status.return_value = None
    return response


class FakeHttp:
    """Stands in for httpx.Client and records each POST."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, *a, **k):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": dict(json), "headers": headers})
        return self.responses.pop(0)


class OpenAIWireTests(unittest.TestCase):
    def complete(self, s: Settings, http: FakeHttp, **kw):
        with patch("harness.gateway.httpx.Client", http), patch("harness.gateway.time.sleep"):
            return gw.LLMGateway(s).complete(ctx=new_context(), messages=[{"role": "user", "content": "hi"}], purpose="worker", **kw)

    def openai(self, **kw) -> Settings:
        return settings(worker_provider="openai", worker_api_key="sk-w", openai_api_key="sk-w", tiers=ModelTiers("r", "gpt-test", "j"), **kw)

    def test_openai_sends_max_completion_tokens_and_a_bearer(self) -> None:
        http = FakeHttp([reply()])
        out = self.complete(self.openai(), http, max_tokens=321)
        call = http.calls[0]
        self.assertEqual(out.text, "ok")
        self.assertTrue(call["url"].startswith("https://api.openai.com/v1/"))
        self.assertEqual(call["json"]["max_completion_tokens"], 321)
        self.assertNotIn("max_tokens", call["json"])
        self.assertEqual(call["headers"]["Authorization"], "Bearer sk-w")

    def test_mistral_still_sends_max_tokens(self) -> None:
        http = FakeHttp([reply()])
        self.complete(settings(), http, max_tokens=99)
        self.assertEqual(http.calls[0]["json"]["max_tokens"], 99)
        self.assertNotIn("max_completion_tokens", http.calls[0]["json"])

    def test_temperature_is_dropped_when_the_model_rejects_it(self) -> None:
        rejection = reply(400, {"error": {"message": "Unsupported value: 'temperature' does not support 0.1 with this model."}})
        http = FakeHttp([rejection, reply()])
        out = self.complete(self.openai(), http)
        self.assertEqual(out.text, "ok")
        self.assertIn("temperature", http.calls[0]["json"])
        self.assertNotIn("temperature", http.calls[1]["json"])

    def test_missing_key_and_wrong_model_family_are_clear_errors(self) -> None:
        no_key = self.openai()
        no_key.worker_api_key = ""
        no_key.openai_api_key = ""
        with self.assertRaises(ToolRuntimeError) as err:
            self.complete(no_key, FakeHttp([]))
        self.assertIn("OPENAI_API_KEY", str(err.exception))
        wrong = settings(worker_provider="openai", worker_api_key="sk", openai_api_key="sk")  # still on the Mistral model ids
        wrong.tiers = ModelTiers("r", "open-mistral-nemo", "j")
        with self.assertRaises(ToolRuntimeError) as err:
            self.complete(wrong, FakeHttp([]))
        self.assertIn("ORION_WORKER_MODEL", str(err.exception))

    def test_token_usage_is_charged_to_the_budget(self) -> None:
        ctx = new_context()
        with patch("harness.gateway.httpx.Client", FakeHttp([reply()])):
            gw.LLMGateway(self.openai()).complete(ctx=ctx, messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.budget.tokens_used, 5)


if __name__ == "__main__":
    unittest.main()
