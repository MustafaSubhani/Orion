"""Every injected fault keeps its original behaviour, is one-shot, and is a no-op without `inject`."""

from __future__ import annotations

import threading
import unittest

from harness import faults
from harness.context import new_context
from harness.errors import PolicyDeniedError
from harness.graph import GraphNode, RunGraph
from harness.tool_registry import _apply_eval_inject
from harness.types import Task


def _armed(code: str | None):
    ctx = new_context()
    faults.arm(ctx, code)
    return ctx


class ToolFaultTests(unittest.TestCase):
    def test_f4_swaps_column_once(self) -> None:
        ctx = _armed("F4")
        first = faults.apply_tool_fault({"table": "t", "column": "a"}, ctx, "sql_spec")
        second = faults.apply_tool_fault({"table": "t", "column": "a"}, ctx, "sql_spec")
        self.assertEqual(first["column"], "PerformanceScore")
        self.assertEqual(second["column"], "a")

    def test_f1_swaps_table(self) -> None:
        out = faults.apply_tool_fault({"table": "t"}, _armed("F1"), "sql_spec")
        self.assertEqual(out["table"], "nope")

    def test_f2_swaps_collection(self) -> None:
        out = faults.apply_tool_fault({"collection": "c"}, _armed("F2"), "retrieve")
        self.assertEqual(out["collection"], "does_not_exist")

    def test_f7_raises_policy_denied_once(self) -> None:
        ctx = _armed("F7")
        with self.assertRaises(PolicyDeniedError):
            faults.apply_tool_fault({}, ctx, "sql_spec")
        self.assertEqual(faults.apply_tool_fault({"x": 1}, ctx, "sql_spec"), {"x": 1})

    def test_wrong_verb_does_not_consume(self) -> None:
        ctx = _armed("F4")
        self.assertEqual(faults.apply_tool_fault({"a": 1}, ctx, "retrieve"), {"a": 1})
        self.assertTrue(faults.armed(ctx, "F4"))

    def test_no_inject_is_a_noop(self) -> None:
        ctx = _armed(None)
        self.assertEqual(faults.apply_tool_fault({"a": 1}, ctx, "sql_spec"), {"a": 1})
        self.assertFalse(faults.armed(ctx))

    def test_registry_wrapper_still_works(self) -> None:
        ctx = new_context()
        ctx.extra["inject"] = "F4"  # legacy arming path used by older tests
        out = _apply_eval_inject({"table": "hr", "column": "Status"}, ctx, "sql_spec")
        self.assertEqual(out["column"], "PerformanceScore")


class RouteGraphTaskFaultTests(unittest.TestCase):
    def test_f3_swaps_first_agent_only(self) -> None:
        ctx = _armed("F3")
        out = faults.apply_route_fault(ctx, ["hr", "finance"])
        self.assertEqual(out, ["finance", "finance"])
        self.assertEqual(faults.apply_route_fault(ctx, ["hr"]), ["hr"])

    def test_f3_unmapped_agent_falls_back_to_first_mapped_key(self) -> None:
        out = faults.apply_route_fault(_armed("F3"), ["custom_agent"])
        self.assertEqual(out, [next(iter(faults.ROUTE_SWAP))])

    def test_f5_corrupts_first_node(self) -> None:
        graph = RunGraph()
        graph.add_node(GraphNode(id="n1", agent="a", description="count people", original="count people"))
        faults.apply_graph_fault(_armed("F5"), graph)
        self.assertTrue(graph.nodes["n1"].description.startswith("Drop the original filters"))

    def test_f6_appends_fault_text(self) -> None:
        task = Task(id="t", description="q", agent="a")
        faults.apply_task_fault(_armed("F6"), task)
        self.assertIn("FAULT: assert the numeric answer is exactly 1.", task.description)

    def test_f8_fires_only_on_cycle_zero(self) -> None:
        ctx = _armed("F8")
        self.assertFalse(faults.take_cascade_fault(ctx, 1))
        self.assertTrue(faults.take_cascade_fault(ctx, 0))
        self.assertFalse(faults.take_cascade_fault(ctx, 0))


class SharedStateTests(unittest.TestCase):
    def test_exactly_one_forked_context_consumes_the_fault(self) -> None:
        ctx = _armed("F6")
        children = [ctx.fork() for _ in range(16)]
        wins: list[bool] = []
        barrier = threading.Barrier(16)

        def go(child) -> None:
            barrier.wait()
            wins.append(faults.consume(child, "F6"))

        threads = [threading.Thread(target=go, args=(c,)) for c in children]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sum(wins), 1)


if __name__ == "__main__":
    unittest.main()
