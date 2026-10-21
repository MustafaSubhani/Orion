"""Entity, join, egress and role policy for the MNC / AAPL / MSFT / FRED / SYNTH_CAL lake.

Verbatim move of definitions that used to live in `harness.config`, `harness.permissions` and
`harness.entities`. Core code reads them through `harness.packs`, never directly.
"""

from __future__ import annotations

import re

COLLECTION_HR = "hr_data"
COLLECTION_FINANCE = "finance_data"
COLLECTION_SCHEDULER = "scheduler_data"
KNOWN_COLLECTIONS = (COLLECTION_HR, COLLECTION_FINANCE, COLLECTION_SCHEDULER)

# Order matters: it is the order shown to users in join errors. "KB" (live uploads) is added by core.
ALLOWED_ENTITIES = ("MSFT", "AAPL", "MNC", "FRED", "SYNTH_CAL")

# Legal joins: sources that may be combined in one answer. Anything else must abstain.
# Undirected: AAPL+SYNTH_CAL is calendar context, not a payroll/10-K mix.
LEGAL_JOINS: dict[str, frozenset[str]] = {
    "AAPL": frozenset({"AAPL", "FRED", "SYNTH_CAL"}),
    "FRED": frozenset({"AAPL", "FRED", "MSFT"}),  # macros may accompany a filing
    "MSFT": frozenset({"MSFT", "FRED"}),
    "MNC": frozenset({"MNC"}),
    "SYNTH_CAL": frozenset({"SYNTH_CAL", "AAPL"}),
}


def entities_join_legal(entities: list[str]) -> tuple[bool, str]:
    """True iff every named entity is allowed to appear with every other (pack-local convenience)."""
    from harness.packs import registry

    return registry().entities_join_legal(entities)


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

SALARY_COLUMNS = frozenset({"Salary_INR", "Salary", "SalaryINR", "compensation", "pay"})

_READ_TOOLS = (
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
)

ROLE_TOOL_ALLOWLIST: dict[str, frozenset[str]] = {
    "limited": frozenset(_READ_TOOLS),
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

# Conservative cues: only fire when the query actually names the source.
# Do not tag MNC on generic "employees" (that would poison Apple catalog questions).
ENTITY_CUES: dict[str, tuple[re.Pattern[str], ...]] = {
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

# Eval-only: a plausible but wrong column name makes the F4 injection a realistic schema slip.
FAULT_VALUES = {
    "bad_column": "PerformanceScore",
    "bad_table": "nope",
    "bad_collection": "does_not_exist",
}

TOOL_DESCRIPTIONS = {
    "retrieve": "Retrieve evidence chunks. Optional entity filter (MNC|AAPL|MSFT|FRED|SYNTH_CAL).",
    "forecast": "ARIMA forecast on a numeric column of applfin or a FRED table.",
    "http_fetch": "GET an allow-listed URL (SEC, FRED, Apple, Microsoft). No secrets in the sandbox.",
}

SYNTHESIS_HINT = "Mark entity sources (MNC / AAPL / MSFT / FRED / SYNTH_CAL) when mixing."

# Run-specific recipes that do not transfer (cloned-table caps, one fiscal story).
STALE_SKILL_PATTERNS = (
    "4000 match",
    "4000 matches",
    "match_count=4000",
    "limit=4000",
    "interpret this as zero",
    "fiscal year ends",
    "fiscal quarter mapping",
    "q4 = calendar q4",
    "deduplicate by event_id",
)

ABSTAIN_SUFFIX = "This lake is entity-tagged; illegal pairs cannot be combined into one total."


def default_collection(agent_id: str) -> str:
    """Vector collection a pinned or running agent should search when none is given."""
    if agent_id == "hr":
        return COLLECTION_HR
    if agent_id == "scheduler":
        return COLLECTION_SCHEDULER
    return COLLECTION_FINANCE


def domain_mismatch(agent_id: str, task: str, text: str) -> bool:
    """HR agent answering a filings or macro question is a routing error (F3)."""
    blob = (task + " " + text).lower()
    return agent_id == "hr" and any(w in blob for w in ("10-k", "netsales", "cpi")) and "employee" not in blob
