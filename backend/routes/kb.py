"""Live knowledge base: upload, list, delete, reindex. Never touches data/frozen or bench/."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, File, Request, UploadFile

from backend.deps import Identity, current_identity, require_admin
from backend.errors import ApiError
from kb.ingest import MAX_BYTES, ingest_bytes
from kb.store import delete_resource, kb_table_names, list_resources

router = APIRouter(tags=["KB"])
MAX_FILES = 20


def _max_request_bytes() -> int:
    try:
        return int(os.getenv("ORION_MAX_UPLOAD_BYTES", "") or MAX_BYTES * MAX_FILES)
    except ValueError:
        return MAX_BYTES * MAX_FILES


def _read_capped(upload: UploadFile) -> bytes:
    """Read an upload without ever holding more than MAX_BYTES + 1 bytes."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = upload.file.read(1 << 20)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_BYTES:
            raise ApiError(413, "too_large", f"{upload.filename or 'file'} exceeds {MAX_BYTES // (1 << 20)} MB")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/ingest")
def ingest(
    request: Request,
    files: list[UploadFile] = File(...),
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    """Store uploads in kb/ only. The uploader is the authenticated user, never a form field."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > _max_request_bytes():
        raise ApiError(413, "too_large", "Upload is too large")
    if not files:
        raise ApiError(400, "no_files", "No files were uploaded")
    if len(files) > MAX_FILES:
        raise ApiError(400, "too_many_files", f"At most {MAX_FILES} files per request")
    ingested: list[dict[str, Any]] = []
    for upload in files:
        name = upload.filename or "upload.bin"
        try:
            ingested.append(ingest_bytes(name, _read_capped(upload), uploaded_by=identity.username))
        except ApiError:
            raise
        except Exception as exc:  # per-file failure is reported, the other files still go through
            ingested.append({"filename": name, "error": getattr(exc, "safe_message", None) or "could not ingest this file"})
    return {"ingested": ingested}


@router.get("/kb")
def catalog(_: Identity = Depends(current_identity)) -> dict[str, Any]:
    from kb.connectors import list_connectors

    return {"resources": list_resources(), "tables": kb_table_names(), "connectors": list_connectors()}


@router.delete("/kb/{resource_id}")
def remove(resource_id: str, identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    resource = next((r for r in list_resources() if r.get("id") == resource_id), None)
    if resource is None:
        raise ApiError(404, "not_found", "Unknown KB resource")
    if not identity.is_admin and resource.get("uploaded_by") != identity.username:
        raise ApiError(403, "forbidden", "You can only delete your own uploads")
    delete_resource(resource_id)
    return {"ok": True, "id": resource_id}


@router.post("/kb/reindex")
def reindex(_: Identity = Depends(require_admin)) -> dict[str, Any]:
    from kb.ingest import reindex_all

    return reindex_all()
