"""AI endpoints: actions on a selection, and question answering."""
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from docintel.ai import service as ai
from docintel.ai.provider import LLMError, get_provider
from docintel.core import audit
from docintel.core.deps import CurrentUser, DbSession, client_ip, require_document
from docintel.pdf.engine import PDFEngineError, PasswordRequired
from docintel.services import documents as docsvc

router = APIRouter(prefix="/documents/{document_id}/ai", tags=["ai"])


class SelectionRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    mode: Literal["explain", "summarize", "translate", "rewrite", "shorten"]
    target_language: Optional[str] = Field(default=None, max_length=60)
    page: Optional[int] = Field(default=None, ge=1)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    page_limit: int = Field(default=6, ge=1, le=20)
    source_version: Optional[int] = Field(default=None, ge=1)


def _guard(action):
    try:
        return action()
    except LLMError as exc:
        # 503: the request was fine, the AI provider was not.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=str(exc))
    except PasswordRequired as exc:
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(exc))
    except PDFEngineError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/status")
def ai_status(document_id: str, user: CurrentUser, session: DbSession) -> dict:
    """Whether AI features can run, so the UI can disable rather than fail."""
    require_document(session, user, document_id)
    provider = get_provider()
    return {
        "available": provider.available,
        "provider": provider.name,
        "reason": None if provider.available else
                  "No AI provider is configured. Set OPENROUTER_API_KEY.",
    }


@router.post("/selection")
def selection_action(document_id: str, body: SelectionRequest, request: Request,
                     user: CurrentUser, session: DbSession) -> dict:
    document = require_document(session, user, document_id)

    result = _guard(lambda: ai.act_on_selection(
        body.text, body.mode, target_language=body.target_language,
    ))

    audit.record(
        session, action=f"ai.selection.{body.mode}", actor=user,
        workspace_id=document.workspace_id, document_id=document.id,
        # Length only — the selected text itself never enters the audit log.
        detail=f"{len(body.text)} chars", ip_address=client_ip(request),
    )
    audit.meter(
        session, workspace_id=document.workspace_id, user_id=user.id,
        document_id=document.id, operation=f"ai.{body.mode}",
        units=result.tokens, unit_kind="tokens", model=result.model,
    )
    session.commit()

    return {
        "mode": result.mode,
        "output": result.output,
        "model": result.model,
        "tokens": result.tokens,
        "injection_detected": result.injection_detected,
        "injection_note": result.injection_summary if result.injection_detected else None,
    }


@router.post("/ask")
def ask_document(document_id: str, body: AskRequest, request: Request,
                 user: CurrentUser, session: DbSession) -> dict:
    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))

    answer = _guard(lambda: ai.ask(data, body.question, page_limit=body.page_limit))

    audit.record(
        session, action="ai.ask", actor=user,
        workspace_id=document.workspace_id, document_id=document.id,
        detail=f"{len(answer.citations)} citation(s)", ip_address=client_ip(request),
    )
    audit.meter(
        session, workspace_id=document.workspace_id, user_id=user.id,
        document_id=document.id, operation="ai.ask",
        units=answer.tokens, unit_kind="tokens", model=answer.model,
    )
    session.commit()

    return {
        "question": answer.question,
        "answer": answer.answer,
        "citations": [{"page": c.page, "excerpt": c.excerpt} for c in answer.citations],
        "pages_searched": answer.pages_searched,
        "dropped_citations": answer.dropped_citations,
        "retrieval": answer.retrieval,
        "model": answer.model,
        "tokens": answer.tokens,
        "note": answer.note,
    }


class TranslateRequest(BaseModel):
    target_language: str = Field(min_length=2, max_length=60)
    source_language: str = Field(default="auto", max_length=60)
    pages: Optional[list] = None
    save_as_version: bool = True
    source_version: Optional[int] = Field(default=None, ge=1)


@router.post("/translate")
def translate_document(document_id: str, body: TranslateRequest, request: Request,
                       user: CurrentUser, session: DbSession) -> dict:
    """Translate a document or a subset of its pages.

    The result is stored as a NEW version. The original is never overwritten.
    """
    from docintel.ai import translate as translator
    from docintel.services import documents as docsvc

    document = require_document(session, user, document_id, write=body.save_as_version)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))

    result = _guard(lambda: translator.translate(
        data, body.target_language,
        pages=[int(p) for p in body.pages] if body.pages else None,
        source_language=body.source_language,
    ))

    version = None
    if body.save_as_version:
        output = _guard(lambda: translator.to_pdf(
            result, title=f"{document.filename} ({result.target_language})"))
        saved = docsvc.add_version(
            session, document, output,
            f"translated-{result.target_language.lower()[:20]}",
            actor=user, action="document.translated",
            detail=f"to {result.target_language}",
        )
        version = saved.version.version

    audit.meter(session, workspace_id=document.workspace_id, user_id=user.id,
                document_id=document.id, operation="ai.translate",
                units=result.tokens, unit_kind="tokens", model=result.model)
    session.commit()

    return {
        "target_language": result.target_language,
        "pages": result.pages,
        "glossary": result.glossary,
        "version": version,
        "fidelity": result.fidelity,
        "note": result.note,
        "model": result.model,
        "tokens": result.tokens,
    }


class SummarizeRequest(BaseModel):
    mode: Literal["brief", "detailed", "bullet_points", "executive"] = "detailed"
    source_version: Optional[int] = Field(default=None, ge=1)
    refresh: bool = False


@router.post("/summarize")
def summarize_document(document_id: str, body: SummarizeRequest, request: Request,
                       user: CurrentUser, session: DbSession) -> dict:
    """Summarise the whole document."""
    from docintel.ai import analysis

    document = require_document(session, user, document_id)

    # Summaries are expensive; reuse the stored one unless asked not to.
    if not body.refresh:
        cached = _stored(session, document, f"summary:{body.mode}")
        if cached:
            return {**cached.payload, "cached": True}

    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))
    result = _guard(lambda: analysis.summarize(data, body.mode))

    audit.record(session, action="ai.summarize", actor=user,
                 workspace_id=document.workspace_id, document_id=document.id,
                 detail=f"{body.mode}, {len(result.sections)} section(s)",
                 ip_address=client_ip(request))
    audit.meter(session, workspace_id=document.workspace_id, user_id=user.id,
                document_id=document.id, operation="ai.summarize",
                units=result.tokens, unit_kind="tokens", model=result.model)
    session.commit()

    payload = {
        "document_id": document.id,
        "mode": result.mode,
        "summary": result.summary,
        "page_count": result.page_count,
        "sections": [
            {"index": s.index + 1, "pages": s.pages, "summary": s.summary,
             "failed": s.failed}
            for s in result.sections
        ],
        "model": result.model,
        "tokens": result.tokens,
        "injection_detected": result.injection_detected,
        "injection_note": result.injection_note or None,
        "note": result.note,
    }
    _store(session, document, f"summary:{result.mode}", payload)
    session.commit()
    return {**payload, "cached": False}


@router.post("/analyze")
def analyze_document(document_id: str, request: Request,
                     user: CurrentUser, session: DbSession,
                     version: Optional[int] = None) -> dict:
    """Structured analysis, separating stated content from AI inference."""
    from docintel.ai import analysis

    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, version))
    result = _guard(lambda: analysis.analyze(data))

    audit.record(session, action="ai.analyze", actor=user,
                 workspace_id=document.workspace_id, document_id=document.id,
                 ip_address=client_ip(request))
    audit.meter(session, workspace_id=document.workspace_id, user_id=user.id,
                document_id=document.id, operation="ai.analyze",
                units=result.tokens, unit_kind="tokens", model=result.model)
    session.commit()

    return {
        "from_document": {
            "document_type": result.document_type,
            "purpose": result.purpose,
            "audience": result.audience,
            "topics": result.topics,
            "key_points": result.key_points,
            "entities": result.entities,
            "dates": result.dates,
            "obligations": result.obligations,
            "risks": result.risks,
            "stated_recommendations": result.stated_recommendations,
        },
        "ai_interpretation": {"observations": result.ai_observations},
        "model": result.model,
        "tokens": result.tokens,
        "injection_detected": result.injection_detected,
        "note": result.note,
    }


# ------------------------------------------------------- cached analyses

def _stored(session, document, kind: str):
    """Read a previously computed analysis for the current version."""
    from docintel.db.models import DocumentAnalysis
    from sqlalchemy import select

    row = session.scalar(
        select(DocumentAnalysis).where(
            DocumentAnalysis.document_id == document.id,
            DocumentAnalysis.kind == kind,
        ).order_by(DocumentAnalysis.created_at.desc())
    )
    return row


def _store(session, document, kind: str, payload: dict) -> None:
    """Persist an analysis so repeat views cost nothing."""
    from docintel.db.models import DocumentAnalysis
    from sqlalchemy import delete

    session.execute(
        delete(DocumentAnalysis).where(
            DocumentAnalysis.document_id == document.id,
            DocumentAnalysis.kind == kind,
        )
    )
    session.add(DocumentAnalysis(
        document_id=document.id, kind=kind, payload=payload,
    ))


@router.get("/insights")
def document_insights(document_id: str, user: CurrentUser, session: DbSession,
                      version: Optional[int] = None) -> dict:
    """Keywords, sentiment and readability. Statistical, no model call."""
    from docintel.ai import insights as insight_tools

    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, version))
    result = _guard(lambda: insight_tools.compute(data))

    return {
        "document_id": document.id,
        "word_count": result.word_count,
        "character_count": result.character_count,
        "page_count": result.page_count,
        "keywords": result.keywords,
        "sentiment": result.sentiment,
        "readability": result.readability,
        "method_notes": result.method_notes,
        "note": ("These are statistical measures computed locally. No AI model "
                 "was used and nothing was sent anywhere."),
    }


@router.post("/quotes")
def extract_quotes(document_id: str, request: Request,
                   user: CurrentUser, session: DbSession,
                   count: int = 8, refresh: bool = False,
                   version: Optional[int] = None) -> dict:
    """Key quotations, each verified to appear verbatim in the document."""
    from docintel.ai import insights as insight_tools

    document = require_document(session, user, document_id)

    if not refresh:
        cached = _stored(session, document, "quotes")
        if cached:
            return {**cached.payload, "cached": True}

    data = _guard(lambda: docsvc.read_version(session, document, version))
    quotes = _guard(lambda: insight_tools.key_quotes(data, count=count))

    payload = {
        "document_id": document.id,
        "quotes": [{"text": q.text, "page": q.page} for q in quotes],
        "note": ("Each quotation was checked against the document text. "
                 "Anything the model paraphrased rather than copied was "
                 "discarded, so this list may be shorter than requested."),
    }
    _store(session, document, "quotes", payload)

    audit.record(session, action="ai.quotes", actor=user,
                 workspace_id=document.workspace_id, document_id=document.id,
                 detail=f"{len(quotes)} quote(s)", ip_address=client_ip(request))
    session.commit()
    return {**payload, "cached": False}


@router.get("/summary/export")
def export_summary(document_id: str, user: CurrentUser, session: DbSession,
                   mode: str = "detailed", format: str = "txt") -> Response:
    """Download a stored summary as a text report or section CSV."""
    import csv
    import io as _io
    from datetime import datetime, timezone

    if format not in ("txt", "csv"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Format must be 'txt' or 'csv'.")

    document = require_document(session, user, document_id)
    stored = _stored(session, document, f"summary:{mode}")
    if stored is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {mode} summary has been generated for this document yet.",
        )

    payload = stored.payload
    stem = document.filename.rsplit(".", 1)[0] or "document"

    if format == "txt":
        lines = [
            "PDF SUMMARY REPORT",
            "=" * 60,
            f"Document      : {document.filename}",
            f"Summary type  : {payload.get('mode', mode)}",
            f"Pages         : {payload.get('page_count', '?')}",
            f"Sections      : {len(payload.get('sections', []))}",
            f"Model         : {payload.get('model', '')}",
            f"Generated     : {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            "",
            "SUMMARY",
            "-" * 60,
            payload.get("summary", ""),
            "",
            "SECTION SUMMARIES",
            "-" * 60,
        ]
        for section in payload.get("sections", []):
            lines.append(f"\nSection {section['index']} ({section.get('pages', '')})")
            lines.append(section.get("summary", ""))

        body = "\n".join(lines).encode("utf-8")
        media, extension = "text/plain; charset=utf-8", "txt"
    else:
        buffer = _io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Section", "Pages", "Summary characters", "Status", "Summary"])
        for section in payload.get("sections", []):
            text = section.get("summary", "")
            writer.writerow([
                section["index"], section.get("pages", ""), len(text),
                "Failed" if section.get("failed") else "Success", text,
            ])
        body = buffer.getvalue().encode("utf-8")
        media, extension = "text/csv; charset=utf-8", "csv"

    return Response(
        content=body,
        media_type=media,
        headers={
            "Content-Disposition":
                f'attachment; filename="{stem}_summary.{extension}"',
            "X-Content-Type-Options": "nosniff",
        },
    )
