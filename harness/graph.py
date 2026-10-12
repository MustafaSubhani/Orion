"""Request-scoped execution DAG. Nodes are specialist runs; edges are data dependencies.

This is not a knowledge graph over the lake. Traversal is: ready-set (Kahn waves)
→ checkpoint on success → rewind the failed node (and its descendants) to the last
good parent checkpoint. Independent nodes still run in parallel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from harness.types import Task, TaskStatus

_PIPELINE = re.compile(
    r"\b("
    r"then|after that|afterwards|"
    r"based on (that|those|the|it)|"
    r"using (that|those|the|them)|"
    r"given (that|those|the)|"
    r"from (that|those) results?|"
    r"and then|followed by"
    r")\b",
    re.I,
)


@dataclass
class Checkpoint:
    node_id: str
    agent: str
    result: str
    status: str


@dataclass
class GraphNode:
    id: str
    agent: str
    description: str
    original: str
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result: str = ""
    failure_class: Optional[str] = None
    heal_cycles: int = 0
    rewinds: int = 0
    checkpoint: Optional[Checkpoint] = None

    def to_task(self) -> Task:
        return Task(
            id=self.id,
            description=self.description,
            agent=self.agent,
            status=self.status,
            result=self.result,
            heal_cycles=self.heal_cycles,
            failure_class=self.failure_class,
        )


class CycleError(ValueError):
    pass


class RunGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, GraphNode] = {}
        self.backtracks: int = 0

    def add_node(self, node: GraphNode) -> None:
        if node.id in self.nodes:
            raise ValueError(f"duplicate node '{node.id}'")
        self.nodes[node.id] = node

    def add_edge(self, src: str, dst: str) -> None:
        if src not in self.nodes or dst not in self.nodes:
            raise ValueError("edge endpoints must exist")
        if src == dst:
            raise CycleError("self-loop")
        deps = self.nodes[dst].depends_on
        if src not in deps:
            deps.append(src)
        if self._cyclic():
            deps.remove(src)
            raise CycleError(f"edge {src}->{dst} creates a cycle")

    def _cyclic(self) -> bool:
        visiting: set[str] = set()
        seen: set[str] = set()

        def walk(nid: str) -> bool:
            if nid in visiting:
                return True
            if nid in seen:
                return False
            visiting.add(nid)
            for dep in self.nodes[nid].depends_on:
                if walk(dep):
                    return True
            visiting.remove(nid)
            seen.add(nid)
            return False

        return any(walk(nid) for nid in self.nodes)

    def ready(self) -> list[GraphNode]:
        out = []
        for node in self.nodes.values():
            if node.status is not TaskStatus.PENDING:
                continue
            if all(self.nodes[d].status is TaskStatus.COMPLETED for d in node.depends_on):
                out.append(node)
        return out

    def pending(self) -> list[GraphNode]:
        return [n for n in self.nodes.values() if n.status is TaskStatus.PENDING]

    def all_terminal(self) -> bool:
        return all(n.status in {TaskStatus.COMPLETED, TaskStatus.FAILED} for n in self.nodes.values())

    def descendants(self, node_id: str) -> list[GraphNode]:
        out: list[GraphNode] = []
        seen: set[str] = set()
        stack = [n for n in self.nodes.values() if node_id in n.depends_on]
        while stack:
            child = stack.pop()
            if child.id in seen:
                continue
            seen.add(child.id)
            out.append(child)
            stack.extend(n for n in self.nodes.values() if child.id in n.depends_on)
        return out

    def evidence_for(self, node_id: str) -> str:
        parts = []
        for dep in self.nodes[node_id].depends_on:
            parent = self.nodes[dep]
            snap = parent.checkpoint.result if parent.checkpoint else parent.result
            if snap:
                parts.append(f"[{parent.agent} / {parent.id}]\n{snap[:2000]}")
        return "\n\n".join(parts)

    def bind_evidence(self, node: GraphNode) -> None:
        ev = self.evidence_for(node.id)
        if ev:
            node.description = (
                node.original
                + "\n\nUpstream checkpoints (use these facts; do not invent numbers missing here):\n"
                + ev
            )
        else:
            node.description = node.original

    def mark_checkpoint(self, node: GraphNode) -> None:
        node.checkpoint = Checkpoint(
            node_id=node.id,
            agent=node.agent,
            result=node.result,
            status=node.status.value,
        )

    def rewind(self, node_id: str, *, max_rewinds: int = 2) -> list[str]:
        """Reset the node and its descendants. Parents stay checkpointed."""
        node = self.nodes[node_id]
        if node.rewinds >= max_rewinds:
            return []
        reset_ids = list(dict.fromkeys([node_id] + [d.id for d in self.descendants(node_id)]))
        for nid in reset_ids:
            n = self.nodes[nid]
            n.status = TaskStatus.PENDING
            n.result = ""
            n.failure_class = None
            n.description = n.original
            n.checkpoint = None
        node.rewinds += 1
        self.backtracks += 1
        return reset_ids

    def replan(self, node_id: str, query: str, previous: str) -> None:
        """F5: rewrite this node and unfinished descendants from current checkpoints."""
        targets = [self.nodes[node_id], *self.descendants(node_id)]
        evidence = []
        for n in self.nodes.values():
            if n.status is TaskStatus.COMPLETED and n.checkpoint:
                evidence.append(f"[{n.agent}] {n.checkpoint.result[:800]}")
        blob = "\n".join(evidence) or previous[:800]
        for n in targets:
            if n.status is TaskStatus.FAILED:
                n.status = TaskStatus.PENDING
            n.description = (
                f"Replan. Original query: {query}\n"
                f"Checkpoints so far:\n{blob}\n"
                f"Your job ({n.agent}): {n.original}"
            )

    def fail_blocked(self) -> list[str]:
        """Pending nodes whose parent failed cannot run — mark F8 (cascades)."""
        blocked: list[str] = []
        changed = True
        while changed:
            changed = False
            for node in list(self.nodes.values()):
                if node.status is not TaskStatus.PENDING:
                    continue
                if any(self.nodes[d].status is TaskStatus.FAILED for d in node.depends_on):
                    node.status = TaskStatus.FAILED
                    node.failure_class = "F8"
                    node.result = "blocked: upstream specialist failed"
                    blocked.append(node.id)
                    changed = True
        return blocked

    def depth(self, node_id: str) -> int:
        node = self.nodes[node_id]
        if not node.depends_on:
            return 0
        return 1 + max(self.depth(d) for d in node.depends_on)

    def cascade_radius(self) -> int:
        failed = [n for n in self.nodes.values() if n.status is TaskStatus.FAILED]
        if not failed:
            return 0
        return max(self.depth(n.id) for n in failed)

    def to_dict(self) -> dict:
        return {
            "nodes": [
                {
                    "id": n.id,
                    "agent": n.agent,
                    "depends_on": n.depends_on,
                    "status": n.status.value,
                    "rewinds": n.rewinds,
                }
                for n in self.nodes.values()
            ],
            "edges": [[d, n.id] for n in self.nodes.values() for d in n.depends_on],
            "backtracks": self.backtracks,
            "cascade_radius": self.cascade_radius(),
        }


def needs_pipeline(query: str) -> bool:
    return bool(_PIPELINE.search(query or ""))


def build_run_graph(query: str, agent_ids: list[str]) -> RunGraph:
    """One node per routed agent. Pipeline queries get a chain; otherwise a parallel set."""
    graph = RunGraph()
    ids = list(agent_ids) or ["hr"]
    sequential = needs_pipeline(query) and len(ids) > 1
    prev: Optional[str] = None
    for i, agent in enumerate(ids):
        nid = f"n{i+1}"
        desc = query if len(ids) == 1 else f"Handle the {agent} aspect of: {query}"
        node = GraphNode(id=nid, agent=agent, description=desc, original=desc)
        graph.add_node(node)
        if sequential and prev:
            graph.add_edge(prev, nid)
        prev = nid
    return graph
