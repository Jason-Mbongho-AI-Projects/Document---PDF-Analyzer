"""
Job handlers.

These reuse the existing extraction and security modules unchanged rather
than reimplementing them — pdf_processor and security_analyzer were written
with no Streamlit dependency precisely so they could run here.
"""
import io
import sys
from pathlib import Path
from typing import Callable, Dict

from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from docintel.core import audit  # noqa: E402
from docintel.db.models import (  # noqa: E402
    Document, DocumentStatus, DocumentVersion, Job, JobType, SecurityFinding,
)
from docintel.storage import storage  # noqa: E402

from security_analyzer import security_analyzer  # noqa: E402


def _load_original(session: Session, document: Document) -> bytes:
    version = session.query(DocumentVersion).filter(
        DocumentVersion.document_id == document.id,
        DocumentVersion.version == 1,
    ).one()
    return storage.get(version.storage_key)


def handle_security_scan(session: Session, job: Job, progress: Callable[[float, str], None]) -> dict:
    document = session.get(Document, job.document_id)
    if document is None:
        raise ValueError("document no longer exists")

    progress(0.2, "Loading document")
    data = _load_original(session, document)

    progress(0.5, "Inspecting PDF structure")
    report = security_analyzer.analyze(io.BytesIO(data))

    progress(0.85, "Recording findings")
    session.query(SecurityFinding).filter(
        SecurityFinding.document_id == document.id
    ).delete(synchronize_session=False)

    for finding in report.by_severity():
        session.add(SecurityFinding(
            document_id=document.id,
            finding_id=finding.id,
            title=finding.title,
            severity=finding.severity,
            detail=finding.detail,
            locations=finding.location_summary,
        ))

    document.doc_metadata = {
        **(document.doc_metadata or {}),
        "security": {
            "risk_level": report.risk_level,
            "risk_label": report.risk_label,
            "headline": report.headline,
            "encrypted": report.encrypted,
            "signed": report.signed,
            "has_forms": report.has_forms,
            "url_count": len(report.urls),
        },
    }

    audit.record(
        session,
        action="document.security_scan",
        workspace_id=document.workspace_id,
        document_id=document.id,
        detail=f"risk={report.risk_level} findings={len(report.findings)}",
    )

    return {
        "risk_level": report.risk_level,
        "finding_count": len(report.findings),
    }


def handle_ingest(session: Session, job: Job, progress: Callable[[float, str], None]) -> dict:
    """Extract structure and page-level profile from an uploaded PDF."""
    document = session.get(Document, job.document_id)
    if document is None:
        raise ValueError("document no longer exists")

    progress(0.15, "Loading document")
    data = _load_original(session, document)

    progress(0.4, "Reading document profile")
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    info = reader.metadata or {}

    def clean(value) -> str:
        text = str(value).strip() if value else ""
        return text[:500]

    pages_profile = []
    native_pages = 0
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        box = page.mediabox
        width, height = float(box.width), float(box.height)
        has_text = len(text.strip()) >= 20
        native_pages += 1 if has_text else 0
        pages_profile.append({
            "page": index,
            "width": round(width, 1),
            "height": round(height, 1),
            "orientation": "landscape" if width > height else "portrait",
            "characters": len(text.strip()),
            # Honest classification: with no OCR engine installed we can say a
            # page has no extractable text, not that it is definitely a scan.
            "text_layer": "native" if has_text else "none",
        })
        progress(0.4 + 0.45 * (index / max(len(reader.pages), 1)),
                 f"Profiling page {index} of {len(reader.pages)}")

    total = len(pages_profile)
    if total == 0:
        classification = "empty"
    elif native_pages == total:
        classification = "native"
    elif native_pages == 0:
        classification = "no_text_layer"
    else:
        classification = "mixed"

    document.page_count = total
    document.doc_metadata = {
        **(document.doc_metadata or {}),
        "profile": {
            "pdf_version": getattr(reader, "pdf_header", "") or "",
            "title": clean(info.get("/Title")),
            "author": clean(info.get("/Author")),
            "subject": clean(info.get("/Subject")),
            "keywords": clean(info.get("/Keywords")),
            "creator": clean(info.get("/Creator")),
            "producer": clean(info.get("/Producer")),
            "created": clean(info.get("/CreationDate")),
            "modified": clean(info.get("/ModDate")),
            "page_count": total,
            "pages_with_text": native_pages,
            "classification": classification,
            "outline_entries": _outline_count(reader),
        },
        "pages": pages_profile[:2000],
    }
    document.status = DocumentStatus.READY
    document.status_detail = None

    audit.record(
        session,
        action="document.ingest",
        workspace_id=document.workspace_id,
        document_id=document.id,
        detail=f"pages={total} classification={classification}",
    )
    audit_units = float(total)
    from docintel.core.audit import meter
    meter(session, workspace_id=document.workspace_id, document_id=document.id,
          operation="ingest.pages", units=audit_units, unit_kind="pages")

    return {"page_count": total, "classification": classification}


def _outline_count(reader) -> int:
    try:
        outline = reader.outline
    except Exception:
        return 0

    def walk(items) -> int:
        total = 0
        for item in items:
            if isinstance(item, list):
                total += walk(item)
            else:
                total += 1
        return total

    try:
        return walk(outline or [])
    except Exception:
        return 0


HANDLERS: Dict[JobType, Callable] = {
    JobType.INGEST: handle_ingest,
    JobType.SECURITY_SCAN: handle_security_scan,
}
