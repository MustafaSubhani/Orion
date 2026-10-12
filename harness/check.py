"""Harness doctor: frozen lake, task lock, and optional LLM ping. No secrets printed."""

from __future__ import annotations

import argparse
import json

from harness.config import FROZEN_DIR, TASKS_DIR, get_settings
from retrieval.tables import TABLE_FILES


def snapshot() -> dict:
    settings = get_settings()
    frozen = {name: path.exists() for name, path in TABLE_FILES.items()}
    lock = TASKS_DIR / "SPLIT_LOCK.json"
    return {
        "worker_provider": settings.worker_provider,
        "router_provider": settings.router_provider,
        "judge_provider": settings.judge_provider,
        "models": {
            "router": settings.tiers.router,
            "worker": settings.tiers.worker,
            "judge": settings.tiers.judge,
        },
        "worker_base_url": settings.worker_base_url or None,
        "llm_timeout_s": settings.llm_timeout_s,
        "frozen": frozen,
        "frozen_ok": all(frozen.values()),
        "split_lock": lock.exists(),
        "has_worker_key": bool(settings.worker_api_key),
        "has_mistral_key": bool(settings.mistral_api_key),
    }


def ping() -> dict:
    from harness.context import new_context
    from harness.gateway import LLMGateway

    gw = LLMGateway()
    ctx = new_context(user="check")
    resp = gw.complete(
        ctx=ctx,
        messages=[{"role": "user", "content": "Reply with the single word pong."}],
        purpose="router",
        name="check.ping",
        max_tokens=16,
        tools=None,
        force_json=False,
    )
    return {"ok": True, "model": resp.model, "text": (resp.text or "")[:120]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ping", action="store_true", help="one tiny LLM call")
    args = parser.parse_args()
    report = snapshot()
    if args.ping:
        try:
            report["ping"] = ping()
        except Exception as exc:
            report["ping"] = {"ok": False, "error": str(exc)[:300]}
    print(json.dumps(report, indent=2))
    if not report["frozen_ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
