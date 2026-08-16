"""Document endpoints: upload, list, inspect, download, delete."""
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from docintel.api.schemas import (
    DocumentDetail, DocumentResponse, DocumentUploadResponse, Page,
    SecurityFindingResponse, SecurityReportResponse,
)
from docintel.config import settings
from docintel.core import audit
from docintel.core.deps import CurrentUser, DbSession, client_ip, require_document, require_workspace
from docintel.core.uploads import validate_upload
from docintel.db.models import (
    Document, DocumentStatus, DocumentVersion, JobType, SecurityFinding,
)
from docintel.jobs.queue import queue
from docintel.storage import build_key, content_hash, storage

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", response_model=DocumentUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    workspace_id: str = Form(...),
    file: UploadFile = File(...),
) -> DocumentUploadResponse:
    require_workspace(session, user, workspace_id, write=True)

    # Read with a hard ceiling so an oversized upload cannot exhaust memory
    # before the size check runs.
    data = await file.read(settings.max_upload_bytes + 1)
    await file.close()

    if len(data) > settings.max_upload_bytes:
        audit.record(session, action="document.upload", actor=user, workspace_id=workspace_id,
                     result="rejected", detail="exceeds size limit", ip_address=client_ip(request))
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.max_upload_mb} MB limit.",
        )

    result = validate_upload(file.filename or "", file.content_type, data)
    if not result.ok:
        audit.record(session, action="document.upload", actor=user, workspace_id=workspace_id,
                     result="rejected", detail=result.message, ip_address=client_ip(request))
        session.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.message)

    digest = content_hash(data)

    document = Document(
        workspace_id=workspace_id,
        uploaded_by=user.id,
        filename=result.safe_filename,
        mime_type=result.detected_type,
        size_bytes=len(data),
        content_hash=digest,
        status=DocumentStatus.PROCESSING,
        doc_metadata={},
    )
    session.add(document)
    session.flush()

    key = build_key(workspace_id, document.id, 1, "original")
    storage.put(key, data)

    session.add(DocumentVersion(
        document_id=document.id, version=1, label="original",
        storage_key=key, size_bytes=len(data), content_hash=digest,
    ))

    # Idempotency keyed on content and job type: re-uploading the same bytes
    # will not queue duplicate processing.
    jobs = []
    for job_type in (JobType.INGEST, JobType.SECURITY_SCAN):
        job = queue.enqueue(
            session,
            workspace_id=workspace_id,
            job_type=job_type,
            document_id=document.id,
            idempotency_key=f"{job_type.value}:{document.id}",
        )
        jobs.append(job.id)

    audit.record(session, action="document.upload", actor=user, workspace_id=workspace_id,
                 document_id=document.id, detail=f"{len(data)} bytes",
                 ip_address=client_ip(request))
    session.commit()
    session.refresh(document)

    return DocumentUploadResponse(
        document=DocumentResponse.model_validate(document), jobs=jobs
    )


@router.get("", response_model=Page)
def list_documents(
    user: CurrentUser,
    session: DbSession,
    workspace_id: str = Query(...),
    search: Optional[str] = Query(None, max_length=200),
    include_archived: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page:
    require_workspace(session, user, workspace_id)

    conditions = [Document.workspace_id == workspace_id]
    if not include_archived:
        conditions.append(Document.is_archived.is_(False))
    if search:
        conditions.append(Document.filename.ilike(f"%{search}%"))

    total = session.scalar(select(func.count()).select_from(Document).where(*conditions)) or 0
    rows = session.scalars(
        select(Document).where(*conditions)
        .order_by(Document.created_at.desc())
        .limit(limit).offset(offset)
    ).all()

    return Page(
        items=[DocumentResponse.model_validate(d).model_dump(mode="json") for d in rows],
        total=total, limit=limit, offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentDetail)
def get_document(document_id: str, user: CurrentUser, session: DbSession) -> DocumentDetail:
    document = require_document(session, user, document_id)
    count = session.scalar(
        select(func.count()).select_from(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
    ) or 0

    payload = DocumentDetail.model_validate(document)
    payload.version_count = count
    return payload


@router.get("/{document_id}/download")
def download_document(
    document_id: str, request: Request, user: CurrentUser, session: DbSession,
    version: Optional[int] = Query(None, ge=1),
) -> Response:
    document = require_document(session, user, document_id)

    query = select(DocumentVersion).where(DocumentVersion.document_id == document.id)
    query = (query.where(DocumentVersion.version == version) if version
             else query.order_by(DocumentVersion.version.desc()))
    record = session.scalars(query.limit(1)).first()

    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")

    data = storage.get(record.storage_key)

    audit.record(session, action="document.download", actor=user,
                 workspace_id=document.workspace_id, document_id=document.id,
                 detail=f"version {record.version}", ip_address=client_ip(request))
    session.commit()

    # RFC 5987 encoding; the filename is sanitised at upload but is still
    # user-supplied, so it is never interpolated raw into the header.
    ascii_name = document.filename.encode("ascii", "ignore").decode() or "document.pdf"
    return Response(
        content=data,
        media_type=document.mime_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(document.filename)}"
            ),
            # The bytes are untrusted; never let a browser render them inline
            # in our origin.
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.get("/{document_id}/security", response_model=SecurityReportResponse)
def get_security_report(
    document_id: str, user: CurrentUser, session: DbSession
) -> SecurityReportResponse:
    document = require_document(session, user, document_id)
    summary = (document.doc_metadata or {}).get("security")

    if not summary:
        return SecurityReportResponse(document_id=document.id, scanned=False)

    findings = session.scalars(
        select(SecurityFinding).where(SecurityFinding.document_id == document.id)
    ).all()

    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings = sorted(findings, key=lambda f: order.get(f.severity, 9))

    return SecurityReportResponse(
        document_id=document.id,
        scanned=True,
        risk_level=summary.get("risk_level"),
        risk_label=summary.get("risk_label"),
        headline=summary.get("headline"),
        encrypted=summary.get("encrypted"),
        signed=summary.get("signed"),
        has_forms=summary.get("has_forms"),
        url_count=summary.get("url_count"),
        findings=[SecurityFindingResponse.model_validate(f) for f in findings],
    )


@router.get("/{document_id}/pages", response_model=Page)
def get_pages(
    document_id: str, user: CurrentUser, session: DbSession,
    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
) -> Page:
    document = require_document(session, user, document_id)
    pages = (document.doc_metadata or {}).get("pages", [])
    return Page(items=pages[offset:offset + limit], total=len(pages),
                limit=limit, offset=offset)


@router.post("/{document_id}/archive", response_model=DocumentResponse)
def archive_document(
    document_id: str, request: Request, user: CurrentUser, session: DbSession
) -> Document:
    document = require_document(session, user, document_id, write=True)
    document.is_archived = True
    audit.record(session, action="document.archive", actor=user,
                 workspace_id=document.workspace_id, document_id=document.id,
                 ip_address=client_ip(request))
    session.commit()
    session.refresh(document)
    return document


@router.post("/{document_id}/restore", response_model=DocumentResponse)
def restore_document(
    document_id: str, request: Request, user: CurrentUser, session: DbSession
) -> Document:
    document = require_document(session, user, document_id, write=True)
    document.is_archived = False
    audit.record(session, action="document.restore", actor=user,
                 workspace_id=document.workspace_id, document_id=document.id,
                 ip_address=client_ip(request))
    session.commit()
    session.refresh(document)
    return document


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: str, request: Request, user: CurrentUser, session: DbSession
) -> None:
    """Hard delete: database rows and every stored byte."""
    document = require_document(session, user, document_id, write=True)
    workspace_id = document.workspace_id

    storage.delete_prefix(f"{workspace_id}/{document.id}")
    session.delete(document)

    audit.record(session, action="document.delete", actor=user,
                 workspace_id=workspace_id, document_id=document_id,
                 detail="document and stored objects removed",
                 ip_address=client_ip(request))
    session.commit()


@router.get("/{document_id}/versions")
def list_versions(document_id: str, user: CurrentUser, session: DbSession) -> dict:
    """Full version history, newest first."""
    document = require_document(session, user, document_id)
    rows = session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version.desc())
    ).all()

    return {
        "document_id": document.id,
        "current": rows[0].version if rows else None,
        "versions": [
            {
                "version": v.version,
                "label": v.label,
                "size_bytes": v.size_bytes,
                "content_hash": v.content_hash[:16],
                "created_at": v.created_at.isoformat(),
            }
            for v in rows
        ],
        "note": ("Every operation appends a version; nothing is overwritten. "
                 "Restoring copies an earlier version forward as a new one, so "
                 "the history stays intact and the restore itself is undoable."),
    }


class RestoreRequest(BaseModel):
    version: int = Field(ge=1)


@router.post("/{document_id}/versions/restore")
def restore_version(document_id: str, body: RestoreRequest, request: Request,
                    user: CurrentUser, session: DbSession) -> dict:
    """Undo, by copying an earlier version forward.

    Deliberately additive rather than destructive: the versions created after
    the restore point are kept, so a restore can itself be undone.
    """
    from docintel.services import documents as docsvc
    from docintel.pdf.engine import PDFEngineError

    document = require_document(session, user, document_id, write=True)

    latest = docsvc.latest_version(session, document)
    if body.version == latest.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Version {body.version} is already the current version.",
        )

    try:
        data = docsvc.read_version(session, document, body.version)
    except PDFEngineError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    result = docsvc.add_version(
        session, document, data, f"restored-v{body.version}",
        actor=user, action="document.version_restored",
        detail=f"restored version {body.version} as {latest.version + 1}",
    )
    session.commit()
    session.refresh(document)

    return {
        "document_id": document.id,
        "restored_from": body.version,
        "version": result.version.version,
        "reused_existing_bytes": result.reused_existing_bytes,
        "note": (f"Version {body.version} is now current, saved as version "
                 f"{result.version.version}. Nothing was deleted."),
    }
