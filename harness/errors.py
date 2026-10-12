"""Typed harness errors. Never leak raw Python exceptions through the control plane."""

from __future__ import annotations

from enum import Enum


class ErrorClass(str, Enum):
    TIMEOUT = "Timeout"
    CIRCUIT_OPEN = "CircuitOpen"
    SCHEMA_INVALID = "SchemaInvalid"
    POLICY_DENIED = "PolicyDenied"
    AGENT_CRASHED = "AgentCrashed"
    ROUTING = "RoutingError"
    RETRIEVAL_MISS = "RetrievalMiss"
    TOOL_RUNTIME = "ToolRuntime"
    BUDGET_EXCEEDED = "BudgetExceeded"
    UNKNOWN_TOOL = "UnknownTool"
    UNKNOWN_AGENT = "UnknownAgent"
    SANDBOX = "SandboxError"


class HarnessError(Exception):
    """Base error. Contract: `error_class` is always set; `safe_message` is user-facing."""

    error_class: ErrorClass = ErrorClass.AGENT_CRASHED

    def __init__(self, message: str, *, details: str | None = None) -> None:
        super().__init__(message)
        self.safe_message = message
        self.details = details

    def to_dict(self) -> dict:
        return {
            "error_class": self.error_class.value,
            "message": self.safe_message,
            "details": self.details,
        }


class TimeoutError_(HarnessError):
    error_class = ErrorClass.TIMEOUT


class CircuitOpenError(HarnessError):
    error_class = ErrorClass.CIRCUIT_OPEN


class SchemaInvalidError(HarnessError):
    error_class = ErrorClass.SCHEMA_INVALID


class PolicyDeniedError(HarnessError):
    error_class = ErrorClass.POLICY_DENIED


class AgentCrashedError(HarnessError):
    error_class = ErrorClass.AGENT_CRASHED


class RoutingError(HarnessError):
    error_class = ErrorClass.ROUTING


class RetrievalMissError(HarnessError):
    error_class = ErrorClass.RETRIEVAL_MISS


class ToolRuntimeError(HarnessError):
    error_class = ErrorClass.TOOL_RUNTIME


class BudgetExceededError(HarnessError):
    error_class = ErrorClass.BUDGET_EXCEEDED


class UnknownToolError(HarnessError):
    error_class = ErrorClass.UNKNOWN_TOOL


class UnknownAgentError(HarnessError):
    error_class = ErrorClass.UNKNOWN_AGENT


class SandboxError(HarnessError):
    error_class = ErrorClass.SANDBOX
