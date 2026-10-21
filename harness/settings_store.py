"""Runtime settings an admin can change without a restart.

Precedence for `effective(key)`: admin override in SQLite, then the `ORION_<KEY>` environment
variable, then the declared default. Every key is typed and validated. Eval mode ignores every
product key, so benchmark behaviour never depends on this store.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from harness import statedb

DEFAULT_TENANT = "default"
CACHE_TTL_S = 2.0

statedb.register_schema(
    """
    CREATE TABLE IF NOT EXISTS settings (
        tenant TEXT NOT NULL DEFAULT 'default',
        key TEXT NOT NULL,
        value_json TEXT NOT NULL,
        updated_by TEXT,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (tenant, key)
    );
    """
)


class SettingsError(ValueError):
    """Unknown key or a value that fails validation. The message is safe to show an admin."""


@dataclass(frozen=True)
class Setting:
    type: type
    default: Any
    description: str
    choices: tuple[str, ...] = ()
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    prefixes: tuple[str, ...] = ()  # extra accepted prefixes for string settings, e.g. "mcp:"


SCHEMA: dict[str, Setting] = {
    "guard.enabled": Setting(bool, False, "Run the Hugging Face prompt-injection classifier on live chat."),
    "guard.model_id": Setting(str, "protectai/deberta-v3-base-prompt-injection-v2", "Hugging Face model id."),
    "guard.threshold": Setting(float, 0.9, "Injection score at or above which text is blocked.", minimum=0.0, maximum=1.0),
    "guard.scan_tool_outputs": Setting(bool, True, "Also scan untrusted tool output (web, MCP, KB, fetch)."),
    "guard.fail_mode": Setting(str, "open", "If the model cannot load: open continues, closed blocks.", choices=("open", "closed")),
    "web_search.enabled": Setting(bool, False, "Expose the web_search tool to agents in live chat."),
    "web_search.backend": Setting(str, "auto", "auto, openai, ddg or mcp:<server>.", choices=("auto", "openai", "ddg"), prefixes=("mcp:",)),
    "web_search.max_results": Setting(int, 5, "Maximum search results returned.", minimum=1, maximum=10),
    "web_search.allowed_domains": Setting(list, [], "Only return results from these domains (empty means any)."),
    "web_search.openai_model": Setting(str, "", "Model for OpenAI hosted search. Empty uses the worker model."),
    "mcp.enabled": Setting(bool, True, "Connect configured external MCP servers."),
    "memory.enabled": Setting(bool, True, "Keep conversation threads and per-user skills."),
    "memory.summary_trigger_tokens": Setting(int, 6000, "Thread size that triggers a rolling summary.", minimum=500, maximum=200000),
    "memory.summary_max_tokens": Setting(int, 400, "Token cap for a generated summary.", minimum=64, maximum=4000),
    "memory.curate_every": Setting(int, 20, "Run the skill curator after this many skill saves.", minimum=1, maximum=1000),
    "context.summarize": Setting(str, "abstractive", "How old turns are compacted.", choices=("off", "extractive", "abstractive")),
    "lab.auto_eval": Setting(bool, True, "Evaluate Lab agents automatically before promotion."),
    "lab.min_score": Setting(float, 0.8, "Minimum probe score to promote a Lab agent.", minimum=0.0, maximum=1.0),
    "parallel.max_workers": Setting(int, 4, "Concurrent agents per wave.", minimum=1, maximum=32),
    "egress.allowlist": Setting(list, [], "Extra hosts http_fetch may call (added to the domain pack list)."),
    "auth.open_signup": Setting(bool, False, "Let anyone create a limited account."),
    "retrieval.min_similarity": Setting(float, 0.0, "Retrieval below this similarity is a miss (0 disables).", minimum=0.0, maximum=1.0),
    "tools.write_policy": Setting(str, "confirm", "Write and egress tools: deny, confirm or allow.", choices=("deny", "confirm", "allow")),
    "sandbox.memory_mb": Setting(int, 512, "Memory cap for generated code.", minimum=64, maximum=8192),
    "sandbox.cpu_s": Setting(int, 10, "CPU seconds for generated code.", minimum=1, maximum=300),
    "sandbox.wall_s": Setting(int, 30, "Wall seconds for generated code.", minimum=1, maximum=600),
    "sandbox.backend": Setting(str, "subprocess", "Isolation backend.", choices=("subprocess", "docker")),
}

_cache: dict[tuple[str, str], tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def env_name(key: str) -> str:
    return "ORION_" + key.upper().replace(".", "_")


def _coerce(spec: Setting, raw: Any, key: str) -> Any:
    """Convert and validate `raw` against `spec`. Raises SettingsError."""
    try:
        if spec.type is bool:
            if isinstance(raw, bool):
                value: Any = raw
            else:
                text = str(raw).strip().lower()
                if text in {"1", "true", "yes", "on"}:
                    value = True
                elif text in {"0", "false", "no", "off"}:
                    value = False
                else:
                    raise ValueError("expected true or false")
        elif spec.type is int:
            if isinstance(raw, bool):
                raise ValueError("expected an integer")
            value = int(raw)
        elif spec.type is float:
            value = float(raw)
        elif spec.type is list:
            if isinstance(raw, str):
                text = raw.strip()
                raw = json.loads(text) if text.startswith("[") else [p.strip() for p in text.split(",") if p.strip()]
            if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
                raise ValueError("expected a list of strings")
            value = [x.strip().lower() for x in raw if x.strip()]
        else:
            value = str(raw).strip()
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SettingsError(f"{key}: {exc}") from exc
    if spec.type in (int, float):
        if spec.minimum is not None and value < spec.minimum:
            raise SettingsError(f"{key}: must be at least {spec.minimum}")
        if spec.maximum is not None and value > spec.maximum:
            raise SettingsError(f"{key}: must be at most {spec.maximum}")
    if spec.choices and spec.type is str and value not in spec.choices:
        if not any(value.startswith(p) and len(value) > len(p) for p in spec.prefixes):
            allowed = list(spec.choices) + [p + "<name>" for p in spec.prefixes]
            raise SettingsError(f"{key}: must be one of {allowed}")
    return value


def _spec(key: str) -> Setting:
    spec = SCHEMA.get(key)
    if spec is None:
        raise SettingsError(f"unknown setting '{key}'")
    return spec


def _db_get(key: str, tenant: str) -> Optional[Any]:
    conn = statedb.connect()
    try:
        row = conn.execute(
            "SELECT value_json FROM settings WHERE tenant = ? AND key = ?", (tenant, key)
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row["value_json"]) if row else None


def _invalidate(key: str, tenant: str) -> None:
    with _cache_lock:
        _cache.pop((tenant, key), None)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def effective(key: str, tenant: str = DEFAULT_TENANT) -> Any:
    """Current value for `key`: admin override, else env, else default."""
    spec = _spec(key)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get((tenant, key))
        if hit and now - hit[0] < CACHE_TTL_S:
            return hit[1]
    value: Any
    try:
        stored = _db_get(key, tenant)
    except Exception:  # a broken state DB must never take chat down; fall back to env/default
        stored = None
    if stored is not None:
        try:
            value = _coerce(spec, stored, key)
        except SettingsError:
            value = spec.default
    else:
        raw_env = os.getenv(env_name(key))
        try:
            value = _coerce(spec, raw_env, key) if raw_env not in (None, "") else spec.default
        except SettingsError:
            value = spec.default
    if isinstance(value, list):
        value = list(value)
    with _cache_lock:
        _cache[(tenant, key)] = (now, value)
    return value


def source(key: str, tenant: str = DEFAULT_TENANT) -> str:
    _spec(key)
    try:
        if _db_get(key, tenant) is not None:
            return "admin"
    except Exception:
        pass
    return "env" if os.getenv(env_name(key)) not in (None, "") else "default"


def set_value(key: str, value: Any, actor: str, tenant: str = DEFAULT_TENANT) -> Any:
    """Validate and persist an admin override. Returns the stored value."""
    spec = _spec(key)
    clean = _coerce(spec, value, key)
    conn = statedb.connect()
    try:
        conn.execute(
            "INSERT INTO settings (tenant, key, value_json, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(tenant, key) DO UPDATE SET value_json = excluded.value_json, "
            "updated_by = excluded.updated_by, updated_at = CURRENT_TIMESTAMP",
            (tenant, key, json.dumps(clean), actor),
        )
        conn.commit()
    finally:
        conn.close()
    _invalidate(key, tenant)
    _audit(actor, key, clean)
    return clean


def reset(key: str, actor: str, tenant: str = DEFAULT_TENANT) -> None:
    """Drop the admin override so env or default applies again."""
    _spec(key)
    conn = statedb.connect()
    try:
        conn.execute("DELETE FROM settings WHERE tenant = ? AND key = ?", (tenant, key))
        conn.commit()
    finally:
        conn.close()
    _invalidate(key, tenant)
    _audit(actor, key, "<reset>")


def describe(tenant: str = DEFAULT_TENANT) -> list[dict[str, Any]]:
    """Every key with its current value, source, default and constraints (for the admin UI)."""
    rows = []
    for key, spec in SCHEMA.items():
        rows.append(
            {
                "key": key,
                "value": effective(key, tenant),
                "source": source(key, tenant),
                "default": spec.default,
                "type": spec.type.__name__,
                "description": spec.description,
                "choices": list(spec.choices),
                "minimum": spec.minimum,
                "maximum": spec.maximum,
            }
        )
    return rows


def _audit(actor: str, key: str, value: Any) -> None:
    try:
        from harness.trace import get_tracer

        get_tracer().log_event("settings_change", actor, "-", {"key": key, "value": value}, role="admin")
    except Exception:  # auditing must not block a settings change
        pass
