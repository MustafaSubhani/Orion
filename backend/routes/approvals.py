"""Approvals for write tools. Users see their own requests; only administrators decide."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from backend.deps import Identity, current_identity, require_admin
from backend.errors import ApiError
from harness import approvals

router = APIRouter(prefix="/approvals", tags=["Approvals"])


class Decision(BaseModel):
    decision: str = Field(pattern="^(approve|deny)$")


@router.get("")
def list_approvals(
    status: Optional[str] = Query(default=None, pattern="^(pending|approved|denied)$"),
    identity: Identity = Depends(current_identity),
) -> list[dict[str, Any]]:
    user = None if identity.is_admin else identity.username
    return approvals.list_for(tenant=identity.tenant, user=user, status=status)


@router.post("/{approval_id}")
def decide(approval_id: str, body: Decision, admin: Identity = Depends(require_admin)) -> dict[str, Any]:
    existing = approvals.get(approval_id, admin.tenant)
    if existing is None:
        raise ApiError(404, "not_found", "Unknown approval")
    if existing["username"] == admin.username:
        raise ApiError(403, "self_approval", "You cannot decide an approval for your own request")
    row = approvals.decide(approval_id, approver=admin.username, tenant=admin.tenant, approve=body.decision == "approve")
    if row is None:
        raise ApiError(409, "already_decided", f"Approval is already {existing['status']}")
    return row
