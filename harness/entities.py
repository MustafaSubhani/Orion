"""Entity mention tagging and join gates. Mixed-lake isolation lives here, not in worker prose."""

from __future__ import annotations

import re
from typing import Any

from harness.config import entities_join_legal
from harness.errors import PolicyDeniedError

# Conservative cues: only fire when the query actually names the source.
# Do not tag MNC on generic "employees" (that would poison Apple catalog questions).
_CUES: dict[str, tuple[re.Pattern[str], ...]] = {
    "MSFT": (
        re.compile(r"\bmsft\b", re.I),
        re.compile(r"\bmicrosoft\b", re.I),
        re.compile(r"\b10-?k\b", re.I),
        re.compile(r"\bedgar\b", re.I),
    ),
    "MNC": (
        re.compile(r"\bmnc\b", re.I),
        re.compile(r"\bpayroll\b", re.I),
        re.compile(r"\bsalary_inr\b", re.I),
    ),
    "AAPL": (
        re.compile(r"\baapl\b", re.I),
        re.compile(r"\bapple\b", re.I),
        re.compile(r"\bapplfin\b", re.I),
        re.compile(r"\bnetsales", re.I),
    ),
    "FRED": (
        re.compile(r"\bfred\b", re.I),
        re.compile(r"\bcpi", re.I),
        re.compile(r"\bfedfunds\b", re.I),
        re.compile(r"\bumcsent\b", re.I),
        re.compile(r"\bcpiAucsl\b", re.I),
    ),
    "SYNTH_CAL": (
        re.compile(r"\bcalendar\b", re.I),
        re.compile(r"\bmeetings?\b", re.I),
        re.compile(r"\borganizer\b", re.I),
    ),
}


def detect_mentioned_entities(text: str) -> list[str]:
    blob = text or ""
    found: list[str] = []
    for entity, patterns in _CUES.items():
        if any(p.search(blob) for p in patterns):
            found.append(entity)
    return found


def assert_entity_allowed(ctx: Any, entity: str) -> None:
    """Record a table/chunk entity on the request; deny illegal mixes (F7)."""
    token = str(entity or "").strip().upper()
    if not token or token == "KB":
        return
    extra = ctx.extra if ctx is not None else None
    if extra is None:
        return
    touched = [str(e) for e in (extra.get("touched_entities") or [])]
    trial = list(dict.fromkeys([*touched, token]))
    legal, reason = entities_join_legal(trial)
    if not legal:
        raise PolicyDeniedError(reason)
    extra["touched_entities"] = trial


def abstain_message(reason: str) -> str:
    return (
        "I abstain. "
        f"{reason} "
        "This lake is entity-tagged; illegal pairs cannot be combined into one total."
    )
