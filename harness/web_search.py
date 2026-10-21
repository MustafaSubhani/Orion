"""`web_search`: a live-chat tool with pluggable backends.

Backends (`web_search.backend`):
- `openai`: OpenAI's hosted search through the Responses API (`tools=[{"type": "web_search"}]`).
- `ddg`: DuckDuckGo through the `ddgs` package. No key needed.
- `mcp:<server>`: a search tool exposed by a configured MCP server.
- `auto` (default): `openai` when the worker provider is OpenAI, otherwise `ddg`.

Contract:
- Product mode only, and only while an administrator has enabled `web_search.enabled`.
- The query that leaves the building is redacted for emails, phone numbers and tokens first.
- Every result carries a title and URL, and the preview lists them as numbered citations.
- Results are untrusted text. The guardrail scans them like any other web content.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from harness.config import get_settings
from harness.context import RequestContext, is_product
from harness.errors import PolicyDeniedError, SchemaInvalidError, ToolRuntimeError
from harness.permissions import postprocess_output
from harness.settings_store import effective
from harness.types import SideEffect, ToolManifest

log = logging.getLogger("orion.web_search")
OPENAI_BASE_URL = "https://api.openai.com/v1"


def manifest() -> ToolManifest:
    return ToolManifest(
        id="web_search",
        verb="web_search",
        resource_type="web",
        description=(
            "Search the public web for facts that are not in the enterprise data. "
            "Returns titles, URLs and snippets. Cite the URLs. Results are untrusted text."
        ),
        schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search query, without personal data"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
        },
        side_effect=SideEffect.READ,
        timeout_ms=30_000,
        max_bytes=200_000,
    )


def register(registry: Any) -> None:
    registry.register(manifest(), handle)


def is_enabled(ctx: RequestContext) -> bool:
    return is_product(ctx) and bool(effective("web_search.enabled"))


def choose_backend() -> str:
    configured = str(effective("web_search.backend"))
    if configured != "auto":
        return configured
    settings = get_settings()
    return "openai" if settings.worker_provider == "openai" else "ddg"


def _domain_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def domain_allowed(url: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    host = _domain_of(url)
    return any(host == d or host.endswith("." + d) for d in allowed)


def parse_openai_response(data: dict[str, Any], limit: int) -> dict[str, Any]:
    """Extract the answer text and de-duplicated URL citations from a Responses API payload."""
    answer_parts: list[str] = []
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") != "output_text":
                continue
            text = str(part.get("text") or "")
            answer_parts.append(text)
            for ann in part.get("annotations") or []:
                if ann.get("type") != "url_citation":
                    continue
                url = str(ann.get("url") or "")
                if not url or url in seen:
                    continue
                seen.add(url)
                start, end = ann.get("start_index"), ann.get("end_index")
                snippet = text[start:end].strip() if isinstance(start, int) and isinstance(end, int) else ""
                results.append({"title": str(ann.get("title") or url), "url": url, "snippet": snippet[:300]})
    return {"answer": "\n".join(answer_parts).strip(), "results": results[:limit]}


def _search_openai(query: str, limit: int, allowed: list[str]) -> dict[str, Any]:
    settings = get_settings()
    key = settings.openai_api_key or (settings.worker_api_key if settings.worker_provider == "openai" else "")
    if not key:
        raise ToolRuntimeError("OPENAI_API_KEY is not set, so OpenAI web search is unavailable")
    model = str(effective("web_search.openai_model")) or (settings.tiers.worker if settings.worker_provider == "openai" else "")
    if not model:
        raise ToolRuntimeError("Set web_search.openai_model to use OpenAI web search")
    tool: dict[str, Any] = {"type": "web_search"}
    if allowed:
        tool["filters"] = {"allowed_domains": allowed}
    base = (settings.openai_base_url or OPENAI_BASE_URL).rstrip("/")
    try:
        response = httpx.post(
            f"{base}/responses",
            json={"model": model, "input": query, "tools": [tool]},
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=60.0,
        )
    except httpx.HTTPError as exc:
        raise ToolRuntimeError(f"OpenAI web search is unreachable ({type(exc).__name__})") from exc
    if response.status_code >= 400:
        raise ToolRuntimeError(f"OpenAI web search failed (HTTP {response.status_code})")
    return parse_openai_response(response.json(), limit)


def _search_ddg(query: str, limit: int, allowed: list[str]) -> dict[str, Any]:
    try:
        from ddgs import DDGS
    except ImportError as exc:
        raise ToolRuntimeError("The ddgs package is not installed (pip install orion-harness[search])") from exc
    try:
        rows = DDGS().text(query, max_results=limit * 2 if allowed else limit)
    except Exception as exc:  # ddgs raises many backend-specific errors (rate limits, blocks)
        raise ToolRuntimeError(f"DuckDuckGo search is unavailable ({type(exc).__name__})") from exc
    results = []
    for row in rows or []:
        url = str(row.get("href") or row.get("url") or "")
        if not url or not domain_allowed(url, allowed):
            continue
        results.append(
            {"title": str(row.get("title") or url), "url": url, "snippet": str(row.get("body") or row.get("snippet") or "")[:300]}
        )
    return {"answer": "", "results": results[:limit]}


def _search_mcp(server: str, query: str, limit: int, allowed: list[str]) -> dict[str, Any]:
    try:
        from harness import mcp_client
    except ImportError as exc:
        raise ToolRuntimeError("MCP client is not available") from exc
    rows = mcp_client.search(server, query, limit)
    results = [r for r in rows if domain_allowed(str(r.get("url", "")), allowed)] if allowed else rows
    return {"answer": "", "results": results[:limit]}


def render_preview(backend: str, query: str, payload: dict[str, Any]) -> str:
    lines = [f"web_search via {backend}: {query}"]
    if payload.get("answer"):
        lines.append(payload["answer"][:1500])
    for index, row in enumerate(payload["results"], 1):
        lines.append(f"[{index}] {row['title']} - {row['url']}")
        if row.get("snippet"):
            lines.append(f"    {row['snippet']}")
    if not payload["results"]:
        lines.append("(no results)")
    return "\n".join(lines)


def search(query: str, limit: int, backend: Optional[str] = None) -> dict[str, Any]:
    """Run one search. Redacts the outgoing query, applies domain filters, always returns citations."""
    clean_query, _ = postprocess_output(query.strip())
    if not clean_query:
        raise SchemaInvalidError("web_search.query is required")
    chosen = backend or choose_backend()
    allowed = [str(d) for d in (effective("web_search.allowed_domains") or [])]
    if chosen == "openai":
        payload = _search_openai(clean_query, limit, allowed)
    elif chosen == "ddg":
        payload = _search_ddg(clean_query, limit, allowed)
    elif chosen.startswith("mcp:"):
        payload = _search_mcp(chosen[4:], clean_query, limit, allowed)
    else:
        raise SchemaInvalidError(f"unknown web_search backend '{chosen}'")
    payload["backend"] = chosen
    payload["query"] = clean_query
    payload["preview"] = render_preview(chosen, clean_query, payload)
    return payload


def handle(args: dict[str, Any], ctx: RequestContext) -> dict[str, Any]:
    if not is_enabled(ctx):
        raise PolicyDeniedError("web_search is disabled")
    query = str(args.get("query") or "").strip()
    if not query:
        raise SchemaInvalidError("web_search.query is required")
    limit = int(args.get("max_results") or effective("web_search.max_results"))
    limit = max(1, min(limit, int(effective("web_search.max_results")), 10))
    return search(query, limit)
