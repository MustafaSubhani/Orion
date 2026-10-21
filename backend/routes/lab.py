"""Lab: synthesise new agents. Writes code to disk, so it is administrator-only."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.deps import Identity, require_admin
from backend.schemas import LabAnswer, LabMessage

router = APIRouter(prefix="/lab", tags=["Lab"])


@router.post("", response_model=LabAnswer)
def lab_chat(body: LabMessage, admin: Identity = Depends(require_admin)) -> LabAnswer:
    from lab.synthesizer import ask_lab

    return LabAnswer(response=ask_lab(body.message.strip(), admin.username), username=admin.username)
