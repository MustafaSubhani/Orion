"""Pinned paths, model tiers, and environment. Workers must not read os.getenv themselves."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
FROZEN_DIR = DATA_DIR / "frozen"
KB_DIR = PROJECT_ROOT / "kb"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
TRACES_DIR = OUTPUTS_DIR / "traces"
GRAPHS_DIR = OUTPUTS_DIR / "graphs"
EXPORTS_DIR = OUTPUTS_DIR / "exports"
LAB_GENERATED_DIR = PROJECT_ROOT / "lab" / "generated"
SKILLS_DIR = OUTPUTS_DIR / "skills"
BENCH_DIR = PROJECT_ROOT / "bench"
TASKS_DIR = BENCH_DIR / "tasks"
CHROMA_DIR = PROJECT_ROOT / "chroma_db"

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

# Collections match existing ChromaDB names so a rebuilt index is not required on day one.
COLLECTION_HR = "hr_data"
COLLECTION_FINANCE = "finance_data"
COLLECTION_SCHEDULER = "scheduler_data"
COLLECTION_KB = "kb_live"  # product chat only; never in KNOWN_COLLECTIONS / retrieve()
KNOWN_COLLECTIONS = (COLLECTION_HR, COLLECTION_FINANCE, COLLECTION_SCHEDULER)

ALLOWED_ENTITIES = ("MSFT", "AAPL", "MNC", "FRED", "SYNTH_CAL", "KB")

# Legal joins: sources that may be combined in one answer. Anything else must abstain.
# Undirected: AAPL+SYNTH_CAL is calendar context, not a payroll/10-K mix.
LEGAL_JOINS: dict[str, frozenset[str]] = {
    "AAPL": frozenset({"AAPL", "FRED", "SYNTH_CAL"}),
    "FRED": frozenset({"AAPL", "FRED", "MSFT"}),  # macros may accompany a filing
    "MSFT": frozenset({"MSFT", "FRED"}),
    "MNC": frozenset({"MNC"}),
    "SYNTH_CAL": frozenset({"SYNTH_CAL", "AAPL"}),
    "KB": frozenset({"KB"}),  # live uploads never join the paper lake
}


def entities_join_legal(entities: list[str]) -> tuple[bool, str]:
    """True iff every named entity is allowed to appear with every other."""
    ents = []
    for raw in entities:
        token = str(raw or "").strip().upper()
        if token:
            ents.append(token)
    ents = list(dict.fromkeys(ents))
    if len(ents) <= 1:
        return True, "single entity or empty"
    unknown = [e for e in ents if e not in LEGAL_JOINS]
    if unknown:
        return False, f"unknown entity {unknown[0]}. known: {list(ALLOWED_ENTITIES)}"
    wanted = set(ents)
    for e in ents:
        if not wanted.issubset(LEGAL_JOINS[e]):
            blocked = sorted(wanted - LEGAL_JOINS[e])
            return False, f"{e} cannot join {blocked} (illegal mix; abstain)"
    return True, "legal join"

EGRESS_ALLOWLIST = (
    "sec.gov",
    "www.sec.gov",
    "data.sec.gov",
    "api.stlouisfed.org",
    "fred.stlouisfed.org",
    "www.apple.com",
    "apple.com",
    "www.microsoft.com",
    "microsoft.com",
)


@dataclass(frozen=True)
class ModelTiers:
    router: str
    worker: str
    judge: str


@dataclass
class Settings:
    """Process-wide settings. Loaded once. Never mutated by workers."""

    mistral_api_key: str = ""
    mistral_api_url: str = "https://api.mistral.ai/v1/chat/completions"
    worker_provider: str = "mistral"  # mistral | openai_compat
    worker_base_url: str = ""
    worker_api_key: str = ""
    router_provider: str = "mistral"
    judge_provider: str = "mistral"
    judge_base_url: str = ""
    judge_api_key: str = ""
    llm_timeout_s: float = 90.0
    tiers: ModelTiers = field(
        default_factory=lambda: ModelTiers(
            router="ministral-8b-latest",
            worker="open-mistral-nemo",
            judge="mistral-large-latest",
        )
    )
    max_heal_cycles: int = 2
    max_tool_rounds: int = 6
    max_tokens_per_request: int = 80_000
    max_seconds_per_request: float = 180.0
    circuit_fail_threshold: int = 3
    circuit_cooldown_s: float = 30.0
    router_topk: int = 3
    retrieve_k: int = 8
    context_window: int = 0  # 0 = infer from model id
    compact_ratio: float = 0.75
    context_tail: int = 8
    tool_preview_chars: int = 1200
    eval_every: int = 4
    skill_topk: int = 2
    cors_origins: tuple[str, ...] = (
        "http://localhost:7860",
        "http://127.0.0.1:7860",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    worker_provider = os.getenv("ORION_WORKER_PROVIDER", "mistral").strip().lower()
    judge_provider = os.getenv("ORION_JUDGE_PROVIDER", worker_provider).strip().lower()
    router_provider = os.getenv("ORION_ROUTER_PROVIDER", worker_provider).strip().lower()
    mistral_key = os.getenv("MISTRAL_API_KEY", "")
    worker_key = os.getenv("ORION_WORKER_API_KEY", mistral_key)
    if worker_provider == "openai_compat" and not worker_key:
        worker_key = "local"
    judge_key = os.getenv("ORION_JUDGE_API_KEY", mistral_key if judge_provider == "mistral" else worker_key)
    if judge_provider == "openai_compat" and not judge_key:
        judge_key = "local"
    return Settings(
        mistral_api_key=mistral_key,
        mistral_api_url=os.getenv("MISTRAL_API_URL", "https://api.mistral.ai/v1/chat/completions"),
        worker_provider=worker_provider,
        worker_base_url=os.getenv("ORION_WORKER_BASE_URL", ""),
        worker_api_key=worker_key,
        router_provider=router_provider,
        judge_provider=judge_provider,
        judge_base_url=os.getenv("ORION_JUDGE_BASE_URL", ""),
        judge_api_key=judge_key,
        tiers=ModelTiers(
            router=os.getenv("ORION_ROUTER_MODEL", "ministral-8b-latest"),
            worker=os.getenv("ORION_WORKER_MODEL", "open-mistral-nemo"),
            judge=os.getenv("ORION_JUDGE_MODEL", "mistral-small-latest"),
        ),
        max_heal_cycles=int(os.getenv("ORION_MAX_HEAL_CYCLES", "2")),
        max_tool_rounds=int(os.getenv("ORION_MAX_TOOL_ROUNDS", "6")),
        max_tokens_per_request=int(os.getenv("ORION_MAX_TOKENS", "80000")),
        max_seconds_per_request=float(os.getenv("ORION_MAX_SECONDS", "180")),
        context_window=int(os.getenv("ORION_CONTEXT_WINDOW", "0") or 0),
        compact_ratio=float(os.getenv("ORION_COMPACT_RATIO", "0.75")),
        context_tail=int(os.getenv("ORION_CONTEXT_TAIL", "8")),
        tool_preview_chars=int(os.getenv("ORION_TOOL_PREVIEW_CHARS", "1200")),
        eval_every=int(os.getenv("ORION_EVAL_EVERY", "4")),
        skill_topk=int(os.getenv("ORION_SKILL_TOPK", "2")),
        llm_timeout_s=float(os.getenv("ORION_LLM_TIMEOUT", "90")),
    )


def ensure_dirs() -> None:
    for path in (
        FROZEN_DIR,
        KB_DIR,
        OUTPUTS_DIR,
        TRACES_DIR,
        GRAPHS_DIR,
        EXPORTS_DIR,
        LAB_GENERATED_DIR,
        TASKS_DIR,
        SKILLS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
