"""Document endpoints: upload, list, inspect, download, delete."""
from typing import List, Literal, Optional
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
from docintel.core.uploads import sanitize_filename, validate_upload
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

    # Everything downstream — the viewer, editing, redaction, signing — works
    # on PDF. Anything else convertible is turned into one at the door, so the
    # rest of the platform never has to care what was uploaded.
    original_name = result.safe_filename
    source_format = result.detected_type
    converted_from = None

    if source_format != "pdf":
        from docintel.pdf import convert as convert_tools

        try:
            data = convert_tools.to_pdf(
                data, source_format,
                title=original_name.rsplit(".", 1)[0],
            )
        except PDFEngineError as exc:
            audit.record(session, action="document.upload", actor=user,
                         workspace_id=workspace_id, result="rejected",
                         detail=f"{source_format} conversion failed",
                         ip_address=client_ip(request))
            session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"This {source_format.upper()} file could not be "
                       f"converted to a PDF: {exc}",
            ) from exc

        converted_from = source_format
        stored_name = original_name.rsplit(".", 1)[0] + ".pdf"
    else:
        stored_name = original_name

    digest = content_hash(data)

    document = Document(
        workspace_id=workspace_id,
        uploaded_by=user.id,
        filename=stored_name,
        mime_type="application/pdf",
        size_bytes=len(data),
        content_hash=digest,
        status=DocumentStatus.PROCESSING,
        doc_metadata=(
            {"converted_from": converted_from, "original_filename": original_name}
            if converted_from else {}
        ),
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


class CreateRequest(BaseModel):
    """A document composed here rather than uploaded."""
    workspace_id: str = Field(min_length=8, max_length=64)
    filename: str = Field(default="Untitled.pdf", max_length=200)
    title: str = Field(default="", max_length=300)
    # Markdown-ish: headings, paragraphs and bullets. Empty means blank pages.
    content: str = Field(default="", max_length=500_000)
    blank_pages: int = Field(default=1, ge=1, le=200)
    page_size: Literal["letter", "a4"] = "letter"


@router.post("/create", response_model=DocumentUploadResponse,
             status_code=status.HTTP_201_CREATED)
def create_document(
    body: CreateRequest,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> DocumentUploadResponse:
    """Make a new PDF from typed content, or a blank one to work on.

    Declared before /{document_id} so the literal path is matched first.
    """
    from docintel.pdf import assemble
    from docintel.pdf import convert as convert_tools
    from docintel.pdf.engine import PDFEngineError

    require_workspace(session, user, body.workspace_id, write=True)

    try:
        if body.content.strip():
            data = convert_tools.text_to_pdf(
                body.content, title=body.title.strip(),
                page_size=body.page_size)
            label = "created"
        else:
            # A single blank page to build on, then any others asked for.
            data = convert_tools.text_to_pdf(
                body.title.strip() or " ", title="", page_size=body.page_size)
            if body.blank_pages > 1:
                data = assemble.insert_blank(
                    data, after=1, count=body.blank_pages - 1)
            label = "blank"
    except PDFEngineError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=str(exc)) from exc

    name = sanitize_filename(body.filename or "Untitled.pdf")
    if not name.lower().endswith(".pdf"):
        name = name.rsplit(".", 1)[0] + ".pdf" if "." in name else name + ".pdf"

    digest = content_hash(data)
    document = Document(
        workspace_id=body.workspace_id,
        uploaded_by=user.id,
        filename=name,
        mime_type="application/pdf",
        size_bytes=len(data),
        content_hash=digest,
        status=DocumentStatus.PROCESSING,
        doc_metadata={"created_in_app": True},
    )
    session.add(document)
    session.flush()

    key = build_key(body.workspace_id, document.id, 1, "original")
    storage.put(key, data)
    session.add(DocumentVersion(
        document_id=document.id, version=1, label=label,
        storage_key=key, size_bytes=len(data), content_hash=digest,
    ))

    jobs = [
        queue.enqueue(session, workspace_id=body.workspace_id, job_type=job_type,
                      document_id=document.id,
                      idempotency_key=f"{job_type.value}:{document.id}").id
        for job_type in (JobType.INGEST, JobType.SECURITY_SCAN)
    ]

    audit.record(session, action="document.created", actor=user,
                 workspace_id=body.workspace_id, document_id=document.id,
                 detail=label, ip_address=client_ip(request))
    session.commit()
    session.refresh(document)

    return DocumentUploadResponse(
        document=DocumentResponse.model_validate(document), jobs=jobs
    )


class MergeRequest(BaseModel):
    # Order matters: the pages appear in exactly this sequence, so the client
    # controls it rather than the server sorting by name or date.
    document_ids: List[str] = Field(min_length=2, max_length=50)
    filename: str = Field(default="combined.pdf", max_length=200)


# Declared before /{document_id} so the literal path is matched first rather
# than being read as a document id.
@router.post("/merge", response_model=DocumentUploadResponse,
             status_code=status.HTTP_201_CREATED)
def merge_documents(
    body: MergeRequest,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> DocumentUploadResponse:
    """Combine several documents, in the given order, into a new one.

    The sources are left untouched — this creates a document rather than
    editing one, so nothing can be lost by combining.
    """
    from docintel.pdf.engine import PDFEngineError, get_engine

    # Every source is authorised individually. Membership of one workspace
    # must not grant reading a document in another.
    sources = [require_document(session, user, doc_id)
               for doc_id in body.document_ids]

    workspaces = {d.workspace_id for d in sources}
    if len(workspaces) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="All documents must belong to the same workspace.",
        )

    workspace_id = sources[0].workspace_id
    require_workspace(session, user, workspace_id, write=True)

    from docintel.services import documents as docsvc
    try:
        payloads = [docsvc.read_version(session, d, None) for d in sources]
        merged = get_engine().merge(payloads)
    except PDFEngineError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=str(exc)) from exc

    name = body.filename.strip() or "combined.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"

    digest = content_hash(merged)
    document = Document(
        workspace_id=workspace_id,
        uploaded_by=user.id,
        filename=name,
        mime_type="application/pdf",
        size_bytes=len(merged),
        content_hash=digest,
        status=DocumentStatus.PROCESSING,
        doc_metadata={"combined_from": [d.id for d in sources]},
    )
    session.add(document)
    session.flush()

    key = build_key(workspace_id, document.id, 1, "original")
    storage.put(key, merged)
    session.add(DocumentVersion(
        document_id=document.id, version=1, label="combined",
        storage_key=key, size_bytes=len(merged), content_hash=digest,
    ))

    jobs = [
        queue.enqueue(session, workspace_id=workspace_id, job_type=job_type,
                      document_id=document.id,
                      idempotency_key=f"{job_type.value}:{document.id}").id
        for job_type in (JobType.INGEST, JobType.SECURITY_SCAN)
    ]

    audit.record(session, action="document.combined", actor=user,
                 workspace_id=workspace_id, document_id=document.id,
                 detail=f"{len(sources)} document(s)", ip_address=client_ip(request))
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
