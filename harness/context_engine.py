"""Cursor-style context packing: per-model windows, tool-result stubs, middle-turn compression.

The LLM never sees the raw session. This module decides what fits:
1. Keep system + the live tail.
2. Stub large tool payloads into the artifact vault (session_get can expand one).
3. Fold the middle into an extractive compact note before the window fills.
4. If still over: drop oldest non-system turns, then shrink tool schemas.

Token estimates are char/4 so packing does not depend on a tokenizer. When the
provider returns usage.prompt_tokens, the gateway still charges the real count.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

# Conservative defaults. Override with ORION_CONTEXT_WINDOW for a local 8k/32k model.
_WINDOW_HINTS: tuple[tuple[str, int], ...] = (
    ("ministral", 128_000),
    ("mistral-nemo", 128_000),
    ("open-mistral-nemo", 128_000),
    ("mistral-large", 128_000),
    ("mistral-medium", 128_000),
    ("mistral-small", 32_000),
    ("codestral", 32_000),
    ("gpt-4o", 128_000),
    ("gpt-4.1", 1_000_000),
    ("claude", 200_000),
    ("qwen2.5", 32_768),
    ("qwen", 32_768),
    ("llama-3.2", 128_000),
    ("llama-3.1", 128_000),
    ("llama3.1", 128_000),
    ("llama3", 8_192),
    ("llama", 8_192),
    ("mistral", 32_000),
    ("deepseek", 64_000),
    ("phi", 16_384),
    ("gemma", 8_192),
    ("glm", 32_000),
)


@dataclass
class PackedPrompt:
    messages: list[dict[str, Any]]
    tools: Optional[list[dict[str, Any]]]
    tokens_est: int
    window: int
    budget: int
    compacted: bool = False
    artifacts_stubbed: int = 0
    middle_folded: bool = False
    schemas_slimmed: bool = False
    dropped: int = 0


def estimate_tokens(value: Any) -> int:
    """Cheap tokenizer stand-in. ~4 chars / token; JSON slightly denser."""
    if value is None:
        return 0
    if isinstance(value, (int, float, bool)):
        return 1
    if isinstance(value, str):
        if not value:
            return 0
        return max(1, (len(value) + 3) // 4)
    try:
        blob = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        blob = str(value)
    return max(1, (len(blob) + 3) // 4)


def window_for(model: str | None, override: int | None = None) -> int:
    if override and override > 0:
        return override
    env = os.getenv("ORION_CONTEXT_WINDOW", "").strip()
    if env.isdigit():
        return int(env)
    name = (model or "").lower()
    for needle, size in _WINDOW_HINTS:
        if needle in name:
            return size
    return 32_768


def _msg_tokens(msg: dict[str, Any]) -> int:
    n = estimate_tokens(msg.get("content") or "")
    n += estimate_tokens(msg.get("role") or "")
    n += estimate_tokens(msg.get("tool_calls"))
    n += 8
    return n


def _total(messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]]) -> int:
    return sum(_msg_tokens(m) for m in messages) + estimate_tokens(tools)


def pack_for_model(
    messages: list[dict[str, Any]],
    *,
    tools: Optional[list[dict[str, Any]]] = None,
    model: str | None = None,
    session: Any = None,
    output_reserve: int = 2048,
    compact_ratio: float = 0.75,
    tail_keep: int = 8,
    preview_chars: int = 1200,
    window_override: int | None = None,
    remaining_tokens: int | None = None,
) -> PackedPrompt:
    """Fit `messages` into the model window. Mutates copies, never the caller's list.

    `remaining_tokens` is the request-level budget still unused. Cursor-style:
    the packer takes the min of (model window × compact_ratio) and leftover
    request tokens so a later ReAct round does not blow ORION_MAX_TOKENS.
    """
    from harness.session import SessionStore

    store: SessionStore = session if session is not None else SessionStore()
    window = window_for(model, window_override)
    reserve = max(256, output_reserve)
    model_budget = max(1024, int((window - reserve) * compact_ratio))
    if remaining_tokens is not None:
        hard = max(512, int(remaining_tokens) - reserve)
        budget = max(512, min(model_budget, hard))
    else:
        budget = model_budget
    packed = [dict(m) for m in messages]
    out_tools = tools
    stats = PackedPrompt(
        messages=packed,
        tools=out_tools,
        tokens_est=_total(packed, out_tools),
        window=window,
        budget=budget,
    )
    if stats.tokens_est <= budget:
        return stats

    packed, n_stub = _stub_tool_payloads(packed, store, preview_chars, keep_last=tail_keep)
    stats.artifacts_stubbed = n_stub
    stats.messages = packed
    stats.tokens_est = _total(packed, out_tools)
    stats.compacted = True
    if stats.tokens_est <= budget:
        return stats

    packed, folded = _fold_middle(packed, tail_keep=tail_keep)
    stats.middle_folded = folded
    stats.messages = packed
    stats.tokens_est = _total(packed, out_tools)
    if stats.tokens_est <= budget:
        return stats

    packed, n_stub_tail = _stub_tool_payloads(packed, store, preview_chars, keep_last=0)
    stats.artifacts_stubbed += n_stub_tail
    stats.messages = packed
    stats.tokens_est = _total(packed, out_tools)
    if stats.tokens_est <= budget:
        return stats

    packed, dropped = _drop_oldest(packed, budget=budget, tools=out_tools)
    stats.dropped = dropped
    stats.messages = packed
    stats.tokens_est = _total(packed, out_tools)
    if stats.tokens_est <= budget:
        return stats

    if out_tools and len(out_tools) > 4:
        out_tools = _slim_tools(out_tools)
        stats.schemas_slimmed = True
        stats.tools = out_tools
        stats.tokens_est = _total(packed, out_tools)
    stats.messages = packed
    stats.tools = out_tools
    return stats


def stub_text(text: str, artifact_id: str, preview_chars: int) -> str:
    preview = text if len(text) <= preview_chars else text[:preview_chars].rstrip() + "…"
    return (
        f"[compacted artifact:{artifact_id} tokens~{estimate_tokens(text)}]\n"
        f"{preview}\n"
        f"Call session_get({{\"artifact_id\": \"{artifact_id}\"}}) if you need the rest."
    )


def _stub_tool_payloads(
    messages: list[dict[str, Any]],
    store: Any,
    preview_chars: int,
    keep_last: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Vault old tool dumps. Pass keep_last>0 to leave the live tail readable (session_get)."""
    n = 0
    out = []
    cutoff = max(0, len(messages) - max(0, keep_last))
    for i, msg in enumerate(messages):
        role = msg.get("role")
        content = msg.get("content")
        if (
            i < cutoff
            and role == "tool"
            and isinstance(content, str)
            and len(content) > preview_chars
        ):
            if "artifact:" in content[:80]:
                out.append(msg)
                continue
            art = store.put(content, kind="tool")
            cloned = dict(msg)
            cloned["content"] = stub_text(content, art.id, preview_chars)
            out.append(cloned)
            n += 1
        else:
            out.append(msg)
    return out, n


def _fold_middle(messages: list[dict[str, Any]], *, tail_keep: int) -> tuple[list[dict[str, Any]], bool]:
    """Keep system + first user + last tail_keep. Fold the rest into one note."""
    if len(messages) <= tail_keep + 2:
        return messages, False
    head: list[dict[str, Any]] = []
    rest = list(messages)
    if rest and rest[0].get("role") == "system":
        head.append(rest.pop(0))
    if rest and rest[0].get("role") == "user":
        head.append(rest.pop(0))
    if len(rest) <= tail_keep:
        return head + rest, False
    middle = rest[:-tail_keep]
    tail = rest[-tail_keep:]
    lines = ["[context compact] Older turns were folded to fit the model window."]
    for msg in middle:
        role = msg.get("role", "?")
        content = str(msg.get("content") or "")
        snippet = content.replace("\n", " ")[:160]
        if msg.get("tool_calls"):
            names = []
            for tc in msg["tool_calls"]:
                fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                names.append(fn.get("name") or "tool")
            snippet = "called " + ",".join(names)
        lines.append(f"- {role}: {snippet}")
    note = {"role": "user", "content": "\n".join(lines)}
    return head + [note] + tail, True


def _drop_oldest(
    messages: list[dict[str, Any]],
    *,
    budget: int,
    tools: Optional[list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], int]:
    """Drop from the left after system, never dropping the last user/tool pair if possible."""
    dropped = 0
    msgs = list(messages)
    while len(msgs) > 2 and _total(msgs, tools) > budget:
        idx = 1 if msgs[0].get("role") == "system" else 0
        if idx >= len(msgs) - 1:
            break
        # do not drop a compact note if it is the only history
        msgs.pop(idx)
        dropped += 1
    return msgs, dropped


def _slim_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Progressive disclosure: keep search_tools + session_get + a few hot verbs."""
    keep_names = {
        "search_tools",
        "session_get",
        "skill_manage",
        "retrieve",
        "sql_spec",
        "schema_lookup",
        "join_check",
        "calendar_query",
        "kb_search",
    }
    slim = []
    for t in tools:
        fn = (t.get("function") or {}) if t.get("type") == "function" else t
        name = fn.get("name") or ""
        if name in keep_names:
            slim.append(t)
        else:
            slim.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": (fn.get("description") or "")[:80] + " (schema hidden — search_tools to load)",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            )
    return slim
