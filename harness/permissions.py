"""Fail-closed policy. Regex is a gate, not the security story; tools still go through the registry."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from harness.errors import PolicyDeniedError

MAX_INPUT_LENGTH = 4000
MAX_OUTPUT_LENGTH = 8000

_BLACKLIST = [
    r";\s*--",
    r"\bdrop\s+table\b",
    r"\bdelete\s+from\b",
    r"\binsert\s+into\b",
    r"\bupdate\s+\w+\s+set\b",
    r"\bexec\s*\(",
    r"\bunion\s+select\b",
    r"\bxp_cmdshell\b",
    r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?",
    r"forget\s+(all\s+)?(previous|prior|above)\s+instructions?",
    r"you\s+are\s+now\s+(?:a\s+)?(?:different|new|another)\s+(?:ai|assistant|model)",
    r"act\s+as\s+(?:a\s+)?(?:different|new|unrestricted|jailbreak)",
    r"your\s+new\s+(system\s+)?prompt\s+is",
    r"disregard\s+(?:your|all|previous)\s+(?:instructions?|rules?|constraints?)",
    r"<\s*system\s*>",
    r"\[system\]",
    r"\bapi[_\s]?key\b",
    r"\bbearer\s+token\b",
    r"\bpassword\s*=",
    r"\bsecret[_\s]?key\b",
    r"\bprivate[_\s]?key\b",
    r"\.\./\.\.",
    r"\.\.\\\.\.\\",
]
_COMPILED = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in _BLACKLIST]

_PII = [
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "[EMAIL REDACTED]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b"), "[PHONE REDACTED]"),
    (re.compile(r"Bearer\s+[A-Za-z0-9\-_.~+/]{20,}", re.IGNORECASE), "Bearer [TOKEN REDACTED]"),
    (re.compile(r"(?<![/\w])[A-Za-z0-9]{40,}(?![/\w])"), "[SECRET REDACTED]"),
]

SALARY_COLUMNS = frozenset({"Salary_INR", "Salary", "SalaryINR", "compensation", "pay"})

ROLE_TOOL_ALLOWLIST: dict[str, frozenset[str]] = {
    "limited": frozenset(
        {
            "retrieve",
            "sql_spec",
            "calendar_query",
            "calendar_conflicts",
            "render_chart",
            "anomaly_detect",
            "search_tools",
            "search_agents",
            "session_get",
            "skill_manage",
            "schema_lookup",
            "distinct_values",
            "describe_column",
            "row_lookup",
            "join_check",
            "evidence_check",
            "compare_series",
            "workforce_slice",
            "ratio_agg",
            "top_n",
            "macro_latest",
            "series_window",
            "date_at",
            "product_search",
            "kb_list",
            "kb_search",
            "kb_query",
        }
    ),
    "admin": frozenset(
        {
            "retrieve",
            "sql_spec",
            "forecast",
            "anomaly_detect",
            "calendar_query",
            "calendar_conflicts",
            "render_chart",
            "http_fetch",
            "search_tools",
            "search_agents",
            "session_get",
            "skill_manage",
            "schema_lookup",
            "distinct_values",
            "describe_column",
            "row_lookup",
            "join_check",
            "evidence_check",
            "compare_series",
            "workforce_slice",
            "ratio_agg",
            "top_n",
            "macro_latest",
            "series_window",
            "date_at",
            "product_search",
            "kb_list",
            "kb_search",
            "kb_query",
        }
    ),
    "super_user": frozenset(),  # empty means all
}


@dataclass
class PreprocessResult:
    approved: bool
    reason: str
    clean_text: str


def sanitize_input(text: str) -> str:
    text = text.replace("\x00", "")
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[^\S\n]+", " ", text).strip()
    if len(text) > MAX_INPUT_LENGTH:
        text = text[:MAX_INPUT_LENGTH]
    return text


def preprocess(text: str, user: Optional[str] = None) -> PreprocessResult:
    try:
        if not text or not text.strip():
            return PreprocessResult(False, "Empty input", "")
        clean = sanitize_input(text)
        for pattern in _COMPILED:
            if pattern.search(clean):
                return PreprocessResult(
                    False,
                    "Input blocked: potential injection or forbidden content detected.",
                    "",
                )
        return PreprocessResult(True, "OK", clean)
    except Exception as exc:
        return PreprocessResult(False, f"Pre-processing error (fail-safe block): {exc}", "")


def postprocess_output(text: str) -> tuple[str, list[str]]:
    flags: list[str] = []
    lower = text.lower()
    for kw in ("api key", "password", "secret", "private key", "bearer"):
        if kw in lower:
            flags.append(f"Output contains sensitive keyword: '{kw}'")
    if len(text) > MAX_OUTPUT_LENGTH:
        flags.append(f"Output is very long ({len(text)} chars > {MAX_OUTPUT_LENGTH})")
    clean = text
    for pattern, replacement in _PII:
        new_clean = pattern.sub(replacement, clean)
        if new_clean != clean:
            flags.append(f"Redacted pattern: {replacement}")
        clean = new_clean
    return clean, flags


def assert_tool_allowed(role: Optional[str], verb: str) -> None:
    if role is None or role in {"super_user", "admin"}:
        return
    allowed = ROLE_TOOL_ALLOWLIST.get(role, ROLE_TOOL_ALLOWLIST["limited"])
    if verb not in allowed:
        raise PolicyDeniedError(f"Role '{role}' cannot use tool '{verb}'")


def strip_salary_if_limited(columns: list[str], role: Optional[str]) -> list[str]:
    if role == "limited":
        return [c for c in columns if c not in SALARY_COLUMNS]
    return columns
