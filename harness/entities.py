"""Entity mention tagging and join gates. Mixed-lake isolation lives here, not in worker prose.

The cue patterns and legal-join matrix come from the loaded domain packs (`harness.packs`).
"""

from __future__ import annotations

from typing import Any

from harness.config import entities_join_legal
from harness.errors import PolicyDeniedError
from harness.packs import KB_ENTITY, registry


def detect_mentioned_entities(text: str) -> list[str]:
    """Entities the query explicitly names. Conservative: only fires on a named source."""
    return registry().detect_entities(text)


def assert_entity_allowed(ctx: Any, entity: str) -> None:
    """Record a table/chunk entity on the request; deny illegal mixes (F7)."""
    token = str(entity or "").strip().upper()
    if not token or token == KB_ENTITY:
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
    suffix = registry().abstain_suffix()
    return f"I abstain. {reason} {suffix}".rstrip() if suffix else f"I abstain. {reason}"
