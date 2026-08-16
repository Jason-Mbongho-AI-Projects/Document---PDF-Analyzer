"""Conversion endpoints: PDF out to other formats, other formats in to PDF."""
from typing import Optional
from urllib.parse import quote

from fastapi import (
    APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status,
)
from pydantic import BaseModel, Field

from docintel.config import settings
from docintel.core import audit
from docintel.core.deps import (
    CurrentUser, DbSession, client_ip, require_document, require_workspace,
)
from docintel.core.uploads import sanitize_filename
from docintel.db.models import Document, DocumentStatus, DocumentVersion
from docintel.pdf import convert as convert_tools
from docintel.pdf.engine import PDFEngineError, PasswordRequired
from docintel.services import documents as docsvc
from docintel.storage import build_key, content_hash, storage

router = APIRouter(tags=["convert"])


class ConvertRequest(BaseModel):
    target: str = Field(min_length=1, max_length=20)
    scale: float = Field(default=2.0, ge=0.5, le=6.0)
    source_version: Optional[int] = Field(default=None, ge=1)


def _guard(action):
    try:
        return action()
    except PasswordRequired as exc:
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(exc))
    except PDFEngineError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/convert/capabilities")
def conversion_capabilities(user: CurrentUser) -> dict:
    """What this server can actually convert, and why anything is unavailable."""
    capabilities = convert_tools.capabilities()
    return {
        "from_pdf": [
            {
                "target": c.target, "label": c.label, "extension": c.extension,
                "fidelity": c.fidelity,
                "fidelity_note": convert_tools.FIDELITY[c.fidelity],
                "available": c.available, "reason": c.reason,
            }
            for c in capabilities
        ],
        "to_pdf": sorted(set(convert_tools.TO_PDF_EXTENSIONS)),
        "note": (
            "Fidelity is stated per target. A PDF does not record paragraphs, "
            "headings or table semantics, so anything that reconstructs them is "
            "a best effort and will not round-trip."
        ),
    }


@router.post("/documents/{document_id}/convert")
def convert_document(document_id: str, body: ConvertRequest, request: Request,
                     user: CurrentUser, session: DbSession) -> Response:
    """Convert and stream the result back. The source document is untouched."""
    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))

    result = _guard(lambda: convert_tools.convert(
        data, body.target, filename=document.filename, scale=body.scale,
    ))

    audit.record(session, action="document.converted", actor=user,
                 workspace_id=document.workspace_id, document_id=document.id,
                 detail=f"to {result.target}", ip_address=client_ip(request))
    audit.meter(session, workspace_id=document.workspace_id, user_id=user.id,
                document_id=document.id, operation=f"convert.{result.target}",
                units=max(result.pages, 1), unit_kind="pages")
    session.commit()

    ascii_name = result.filename.encode("ascii", "ignore").decode() or "converted"
    return Response(
        content=result.data,
        media_type=result.media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(result.filename)}"
            ),
            "X-Content-Type-Options": "nosniff",
            "X-Conversion-Fidelity": result.fidelity,
            "X-Conversion-Note": result.note,
            "X-Conversion-Warnings": " | ".join(result.warnings)[:900],
        },
    )


@router.post("/convert/to-pdf", status_code=status.HTTP_201_CREATED)
async def create_pdf_from_file(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    workspace_id: str = Form(...),
    file: UploadFile = File(...),
) -> dict:
    """Turn a supported non-PDF file into a PDF and store it as a document."""
    require_workspace(session, user, workspace_id, write=True)

    raw = await file.read(settings.max_upload_bytes + 1)
    await file.close()

    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.max_upload_mb} MB limit.",
        )
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="The uploaded file is empty.")

    safe_name = sanitize_filename(file.filename or "document")
    extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""

    if not extension:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="The file has no extension, so its type is unknown.")

    stem = safe_name.rsplit(".", 1)[0]
    pdf_bytes = _guard(lambda: convert_tools.to_pdf(raw, extension, title=stem))

    digest = content_hash(pdf_bytes)
    document = Document(
        workspace_id=workspace_id,
        uploaded_by=user.id,
        filename=f"{stem}.pdf",
        mime_type="application/pdf",
        size_bytes=len(pdf_bytes),
        content_hash=digest,
        status=DocumentStatus.PROCESSING,
        doc_metadata={"converted_from": extension, "original_filename": safe_name},
    )
    session.add(document)
    session.flush()

    key = build_key(workspace_id, document.id, 1, "original")
    storage.put(key, pdf_bytes)
    session.add(DocumentVersion(
        document_id=document.id, version=1, label="original",
        storage_key=key, size_bytes=len(pdf_bytes), content_hash=digest,
    ))

    from docintel.db.models import JobType
    from docintel.jobs.queue import queue

    jobs = [
        queue.enqueue(session, workspace_id=workspace_id, job_type=job_type,
                      document_id=document.id,
                      idempotency_key=f"{job_type.value}:{document.id}").id
        for job_type in (JobType.INGEST, JobType.SECURITY_SCAN)
    ]

    audit.record(session, action="document.created_from_file", actor=user,
                 workspace_id=workspace_id, document_id=document.id,
                 detail=f"from .{extension}", ip_address=client_ip(request))
    session.commit()
    session.refresh(document)

    return {
        "document_id": document.id,
        "filename": document.filename,
        "size_bytes": document.size_bytes,
        "converted_from": extension,
        "jobs": jobs,
    }
