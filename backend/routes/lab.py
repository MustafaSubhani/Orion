"""Lab: synthesise, evaluate and promote new agents. Writes agent definitions to disk, so administrator-only."""

from __future__ import annotations

from typing import Any, Optional, Union

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.deps import Identity, can_see_agent, require_admin
from backend.errors import ApiError
from backend.schemas import LabAnswer, LabMessage
from harness import tenancy
from harness.agent_registry import deploy_agent, get_registry, predeploy_check

router = APIRouter(prefix="/lab", tags=["Lab"])


class AgentSpec(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    job: str = Field(min_length=1, max_length=1000)
    entities: list[str] = Field(default_factory=list, max_length=20)
    tools: Union[str, list[str]] = "default"
    mode: str = Field(default="card", pattern="^(card|code)$")


def _row(card) -> dict[str, Any]:
    evaluation = (card.metadata or {}).get("evaluation")
    return {
        "id": card.id, "name": card.name, "description": card.description, "status": card.status.value,
        "version": card.version, "entities": card.entities, "tools": card.tools,
        "mode": (card.metadata or {}).get("mode", "card"), "evaluation": evaluation,
    }


def _lab_agent(agent_id: str, identity: Identity):
    """A Lab agent the caller's tenant owns. Another tenant's agent is a 404, never a 403."""
    card = get_registry().get(agent_id)
    if card is None or not card.is_custom or not can_see_agent(identity, card):
        raise ApiError(404, "not_found", f"Unknown Lab agent '{agent_id}'")
    return card


@router.post("", response_model=LabAnswer)
def lab_chat(body: LabMessage, admin: Identity = Depends(require_admin)) -> LabAnswer:
    from lab.synthesizer import ask_lab

    return LabAnswer(response=ask_lab(body.message.strip(), admin.username, admin.tenant), username=admin.username)


@router.get("/agents")
def list_lab_agents(admin: Identity = Depends(require_admin)) -> list[dict[str, Any]]:
    return [_row(c) for c in get_registry().custom() if can_see_agent(admin, c)]


@router.post("/agents", status_code=201)
def create_lab_agent(spec: AgentSpec, admin: Identity = Depends(require_admin)) -> dict[str, Any]:
    from harness.packs import registry as packs
    from lab.synthesizer import synthesize

    known = {e.upper() for e in packs().lab_entities()}
    unknown = [e for e in spec.entities if e.upper() not in known]
    if unknown:
        raise ApiError(400, "unknown_entities", f"Unknown entities {unknown}. Known: {sorted(known)}")
    tools = ",".join(spec.tools) if isinstance(spec.tools, list) else spec.tools
    try:
        card = synthesize(
            {"name": spec.name, "job": spec.job, "entities": ",".join(spec.entities), "tools": tools, "mode": spec.mode},
            created_by=admin.username,
            tenant=admin.tenant,
        )
    except ValueError as exc:
        raise ApiError(400, "synthesis_failed", str(exc)) from exc
    return _row(card)


@router.get("/agents/{agent_id}")
def get_lab_agent(agent_id: str, admin: Identity = Depends(require_admin)) -> dict[str, Any]:
    return _row(_lab_agent(agent_id, admin))


@router.post("/agents/{agent_id}/evaluate")
def evaluate_lab_agent(agent_id: str, admin: Identity = Depends(require_admin)) -> dict[str, Any]:
    """Run the contract tests on live data. Calls the configured model, so it can take a while."""
    from lab.evaluate import evaluate

    _lab_agent(agent_id, admin)
    with tenancy.use(admin.tenant):  # lab.min_score and the model settings are this tenant's
        return evaluate(agent_id, actor=admin.username).to_dict()


@router.get("/agents/{agent_id}/evaluation")
def latest_evaluation(agent_id: str, admin: Identity = Depends(require_admin)) -> dict[str, Any]:
    from lab.evaluate import latest

    _lab_agent(agent_id, admin)
    record: Optional[dict[str, Any]] = latest(agent_id)
    if record is None:
        raise ApiError(404, "not_found", "This agent has not been evaluated yet")
    return record


@router.post("/agents/{agent_id}/deploy")
def deploy_lab_agent(agent_id: str, admin: Identity = Depends(require_admin)) -> dict[str, Any]:
    """Promote to DEPLOYED. Needs a passing evaluation for the current version unless `lab.auto_eval` is off."""
    _lab_agent(agent_id, admin)
    with tenancy.use(admin.tenant):
        check = predeploy_check(agent_id)
    if not check["passed"]:
        failed = {c["name"]: c["detail"] for c in check["checks"] if not c["passed"]}
        raise ApiError(409, "predeploy_failed", f"Cannot deploy: {failed}")
    deploy_agent(agent_id)
    return _row(_lab_agent(agent_id, admin))
