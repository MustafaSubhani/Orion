"""Typed recovery. Blind retry is only legal for F1. F6 and F7 must not retry."""

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
    ) -> FailureClass:
        """Classify execution failures using precedence-ranked environmental signals.

        Note on F6 (Execution-State Inconsistency):
        State integrity failures are signaled at runtime via sandbox environmental assertions,
        checksum/dataframe invariant checks (state_integrity_ok=False / assertion_ok=False),
        or explicit assertion exceptions (AssertionError / StateIntegrityError).
        The offline evaluation oracle (bench/oracles.py) is strictly isolated and never accessed at runtime.
        """
        if blocked:
            return FailureClass.F7_POLICY
        if upstream_corrupt:
            return FailureClass.F8_CASCADE
        if unknown_column:
            return FailureClass.F4_SCHEMA
        if empty_retrieval:
            return FailureClass.F2_RETRIEVAL
        if domain_mismatch or (result and result.domain_mismatch):
            return FailureClass.F3_ROUTING
        err = (result.error_class if result else None) or (span.error_class if span else None)
        if err in {"Timeout", "TimeoutError_", "ToolRuntime", "ToolRuntimeError", "SandboxError", "CircuitOpen"}:
            return FailureClass.F1_TOOL
        if err in {"PolicyDenied", "PolicyDeniedError"}:
            return FailureClass.F7_POLICY
        if err in {"RoutingError", "UnknownAgent", "UnknownAgentError"}:
            return FailureClass.F3_ROUTING
        if err in {"SchemaInvalid", "SchemaInvalidError"}:
            blob = ((result.text if result else None) or "").lower()
            if "unknown table" in blob:
                return FailureClass.F1_TOOL
            return FailureClass.F4_SCHEMA
        if err in {"RetrievalMiss", "RetrievalMissError"}:
            return FailureClass.F2_RETRIEVAL
        if err in {"AssertionError", "StateIntegrityError", "InvariantError", "SemanticIntegrityError"}:
            return FailureClass.F6_SEMANTIC
        if judge and judge.failure_class and judge.failure_class not in {"none", "NONE", ""}:
            try:
                return FailureClass(judge.failure_class.split("|")[0])
            except ValueError:
                if judge.overall < 2:
                    return FailureClass.F6_SEMANTIC
        if state_integrity_ok is False or assertion_ok is False or oracle_ok is False:
            return FailureClass.F6_SEMANTIC
        if result and not result.ok:
            return FailureClass.F1_TOOL
        return FailureClass.NONE

    def heal(self, failure_class: FailureClass, *, hint: str = "", retry_only: bool = False) -> HealDecision:
        if failure_class == FailureClass.NONE:
            return HealDecision(failure_class, HealAction.NONE, hint)
        if retry_only and failure_class not in {FailureClass.F6_SEMANTIC, FailureClass.F7_POLICY}:
            return HealDecision(failure_class, HealAction.RETRY, hint or "retry-only ablation")
        return HealDecision(failure_class, _HINTS[failure_class], hint)
