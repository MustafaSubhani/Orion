"""Single LLM client. Workers never call a provider SDK directly.

Contract:
- Every call is a traced span with token accounting.
- Retries only on 429 / 5xx. Schema errors are not retried.
- Swapping a model is a Settings change; no worker file edits.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Optional
from uuid import uuid4

import httpx

from harness.config import Settings, get_settings
from harness.context import RequestContext
from harness.errors import BudgetExceededError, HarnessError, TimeoutError_, ToolRuntimeError
from harness.types import LLMResponse, SpanKind, ToolCall
from harness.trace import get_tracer, hash_payload


class LLMGateway:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def complete(
        self,
        *,
        ctx: RequestContext,
        messages: list[dict[str, Any]],
        model: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        force_json: bool = False,
        temperature: float = 0.1,
        timeout_s: float = 90.0,
        max_tokens: int = 2048,
        purpose: str = "worker",
        name: str = "llm.complete",
    ) -> LLMResponse:
        """Chat completion. `purpose` is worker | router | judge | repair | lab."""
        ctx.check_time()
        settings = self.settings
        timeout_s = timeout_s or settings.llm_timeout_s
        provider, url, api_key, model = _resolve_endpoint(settings, purpose, model)

        if not api_key and provider == "mistral":
            raise ToolRuntimeError("MISTRAL_API_KEY is not set")
        if provider == "openai_compat" and not url:
            raise ToolRuntimeError(
                "ORION_WORKER_BASE_URL is not set for openai_compat (Ollama: http://127.0.0.1:11434/v1)"
            )
        if provider == "openai_compat" and not api_key:
            api_key = "local"

        from harness.context_engine import pack_for_model

        packed = pack_for_model(
            messages,
            tools=tools,
            model=model,
            session=getattr(ctx, "session", None),
            output_reserve=max_tokens,
            compact_ratio=settings.compact_ratio,
            tail_keep=settings.context_tail,
            preview_chars=settings.tool_preview_chars,
            window_override=settings.context_window or None,
            remaining_tokens=max(0, ctx.budget.max_tokens - ctx.budget.tokens_used),
        )
        messages = packed.messages
        tools = packed.tools

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if force_json:
            payload["response_format"] = {"type": "json_object"}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        tracer = get_tracer()
        last_err: Exception | None = None
        max_retries = 4
        with tracer.span(
            ctx.request_id,
            SpanKind.LLM,
            name,
            parent_id=ctx.parent_span_id,
            args={
                "model": model,
                "purpose": purpose,
                "n_messages": len(messages),
                "context_est": packed.tokens_est,
                "context_window": packed.window,
                "compacted": packed.compacted,
            },
        ) as span:
            for attempt in range(max_retries):
                t0 = time.perf_counter()
                try:
                    with httpx.Client(timeout=timeout_s) as client:
                        response = client.post(url, json=payload, headers=headers)
                    if response.status_code == 400 and attempt < max_retries - 1:
                        body = (response.text or "").lower()
                        stripped = False
                        if "response_format" in body or "json_object" in body:
                            payload.pop("response_format", None)
                            stripped = True
                        if "tool" in body and ("tools" in payload):
                            payload.pop("tools", None)
                            payload.pop("tool_choice", None)
                            stripped = True
                        if "max_completion_tokens" in body and "max_tokens" in payload:
                            payload["max_completion_tokens"] = payload.pop("max_tokens")
                            stripped = True
                        if stripped:
                            continue
                    if response.status_code == 429 and attempt < max_retries - 1:
                        time.sleep(min(30, 4 * (attempt + 1)))
                        continue
                    if response.status_code >= 500 and attempt < max_retries - 1:
                        time.sleep(2 * (attempt + 1))
                        continue
                    response.raise_for_status()
                    data = response.json()
                    parsed = _parse_chat_response(data, model=model)
                    parsed.latency_ms = (time.perf_counter() - t0) * 1000
                    span.tokens_in = parsed.tokens_in or packed.tokens_est
                    span.tokens_out = parsed.tokens_out
                    span.result_hash = hash_payload(parsed.text[:500])
                    span.extra["compacted"] = packed.compacted
                    span.extra["artifacts_stubbed"] = packed.artifacts_stubbed
                    span.extra["middle_folded"] = packed.middle_folded
                    parsed.packed_messages = packed.messages
                    ctx.charge_tokens((parsed.tokens_in or packed.tokens_est) + parsed.tokens_out)
                    return parsed
                except BudgetExceededError:
                    raise
                except HarnessError:
                    raise
                except httpx.TimeoutException as exc:
                    last_err = TimeoutError_(f"LLM timeout after {timeout_s}s", details=str(exc))
                    if attempt == max_retries - 1:
                        span.error_class = "Timeout"
                        raise last_err
                except httpx.HTTPStatusError as exc:
                    last_err = ToolRuntimeError(
                        f"LLM HTTP {exc.response.status_code}",
                        details=exc.response.text[:300],
                    )
                    if attempt == max_retries - 1:
                        span.error_class = "ToolRuntime"
                        raise last_err
                except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError) as exc:
                    last_err = ToolRuntimeError("LLM call failed", details=str(exc)[:300])
                    if attempt == max_retries - 1:
                        span.error_class = "ToolRuntime"
                        raise last_err
                    time.sleep(2 * (attempt + 1))
            raise last_err or ToolRuntimeError("LLM call failed")


def _resolve_endpoint(settings: Settings, purpose: str, model: Optional[str]) -> tuple[str, str, str, str]:
    if purpose == "judge":
        provider = settings.judge_provider
        chosen = model or settings.tiers.judge
        if provider == "openai_compat":
            base = settings.judge_base_url or settings.worker_base_url
            key = settings.judge_api_key or "local"
        else:
            base = settings.judge_base_url or settings.mistral_api_url
            key = settings.judge_api_key or settings.mistral_api_key
    elif purpose == "router":
        provider = settings.router_provider
        chosen = model or settings.tiers.router
        if provider == "openai_compat":
            base = settings.worker_base_url
            key = settings.worker_api_key or "local"
        else:
            base = settings.mistral_api_url
            key = settings.worker_api_key or settings.mistral_api_key
    else:
        provider = settings.worker_provider
        chosen = model or settings.tiers.worker
        if provider == "openai_compat":
            base = settings.worker_base_url
            key = settings.worker_api_key or "local"
        else:
            base = settings.mistral_api_url
            key = settings.worker_api_key or settings.mistral_api_key
    return provider, _chat_url(provider, base), key, chosen


def _chat_url(provider: str, base: str) -> str:
    url = (base or "").strip()
    if provider == "openai_compat":
        if not url:
            return ""
        if url.endswith("/chat/completions"):
            return url
        return url.rstrip("/") + "/chat/completions"
    return url or "https://api.mistral.ai/v1/chat/completions"


def _parse_chat_response(data: dict[str, Any], *, model: str) -> LLMResponse:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = message.get("content") or ""
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    usage = data.get("usage") or {}
    tool_calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        fn = raw.get("function") or {}
        args_raw = fn.get("arguments") or "{}"
        if isinstance(args_raw, str):
            try:
                args = json.loads(args_raw) if args_raw.strip() else {}
            except json.JSONDecodeError:
                args = {}
        elif isinstance(args_raw, dict):
            args = args_raw
        else:
            args = {}
        name = fn.get("name") or raw.get("name") or ""
        tool_calls.append(
            ToolCall(
                id=raw.get("id") or uuid4().hex[:10],
                verb=name,
                arguments=args if isinstance(args, dict) else {},
            )
        )
    if not tool_calls:
        fallback = _tool_call_from_text(text)
        if fallback:
            tool_calls.append(fallback)
    return LLMResponse(
        text=text or "",
        model=model,
        tool_calls=tool_calls,
        tokens_in=int(usage.get("prompt_tokens") or 0),
        tokens_out=int(usage.get("completion_tokens") or 0),
        raw_finish_reason=str(choice.get("finish_reason") or ""),
    )


def _tool_call_from_text(text: str) -> Optional[ToolCall]:
    """Fallback when a model dumps a JSON or Hermes tool call in content instead of tool_calls."""
    blob = text.strip()
    if "```" in blob:
        parts = blob.split("```")
        for part in parts[1:]:
            part = part.removeprefix("json").strip()
            blob = part
            break
    tagged = re.search(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.S)
    if tagged:
        blob = tagged.group(1).strip()
    invoke = re.search(
        r"(?:invoke|call)\s+(?:tool\s+)?([a-zA-Z0-9_]+)\s+(?:with\s+)?(\{.*\})",
        text,
        re.S,
    )
    if not blob.startswith("{") and invoke:
        try:
            args = json.loads(invoke.group(2))
        except json.JSONDecodeError:
            args = {}
        if isinstance(args, dict):
            return ToolCall(id=uuid4().hex[:10], verb=invoke.group(1), arguments=args)
    if not blob.startswith("{"):
        return None
    try:
        obj = json.loads(blob)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    name = obj.get("name") or obj.get("tool") or obj.get("verb")
    args = obj.get("arguments") or obj.get("args") or obj.get("parameters")
    if isinstance(name, str) and isinstance(args, dict):
        return ToolCall(id=uuid4().hex[:10], verb=name, arguments=args)
    return None
