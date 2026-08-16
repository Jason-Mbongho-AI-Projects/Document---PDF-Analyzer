"""
Annotation and comment endpoints.

Annotations live in the database, not in the PDF bytes, so highlighting or
commenting never rewrites the document and never invalidates a signature.
Geometry is stored in PDF points so a highlight stays on its text at any zoom.
"""
from datetime import datetime, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from docintel.core import audit
from docintel.core.deps import CurrentUser, DbSession, client_ip, require_document
from docintel.db.models import Annotation, AnnotationKind

router = APIRouter(prefix="/documents/{document_id}/annotations", tags=["annotations"])

KINDS = tuple(k.value for k in AnnotationKind)


class Rect(BaseModel):
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class Point(BaseModel):
    """A position on the page, for marks that are paths rather than areas."""
    x: float
    y: float


class AnnotationCreate(BaseModel):
    kind: Literal[KINDS] = "highlight"          # type: ignore[valid-type]
    page: int = Field(ge=1)
    rect: Optional[Rect] = None
    quads: List[Rect] = Field(default_factory=list)
    # Arrows and freehand strokes are runs of positions. They are kept
    # separate from `quads` so rectangles can stay strictly validated: a
    # highlight with no width is a bug, while a point with no width is normal.
    points: List[Point] = Field(default_factory=list, max_length=5000)
    colour: str = Field(default="#FFD54F", max_length=20)
    opacity: float = Field(default=1.0, ge=0.05, le=1.0)
    selected_text: Optional[str] = Field(default=None, max_length=20000)
    body: Optional[str] = Field(default=None, max_length=20000)
    parent_id: Optional[str] = Field(default=None, max_length=32)
    extra: dict = Field(default_factory=dict)


class AnnotationUpdate(BaseModel):
    rect: Optional[Rect] = None
    quads: Optional[List[Rect]] = None
    colour: Optional[str] = Field(default=None, max_length=20)
    opacity: Optional[float] = Field(default=None, ge=0.05, le=1.0)
    body: Optional[str] = Field(default=None, max_length=20000)
    extra: Optional[dict] = None


class AnnotationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    document_id: str
    kind: str
    page: int
    rect: dict
    quads: list
    colour: str
    opacity: float
    selected_text: Optional[str]
    body: Optional[str]
    parent_id: Optional[str]
    is_resolved: bool
    author_id: Optional[str]
    created_at: datetime
    updated_at: datetime


def _load(session, user, document_id: str, annotation_id: str, write: bool = False):
    document = require_document(session, user, document_id, write=write)
    annotation = session.scalar(
        select(Annotation).where(
            Annotation.id == annotation_id,
            Annotation.document_id == document.id,
        )
    )
    if annotation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return document, annotation


@router.get("", response_model=List[AnnotationResponse])
def list_annotations(document_id: str, user: CurrentUser, session: DbSession,
                     page: Optional[int] = Query(None, ge=1),
                     kind: Optional[str] = Query(None),
                     include_resolved: bool = Query(True)) -> List[Annotation]:
    document = require_document(session, user, document_id)

    conditions = [Annotation.document_id == document.id]
    if page is not None:
        conditions.append(Annotation.page == page)
    if kind:
        if kind not in KINDS:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Unknown annotation kind '{kind}'")
        conditions.append(Annotation.kind == AnnotationKind(kind))
    if not include_resolved:
        conditions.append(Annotation.is_resolved.is_(False))

    return list(session.scalars(
        select(Annotation).where(*conditions)
        .order_by(Annotation.page, Annotation.created_at)
    ).all())


@router.post("", response_model=AnnotationResponse, status_code=status.HTTP_201_CREATED)
def create_annotation(document_id: str, body: AnnotationCreate, request: Request,
                      user: CurrentUser, session: DbSession) -> Annotation:
    document = require_document(session, user, document_id, write=True)

    # page_count is populated by the ingest job. An annotation can arrive
    # before that job finishes, so fall back to counting the stored bytes
    # rather than skipping validation.
    total = document.page_count
    if total is None:
        try:
            from docintel.pdf.engine import get_engine
            from docintel.services import documents as docsvc
            total = get_engine().page_count(docsvc.read_version(session, document))
        except Exception:
            total = None

    if total and body.page > total:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Page {body.page} is beyond this document's {total} page(s).",
        )

    if body.parent_id:
        parent = session.scalar(
            select(Annotation).where(
                Annotation.id == body.parent_id,
                Annotation.document_id == document.id,
            )
        )
        if parent is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="Parent annotation not found on this document")

    annotation = Annotation(
        document_id=document.id,
        workspace_id=document.workspace_id,
        author_id=user.id,
        kind=AnnotationKind(body.kind),
        page=body.page,
        rect=body.rect.model_dump() if body.rect else {},
        # Both land in `quads`: the column holds whichever geometry the kind
        # uses, and the renderer knows which to expect from the kind.
        quads=([p.model_dump() for p in body.points] if body.points
               else [q.model_dump() for q in body.quads]),
        colour=body.colour,
        opacity=body.opacity,
        selected_text=body.selected_text,
        body=body.body,
        parent_id=body.parent_id,
        extra=body.extra,
    )
    session.add(annotation)

    audit.record(session, action="pdf.annotation_added", actor=user,
                 workspace_id=document.workspace_id, document_id=document.id,
                 detail=f"{body.kind} on page {body.page}", ip_address=client_ip(request))
    session.commit()
    session.refresh(annotation)
    return annotation


@router.patch("/{annotation_id}", response_model=AnnotationResponse)
def update_annotation(document_id: str, annotation_id: str, body: AnnotationUpdate,
                      user: CurrentUser, session: DbSession) -> Annotation:
    document, annotation = _load(session, user, document_id, annotation_id, write=True)

    if body.rect is not None:
        annotation.rect = body.rect.model_dump()
    if body.quads is not None:
        annotation.quads = [q.model_dump() for q in body.quads]
    if body.colour is not None:
        annotation.colour = body.colour
    if body.opacity is not None:
        annotation.opacity = body.opacity
    if body.body is not None:
        annotation.body = body.body
    if body.extra is not None:
        annotation.extra = body.extra

    session.commit()
    session.refresh(annotation)
    return annotation


@router.post("/{annotation_id}/resolve", response_model=AnnotationResponse)
def resolve_annotation(document_id: str, annotation_id: str,
                       user: CurrentUser, session: DbSession) -> Annotation:
    document, annotation = _load(session, user, document_id, annotation_id, write=True)
    annotation.is_resolved = True
    annotation.resolved_by = user.id
    annotation.resolved_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(annotation)
    return annotation


@router.post("/{annotation_id}/reopen", response_model=AnnotationResponse)
def reopen_annotation(document_id: str, annotation_id: str,
                      user: CurrentUser, session: DbSession) -> Annotation:
    document, annotation = _load(session, user, document_id, annotation_id, write=True)
    annotation.is_resolved = False
    annotation.resolved_by = None
    annotation.resolved_at = None
    session.commit()
    session.refresh(annotation)
    return annotation


@router.delete("/{annotation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_annotation(document_id: str, annotation_id: str, request: Request,
                      user: CurrentUser, session: DbSession) -> None:
    document, annotation = _load(session, user, document_id, annotation_id, write=True)
    session.delete(annotation)
    audit.record(session, action="pdf.annotation_deleted", actor=user,
                 workspace_id=document.workspace_id, document_id=document.id,
                 ip_address=client_ip(request))
    session.commit()
