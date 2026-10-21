"""Agent catalogue and lifecycle."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.deps import Identity, can_see_agent, current_identity, require_admin
from backend.errors import ApiError
from backend.schemas import AgentInfo, AgentVersionOut, RollbackRequest, StatusChange
from harness.agent_registry import get_registry, predeploy_check
from harness.types import AgentCard, AgentStatus

router = APIRouter(prefix="/agents", tags=["Agents"])
ROUTABLE = {AgentStatus.ACTIVE, AgentStatus.DEPLOYED}


def _info(card: AgentCard) -> AgentInfo:
    return AgentInfo(
        id=card.id,
        name=card.name,
        description=card.description,
        status=card.status.value,
        builtin=card.is_builtin,
        tools=list(card.tools),
        entities=list(card.entities),
    )


def _card(agent_id: str, identity: Identity) -> AgentCard:
    """An agent this caller's tenant may see; another tenant's custom agent is a 404."""
    card = get_registry().get(agent_id)
    if card is None or not can_see_agent(identity, card):
        raise ApiError(404, "not_found", f"Unknown agent '{agent_id}'")
    return card


@router.get("", response_model=list[AgentInfo])
def list_agents(identity: Identity = Depends(current_identity)) -> list[AgentInfo]:
    """Admins see their tenant's agents with lifecycle status. Everyone else sees routable agents only."""
    cards = [c for c in get_registry().all() if can_see_agent(identity, c)]
    if not identity.is_admin:
        cards = [c for c in cards if c.status in ROUTABLE]
    return [_info(c) for c in cards if c.status != AgentStatus.RETIRED or identity.is_admin]


@router.get("/{agent_id}", response_model=AgentInfo)
def get_agent(agent_id: str, identity: Identity = Depends(current_identity)) -> AgentInfo:
    card = _card(agent_id, identity)
    if card.status not in ROUTABLE and not identity.is_admin:
        raise ApiError(404, "not_found", f"Unknown agent '{agent_id}'")
    return _info(card)


@router.post("/{agent_id}/status", response_model=AgentInfo)
def change_status(agent_id: str, body: StatusChange, admin: Identity = Depends(require_admin)) -> AgentInfo:
    registry = get_registry()
    card = _card(agent_id, admin)
    if card.is_builtin:
        raise ApiError(409, "builtin_agent", "Built-in agents cannot change status")
    target = AgentStatus(body.status)
    if target == AgentStatus.ACTIVE and card.status == AgentStatus.DRAFT:
        check = predeploy_check(agent_id)
        if not check["passed"]:
            failed = [c["name"] for c in check["checks"] if not c["passed"]]
            raise ApiError(409, "predeploy_failed", f"Cannot activate: failed checks {failed}")
    registry.set_status(agent_id, target, actor=admin.username)
    return _info(_card(agent_id, admin))


@router.get("/{agent_id}/versions", response_model=list[AgentVersionOut])
def versions(agent_id: str, admin: Identity = Depends(require_admin)) -> list[AgentVersionOut]:
    _card(agent_id, admin)
    return [
        AgentVersionOut(version=v.version, created_at=v.created_at, created_by=v.created_by, change_note=v.change_note)
        for v in get_registry().versions.list(agent_id)
    ]


@router.post("/{agent_id}/rollback", response_model=AgentInfo)
def rollback(agent_id: str, body: RollbackRequest, admin: Identity = Depends(require_admin)) -> AgentInfo:
    _card(agent_id, admin)
    if not get_registry().rollback(agent_id, body.version, actor=admin.username):
        raise ApiError(409, "rollback_failed", "Unknown version, or the agent is built in")
    return _info(_card(agent_id, admin))
