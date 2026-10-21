"""Lab: synthesize NEW workers (card + sandboxed agent.py), not a mixer of builtins."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from harness.agent_registry import deploy_agent, get_agent, get_custom_agents, get_registry, predeploy_check
from harness.config import LAB_GENERATED_DIR, ensure_dirs
from harness.types import AgentCard, AgentStatus, LatencyClass
from sandbox.gate import static_gate

TEMPLATE_AGENT = '''"""Synthesized Orion worker. Must define answer(task) -> str. Allow-listed imports only."""

ROLE = {role!r}
ENTITIES = {entities!r}
TOOLS = {tools!r}


def answer(task: str) -> str:
    return (
        "SYNTHESIZED AGENT\\n"
        "role: " + ROLE + "\\n"
        "entities: " + ", ".join(ENTITIES) + "\\n"
        "tools: " + ", ".join(TOOLS) + "\\n"
        "task: " + task + "\\n"
        "This draft agent is not yet wired to live tools. Promote it after contract tests."
    )
'''


class LabMode(str, Enum):
    IDLE = "idle"
    CUSTOMIZE = "customize"
    CHAT = "chat"


class Step(str, Enum):
    NAME = "name"
    JOB = "job"
    ENTITIES = "entities"
    TOOLS = "tools"
    DONE = "done"


def questions() -> dict:
    """Wizard prompts. The entity list comes from the loaded domain packs, never from this file."""
    from harness.packs import registry

    entities = ", ".join(f"`{e}`" for e in sorted(registry().lab_entities()))
    return {
        Step.NAME: "**Step 1/4 — Name**\nWhat should this new agent be called?",
        Step.JOB: "**Step 2/4 — Job**\nDescribe the job in one paragraph (what data, what answers).",
        Step.ENTITIES: f"**Step 3/4 — Entities**\nWhich data entities? {entities} — comma-separated.",
        Step.TOOLS: (
            "**Step 4/4 — Tools**\nChoose from: `retrieve`, `sql_spec`, `forecast`, "
            "`anomaly_detect`, `calendar_query`, `render_chart`, `http_fetch`. Comma-separated or `default`."
        ),
    }


def _parse_list(raw: str, legal: set[str]) -> list[str]:
    out: list[str] = []
    for p in re.split(r"[,;]+", raw):
        token = p.strip()
        if not token:
            continue
        matched = next((item for item in legal if item.lower() == token.lower()), None)
        if matched and matched not in out:
            out.append(matched)
    return out


@dataclass
class LabSession:
    mode: LabMode = LabMode.IDLE
    step: Optional[Step] = None
    answers: dict[str, str] = field(default_factory=dict)
    created_id: Optional[str] = None
    chat_agent: Optional[str] = None


class LabAgent:
    def __init__(self) -> None:
        self.sessions: dict[str, LabSession] = {}

    def session(self, user: str) -> LabSession:
        if user not in self.sessions:
            self.sessions[user] = LabSession()
        return self.sessions[user]

    def process_message(self, user: str, message: str) -> str:
        text = (message or "").strip()
        lower = text.lower()
        s = self.session(user)
        if lower in {"help", "?"}:
            return self._help()
        if lower in {"cancel", "reset"}:
            self.sessions[user] = LabSession()
            return "Lab session reset."
        if s.mode == LabMode.CUSTOMIZE:
            return self._customize(user, text)
        if s.mode == LabMode.CHAT:
            if lower == "cancel":
                s.mode = LabMode.IDLE
                s.chat_agent = None
                return "Left agent chat."
            return self._chat(user, text)
        if lower in {"customize", "create", "new"}:
            s.mode = LabMode.CUSTOMIZE
            s.step = Step.NAME
            s.answers = {}
            return questions()[Step.NAME]
        if lower == "list":
            return self._list()
        if lower.startswith("use "):
            return self._use(user, text.split(" ", 1)[1].strip())
        if lower.startswith("delete "):
            return self._delete(text.split(" ", 1)[1].strip())
        if lower.startswith("deploy"):
            parts = text.split()
            if len(parts) < 2:
                return "Usage: `deploy <agent_id>`"
            return self._deploy(parts[1])
        if lower.startswith("test "):
            return self._test(text.split(" ", 1)[1].strip())
        return self._help()

    def process_message_streaming(self, user: str, message: str):
        result = self.process_message(user, message)
        acc = ""
        for word in result.split(" "):
            acc += word + " "
            yield acc
            time.sleep(0.01)

    def _customize(self, user: str, text: str) -> str:
        s = self.session(user)
        if s.step == Step.NAME:
            s.answers["name"] = text
            s.step = Step.JOB
            return questions()[Step.JOB]
        if s.step == Step.JOB:
            s.answers["job"] = text
            s.step = Step.ENTITIES
            return questions()[Step.ENTITIES]
        if s.step == Step.ENTITIES:
            s.answers["entities"] = text
            s.step = Step.TOOLS
            return questions()[Step.TOOLS]
        if s.step == Step.TOOLS:
            s.answers["tools"] = text
            card = synthesize(s.answers, created_by=user)
            s.created_id = card.id
            s.mode = LabMode.IDLE
            s.step = Step.DONE
            return (
                f"**Draft agent created:** `{card.id}` ({card.name})\n\n"
                f"- entities: {', '.join(card.entities)}\n"
                f"- tools: {', '.join(card.tools)}\n"
                f"- status: {card.status.value}\n"
                f"- files: `lab/generated/{card.id}/`\n\n"
                f"Run `test {card.id}` then `deploy {card.id}` to promote it. "
                f"It will not be routed in live chat until ACTIVE/DEPLOYED."
            )
        return questions().get(s.step or Step.NAME, self._help())

    def _list(self) -> str:
        agents = get_custom_agents()
        if not agents:
            return "No synthesized agents yet. Type `customize`."
        lines = ["**Synthesized agents**"]
        for a in agents:
            lines.append(f"- `{a.id}` — {a.name} [{a.status.value}] tools={','.join(a.tools)}")
        return "\n".join(lines)

    def _use(self, user: str, agent_id: str) -> str:
        card = get_agent(agent_id)
        if not card:
            return f"Unknown id `{agent_id}`."
        s = self.session(user)
        s.mode = LabMode.CHAT
        s.chat_agent = agent_id
        return f"Now chatting with **{card.name}** (`{card.id}`). Type `cancel` to leave."

    def _chat(self, user: str, message: str) -> str:
        from harness.planner import coordinate

        s = self.session(user)
        if not s.chat_agent:
            s.mode = LabMode.IDLE
            return "No agent selected."
        result = coordinate(message, pin_agent=s.chat_agent)
        return f"**{s.chat_agent}**\n\n{result.final_response}"

    def _delete(self, agent_id: str) -> str:
        ok = get_registry().delete(agent_id)
        return f"Deleted `{agent_id}`." if ok else f"Cannot delete `{agent_id}`."

    def _deploy(self, agent_id: str) -> str:
        check = predeploy_check(agent_id)
        if not check["passed"]:
            return "**Deploy blocked**\n" + json.dumps(check, indent=2)
        ok = deploy_agent(agent_id)
        return f"**Deployed** `{agent_id}`." if ok else f"Deploy failed for `{agent_id}`."

    def _test(self, agent_id: str) -> str:
        card = get_agent(agent_id)
        if not card:
            return f"Unknown `{agent_id}`."
        path = Path(card.module_path) if card.module_path else None
        gate = static_gate(path) if path else None
        lines = [f"**Contract smoke for `{agent_id}`**", f"- tools: {card.tools}", f"- entities: {card.entities}"]
        if gate:
            lines.append(f"- static_gate: {'PASS' if gate.ok else 'FAIL'} ({gate.reason})")
        else:
            lines.append("- static_gate: n/a (card-only)")
        return "\n".join(lines)

    def _help(self) -> str:
        return (
            "**Orion Lab** — synthesizes new agents; it does not wrap HR+Finance together.\n\n"
            "| Command | What it does |\n|---|---|\n"
            "| `customize` | Write a new worker (card + sandboxed module) |\n"
            "| `list` | Show synthesized agents |\n"
            "| `use <id>` | Chat with that agent |\n"
            "| `test <id>` | Static gate / smoke |\n"
            "| `deploy <id>` | DRAFT → DEPLOYED |\n"
            "| `delete <id>` | Remove |\n"
            "| `cancel` | Reset |\n"
        )


lab_agent = LabAgent()


def ask_lab(question: str, user: str = "anonymous") -> str:
    return lab_agent.process_message(user, question)


def ask_lab_stream(question: str, user: str = "anonymous"):
    yield from lab_agent.process_message_streaming(user, question)


def synthesize(answers: dict[str, str], created_by: str = "lab") -> AgentCard:
    ensure_dirs()
    name = answers.get("name") or "untitled"
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "agent"
    agent_id = f"lab_{slug}_{int(time.time()) % 100000}"
    from harness.packs import registry as pack_registry

    packs = pack_registry()
    entities = _parse_list(answers.get("entities") or packs.default_entity(), legal=packs.lab_entities())
    LAB_LEGAL_TOOLS = packs.lab_tools()

    tools_raw = (answers.get("tools") or "default").strip().lower()
    if tools_raw == "all":
        tools = sorted(LAB_LEGAL_TOOLS)
    elif tools_raw == "default":
        tools = [
            "retrieve",
            "sql_spec",
            "schema_lookup",
            "join_check",
            "search_tools",
            "session_get",
            "skill_manage",
        ]
    else:
        tools = _parse_list(answers.get("tools") or "", legal=set(LAB_LEGAL_TOOLS)) or [
            "retrieve",
            "sql_spec",
            "schema_lookup",
        ]
    if "session_get" not in tools:
        tools.append("session_get")
    if "search_tools" not in tools:
        tools.append("search_tools")
    if "skill_manage" not in tools:
        tools.append("skill_manage")
    if "schema_lookup" not in tools:
        tools.append("schema_lookup")
    job = answers.get("job") or "Specialist worker"
    prompt = (
        f"You are {name}, a newly synthesized Orion worker.\n"
        f"Job: {job}\n"
        f"Entities: {', '.join(entities)}. Do not join illegal pairs.\n"
        f"Use only these tools: {', '.join(tools)}.\n"
        "Abstain when evidence is missing. Never invent numbers."
    )
    workdir = LAB_GENERATED_DIR / agent_id
    workdir.mkdir(parents=True, exist_ok=True)
    code = TEMPLATE_AGENT.format(role=prompt, entities=entities, tools=tools)
    module_path = workdir / "agent.py"
    module_path.write_text(code, encoding="utf-8")
    card = AgentCard(
        id=agent_id,
        name=name,
        description=job,
        intent=job,
        tools=tools,
        entities=entities,
        system_prompt=prompt,
        status=AgentStatus.DRAFT,
        is_custom=True,
        is_builtin=False,
        created_by=created_by,
        created_at=datetime.now(timezone.utc).isoformat(),
        module_path=str(module_path),
        latency_class=LatencyClass.SLOW,
        metadata={"source": "lab_synthesizer"},
    )
    (workdir / "card.json").write_text(json.dumps(card.to_dict(), indent=2), encoding="utf-8")
    gate = static_gate(module_path)
    if not gate.ok:
        raise ValueError(f"synthesized agent failed static gate: {gate.reason}")
    return get_registry().register(card, actor=created_by)
