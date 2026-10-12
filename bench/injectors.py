"""Fault injection for FDA / RSR. The injected class is known, so recovery needs no humans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from harness.healing import FailureClass


@dataclass
class InjectedFault:
    failure_class: FailureClass
    apply: Callable[[dict[str, Any]], dict[str, Any]]
    description: str


def broken_column(spec: dict[str, Any]) -> dict[str, Any]:
    patched = dict(spec)
    patched["column"] = "PerformanceScore"  # stale name; real column is Performance_Rating
    return patched


def empty_collection(args: dict[str, Any]) -> dict[str, Any]:
    patched = dict(args)
    patched["collection"] = "does_not_exist"
    return patched


def wrong_agent(agent_id: str) -> str:
    mapping = {"hr": "finance", "finance": "hr", "scheduler": "finance", "sql": "chart", "chart": "sql"}
    return mapping.get(agent_id, "hr")


INJECTORS = {
    FailureClass.F1_TOOL: InjectedFault(FailureClass.F1_TOOL, lambda a: {**a, "table": "nope"}, "invalid table"),
    FailureClass.F2_RETRIEVAL: InjectedFault(FailureClass.F2_RETRIEVAL, empty_collection, "empty retrieval"),
    FailureClass.F3_ROUTING: InjectedFault(FailureClass.F3_ROUTING, lambda a: a, "swap specialist"),
    FailureClass.F4_SCHEMA: InjectedFault(FailureClass.F4_SCHEMA, broken_column, "stale column"),
    FailureClass.F5_DECOMPOSITION: InjectedFault(FailureClass.F5_DECOMPOSITION, lambda a: a, "corrupt task text"),
    FailureClass.F6_SEMANTIC: InjectedFault(FailureClass.F6_SEMANTIC, lambda a: a, "force fluent wrong number"),
    FailureClass.F7_POLICY: InjectedFault(FailureClass.F7_POLICY, lambda a: a, "policy deny"),
    FailureClass.F8_CASCADE: InjectedFault(FailureClass.F8_CASCADE, lambda a: a, "upstream corrupt"),
}

INJECT_CODES = tuple(f.value for f in FailureClass if f is not FailureClass.NONE)
