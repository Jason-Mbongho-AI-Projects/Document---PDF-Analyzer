"""
Text, search and redaction endpoints.

The text endpoint returns word geometry in both PDF and view coordinates,
which is what a browser viewer needs to draw a selection layer over a
rendered page.
"""
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from docintel.core import audit
from docintel.core.deps import CurrentUser, DbSession, client_ip, require_document
from docintel.pdf import redact as redact_tools
from docintel.pdf import text as text_tools
from docintel.pdf.engine import PDFEngineError, PasswordRequired
from docintel.services import documents as docsvc

router = APIRouter(prefix="/documents/{document_id}", tags=["content"])


class RedactTarget(BaseModel):
    page: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=500)
    start: int = Field(default=0, ge=0)
    end: int = Field(default=0, ge=0)
    rects: List[dict] = Field(default_factory=list)
    kind: str = Field(default="manual", max_length=40)


class DetectRequest(BaseModel):
    kinds: Optional[List[str]] = None
    custom_terms: List[str] = Field(default_factory=list)
    custom_regex: Optional[str] = Field(default=None, max_length=500)
    source_version: Optional[int] = Field(default=None, ge=1)


class ApplyRedactionRequest(BaseModel):
    targets: List[RedactTarget] = Field(min_length=1)
    draw_boxes: bool = True
    source_version: Optional[int] = Field(default=None, ge=1)


def _guard(action):
    try:
        return action()
    except PasswordRequired as exc:
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(exc))
    except PDFEngineError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/text")
def get_text(document_id: str, user: CurrentUser, session: DbSession,
             page: Optional[int] = Query(None, ge=1),
             version: Optional[int] = Query(None, ge=1)) -> dict:
    """Extracted text with per-word geometry for a selection layer."""
    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, version))
    pages = _guard(lambda: text_tools.extract(data, [page] if page else None))

    return {
        "document_id": document.id,
        "pages": [p.as_dict() for p in pages],
    }


@router.get("/search")
def search_document(document_id: str, user: CurrentUser, session: DbSession,
                    q: str = Query(min_length=1, max_length=200),
                    case_sensitive: bool = Query(False),
                    whole_words: bool = Query(False),
                    version: Optional[int] = Query(None, ge=1)) -> dict:
    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, version))
    matches = _guard(lambda: text_tools.search(
        data, q, case_sensitive=case_sensitive, whole_words=whole_words,
    ))

    return {
        "document_id": document.id,
        "query": q,
        "total": len(matches),
        "matches": [
            {"page": m.page, "start": m.start, "end": m.end,
             "text": m.text, "context": m.context, "rects": m.rects}
            for m in matches
        ],
    }


@router.post("/redact/detect")
def detect_sensitive(document_id: str, body: DetectRequest,
                     user: CurrentUser, session: DbSession) -> dict:
    """Find candidates for redaction. Nothing is modified.

    This is the review step: the caller decides what to redact and calls
    /redact/apply with the subset it wants removed.
    """
    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))
    candidates = _guard(lambda: redact_tools.detect(
        data, kinds=body.kinds, custom_terms=body.custom_terms,
        custom_regex=body.custom_regex,
    ))

    return {
        "document_id": document.id,
        "available_kinds": sorted(redact_tools.PATTERNS),
        "total": len(candidates),
        "candidates": [c.as_dict() for c in candidates],
        "note": ("Nothing has been changed. Review these and call "
                 "/redact/apply with the ones you want removed. Redaction "
                 "cannot be undone within the version it produces, though the "
                 "source version remains available."),
    }


@router.post("/redact/apply")
def apply_redaction(document_id: str, body: ApplyRedactionRequest, request: Request,
                    user: CurrentUser, session: DbSession) -> dict:
    """Remove the selected content and cover the area.

    The output is verified before it is stored; if any redacted text is still
    extractable the operation fails and nothing is saved.
    """
    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))

    targets = [
        redact_tools.Candidate(
            kind=t.kind, text=t.text, page=t.page,
            start=t.start, end=t.end, rects=t.rects,
        )
        for t in body.targets
    ]

    output = _guard(lambda: redact_tools.apply(
        data, targets, draw_boxes=body.draw_boxes, verify=True,
    ))

    result = docsvc.add_version(
        session, document, output, "redacted",
        actor=user, action="pdf.redacted",
        # Deliberately does not record what was redacted — an audit log that
        # quotes redacted content defeats the redaction.
        detail=f"{len(targets)} item(s) removed",
    )
    session.commit()
    session.refresh(document)

    return {
        "document_id": document.id,
        "version": result.version.version,
        "label": result.version.label,
        "size_bytes": result.size_bytes,
        "redacted_count": len(targets),
        "verified": True,
        "note": ("Content was removed from the page's content stream and "
                 "verified as no longer extractable. Earlier versions of this "
                 "document still contain the original text — delete them if "
                 "the original must not be recoverable."),
    }


class CompareRequest(BaseModel):
    against_document_id: str = Field(min_length=1, max_length=32)
    source_version: Optional[int] = Field(default=None, ge=1)
    against_version: Optional[int] = Field(default=None, ge=1)
    interpret: bool = False


@router.post("/compare")
def compare_documents(document_id: str, body: CompareRequest, request: Request,
                      user: CurrentUser, session: DbSession) -> dict:
    """Compare this document against another.

    Both documents are resolved through the same authorization path, so a
    caller cannot use one document they can see to read the contents of one
    they cannot.
    """
    from docintel.pdf import compare as compare_tools

    original = require_document(session, user, document_id)
    revised = require_document(session, user, body.against_document_id)

    left = _guard(lambda: docsvc.read_version(session, original, body.source_version))
    right = _guard(lambda: docsvc.read_version(session, revised, body.against_version))

    result = _guard(lambda: compare_tools.compare(left, right))
    payload = result.as_dict()

    if body.interpret and not result.identical:
        from docintel.ai.provider import LLMError
        try:
            payload["interpretation"] = compare_tools.interpret(result)
        except LLMError as exc:
            # The structural diff is still valid without the commentary.
            payload["interpretation"] = None
            payload["interpretation_error"] = str(exc)

    payload["original"] = {"id": original.id, "filename": original.filename}
    payload["revised"] = {"id": revised.id, "filename": revised.filename}
    payload["note"] = (
        "Added, removed and changed text is computed mechanically and is "
        "factual. Any interpretation is a model's reading of that diff and "
        "should be checked against the changes themselves."
    )

    audit.record(session, action="document.compared", actor=user,
                 workspace_id=original.workspace_id, document_id=original.id,
                 detail=f"against {revised.id}", ip_address=client_ip(request))
    session.commit()

    return payload


@router.get("/ocr/assess")
def assess_ocr(document_id: str, user: CurrentUser, session: DbSession,
               version: Optional[int] = Query(None, ge=1)) -> dict:
    """Which pages would need OCR. Needs no OCR engine to answer."""
    from docintel.pdf import ocr

    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, version))
    assessment = ocr.assess(data)
    provider = ocr.get_provider()

    return {
        "document_id": document.id,
        "classification": assessment.classification,
        "summary": assessment.summary,
        "pages_needing_ocr": assessment.pages_needing_ocr,
        "pages": [
            {"page": p.page, "characters": p.characters,
             "needs_ocr": p.needs_ocr, "reason": p.reason}
            for p in assessment.pages
        ],
        "engine": {
            "name": provider.name,
            "available": provider.available,
            "reason": provider.reason,
            "languages": provider.languages() if provider.available else [],
        },
    }


class OcrRequest(BaseModel):
    pages: Optional[List[int]] = None
    language: str = Field(default="eng", max_length=20)
    source_version: Optional[int] = Field(default=None, ge=1)
    # On by default: OCR whose result is not written back leaves the document
    # exactly as unsearchable as before, and every other feature still sees an
    # empty page.
    save_searchable: bool = True


@router.post("/ocr")
def run_ocr(document_id: str, body: OcrRequest, request: Request,
            user: CurrentUser, session: DbSession) -> dict:
    """Recognise text on scanned pages.

    Returns 503 with the install instructions when no engine is configured,
    rather than returning empty text that looks like a clean result.
    """
    from docintel.pdf import ocr

    document = require_document(session, user, document_id, write=True)
    provider = ocr.get_provider()

    if not provider.available:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=provider.reason or "OCR is not configured.")

    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))
    result = _guard(lambda: provider.recognise(
        data, pages=body.pages, language=body.language))

    # Write the recognised words back as an invisible text layer, so search,
    # selection and every AI feature can read the document afterwards. The
    # result is re-parsed before it is kept: a layer that cannot be read back
    # would leave the document reported as searchable while still being empty.
    version: Optional[int] = None
    note = "Text recognised. Not saved to the document."

    if body.save_searchable:
        from docintel.pdf import ocr_layer

        searchable = _guard(lambda: ocr_layer.build(data, result.pages))
        still_empty = ocr_layer.verify(searchable, [p["page"] for p in result.pages])
        if still_empty:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "The text layer could not be read back on page(s) "
                    f"{', '.join(str(p) for p in still_empty)}, so it was discarded "
                    "rather than saved as a searchable document that is not."
                ),
            )

        saved = docsvc.add_version(
            session, document, searchable, "ocr",
            actor=user, action="pdf.ocr",
            detail=f"{result.engine}, {result.language}, "
                   f"{len(result.pages)} page(s)",
        )
        version = saved.version.version
        note = f"Searchable text layer saved as version {version}."

    document.doc_metadata = {
        **(document.doc_metadata or {}),
        "ocr": {
            "engine": result.engine,
            "language": result.language,
            "mean_confidence": result.mean_confidence,
            "pages": [p["page"] for p in result.pages],
            "version": version,
        },
    }
    audit.record(session, action="document.ocr", actor=user,
                 workspace_id=document.workspace_id, document_id=document.id,
                 detail=f"{len(result.pages)} page(s)", ip_address=client_ip(request))
    session.commit()

    return {
        "document_id": document.id,
        "engine": result.engine,
        "language": result.language,
        "mean_confidence": result.mean_confidence,
        "version": version,
        "note": note,
        # Word geometry is large and only needed server-side to build the
        # layer; the client wants the transcript.
        "pages": [
            {k: v for k, v in page.items() if k not in ("words", "image_size")}
            for page in result.pages
        ],
    }
