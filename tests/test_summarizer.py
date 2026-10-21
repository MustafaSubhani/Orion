"""Verified abstractive summarization: accepted when faithful, rejected (with fallback) when not."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness import settings_store as ss
from harness import summarizer, threads
from harness.config import ModelTiers, Settings
from harness.context import new_context
from harness.context_engine import MIN_SUMMARY_TOKENS, pack_for_model
from harness.errors import TimeoutError_
from tests.apitest import FakeGateway, installed_runtime

ART = "artifact:abc123def456"
INPUT_TURNS = [
    {"role": "user", "content": "How many employees are in IT for MNC on 2024-05-15?"},
    {"role": "assistant", "content": f"There are 1,234 employees. Evidence is in {ART}. Average salary 55.5."},
]
FAITHFUL = f"Goal: IT headcount.\nFacts established: 1234 employees (MNC) on 2024-05-15, average 55.5, see {ART}.\nTools called and outcomes: none.\nOpen questions: none."


class SummaryCase(unittest.TestCase):
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


class FactTests(unittest.TestCase):
    def test_numbers_dates_artifacts_and_entities_are_extracted(self) -> None:
        found = summarizer.facts(f"MNC had 1,234 staff on 2024-05-15 (-3.5%), see {ART}. Also AAPL.")
        self.assertTrue({"1234", "2024-05-15", "-3.5", ART, "MNC", "AAPL"} <= found, found)

    def test_list_markers_are_not_facts(self) -> None:
        self.assertEqual(summarizer.facts("1. first point\n2. second point"), set())
        self.assertIn("2", summarizer.facts("2 employees left"))

    def test_trailing_punctuation_and_thousands_separators_normalise(self) -> None:
        self.assertEqual(summarizer.facts("It was 1,234."), {"1234"})
        self.assertEqual(summarizer.facts("It was 1234"), {"1234"})

    def test_extractive_fallback_keeps_the_old_summary_and_every_turn(self) -> None:
        out = summarizer.extractive_summary("earlier", [{"role": "user", "content": "x " * 300}, {"role": "assistant", "content": "y"}])
        self.assertTrue(out.startswith("earlier"))
        self.assertIn("- assistant: y", out)
        self.assertLessEqual(len(out), 3000)


class SummarizeTurnsTests(SummaryCase):
    def run_with(self, gateway, ctx=None):
        ctx = ctx or self.product()
        with installed_runtime(gateway):
            return summarizer.summarize_turns(ctx, INPUT_TURNS), ctx

    def test_a_faithful_summary_is_accepted(self) -> None:
        gateway = FakeGateway([FAITHFUL])
        (summary, reason), _ = self.run_with(gateway)
        self.assertEqual((summary, reason), (FAITHFUL, "ok"))
        call = gateway.calls[0]
        self.assertEqual(call["purpose"], "summarize")
        self.assertEqual(call["max_tokens"], 400)
        self.assertIn("Copy every number", call["messages"][0]["content"])

    def test_a_summary_that_drops_a_number_is_rejected(self) -> None:
        bad = FAITHFUL.replace("55.5", "about fifty")
        (summary, reason), _ = self.run_with(FakeGateway([bad]))
        self.assertIsNone(summary)
        self.assertEqual(reason, "dropped_facts:1")

    def test_a_summary_that_drops_an_artifact_id_is_rejected(self) -> None:
        (summary, reason), _ = self.run_with(FakeGateway([FAITHFUL.replace(ART, "the earlier table")]))
        self.assertIsNone(summary)
        self.assertTrue(reason.startswith("dropped_facts"))

    def test_a_summary_that_drops_the_entity_tag_or_date_is_rejected(self) -> None:
        self.assertIsNone(self.run_with(FakeGateway([FAITHFUL.replace("MNC", "the company")]))[0][0])
        self.assertIsNone(self.run_with(FakeGateway([FAITHFUL.replace("2024-05-15", "last spring")]))[0][0])

    def test_a_timeout_or_api_error_falls_back_without_raising(self) -> None:
        (summary, reason), _ = self.run_with(FakeGateway(error=TimeoutError_("slow")))
        self.assertIsNone(summary)
        self.assertEqual(reason, "error:Timeout")

    def test_an_empty_reply_is_rejected(self) -> None:
        (summary, reason), _ = self.run_with(FakeGateway(["   "]))
        self.assertEqual((summary, reason), (None, "empty_summary"))

    def test_repeating_the_same_prefix_hits_the_cache(self) -> None:
        gateway = FakeGateway([FAITHFUL, "should not be used"])
        ctx = self.product()
        with installed_runtime(gateway):
            first = summarizer.summarize_turns(ctx, INPUT_TURNS)
            second = summarizer.summarize_turns(ctx, INPUT_TURNS)
        self.assertEqual(first, (FAITHFUL, "ok"))
        self.assertEqual(second, (FAITHFUL, "cache"))
        self.assertEqual(len(gateway.calls), 1)

    def test_a_rejected_summary_is_not_cached(self) -> None:
        gateway = FakeGateway(["wrong 99999", FAITHFUL])
        ctx = self.product()
        with installed_runtime(gateway):
            self.assertIsNone(summarizer.summarize_turns(ctx, INPUT_TURNS)[0])
            self.assertEqual(summarizer.summarize_turns(ctx, INPUT_TURNS)[1], "ok")

    def test_eval_mode_makes_zero_model_calls(self) -> None:
        gateway = FakeGateway([FAITHFUL])
        (summary, reason), _ = self.run_with(gateway, ctx=new_context())
        self.assertEqual((summary, reason), (None, "eval_mode"))
        self.assertEqual(gateway.calls, [])

    def test_the_setting_can_turn_it_off(self) -> None:
        ss.set_value("context.summarize", "extractive", "test")
        gateway = FakeGateway([FAITHFUL])
        (summary, reason), _ = self.run_with(gateway)
        self.assertEqual((summary, reason), (None, "disabled"))
        self.assertEqual(gateway.calls, [])

    def test_tokens_are_charged_to_the_request_budget(self) -> None:
        (_, _), ctx = self.run_with(FakeGateway([FAITHFUL]))
        self.assertEqual(ctx.budget.tokens_used, 15)


def long_conversation(turns: int = 8, size: int = 400) -> list[dict]:
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "first question"}]
    for i in range(turns):
        msgs.append({"role": "assistant" if i % 2 == 0 else "user", "content": f"turn {i} 42 " + "word " * size})
    return msgs


class PackerIntegrationTests(unittest.TestCase):
    KW = dict(model="mistral-large", output_reserve=512, tail_keep=2, window_override=4000, compact_ratio=0.75)

    def test_the_summary_replaces_the_middle_turns(self) -> None:
        calls = []

        def summarize(turns):
            calls.append(turns)
            return "SUMMARY 42", "ok"

        out = pack_for_model(long_conversation(), summarize=summarize, **self.KW)
        self.assertTrue(out.summarized)
        self.assertEqual(out.summary_reason, "ok")
        self.assertTrue(any(m["content"].startswith("[context summary]") for m in out.messages))
        self.assertEqual(len(calls), 1)
        self.assertEqual(out.messages[0]["content"], "sys")  # system prompt and first user turn are kept
        self.assertEqual(out.messages[1]["content"], "first question")

    def test_a_rejected_summary_falls_back_to_the_extractive_fold(self) -> None:
        out = pack_for_model(long_conversation(), summarize=lambda turns: (None, "dropped_facts:2"), **self.KW)
        self.assertFalse(out.summarized)
        self.assertEqual(out.summary_reason, "dropped_facts:2")
        self.assertTrue(out.middle_folded)

    def test_without_a_summarizer_behaviour_is_the_old_fold(self) -> None:
        out = pack_for_model(long_conversation(), **self.KW)
        self.assertTrue(out.middle_folded)
        self.assertFalse(out.summarized)
        self.assertEqual(out.summary_reason, "")

    def test_small_middles_are_not_worth_a_model_call(self) -> None:
        msgs = long_conversation(turns=2, size=10)
        called = []
        out = pack_for_model(msgs, summarize=lambda t: called.append(t) or ("S", "ok"), **{**self.KW, "window_override": 600})
        self.assertEqual(called, [])
        self.assertLess(MIN_SUMMARY_TOKENS, 1000)

    def test_a_tail_never_starts_with_an_orphaned_tool_result(self) -> None:
        msgs = long_conversation(turns=6)
        msgs.insert(-1, {"role": "tool", "tool_call_id": "c1", "content": "x " * 10})
        msgs.append({"role": "assistant", "content": "final " * 5})
        out = pack_for_model(msgs, summarize=lambda t: ("S 42", "ok"), **self.KW)
        tail_start = next(i for i, m in enumerate(out.messages) if m["content"].startswith("[context summary]")) + 1
        self.assertNotEqual(out.messages[tail_start]["role"], "tool")

    def test_fold_off_skips_the_extractive_fold(self) -> None:
        out = pack_for_model(long_conversation(), fold=False, **self.KW)
        self.assertFalse(out.middle_folded)


class GatewayIntegrationTests(SummaryCase):
    """The real gateway wires the summarizer in for live chat only, and the summarizer never recurses."""

    def make_gateway(self, **settings):
        from harness.gateway import LLMGateway

        base = dict(mistral_api_key="k", worker_api_key="k", judge_api_key="k", context_window=4000, context_tail=2,
                    tiers=ModelTiers(router="router-model", worker="worker-model", judge="j"))
        base.update(settings)
        return LLMGateway(Settings(**base))

    def exchange(self, ctx, replies):
        from tests.test_gateway_providers import FakeHttp, reply

        http = FakeHttp([reply(200, {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}) for text in replies])
        gateway = self.make_gateway()
        with patch("harness.gateway.httpx.Client", http), installed_runtime(gateway):
            gateway.complete(ctx=ctx, messages=long_conversation(), max_tokens=512, purpose="worker")
        return http

    def test_live_chat_summarizes_with_the_router_tier_then_answers(self) -> None:
        http = self.exchange(self.product(), ["Turns 0 1 2 3 4 5 each mention 42.", "final answer"])
        models = [c["json"]["model"] for c in http.calls]
        self.assertEqual(models, ["router-model", "worker-model"])
        self.assertIn("[context summary]", str(http.calls[1]["json"]["messages"]))
        self.assertIn("summarize", str(http.calls[0]["json"]["messages"][0]["content"]).lower())

    def test_a_summary_that_loses_facts_is_replaced_by_the_extractive_fold(self) -> None:
        http = self.exchange(self.product(), ["a vague summary", "final answer"])
        sent = str(http.calls[1]["json"]["messages"])
        self.assertNotIn("[context summary]", sent)
        self.assertIn("[context compact]", sent)

    def test_eval_mode_never_calls_the_summarizer(self) -> None:
        http = self.exchange(new_context(), ["final answer"])
        self.assertEqual([c["json"]["model"] for c in http.calls], ["worker-model"])
        self.assertNotIn("[context summary]", str(http.calls[0]["json"]["messages"]))

    def test_off_disables_folding_entirely(self) -> None:
        ss.set_value("context.summarize", "off", "test")
        http = self.exchange(self.product(), ["final answer"])
        self.assertEqual(len(http.calls), 1)
        self.assertNotIn("Older turns were folded", str(http.calls[0]["json"]["messages"]))


if __name__ == "__main__":
    unittest.main()
