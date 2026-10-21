"""Declared F7 > F8 > F4 > F1 > F2 > F5 > F3 > F6 precedence, plus the Phase 1 healing additions."""

from __future__ import annotations

import itertools
import unittest

from harness.context import new_context
from harness.graph import Checkpoint, GraphNode, RunGraph, build_run_graph
from harness.errors import StateIntegrityError
from harness.healing import PRECEDENCE, FailureClass, Healer
from harness.tool_registry import rebind_verb
from harness.types import TaskStatus, WorkerResult
from workers.generic import _collapse_failure, _typed_error_line

SIGNALS = {
    FailureClass.F7_POLICY: {"blocked": True},
    FailureClass.F8_CASCADE: {"upstream_corrupt": True},
    FailureClass.F4_SCHEMA: {"unknown_column": True},
    FailureClass.F1_TOOL: {"result": WorkerResult(text="boom", ok=False, error_class="ToolRuntime")},
    FailureClass.F2_RETRIEVAL: {"empty_retrieval": True},
    FailureClass.F5_DECOMPOSITION: {"incomplete_plan": True},
    FailureClass.F3_ROUTING: {"domain_mismatch": True},
    FailureClass.F6_SEMANTIC: {"state_integrity_ok": False},
}


class PrecedenceTests(unittest.TestCase):
    def test_precedence_order_is_the_declared_one(self) -> None:
        self.assertEqual([c.value for c in PRECEDENCE], ["F7", "F8", "F4", "F1", "F2", "F5", "F3", "F6"])

    def test_every_pair_resolves_to_the_higher_class(self) -> None:
        healer = Healer()
        for a, b in itertools.combinations(PRECEDENCE, 2):
            merged = {**SIGNALS[a], **SIGNALS[b]}
            self.assertEqual(healer.classify(**merged), a, f"{a.value} must beat {b.value}")

    def test_all_signals_at_once_resolve_to_f7(self) -> None:
        merged: dict = {}
        for sig in SIGNALS.values():
            merged.update(sig)
        self.assertEqual(Healer().classify(**merged), FailureClass.F7_POLICY)

    def test_each_signal_alone_maps_to_its_class(self) -> None:
        for cls, sig in SIGNALS.items():
            self.assertEqual(Healer().classify(**sig), cls)

    def test_f5_never_fires_without_its_signal(self) -> None:
        self.assertEqual(Healer().classify(result=WorkerResult(text="ok", ok=True)), FailureClass.NONE)

    def test_state_integrity_error_class_is_f6(self) -> None:
        res = WorkerResult(text="x", ok=False, error_class="StateIntegrityError")
        self.assertEqual(Healer().classify(result=res), FailureClass.F6_SEMANTIC)


class RebindTests(unittest.TestCase):
    ALLOWED = ["sql_spec", "retrieve", "schema_lookup", "date_at"]

    def test_misspelled_verb_is_rebound(self) -> None:
        self.assertEqual(rebind_verb("sql_specc", self.ALLOWED), "sql_spec")
        self.assertEqual(rebind_verb("retreive", self.ALLOWED), "retrieve")

    def test_unrelated_verb_is_not_rebound(self) -> None:
        self.assertIsNone(rebind_verb("drop_database", self.ALLOWED))

    def test_rebind_never_leaves_the_allowed_set(self) -> None:
        self.assertIsNone(rebind_verb("http_fetchh", self.ALLOWED))


class PruningTests(unittest.TestCase):
    def test_error_line_is_one_typed_line_without_traceback(self) -> None:
        text = "Traceback (most recent call last):\n  File \"x.py\", line 1\nKeyError: 'a'"
        line = _typed_error_line("ToolRuntime", text)
        self.assertNotIn("\n", line)
        self.assertTrue(line.startswith("[ToolRuntime]"))

    def test_schema_error_carries_a_hint(self) -> None:
        self.assertIn("Hint:", _typed_error_line("SchemaInvalid", "unknown column 'x'"))

    def test_recovered_failure_is_collapsed(self) -> None:
        messages = [
            {"role": "tool", "tool_call_id": "c1", "content": "[SchemaInvalid] unknown column"},
            {"role": "tool", "tool_call_id": "c2", "content": "ok"},
        ]
        _collapse_failure(messages, "c1", "sql_spec")
        self.assertIn("[recovered]", messages[0]["content"])
        self.assertEqual(messages[1]["content"], "ok")


class CheckpointTests(unittest.TestCase):
    def _graph(self) -> RunGraph:
        graph = RunGraph()
        graph.add_node(GraphNode(id="n1", agent="a", description="d", original="d"))
        graph.add_node(GraphNode(id="n2", agent="a", description="d", original="d"))
        graph.add_edge("n1", "n2")
        parent = graph.nodes["n1"]
        parent.status, parent.result = TaskStatus.COMPLETED, "42 employees"
        graph.mark_checkpoint(parent)
        return graph

    def test_untampered_checkpoint_verifies(self) -> None:
        graph = self._graph()
        self.assertTrue(graph.nodes["n1"].checkpoint.verify())
        graph.bind_evidence(graph.nodes["n2"])
        self.assertIn("42 employees", graph.nodes["n2"].description)

    def test_tampered_checkpoint_raises_integrity_error(self) -> None:
        graph = self._graph()
        graph.nodes["n1"].checkpoint.result = "9999 employees"
        with self.assertRaises(StateIntegrityError):
            graph.bind_evidence(graph.nodes["n2"])

    def test_legacy_checkpoint_without_hash_is_trusted(self) -> None:
        cp = Checkpoint(node_id="n1", agent="a", result="x", status="completed")
        self.assertTrue(cp.verify())

    def test_topo_order_puts_parents_first(self) -> None:
        graph = self._graph()
        self.assertEqual(graph.topo_order(), ["n1", "n2"])


class DefaultAgentTests(unittest.TestCase):
    def test_no_route_and_no_default_is_an_error_not_a_guess(self) -> None:
        with self.assertRaises(ValueError):
            build_run_graph("q", [])

    def test_default_agent_is_used_only_when_routing_is_empty(self) -> None:
        graph = build_run_graph("q", [], default_agent="x")
        self.assertEqual([n.agent for n in graph.nodes.values()], ["x"])
        graph = build_run_graph("q", ["y"], default_agent="x")
        self.assertEqual([n.agent for n in graph.nodes.values()], ["y"])


if __name__ == "__main__":
    unittest.main()
