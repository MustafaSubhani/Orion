"""Parallel execution safety: forked contexts, exact budgets, node timeouts, cancellation."""

from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness.context import is_product, new_context
from harness.errors import BudgetExceededError
from harness.graph import GraphNode, RunGraph
from harness.planner import _run_wave
from harness.session import SessionStore
from harness.types import Task, TaskStatus


def _graph(n: int) -> RunGraph:
    graph = RunGraph()
    for i in range(n):
        graph.add_node(GraphNode(id=f"n{i}", agent="a", description=f"t{i}", original=f"t{i}"))
    return graph


class ContextForkTests(unittest.TestCase):
    def test_parallel_token_charges_are_exact(self) -> None:
        ctx = new_context()
        ctx.budget.max_tokens = 10**9

        def work() -> None:
            for _ in range(100):
                ctx.fork().charge_tokens(1)

        threads = [threading.Thread(target=work) for _ in range(50)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(ctx.budget.tokens_used, 5000)

    def test_fork_isolates_spans_and_extra_but_shares_budget(self) -> None:
        ctx = new_context()
        ctx.parent_span_id = "root"
        child = ctx.fork()
        child.parent_span_id = "child-span"
        child.extra["marker"] = 1
        self.assertEqual(ctx.parent_span_id, "root")
        self.assertNotIn("marker", ctx.extra)
        self.assertIs(child.budget, ctx.budget)
        self.assertIs(child.session, ctx.session)

    def test_cancelling_a_child_does_not_cancel_siblings(self) -> None:
        ctx = new_context()
        a, b = ctx.fork(), ctx.fork()
        a.cancel.set()
        with self.assertRaises(BudgetExceededError):
            a.check_time()
        b.check_time()
        ctx.cancel.set()
        with self.assertRaises(BudgetExceededError):
            b.check_time()

    def test_is_product_reads_mode(self) -> None:
        ctx = new_context()
        self.assertFalse(is_product(ctx))
        ctx.extra["mode"] = "product"
        self.assertTrue(is_product(ctx))

    def test_session_store_is_thread_safe(self) -> None:
        store = SessionStore()

        def put() -> None:
            for _ in range(200):
                store.put("x" * 10)

        threads = [threading.Thread(target=put) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(store), 1600)


class WaveTests(unittest.TestCase):
    def _run(self, n: int, fake, *, timeout: float = 0.0) -> RunGraph:
        graph = _graph(n)
        ctx = new_context()
        ctx.graph = graph
        settings = SimpleNamespace(node_timeout_s=timeout, parallel_workers=4)
        with patch("harness.planner._execute_with_heal", side_effect=fake), patch(
            "harness.planner.get_settings", return_value=settings
        ):
            _run_wave(list(graph.nodes.values()), "q", ctx)
        return graph

    def test_independent_nodes_run_concurrently(self) -> None:
        def fake(task: Task, query: str, ctx) -> Task:
            time.sleep(0.3)
            task.status, task.result = TaskStatus.COMPLETED, "ok"
            return task

        t0 = time.perf_counter()
        graph = self._run(4, fake)
        self.assertLess(time.perf_counter() - t0, 0.9)  # sequential would be 1.2s
        self.assertTrue(all(n.status is TaskStatus.COMPLETED for n in graph.nodes.values()))
        self.assertTrue(all(n.checkpoint and n.checkpoint.verify() for n in graph.nodes.values()))

    def test_timeout_fails_only_the_slow_node(self) -> None:
        def fake(task: Task, query: str, ctx) -> Task:
            if task.id == "n1":
                for _ in range(40):
                    time.sleep(0.05)
                    ctx.check_time()  # a cooperative worker stops once cancelled
            task.status, task.result = TaskStatus.COMPLETED, "ok"
            return task

        graph = self._run(3, fake, timeout=0.3)
        self.assertEqual(graph.nodes["n1"].status, TaskStatus.FAILED)
        self.assertIn("timed out", graph.nodes["n1"].result)
        self.assertEqual(graph.nodes["n0"].status, TaskStatus.COMPLETED)
        self.assertEqual(graph.nodes["n2"].status, TaskStatus.COMPLETED)

    def test_crashed_worker_fails_its_node_not_the_request(self) -> None:
        def fake(task: Task, query: str, ctx) -> Task:
            if task.id == "n0":
                raise RuntimeError("boom")
            task.status, task.result = TaskStatus.COMPLETED, "ok"
            return task

        graph = self._run(2, fake)
        self.assertEqual(graph.nodes["n0"].status, TaskStatus.FAILED)
        self.assertEqual(graph.nodes["n1"].status, TaskStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
