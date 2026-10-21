"""Lab: card agents that really run, live-schema contract tests, promotion gate, guarded repair, router, quality loop."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from harness import connectors as conn_pkg
from harness import quality
from harness import settings_store as ss
from harness.agent_registry import predeploy_check
from harness.connectors import ConnectorConfig, ConnectorRegistry
from harness.context import new_context
from harness.healing import Healer
from harness.packs import PackRegistry, set_registry
from harness.tool_registry import ToolRegistry
from harness.types import AgentCard, AgentStatus, JudgeCard, WorkerResult
from lab import evaluate as lab_eval
from lab.evaluate import Contract, build_contracts, evaluate, grade, guarded_repair, promotion_status
from lab.synthesizer import synthesize
from packs.enterprise_lake import PACK
from tests.apitest import ApiTestCase, FakeGateway, installed_runtime
from tests.labkit import SQLITE_ROWS, lab_workspace, make_sqlite


class LabCase(unittest.TestCase):
    """Temp Lab workspace, temp state DB, a sqlite connector with entity HRDB, and a fresh pack registry."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        make_sqlite(root / "hr.db")
        self._env = patch.dict(os.environ, {"ORION_STATE_DB": str(root / "s.db"), "ORION_TEST_LAB_DB": f"sqlite:///{(root / 'hr.db').as_posix()}"})
        self._env.start()
        ss.clear_cache()
        set_registry(PackRegistry([PACK]))
        self.connectors = ConnectorRegistry(
            [ConnectorConfig.from_dict({"id": "hrdb", "type": "sqlalchemy", "url_env": "ORION_TEST_LAB_DB", "entity": "HRDB", "tables": ["employees"]})]
        )
        conn_pkg.set_registry(self.connectors)
        self._ws = lab_workspace()
        self.registry, self.generated = self._ws.__enter__()
        self.tools = ToolRegistry()

    def tearDown(self) -> None:
        self._ws.__exit__(None, None, None)
        conn_pkg.set_registry(None)
        set_registry(None)
        self.connectors.clear()
        self._env.stop()
        ss.clear_cache()
        self._tmp.cleanup()

    def card(self, **answers) -> AgentCard:
        spec = {"name": "HR bot", "job": "Answer questions about the HR database", "entities": "HRDB", "tools": "default"}
        spec.update(answers)
        return synthesize(spec, created_by="tester")

    def oracle_runner(self, contracts):
        by_question = {c.question: c for c in contracts}

        def run(question: str, agent_id: str) -> str:
            c = by_question[question]
            return "I abstain: those sources cannot be combined." if c.gold == "abstain" else f"The answer is {c.gold}."

        return run


class SynthesisTests(LabCase):
    def test_default_agents_are_card_only_and_run_the_real_worker(self) -> None:
        card = self.card()
        self.assertIsNone(card.module_path)
        self.assertFalse((self.generated / card.id / "agent.py").exists())
        self.assertTrue((self.generated / card.id / "card.json").exists())
        self.assertEqual(card.status, AgentStatus.DRAFT)
        self.assertEqual(card.metadata["mode"], "card")
        gateway = FakeGateway(["There are 6 employees in the HR database."])
        from workers.generic import run_worker

        with installed_runtime(gateway, agents=self.registry, tools=self.tools):
            ctx = new_context()
            result = run_worker(card, "how many employees?", ctx)
        self.assertTrue(result.ok)
        self.assertEqual(result.text, "There are 6 employees in the HR database.")
        self.assertNotIn("SYNTHESIZED AGENT", result.text)  # the old stub text must never be an answer

    def test_code_mode_still_produces_a_gated_sandboxed_module(self) -> None:
        card = self.card(mode="code")
        path = Path(card.module_path)
        self.assertTrue(path.exists())
        from sandbox.gate import static_gate

        self.assertTrue(static_gate(path).ok)
        self.assertEqual(card.metadata["mode"], "code")

    def test_ids_are_unique_even_within_one_second(self) -> None:
        ids = {self.card(name="same name").id for _ in range(5)}
        self.assertEqual(len(ids), 5)

    def test_unknown_entities_fall_back_and_mcp_wildcards_are_kept(self) -> None:
        card = self.card(entities="NOPE", tools="retrieve, mcp:echo, mcp:*")
        self.assertTrue(card.entities)  # fell back to the pack default entity
        self.assertIn("mcp:echo", card.tools)
        self.assertIn("mcp:*", card.tools)
        self.assertIn("retrieve", card.tools)


class GradingTests(unittest.TestCase):
    def test_integer_gold_needs_the_exact_number(self) -> None:
        c = Contract("count", "q", 1234)
        self.assertTrue(grade(c, "There are 1,234 rows."))
        self.assertFalse(grade(c, "There are 1,235 rows."))
        self.assertFalse(grade(c, "about 1200"))

    def test_mean_allows_half_a_percent(self) -> None:
        c = Contract("mean", "q", 100.0)
        self.assertTrue(grade(c, "The average is 100.4."))
        self.assertFalse(grade(c, "The average is 101."))

    def test_an_integer_valued_mean_still_uses_tolerance(self) -> None:
        self.assertTrue(grade(Contract("mean", "q", 90.0), "average 90.2"))

    def test_abstain_needs_a_refusal(self) -> None:
        c = Contract("abstain", "q", "abstain")
        self.assertTrue(grade(c, "I abstain because these cannot be combined."))
        self.assertFalse(grade(c, "The combined total is 42."))
        self.assertFalse(grade(c, ""))

    def test_numbers_are_parsed_with_signs_and_separators(self) -> None:
        self.assertEqual(lab_eval.numbers_in("a -3.5 and 1,200 and 7"), [-3.5, 1200.0, 7.0])


class ContractGenerationTests(LabCase):
    def test_four_contracts_with_gold_from_the_live_data(self) -> None:
        contracts = build_contracts(self.card(), tools=self.tools)
        by_kind = {c.kind: c for c in contracts}
        self.assertEqual(set(by_kind), {"count", "filter_count", "mean", "abstain"})
        self.assertEqual(by_kind["count"].gold, len(SQLITE_ROWS))
        self.assertEqual(by_kind["filter_count"].gold, 1)
        self.assertAlmostEqual(by_kind["mean"].gold, sum(r[3] for r in SQLITE_ROWS) / len(SQLITE_ROWS))
        self.assertEqual(by_kind["abstain"].gold, "abstain")
        self.assertIn("hrdb.employees", by_kind["count"].question)

    def test_contracts_follow_a_changed_schema_not_a_stored_answer(self) -> None:
        import sqlite3

        card = self.card()
        before = {c.kind: c.gold for c in build_contracts(card, tools=self.tools)}
        conn = sqlite3.connect(Path(self._tmp.name) / "hr.db")
        conn.execute("INSERT INTO employees VALUES (7, 'Gus', 'eng', 200.0)")
        conn.commit()
        conn.close()
        after = {c.kind: c.gold for c in build_contracts(card, tools=self.tools)}
        self.assertEqual(before["count"], 6)
        self.assertEqual(after["count"], 7)
        self.assertNotEqual(before["mean"], after["mean"])

    def test_no_tables_means_no_contracts(self) -> None:
        card = self.card(entities="MSFT")  # entity with no readable table in this workspace
        with patch("lab.evaluate._tables_for", return_value=[]):
            self.assertEqual(build_contracts(card, tools=self.tools), [])

    def test_generation_never_reads_the_benchmark(self) -> None:
        import inspect

        import re

        source = inspect.getsource(lab_eval)
        self.assertIsNone(re.search(r"^\s*(from|import)\s+bench", source, re.M))
        self.assertNotIn("TASKS_DIR", source)  # the task and gold directory
        self.assertNotIn("orion_bench", source)
        self.assertNotIn("SPLIT_LOCK", source)


class EvaluationTests(LabCase):
    def test_a_correct_agent_passes_and_the_verdict_is_persisted(self) -> None:
        card = self.card()
        contracts = build_contracts(card, tools=self.tools)
        result = evaluate(card.id, actor="admin", runner=self.oracle_runner(contracts), contracts=contracts)
        self.assertTrue(result.passed)
        self.assertEqual(result.score, 1.0)
        self.assertEqual(len(result.cases), 4)
        saved = json.loads((self.generated / card.id / "evaluation.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["score"], 1.0)
        self.assertEqual(self.registry.get(card.id).metadata["evaluation"]["version"], card.version)
        self.assertEqual(lab_eval.latest(card.id)["agent_id"], card.id)

    def test_a_wrong_agent_fails(self) -> None:
        card = self.card()
        contracts = build_contracts(card, tools=self.tools)
        result = evaluate(card.id, runner=lambda q, a: "I have no idea, maybe 12345", contracts=contracts)
        self.assertFalse(result.passed)
        self.assertEqual(result.score, 0.0)

    def test_the_threshold_is_the_admin_setting(self) -> None:
        card = self.card()
        contracts = build_contracts(card, tools=self.tools)
        three_of_four = lambda q, a: "abstain, cannot combine" if "combined total" in q else ("wrong" if "average" in q else "The answer is 6 and 1.")  # noqa: E731
        self.assertFalse(evaluate(card.id, runner=three_of_four, contracts=contracts).passed)  # 0.75 < 0.8
        ss.set_value("lab.min_score", 0.7, "test")
        self.assertTrue(evaluate(card.id, runner=three_of_four, contracts=contracts).passed)

    def test_a_crashing_probe_is_a_failed_case_not_a_failed_run(self) -> None:
        card = self.card()
        contracts = build_contracts(card, tools=self.tools)

        def flaky(question: str, agent_id: str) -> str:
            if "average" in question:
                raise RuntimeError("model unavailable")
            return self.oracle_runner(contracts)(question, agent_id)

        result = evaluate(card.id, runner=flaky, contracts=contracts)
        self.assertEqual(result.score, 0.75)
        self.assertIn("model unavailable", next(c for c in result.cases if not c.ok).error)

    def test_an_agent_with_no_testable_tables_cannot_pass(self) -> None:
        card = self.card()
        result = evaluate(card.id, runner=lambda q, a: "x", contracts=[])
        self.assertFalse(result.passed)
        self.assertIn("no contract tests", result.note)

    def test_only_lab_agents_can_be_evaluated(self) -> None:
        with self.assertRaises(ValueError):
            evaluate("hr", runner=lambda q, a: "x", contracts=[])
        with self.assertRaises(ValueError):
            evaluate("ghost", runner=lambda q, a: "x", contracts=[])

    def test_the_summary_text_lists_every_case(self) -> None:
        card = self.card()
        contracts = build_contracts(card, tools=self.tools)
        text = lab_eval.summarize(evaluate(card.id, runner=self.oracle_runner(contracts), contracts=contracts))
        self.assertIn("PASSED", text)
        self.assertIn(f"deploy {card.id}", text)
        self.assertEqual(text.count("- ok ["), 4)


class PromotionGateTests(LabCase):
    def passing(self, card):
        contracts = build_contracts(card, tools=self.tools)
        return evaluate(card.id, runner=self.oracle_runner(contracts), contracts=contracts)

    def failed_checks(self, card):
        return {c["name"]: c["detail"] for c in predeploy_check(card.id)["checks"] if not c["passed"]}

    def test_deploy_needs_an_evaluation(self) -> None:
        card = self.card()
        self.assertIn("evaluation", self.failed_checks(card))
        self.assertIn("not evaluated", self.failed_checks(card)["evaluation"])

    def test_a_passing_evaluation_unlocks_deploy(self) -> None:
        card = self.card()
        self.passing(card)
        self.assertEqual(self.failed_checks(self.registry.get(card.id)), {})
        from harness.agent_registry import deploy_agent

        self.assertTrue(deploy_agent(card.id))
        self.assertEqual(self.registry.get(card.id).status, AgentStatus.DEPLOYED)

    def test_a_failing_evaluation_blocks_deploy(self) -> None:
        card = self.card()
        evaluate(card.id, runner=lambda q, a: "nope", contracts=build_contracts(card, tools=self.tools))
        self.assertIn("below", self.failed_checks(self.registry.get(card.id))["evaluation"])

    def test_a_new_card_version_invalidates_the_old_evaluation(self) -> None:
        card = self.card()
        self.passing(card)
        edited = AgentCard.from_dict({**self.registry.get(card.id).to_dict(), "version": card.version + 1})
        self.registry.update(edited, actor="test")
        self.assertIn("version", self.failed_checks(self.registry.get(card.id))["evaluation"])

    def test_turning_auto_eval_off_removes_the_requirement(self) -> None:
        card = self.card()
        ss.set_value("lab.auto_eval", False, "test")
        self.assertEqual(self.failed_checks(card), {})
        self.assertTrue(promotion_status(card)[0])

    def test_the_wizard_can_evaluate_and_deploy(self) -> None:
        from lab.synthesizer import LabAgent

        card = self.card()
        wizard = LabAgent()
        contracts = build_contracts(card, tools=self.tools)
        self.assertIn("Deploy blocked", wizard.process_message("admin", f"deploy {card.id}"))
        with patch("lab.evaluate.build_contracts", return_value=contracts), patch("lab.evaluate.default_runner", self.oracle_runner(contracts)):
            self.assertIn("PASSED", wizard.process_message("admin", f"evaluate {card.id}"))
        self.assertIn("Deployed", wizard.process_message("admin", f"deploy {card.id}"))


class GuardedRepairTests(LabCase):
    def setUp(self) -> None:
        super().setUp()
        self.card_ = self.card()
        self.contracts = build_contracts(self.card_, tools=self.tools)
        self.good = self.oracle_runner(self.contracts)

    def runner_that_breaks_after_patch(self, question: str, agent_id: str) -> str:
        if "BROKEN" in self.registry.get(agent_id).system_prompt:
            return "no idea"
        return self.good(question, agent_id)

    def patch(self, prompt: str):
        def apply():
            current = self.registry.get(self.card_.id)
            edited = AgentCard.from_dict({**current.to_dict(), "system_prompt": prompt, "version": current.version + 1})
            (self.generated / self.card_.id / "card.json").write_text(json.dumps(edited.to_dict()), encoding="utf-8")
            self.registry.update(edited, actor="repair", note="patch")
            return SimpleNamespace(accepted=True, reason="applied")

        return apply

    def test_a_patch_that_lowers_the_contract_score_is_reverted(self) -> None:
        original_file = (self.generated / self.card_.id / "card.json").read_text(encoding="utf-8")
        original_prompt = self.registry.get(self.card_.id).system_prompt
        original_version = self.registry.get(self.card_.id).version
        out = guarded_repair(self.card_.id, self.patch("BROKEN prompt"), runner=self.runner_that_breaks_after_patch, tools=self.tools)
        self.assertFalse(out["accepted"])
        self.assertTrue(out["rolled_back"])
        self.assertEqual((out["before"], out["after"]), (1.0, 0.0))
        self.assertEqual(self.registry.get(self.card_.id).system_prompt, original_prompt)
        self.assertEqual(self.registry.get(self.card_.id).version, original_version)
        self.assertEqual((self.generated / self.card_.id / "card.json").read_text(encoding="utf-8"), original_file)

    def test_a_judge_score_rise_does_not_excuse_a_contract_drop(self) -> None:
        out = guarded_repair(
            self.card_.id, self.patch("BROKEN prompt"), runner=self.runner_that_breaks_after_patch, tools=self.tools,
            judge_before=1.0, judge_after=2.0,
        )
        self.assertTrue(out["reward_hacked"])
        self.assertTrue(out["rolled_back"])
        self.assertIn("reward hacking", out["reason"])

    def test_a_harmless_patch_is_kept_and_reevaluated(self) -> None:
        out = guarded_repair(self.card_.id, self.patch("A clearer prompt"), runner=self.runner_that_breaks_after_patch, tools=self.tools)
        self.assertTrue(out["accepted"])
        self.assertFalse(out["rolled_back"])
        self.assertEqual(self.registry.get(self.card_.id).system_prompt, "A clearer prompt")
        self.assertEqual(self.registry.get(self.card_.id).metadata["evaluation"]["version"], self.registry.get(self.card_.id).version)

    def test_a_rejected_proposal_changes_nothing(self) -> None:
        out = guarded_repair(
            self.card_.id, lambda: SimpleNamespace(accepted=False, reason="static gate"), runner=self.good, tools=self.tools
        )
        self.assertEqual((out["accepted"], out["rolled_back"], out["reason"]), (False, False, "static gate"))

    def test_the_repair_agent_can_only_write_to_generated_agents(self) -> None:
        from harness.config import PROJECT_ROOT
        from repair.repair_agent import path_allowed

        self.assertTrue(path_allowed(PROJECT_ROOT / "lab" / "generated" / "x" / "card.json"))
        for denied in ("workers/generic.py", "harness/planner.py", "bench/oracles.py", "tests/test_x.py", "backend/api.py", "packs/enterprise_lake/tools.py", "sandbox/gate.py"):
            self.assertFalse(path_allowed(PROJECT_ROOT / denied), denied)

    def test_builtin_agents_are_never_auto_patched(self) -> None:
        from repair.repair_agent import repair

        builtin = self.registry.get("hr")
        proposal = repair(new_context(), builtin, failing_trace="t", judge=JudgeCard(), oracle_diff="d")
        self.assertFalse(proposal.accepted)
        self.assertEqual(proposal.reason, "builtin")


class RouterTests(LabCase):
    def test_card_vectors_are_cached_until_the_registry_reports_a_change(self) -> None:
        from harness import router

        router._VECTORS.clear()
        calls: list[str] = []

        def embed(text: str) -> list[float]:
            calls.append(text)
            return [float(len(text) % 7), 1.0, 0.5]

        routable = self.card()
        self.registry.set_status(routable.id, AgentStatus.ACTIVE)  # DRAFT agents are not in the routing pool
        ctx = new_context()
        with installed_runtime(FakeGateway(), agents=self.registry), patch("retrieval.embeddings.get_embedding", embed):
            router.shortlist(ctx, "how many employees")
            first = len(calls)
            router.shortlist(ctx, "how many employees")
            self.assertEqual(len(calls) - first, 1)  # only the query is embedded the second time
            card = next(c for c in self.registry.custom())
            edited = AgentCard.from_dict({**card.to_dict(), "intent": "a brand new purpose"})
            self.registry.update(edited, actor="t")
            before = len(calls)
            router.shortlist(ctx, "how many employees")
            self.assertEqual(len(calls) - before, 2)  # the changed card plus the query

    def test_a_promoted_agent_is_routable_immediately_and_a_paused_one_disappears(self) -> None:
        from harness import router

        card = self.card()
        ctx = new_context()
        with installed_runtime(FakeGateway(), agents=self.registry), patch("retrieval.embeddings.get_embedding", side_effect=RuntimeError("no model")):
            ids = lambda: {c.id for c in router.shortlist(ctx, "HR database employees", k=64)}  # noqa: E731
            self.assertNotIn(card.id, ids())  # DRAFT agents are not routed
            self.registry.set_status(card.id, AgentStatus.ACTIVE)
            self.assertIn(card.id, ids())
            self.registry.set_status(card.id, AgentStatus.PAUSED)
            self.assertNotIn(card.id, ids())

    def test_heal_reroute_considers_every_routable_agent_and_picks_the_best_other(self) -> None:
        from harness import planner

        chosen = AgentCard(id="a", name="A", description="d", intent="i", tools=["retrieve"], status=AgentStatus.ACTIVE, is_custom=True, is_builtin=False)
        better = AgentCard(id="b", name="B", description="d", intent="i", tools=["retrieve"], status=AgentStatus.ACTIVE, is_custom=True, is_builtin=False)
        self.registry.register(chosen, actor="t")
        self.registry.register(better, actor="t")
        task = planner.Task(id="t1", description="q", agent="a")
        replies = [WorkerResult(text="wrong domain", ok=True, domain_mismatch=True), WorkerResult(text="fixed", ok=True)]
        seen: list = []

        def spy(ctx, query, k=3):
            seen.append(k)
            return [better, chosen]  # b ranks first, but is outside a naive top-k shortlist

        ctx = new_context()
        ctx.budget.max_heal_cycles = 2
        with installed_runtime(FakeGateway(), agents=self.registry, healer=Healer()), patch.object(planner, "shortlist", spy), patch.object(
            planner, "run_worker", side_effect=replies
        ):
            done = planner._execute_with_heal(task, "q", ctx)
        self.assertGreaterEqual(seen[0], 10)
        self.assertEqual(done.agent, "b")
        self.assertEqual(done.result, "fixed")


class QualityLoopTests(LabCase):
    def weak(self, **kw):
        return JudgeCard(overall=0, grounded=False, complete=False, safe=True, failure_class="F4", repair_hint="use schema_lookup", **kw)

    def strong(self):
        return JudgeCard(overall=2, grounded=True, complete=True, safe=True, failure_class="none")

    def run_judge(self, card, **kw):
        return quality.judge_and_queue(
            tenant="default", request_id="REQ-1", query="how many?", answer="12", agents=["hr"], judge=lambda ctx, **k: card, **kw
        )

    def test_sampling_follows_the_rate(self) -> None:
        self.assertFalse(quality.should_sample(0.0))
        self.assertTrue(quality.should_sample(1.0, rng=lambda: 0.99))
        self.assertTrue(quality.should_sample(0.3, rng=lambda: 0.1))
        self.assertFalse(quality.should_sample(0.3, rng=lambda: 0.5))

    def test_the_default_rate_samples_nothing(self) -> None:
        with patch("harness.quality.threading.Thread") as thread:
            self.assertFalse(quality.maybe_sample(tenant="default", request_id="r", query="q", answer="a", agents=[]))
        thread.assert_not_called()

    def test_a_positive_rate_starts_a_background_judge(self) -> None:
        ss.set_value("quality.sample_rate", 1.0, "test")
        with patch("harness.quality.threading.Thread") as thread:
            self.assertTrue(quality.maybe_sample(tenant="default", request_id="r", query="q", answer="a", agents=["hr"]))
        thread.return_value.start.assert_called_once()

    def test_weak_answers_are_queued_and_strong_ones_are_not(self) -> None:
        self.assertIsNone(self.run_judge(self.strong()))
        item_id = self.run_judge(self.weak())
        queue = quality.list_queue(tenant="default", status="pending")
        self.assertEqual([i["id"] for i in queue], [item_id])
        self.assertEqual(queue[0]["agent_id"], "hr")
        self.assertEqual(queue[0]["judge"]["failure_class"], "F4")
        self.assertEqual(queue[0]["reason"], "use schema_lookup")

    def test_a_judge_failure_never_raises_into_the_request(self) -> None:
        def boom(ctx, **k):
            raise RuntimeError("judge model down")

        with self.assertLogs("orion.quality", level="ERROR"):
            self.assertIsNone(quality.judge_and_queue(tenant="default", request_id="r", query="q", answer="a", agents=[], judge=boom))

    def test_the_queue_is_tenant_scoped_and_decisions_are_final(self) -> None:
        item_id = self.run_judge(self.weak())
        self.assertEqual(quality.list_queue(tenant="other"), [])
        self.assertTrue(quality.decide(item_id, tenant="default", status="dismissed", actor="admin"))
        self.assertFalse(quality.decide(item_id, tenant="default", status="resolved", actor="admin"))
        self.assertEqual(quality.list_queue(tenant="default", status="pending"), [])
        with self.assertRaises(ValueError):
            quality.decide(item_id, tenant="default", status="bogus", actor="admin")

    def test_builtin_agents_are_refused_repair(self) -> None:
        item_id = self.run_judge(self.weak())  # agent "hr" is built in
        with self.assertRaises(PermissionError):
            quality.apply_repair(item_id, tenant="default", actor="admin")

    def test_an_approved_repair_of_a_lab_agent_goes_through_the_contract_guard(self) -> None:
        card = self.card()
        contracts = build_contracts(card, tools=self.tools)
        item_id = quality.enqueue(
            tenant="default", request_id="REQ-2", agent_id=card.id, query="q", answer="a", judge=self.weak().to_dict(), reason="bad"
        )

        def bad_patch():
            current = self.registry.get(card.id)
            self.registry.update(AgentCard.from_dict({**current.to_dict(), "system_prompt": "BROKEN", "version": current.version + 1}), actor="r")
            return SimpleNamespace(accepted=True, reason="applied")

        def runner(question: str, agent_id: str) -> str:
            if "BROKEN" in self.registry.get(agent_id).system_prompt:
                return "no idea"
            return self.oracle_runner(contracts)(question, agent_id)

        with patch("lab.evaluate.build_contracts", return_value=contracts):
            outcome = quality.apply_repair(item_id, tenant="default", actor="admin", runner=runner, repair_fn=bad_patch)
        self.assertTrue(outcome["rolled_back"])
        self.assertNotIn("BROKEN", self.registry.get(card.id).system_prompt)
        self.assertEqual(quality.list_queue(tenant="default", status="dismissed")[0]["id"], item_id)


class LabApiTests(ApiTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        make_sqlite(Path(self._tmp.name) / "hr.db")
        self._env = patch.dict(os.environ, {"ORION_TEST_LAB_DB": f"sqlite:///{(Path(self._tmp.name) / 'hr.db').as_posix()}"})
        self._env.start()
        set_registry(PackRegistry([PACK]))
        self.connectors = ConnectorRegistry(
            [ConnectorConfig.from_dict({"id": "hrdb", "type": "sqlalchemy", "url_env": "ORION_TEST_LAB_DB", "entity": "HRDB", "tables": ["employees"]})]
        )
        conn_pkg.set_registry(self.connectors)
        self._ws = lab_workspace()
        self.registry, self.generated = self._ws.__enter__()
        self.spec = {"name": "API bot", "job": "Answer HR questions", "entities": ["HRDB"], "tools": "default"}

    def tearDown(self) -> None:
        self._ws.__exit__(None, None, None)
        conn_pkg.set_registry(None)
        set_registry(None)
        self.connectors.clear()
        self._env.stop()
        self._tmp.cleanup()

    def test_all_lab_routes_are_admin_only(self) -> None:
        user = self.make_user("lab-plain")
        for method, path, body in [
            ("get", "/lab/agents", None), ("post", "/lab/agents", self.spec), ("get", "/lab/agents/x", None),
            ("post", "/lab/agents/x/evaluate", None), ("get", "/lab/agents/x/evaluation", None), ("post", "/lab/agents/x/deploy", None),
            ("get", "/admin/quality/queue", None), ("post", "/admin/quality/queue/x", {"action": "dismiss"}),
        ]:
            res = getattr(self.client, method)(path, headers=user, **({"json": body} if body is not None else {}))
            self.assertEqual(res.status_code, 403, path)

    def test_create_evaluate_and_deploy_end_to_end(self) -> None:
        admin = self.admin()
        created = self.client.post("/lab/agents", headers=admin, json=self.spec)
        self.assertEqual(created.status_code, 201, created.text)
        card = created.json()
        self.assertEqual((card["status"], card["mode"]), ("draft", "card"))
        self.assertIn(card["id"], [a["id"] for a in self.client.get("/lab/agents", headers=admin).json()])
        blocked = self.client.post(f"/lab/agents/{card['id']}/deploy", headers=admin)
        self.assertEqual((blocked.status_code, blocked.json()["error"]["code"]), (409, "predeploy_failed"))
        self.assertEqual(self.client.get(f"/lab/agents/{card['id']}/evaluation", headers=admin).status_code, 404)
        contracts = build_contracts(self.registry.get(card["id"]), tools=ToolRegistry())
        by_question = {c.question: c for c in contracts}
        answer = lambda q, a: "I abstain, cannot combine." if by_question[q].gold == "abstain" else f"It is {by_question[q].gold}"  # noqa: E731
        with patch("lab.evaluate.build_contracts", return_value=contracts), patch("lab.evaluate.default_runner", answer):
            result = self.client.post(f"/lab/agents/{card['id']}/evaluate", headers=admin)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["passed"])
        self.assertEqual(self.client.get(f"/lab/agents/{card['id']}/evaluation", headers=admin).json()["score"], 1.0)
        deployed = self.client.post(f"/lab/agents/{card['id']}/deploy", headers=admin)
        self.assertEqual((deployed.status_code, deployed.json()["status"]), (200, "deployed"))

    def test_validation(self) -> None:
        admin = self.admin()
        bad = self.client.post("/lab/agents", headers=admin, json={**self.spec, "entities": ["NOT_AN_ENTITY"]})
        self.assertEqual((bad.status_code, bad.json()["error"]["code"]), (400, "unknown_entities"))
        self.assertEqual(self.client.post("/lab/agents", headers=admin, json={**self.spec, "mode": "weird"}).status_code, 422)
        self.assertEqual(self.client.post("/lab/agents", headers=admin, json={"name": "", "job": "x"}).status_code, 422)
        for path in ("/lab/agents/ghost", "/lab/agents/ghost/evaluation"):
            self.assertEqual(self.client.get(path, headers=admin).status_code, 404)
        self.assertEqual(self.client.post("/lab/agents/hr/evaluate", headers=admin).status_code, 404)  # built-in

    def test_quality_queue_api(self) -> None:
        admin = self.admin()
        item = quality.enqueue(tenant="default", request_id="R9", agent_id="hr", query="q", answer="a", judge={"overall": 0}, reason="r")
        listed = self.client.get("/admin/quality/queue", headers=admin).json()
        self.assertIn(item, [i["id"] for i in listed])
        self.assertEqual(self.client.post(f"/admin/quality/queue/{item}", headers=admin, json={"action": "repair"}).status_code, 409)
        self.assertEqual(self.client.post(f"/admin/quality/queue/{item}", headers=admin, json={"action": "dismiss"}).json()["status"], "dismissed")
        self.assertEqual(self.client.post(f"/admin/quality/queue/{item}", headers=admin, json={"action": "dismiss"}).status_code, 404)
        self.assertEqual(self.client.post("/admin/quality/queue/rq_none", headers=admin, json={"action": "repair"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
