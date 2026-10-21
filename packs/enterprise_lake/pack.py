"""The default pack: HR payroll, SEC filings, Apple financials and catalog, FRED macros, calendar."""

from __future__ import annotations

from typing import Any

from packs.enterprise_lake import policy


class EnterpriseLakePack:
    name = "enterprise_lake"

    def agents(self) -> list:
        from packs.enterprise_lake.agents import builtin_cards

        return builtin_cards()

    def register_tools(self, tools: Any) -> None:
        from packs.enterprise_lake.tools import register_extras

        register_extras(tools)

    def fault_values(self) -> dict[str, str]:
        return dict(policy.FAULT_VALUES)

    def tool_descriptions(self) -> dict[str, str]:
        return dict(policy.TOOL_DESCRIPTIONS)

    def lab_tools(self) -> frozenset[str]:
        from packs.enterprise_lake.tools import LAB_LEGAL_TOOLS

        return LAB_LEGAL_TOOLS

    def entities(self) -> tuple[str, ...]:
        return policy.ALLOWED_ENTITIES

    def legal_joins(self) -> dict[str, frozenset[str]]:
        return dict(policy.LEGAL_JOINS)

    def egress_allowlist(self) -> tuple[str, ...]:
        return policy.EGRESS_ALLOWLIST

    def role_tools(self) -> dict[str, frozenset[str]]:
        return dict(policy.ROLE_TOOL_ALLOWLIST)

    def sensitive_columns(self) -> frozenset[str]:
        return policy.SALARY_COLUMNS

    def known_collections(self) -> tuple[str, ...]:
        return policy.KNOWN_COLLECTIONS

    def default_collection(self, agent_id: str) -> str:
        return policy.default_collection(agent_id)

    def entity_cues(self) -> dict:
        return policy.ENTITY_CUES

    def synthesis_hint(self) -> str:
        return policy.SYNTHESIS_HINT

    def domain_mismatch(self, agent_id: str, task: str, text: str) -> bool:
        return policy.domain_mismatch(agent_id, task, text)

    def stale_skill_patterns(self) -> tuple[str, ...]:
        return policy.STALE_SKILL_PATTERNS

    def abstain_suffix(self) -> str:
        return policy.ABSTAIN_SUFFIX

    def schema_text(self, include_kb: bool = False) -> str:
        from retrieval.tables import schema_text

        return schema_text(include_kb=include_kb)


PACK = EnterpriseLakePack()
