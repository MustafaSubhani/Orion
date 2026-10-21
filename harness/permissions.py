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

# Role tool allow-lists and sensitive columns come from the loaded domain packs. The names stay
# importable from here (`ROLE_TOOL_ALLOWLIST`, `SALARY_COLUMNS`) and resolve lazily.
#
# Platform tools are not pack specific. They are allowed for limited users, and the tool registry
# still blocks any of them that is not read-only for a limited role.
PLATFORM_TOOLS = frozenset({"web_search"})


def __getattr__(name: str):
    if name in {"ROLE_TOOL_ALLOWLIST", "SALARY_COLUMNS"}:
        from harness.packs import registry

        reg = registry()
        return reg.role_tools() if name == "ROLE_TOOL_ALLOWLIST" else reg.sensitive_columns()
    raise AttributeError(f"module 'harness.permissions' has no attribute {name!r}")


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
    if verb in PLATFORM_TOOLS or verb.startswith("mcp."):
        return  # gated by side effect in the registry, not by a pack list
    from harness.packs import registry

    role_tools = registry().role_tools()
    allowed = role_tools.get(role, role_tools.get("limited", frozenset()))
    if verb not in allowed:
        raise PolicyDeniedError(f"Role '{role}' cannot use tool '{verb}'")


def strip_salary_if_limited(columns: list[str], role: Optional[str]) -> list[str]:
    """Drop pack-declared sensitive columns for limited roles (name kept for compatibility)."""
    if role == "limited":
        from harness.packs import registry

        sensitive = registry().sensitive_columns()
        return [c for c in columns if c not in sensitive]
    return columns
