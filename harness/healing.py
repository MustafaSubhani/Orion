"""Typed recovery. Blind retry is only legal for F1. F6 and F7 must not retry.

Precedence (highest first): F7 > F8 > F4 > F1 > F2 > F5 > F3 > F6.
When several signals fire at once the resolver picks exactly one class, deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from harness.types import JudgeCard, Span, WorkerResult


class FailureClass(str, Enum):
    F1_TOOL = "F1"
    F2_RETRIEVAL = "F2"
    F3_ROUTING = "F3"
    F4_SCHEMA = "F4"
    F5_DECOMPOSITION = "F5"
    F6_SEMANTIC = "F6"
    F7_POLICY = "F7"
    F8_CASCADE = "F8"
    NONE = "none"


class HealAction(str, Enum):
    RETRY = "RETRY"
    REPLAN = "REPLAN"
    RE_RETRIEVE = "RE_RETRIEVE"
    RE_ROUTE = "RE_ROUTE"
    SCHEMA_REPAIR = "SCHEMA_REPAIR"
    REWIND = "REWIND"
    ESCALATE_ABSTAIN = "ESCALATE_ABSTAIN"
    FAIL_CLOSED = "FAIL_CLOSED"
    NONE = "NONE"


PRECEDENCE: tuple[FailureClass, ...] = (
    FailureClass.F7_POLICY,
    FailureClass.F8_CASCADE,
    FailureClass.F4_SCHEMA,
    FailureClass.F1_TOOL,
    FailureClass.F2_RETRIEVAL,
    FailureClass.F5_DECOMPOSITION,
    FailureClass.F3_ROUTING,
    FailureClass.F6_SEMANTIC,
)

_HINTS = {
    FailureClass.F1_TOOL: HealAction.RETRY,
    FailureClass.F2_RETRIEVAL: HealAction.RE_RETRIEVE,
    FailureClass.F3_ROUTING: HealAction.RE_ROUTE,
    FailureClass.F4_SCHEMA: HealAction.SCHEMA_REPAIR,
    FailureClass.F5_DECOMPOSITION: HealAction.REPLAN,
    FailureClass.F6_SEMANTIC: HealAction.ESCALATE_ABSTAIN,
    FailureClass.F7_POLICY: HealAction.FAIL_CLOSED,
    FailureClass.F8_CASCADE: HealAction.REWIND,
}

_TOOL_ERRORS = {"Timeout", "TimeoutError_", "ToolRuntime", "ToolRuntimeError", "SandboxError", "CircuitOpen"}
_POLICY_ERRORS = {"PolicyDenied", "PolicyDeniedError"}
_ROUTING_ERRORS = {"RoutingError", "UnknownAgent", "UnknownAgentError"}
_SCHEMA_ERRORS = {"SchemaInvalid", "SchemaInvalidError"}
_RETRIEVAL_ERRORS = {"RetrievalMiss", "RetrievalMissError"}
_INTEGRITY_ERRORS = {
    "AssertionError",
    "StateIntegrityError",
    "InvariantError",
    "SemanticIntegrityError",
}


@dataclass
class HealDecision:
    failure_class: FailureClass
    action: HealAction
    hint: str = ""


class Healer:
    def classify(
        self,
        *,
        result: Optional[WorkerResult] = None,
        span: Optional[Span] = None,
        judge: Optional[JudgeCard] = None,
        state_integrity_ok: Optional[bool] = None,
        assertion_ok: Optional[bool] = None,
        oracle_ok: Optional[bool] = None,
        blocked: bool = False,
        unknown_column: bool = False,
        empty_retrieval: bool = False,
        domain_mismatch: bool = False,
        upstream_corrupt: bool = False,
        incomplete_plan: bool = False,
    ) -> FailureClass:
        """Collect every environmental signal, then resolve one class by `PRECEDENCE`.

        F6 (execution-state inconsistency) is signalled at runtime by sandbox assertions,
        dataframe or checkpoint invariants (`state_integrity_ok=False`, `assertion_ok=False`) or an
        explicit assertion error. The offline oracle in bench/ is never read at runtime.
        F5 (decomposition) fires from `incomplete_plan`, set by the planner from generic signals.
        """
        err = (result.error_class if result else None) or (span.error_class if span else None)
        blob = ((result.text if result else None) or "").lower()
        unknown_table = err in _SCHEMA_ERRORS and "unknown table" in blob
        found: set[FailureClass] = set()
        if blocked or err in _POLICY_ERRORS:
            found.add(FailureClass.F7_POLICY)
        if upstream_corrupt:
            found.add(FailureClass.F8_CASCADE)
        if unknown_column or (err in _SCHEMA_ERRORS and not unknown_table):
            found.add(FailureClass.F4_SCHEMA)
        if err in _TOOL_ERRORS or unknown_table:
            found.add(FailureClass.F1_TOOL)
        if empty_retrieval or err in _RETRIEVAL_ERRORS:
            found.add(FailureClass.F2_RETRIEVAL)
        if incomplete_plan:
            found.add(FailureClass.F5_DECOMPOSITION)
        if domain_mismatch or (result and result.domain_mismatch) or err in _ROUTING_ERRORS:
            found.add(FailureClass.F3_ROUTING)
        if err in _INTEGRITY_ERRORS or state_integrity_ok is False or assertion_ok is False or oracle_ok is False:
            found.add(FailureClass.F6_SEMANTIC)
        found |= self._from_judge(judge)
        if not found and result and not result.ok:
            found.add(FailureClass.F1_TOOL)
        return next((fc for fc in PRECEDENCE if fc in found), FailureClass.NONE)

    @staticmethod
    def _from_judge(judge: Optional[JudgeCard]) -> set[FailureClass]:
        if not judge or not judge.failure_class or judge.failure_class in {"none", "NONE", ""}:
            return set()
        try:
            return {FailureClass(judge.failure_class.split("|")[0])}
        except ValueError:
            return {FailureClass.F6_SEMANTIC} if judge.overall < 2 else set()

    def heal(self, failure_class: FailureClass, *, hint: str = "", retry_only: bool = False) -> HealDecision:
        if failure_class == FailureClass.NONE:
            return HealDecision(failure_class, HealAction.NONE, hint)
        if retry_only and failure_class not in {FailureClass.F6_SEMANTIC, FailureClass.F7_POLICY}:
            return HealDecision(failure_class, HealAction.RETRY, hint or "retry-only ablation")
        return HealDecision(failure_class, _HINTS[failure_class], hint)
