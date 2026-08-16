"""
PDF editing, organisation, composition and security endpoints.

Every operation reads a version, produces new bytes, and appends a new
version. Nothing here overwrites the original — `version` in each response is
the newly created one, and the source remains downloadable.
"""
from typing import List, Literal, Optional, Tuple

from fastapi import (
    APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status,
)
from pydantic import BaseModel, Field

from docintel.api.schemas import DocumentResponse
from docintel.config import settings
from docintel.core.deps import CurrentUser, DbSession, client_ip, require_document
from docintel.db.models import Document
from docintel.pdf import forms as form_tools
from docintel.pdf import render as render_tools
from docintel.pdf.engine import PDFEngineError, PasswordRequired, get_engine
from docintel.pdf.operations import PERMISSION_CAVEAT
from docintel.services import documents as docsvc

router = APIRouter(prefix="/documents/{document_id}", tags=["edit"])
engine = get_engine()


# --------------------------------------------------------------- schemas

class VersionResponse(BaseModel):
    document_id: str
    version: int
    label: str
    size_bytes: int
    page_count: Optional[int] = None
    reused_existing_bytes: bool = False
    note: Optional[str] = None


class PagesRequest(BaseModel):
    pages: List[int] = Field(min_length=1)
    source_version: Optional[int] = Field(default=None, ge=1)


class RotateRequest(PagesRequest):
    degrees: int = Field(default=90)


class ReorderRequest(BaseModel):
    order: List[int] = Field(min_length=1)
    source_version: Optional[int] = Field(default=None, ge=1)


class CropRequest(PagesRequest):
    left: float
    bottom: float
    right: float
    top: float


class SplitRequest(BaseModel):
    ranges: List[Tuple[int, int]] = Field(min_length=1)
    source_version: Optional[int] = Field(default=None, ge=1)


class WatermarkRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    opacity: float = Field(default=0.15, ge=0.02, le=1.0)
    rotation: float = Field(default=45, ge=-360, le=360)
    font_size: float = Field(default=48, ge=6, le=200)
    behind: bool = False
    pages: Optional[List[int]] = None
    source_version: Optional[int] = Field(default=None, ge=1)


class PageNumberRequest(BaseModel):
    position: Literal["top-left", "top-center", "top-right",
                      "bottom-left", "bottom-center", "bottom-right"] = "bottom-center"
    start_at: int = Field(default=1, ge=0)
    format: str = Field(default="{page}", max_length=60)
    font_size: float = Field(default=10, ge=5, le=48)
    margin: float = Field(default=36, ge=0, le=200)
    pages: Optional[List[int]] = None
    source_version: Optional[int] = Field(default=None, ge=1)


class HeaderFooterRequest(BaseModel):
    header: str = Field(default="", max_length=300)
    footer: str = Field(default="", max_length=300)
    align: Literal["left", "center", "right"] = "center"
    font_size: float = Field(default=9, ge=5, le=48)
    margin: float = Field(default=28, ge=0, le=200)
    pages: Optional[List[int]] = None
    source_version: Optional[int] = Field(default=None, ge=1)


class CompressRequest(BaseModel):
    preset: Literal["maximum-quality", "balanced", "maximum-compression"] = "balanced"
    source_version: Optional[int] = Field(default=None, ge=1)


class CompressResponse(VersionResponse):
    original_bytes: int
    compressed_bytes: int
    reduction_percent: float


class ProtectRequest(BaseModel):
    user_password: str = Field(min_length=4, max_length=200)
    owner_password: Optional[str] = Field(default=None, max_length=200)
    allow_print: bool = True
    allow_copy: bool = True
    allow_modify: bool = False
    allow_annotate: bool = True
    source_version: Optional[int] = Field(default=None, ge=1)


class UnlockRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    source_version: Optional[int] = Field(default=None, ge=1)


class FillFormRequest(BaseModel):
    values: dict = Field(default_factory=dict)
    flatten: bool = False
    source_version: Optional[int] = Field(default=None, ge=1)


class SnapshotRequest(BaseModel):
    page: int = Field(ge=1)
    left: float
    top: float
    right: float
    bottom: float
    scale: float = Field(default=2.0, ge=0.1, le=8.0)
    format: Literal["png", "jpg", "webp"] = "png"
    source_version: Optional[int] = Field(default=None, ge=1)


# ----------------------------------------------------------- helpers

def _guard(action):
    """Translate engine errors into clean HTTP responses.

    Engine messages are written to be user-facing; nothing internal leaks.
    """
    try:
        return action()
    except PasswordRequired as exc:
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(exc))
    except PDFEngineError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _respond(document: Document, result, note: Optional[str] = None) -> VersionResponse:
    return VersionResponse(
        document_id=document.id,
        version=result.version.version,
        label=result.version.label,
        size_bytes=result.size_bytes,
        page_count=document.page_count,
        reused_existing_bytes=result.reused_existing_bytes,
        note=note,
    )


def _apply(session, document, user, request, fn, label, action, detail=None):
    result = _guard(lambda: docsvc.apply_operation(
        session, document, fn, label=label, action=action,
        actor=user, source_version=getattr(request, "source_version", None),
        detail=detail,
    ))
    session.commit()
    session.refresh(document)
    return result


# ------------------------------------------------- page organisation

@router.post("/pages/rotate", response_model=VersionResponse)
def rotate_pages(document_id: str, body: RotateRequest, request: Request,
                 user: CurrentUser, session: DbSession) -> VersionResponse:
    document = require_document(session, user, document_id, write=True)
    result = _apply(session, document, user, body,
                    lambda data: engine.rotate(data, body.pages, body.degrees),
                    "rotated", "pdf.pages_rotated",
                    f"{len(body.pages)} page(s) by {body.degrees}deg")
    return _respond(document, result)


@router.post("/pages/reorder", response_model=VersionResponse)
def reorder_pages(document_id: str, body: ReorderRequest, request: Request,
                  user: CurrentUser, session: DbSession) -> VersionResponse:
    document = require_document(session, user, document_id, write=True)
    result = _apply(session, document, user, body,
                    lambda data: engine.reorder(data, body.order),
                    "reordered", "pdf.pages_reordered")
    return _respond(document, result)


@router.post("/pages/delete", response_model=VersionResponse)
def delete_pages(document_id: str, body: PagesRequest, request: Request,
                 user: CurrentUser, session: DbSession) -> VersionResponse:
    document = require_document(session, user, document_id, write=True)
    result = _apply(session, document, user, body,
                    lambda data: engine.delete_pages(data, body.pages),
                    "pages-deleted", "pdf.pages_deleted",
                    f"{len(body.pages)} page(s)")
    return _respond(
        document, result,
        note="The previous version still contains these pages and remains downloadable.",
    )


@router.post("/pages/duplicate", response_model=VersionResponse)
def duplicate_pages(document_id: str, body: PagesRequest, request: Request,
                    user: CurrentUser, session: DbSession) -> VersionResponse:
    document = require_document(session, user, document_id, write=True)
    result = _apply(session, document, user, body,
                    lambda data: engine.duplicate_pages(data, body.pages),
                    "pages-duplicated", "pdf.pages_duplicated")
    return _respond(document, result)


@router.post("/pages/crop", response_model=VersionResponse)
def crop_pages(document_id: str, body: CropRequest, request: Request,
               user: CurrentUser, session: DbSession) -> VersionResponse:
    document = require_document(session, user, document_id, write=True)
    box = (body.left, body.bottom, body.right, body.top)
    result = _apply(session, document, user, body,
                    lambda data: engine.crop(data, body.pages, box),
                    "cropped", "pdf.pages_cropped")
    return _respond(document, result)


@router.post("/pages/extract")
def extract_pages(document_id: str, body: PagesRequest, request: Request,
                  user: CurrentUser, session: DbSession) -> Response:
    """Extract pages as a NEW downloadable PDF. The source is untouched."""
    document = require_document(session, user, document_id)
    source = _guard(lambda: docsvc.read_version(session, document, body.source_version))
    output = _guard(lambda: engine.extract_pages(source, body.pages))

    from docintel.core import audit
    audit.record(session, action="pdf.pages_extracted", actor=user,
                 workspace_id=document.workspace_id, document_id=document.id,
                 detail=f"{len(body.pages)} page(s)", ip_address=client_ip(request))
    session.commit()

    stem = document.filename.rsplit(".", 1)[0]
    return Response(
        content=output, media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{stem}_extract.pdf"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/pages/split")
def split_document(document_id: str, body: SplitRequest, request: Request,
                   user: CurrentUser, session: DbSession) -> dict:
    """Split into several PDFs, each stored as a new document version set.

    Returns metadata; each part is fetched from its own download URL.
    """
    document = require_document(session, user, document_id, write=True)
    source = _guard(lambda: docsvc.read_version(session, document, body.source_version))
    parts = _guard(lambda: engine.split_ranges(source, body.ranges))

    created = []
    for (start, end), data in zip(body.ranges, parts):
        result = docsvc.add_version(
            session, document, data, f"split-{start}-{end}",
            actor=user, action="pdf.split",
            detail=f"pages {start}-{end}",
        )
        created.append({
            "version": result.version.version,
            "label": result.version.label,
            "pages": f"{start}-{end}",
            "size_bytes": result.size_bytes,
        })

    session.commit()
    return {"document_id": document.id, "parts": created}


# ------------------------------------------------------- composition

@router.post("/watermark", response_model=VersionResponse)
def add_watermark(document_id: str, body: WatermarkRequest, request: Request,
                  user: CurrentUser, session: DbSession) -> VersionResponse:
    document = require_document(session, user, document_id, write=True)
    result = _apply(
        session, document, user, body,
        lambda data: engine.watermark_text(
            data, body.text, opacity=body.opacity, rotation=body.rotation,
            font_size=body.font_size, behind=body.behind, pages=body.pages,
        ),
        "watermarked", "pdf.watermarked",
    )
    return _respond(document, result)


@router.post("/page-numbers", response_model=VersionResponse)
def add_page_numbers(document_id: str, body: PageNumberRequest, request: Request,
                     user: CurrentUser, session: DbSession) -> VersionResponse:
    document = require_document(session, user, document_id, write=True)
    result = _apply(
        session, document, user, body,
        lambda data: engine.page_numbers(
            data, position=body.position, start_at=body.start_at,
            format=body.format, font_size=body.font_size,
            margin=body.margin, pages=body.pages,
        ),
        "page-numbers", "pdf.page_numbers_added",
    )
    return _respond(document, result)


@router.post("/header-footer", response_model=VersionResponse)
def add_header_footer(document_id: str, body: HeaderFooterRequest, request: Request,
                      user: CurrentUser, session: DbSession) -> VersionResponse:
    document = require_document(session, user, document_id, write=True)
    result = _apply(
        session, document, user, body,
        lambda data: engine.header_footer(
            data, header=body.header, footer=body.footer, align=body.align,
            font_size=body.font_size, margin=body.margin, pages=body.pages,
        ),
        "header-footer", "pdf.header_footer_added",
    )
    return _respond(document, result)


# --------------------------------------------------- security / size

@router.post("/compress", response_model=CompressResponse)
def compress_document(document_id: str, body: CompressRequest, request: Request,
                      user: CurrentUser, session: DbSession) -> CompressResponse:
    document = require_document(session, user, document_id, write=True)
    source = _guard(lambda: docsvc.read_version(session, document, body.source_version))
    outcome = _guard(lambda: engine.compress(source, body.preset))

    result = docsvc.add_version(
        session, document, outcome.data, "compressed",
        actor=user, action="pdf.compressed",
        detail=f"{outcome.original_bytes} -> {outcome.compressed_bytes} bytes",
    )
    session.commit()
    session.refresh(document)

    note = None
    if outcome.compressed_bytes >= outcome.original_bytes:
        note = ("This document could not be made smaller; the original bytes were "
                "kept. Reported figures are measured, not estimated.")

    return CompressResponse(
        document_id=document.id, version=result.version.version,
        label=result.version.label, size_bytes=result.size_bytes,
        page_count=document.page_count,
        reused_existing_bytes=result.reused_existing_bytes,
        original_bytes=outcome.original_bytes,
        compressed_bytes=outcome.compressed_bytes,
        reduction_percent=outcome.reduction_percent,
        note=note,
    )


@router.post("/protect", response_model=VersionResponse)
def protect_document(document_id: str, body: ProtectRequest, request: Request,
                     user: CurrentUser, session: DbSession) -> VersionResponse:
    document = require_document(session, user, document_id, write=True)
    result = _apply(
        session, document, user, body,
        lambda data: engine.protect(
            data, body.user_password, body.owner_password,
            allow_print=body.allow_print, allow_copy=body.allow_copy,
            allow_modify=body.allow_modify, allow_annotate=body.allow_annotate,
        ),
        "protected", "pdf.protected",
        "AES-256",
    )
    return _respond(document, result, note=PERMISSION_CAVEAT)


@router.post("/unlock", response_model=VersionResponse)
def unlock_document(document_id: str, body: UnlockRequest, request: Request,
                    user: CurrentUser, session: DbSession) -> VersionResponse:
    """Remove encryption using the correct password.

    Requires write access to the document AND the password. No recovery or
    cracking is performed; a wrong password fails.
    """
    document = require_document(session, user, document_id, write=True)
    result = _apply(session, document, user, body,
                    lambda data: engine.unlock(data, body.password),
                    "unlocked", "pdf.unlocked")
    return _respond(document, result)


# -------------------------------------------------------------- forms

@router.get("/form")
def inspect_form(document_id: str, user: CurrentUser, session: DbSession,
                 version: Optional[int] = None) -> dict:
    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, version))
    report = _guard(lambda: form_tools.inspect(data))

    return {
        "document_id": document.id,
        "has_form": report.has_form,
        "is_xfa": report.is_xfa,
        "fillable": report.fillable,
        "note": report.note,
        "required_fields": report.required_names,
        "fields": [
            {
                "name": f.name, "kind": f.kind, "value": f.value,
                "default": f.default, "required": f.required,
                "read_only": f.read_only, "multiline": f.multiline,
                "options": f.options, "tooltip": f.tooltip,
                "page": f.page, "rect": f.rect, "max_length": f.max_length,
            }
            for f in report.fields
        ],
    }


@router.post("/form/fill", response_model=VersionResponse)
def fill_form(document_id: str, body: FillFormRequest, request: Request,
              user: CurrentUser, session: DbSession) -> VersionResponse:
    document = require_document(session, user, document_id, write=True)
    source = _guard(lambda: docsvc.read_version(session, document, body.source_version))

    missing = _guard(lambda: form_tools.validate_required(source, body.values))
    output = _guard(lambda: form_tools.fill(source, body.values, flatten=body.flatten))

    result = docsvc.add_version(
        session, document, output,
        "form-filled-flat" if body.flatten else "form-filled",
        actor=user, action="pdf.form_filled",
        detail=f"{len(body.values)} field(s)",
    )
    session.commit()
    session.refresh(document)

    note = None
    if missing:
        # Report rather than block: a partially filled draft is legitimate.
        note = f"Required field(s) still empty: {', '.join(missing)}"

    return _respond(document, result, note=note)


# ----------------------------------------------------------- rendering

@router.get("/render/{page}")
def render_page(document_id: str, page: int, user: CurrentUser, session: DbSession,
                scale: float = 1.5, format: str = "png",
                version: Optional[int] = None) -> Response:
    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, version))
    image = _guard(lambda: render_tools.render_page(data, page, scale=scale, fmt=format))

    return Response(
        content=image.data,
        media_type=f"image/{'jpeg' if format in ('jpg', 'jpeg') else format}",
        headers={
            "X-Content-Type-Options": "nosniff",
            # Private: a rendered page is document content.
            "Cache-Control": "private, max-age=300",
        },
    )


@router.post("/snapshot")
def snapshot(document_id: str, body: SnapshotRequest, request: Request,
             user: CurrentUser, session: DbSession) -> Response:
    """Capture a region of a page as an image.

    Rendered from the document server-side, so the result contains only the
    selected region at the requested resolution.
    """
    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))
    image = _guard(lambda: render_tools.render_region(
        data, body.page, (body.left, body.top, body.right, body.bottom),
        scale=body.scale, fmt=body.format,
    ))

    from docintel.core import audit
    audit.record(session, action="pdf.snapshot", actor=user,
                 workspace_id=document.workspace_id, document_id=document.id,
                 detail=f"page {body.page}", ip_address=client_ip(request))
    session.commit()

    extension = "jpg" if body.format in ("jpg", "jpeg") else body.format
    return Response(
        content=image.data,
        media_type=f"image/{'jpeg' if extension == 'jpg' else extension}",
        headers={
            "Content-Disposition": f'attachment; filename="snapshot_p{body.page}.{extension}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


class FormFieldSpec(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: Literal["text", "multiline", "checkbox", "radio", "dropdown",
                  "date", "signature"]
    page: int = Field(ge=1)
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    required: bool = False
    read_only: bool = False
    default: str = Field(default="", max_length=500)
    tooltip: str = Field(default="", max_length=200)
    options: List[str] = Field(default_factory=list, max_length=100)
    max_length: Optional[int] = Field(default=None, ge=1, le=10000)
    font_size: float = Field(default=10, ge=5, le=36)


class BuildFormRequest(BaseModel):
    fields: List[FormFieldSpec] = Field(min_length=1, max_length=200)
    source_version: Optional[int] = Field(default=None, ge=1)


@router.get("/form/builder")
def describe_form(document_id: str, user: CurrentUser, session: DbSession,
                  version: Optional[int] = None) -> dict:
    """Fields already on the document, so the builder can show them."""
    from docintel.pdf import formbuilder

    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, version))
    return {"document_id": document.id, **_guard(lambda: formbuilder.describe(data))}


@router.post("/form/builder", response_model=VersionResponse)
def build_form(document_id: str, body: BuildFormRequest, request: Request,
               user: CurrentUser, session: DbSession) -> VersionResponse:
    """Add fillable fields, producing a new version.

    The output is re-inspected before it is stored; if it does not come back
    as a genuine fillable form the operation fails and nothing is saved.
    """
    from docintel.pdf import formbuilder

    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))

    specs = [
        formbuilder.FieldSpec(
            name=f.name, type=f.type, page=f.page,
            x=f.x, y=f.y, width=f.width, height=f.height,
            required=f.required, read_only=f.read_only,
            default=f.default, tooltip=f.tooltip, options=f.options,
            max_length=f.max_length, font_size=f.font_size,
        )
        for f in body.fields
    ]

    output = _guard(lambda: formbuilder.build(data, specs, verify=True))
    result = docsvc.add_version(
        session, document, output, "fillable-form",
        actor=user, action="pdf.form_built",
        detail=f"{len(specs)} field(s)",
    )
    session.commit()
    session.refresh(document)

    return _respond(
        document, result,
        note=f"{len(specs)} fillable field(s) added and verified. "
             "The previous version remains available.",
    )


# ------------------------------------------------------------ text editing

class TextStyle(BaseModel):
    font: Literal["Helvetica", "Times", "Courier"] = "Helvetica"
    size: Optional[float] = Field(default=None, ge=4, le=200)
    colour: str = Field(default="#000000", max_length=9)
    bold: bool = False
    italic: bool = False


class TextEditItem(BaseModel):
    page: int = Field(ge=1)
    find: str = Field(min_length=1, max_length=2000)
    replace: str = Field(default="", max_length=2000)
    style: TextStyle = Field(default_factory=TextStyle)
    occurrence: Optional[int] = Field(default=None, ge=0)


class TextEditRequest(BaseModel):
    edits: List[TextEditItem] = Field(min_length=1, max_length=100)
    source_version: Optional[int] = Field(default=None, ge=1)


class AddTextRequest(BaseModel):
    page: int = Field(ge=1)
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    text: str = Field(min_length=1, max_length=2000)
    style: TextStyle = Field(default_factory=TextStyle)
    source_version: Optional[int] = Field(default=None, ge=1)


def _style(model: TextStyle):
    from docintel.pdf.textedit import Style
    return Style(font=model.font, size=model.size, colour=model.colour,
                 bold=model.bold, italic=model.italic)


@router.get("/text/find")
def find_text(document_id: str, q: str, user: CurrentUser, session: DbSession,
              page: Optional[int] = None,
              version: Optional[int] = None) -> dict:
    """Where a phrase appears, so the client can offer to edit it."""
    from docintel.pdf import textedit

    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, version))
    found = textedit.find_text(data, q, page=page)

    return {
        "document_id": document.id,
        "query": q,
        "count": len(found),
        "occurrences": [o.as_dict() for o in found],
    }


@router.post("/text/edit", response_model=VersionResponse)
def edit_text(document_id: str, body: TextEditRequest, request: Request,
              user: CurrentUser, session: DbSession) -> VersionResponse:
    """Replace or delete text, verified by re-reading the result.

    Replacement text is drawn in a standard font: an embedded font is usually
    subsetted and cannot be extended to new glyphs. The response names the
    font actually used and flags any replacement wider than what it replaced,
    because PDF text does not reflow.
    """
    from docintel.pdf import textedit

    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))

    edits = [
        textedit.Edit(page=item.page, find=item.find, replace=item.replace,
                      style=_style(item.style), occurrence=item.occurrence)
        for item in body.edits
    ]

    output, report = _guard(lambda: textedit.apply_edits(data, edits, verify=True))

    changed = sum(1 for r in report if r["replaced_with"])
    removed = len(report) - changed
    result = docsvc.add_version(
        session, document, output, "text-edited",
        actor=user, action="pdf.text_edited",
        # Deliberately counts only: an audit log that quotes the edited text
        # would carry the document's content into the log.
        detail=f"{changed} replaced, {removed} deleted",
    )
    session.commit()
    session.refresh(document)

    fonts = sorted({r["font"] for r in report if r["font"]})
    overflow = [r for r in report if r.get("overflows")]
    note = f"{changed} replacement(s) and {removed} deletion(s), verified."
    if fonts:
        note += f" Replacement text drawn in {', '.join(fonts)}."
    if overflow:
        note += (f" {len(overflow)} replacement(s) are wider than the original "
                 "text and may overlap what follows.")

    return _respond(document, result, note=note)


@router.post("/text/add", response_model=VersionResponse)
def add_text(document_id: str, body: AddTextRequest, request: Request,
             user: CurrentUser, session: DbSession) -> VersionResponse:
    """Draw new text at a point on the page, in PDF coordinates."""
    from docintel.pdf import textedit

    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))

    output = _guard(lambda: textedit.add_text(
        data, body.page, body.x, body.y, body.text,
        _style(body.style), verify=True,
    ))

    result = docsvc.add_version(
        session, document, output, "text-added",
        actor=user, action="pdf.text_added",
        detail=f"page {body.page}",
    )
    session.commit()
    session.refresh(document)

    return _respond(
        document, result,
        note=f"Text added to page {body.page} and verified as readable.",
    )


# --------------------------------------------------- assembling from sources

class InsertPagesRequest(BaseModel):
    source_document_id: str = Field(min_length=8, max_length=64)
    after: int = Field(default=0, ge=0)
    pages: Optional[List[int]] = None
    source_version: Optional[int] = Field(default=None, ge=1)


class ReplacePagesRequest(BaseModel):
    source_document_id: str = Field(min_length=8, max_length=64)
    targets: List[int] = Field(min_length=1)
    pages: Optional[List[int]] = None
    source_version: Optional[int] = Field(default=None, ge=1)


class BlankPagesRequest(BaseModel):
    after: int = Field(default=0, ge=0)
    count: int = Field(default=1, ge=1, le=100)
    source_version: Optional[int] = Field(default=None, ge=1)


def _source_bytes(session, user, document, source_id: str) -> bytes:
    """Read another document, authorised in its own right.

    Naming a document must never become a way to read one you cannot open, so
    the source goes through the same check as a direct request and an
    unauthorised id is indistinguishable from a missing one.
    """
    source = require_document(session, user, source_id)
    if source.workspace_id != document.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Both documents must be in the same workspace.",
        )
    return _guard(lambda: docsvc.read_version(session, source, None))


@router.post("/pages/insert", response_model=VersionResponse)
def insert_pages(document_id: str, body: InsertPagesRequest, request: Request,
                 user: CurrentUser, session: DbSession) -> VersionResponse:
    """Insert pages from another document. after=0 places them first."""
    from docintel.pdf import assemble

    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))
    source = _source_bytes(session, user, document, body.source_document_id)

    output = _guard(lambda: assemble.insert_pages(
        data, source, after=body.after, pages=body.pages))

    result = docsvc.add_version(
        session, document, output, "pages-inserted",
        actor=user, action="pdf.pages_inserted",
        detail=f"after page {body.after}",
    )
    session.commit()
    session.refresh(document)
    return _respond(document, result,
                    note=f"Pages inserted after page {body.after}.")


@router.post("/pages/replace", response_model=VersionResponse)
def replace_pages(document_id: str, body: ReplacePagesRequest, request: Request,
                  user: CurrentUser, session: DbSession) -> VersionResponse:
    """Swap pages for pages taken from another document."""
    from docintel.pdf import assemble

    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))
    source = _source_bytes(session, user, document, body.source_document_id)

    output = _guard(lambda: assemble.replace_pages(
        data, source, targets=body.targets, pages=body.pages))

    result = docsvc.add_version(
        session, document, output, "pages-replaced",
        actor=user, action="pdf.pages_replaced",
        detail=f"{len(body.targets)} page(s)",
    )
    session.commit()
    session.refresh(document)
    return _respond(document, result,
                    note=f"{len(body.targets)} page(s) replaced.")


@router.post("/pages/blank", response_model=VersionResponse)
def add_blank_pages(document_id: str, body: BlankPagesRequest, request: Request,
                    user: CurrentUser, session: DbSession) -> VersionResponse:
    """Add blank pages, matching the neighbouring page size."""
    from docintel.pdf import assemble

    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))

    output = _guard(lambda: assemble.insert_blank(
        data, after=body.after, count=body.count))

    result = docsvc.add_version(
        session, document, output, "blank-pages",
        actor=user, action="pdf.blank_pages",
        detail=f"{body.count} page(s) after {body.after}",
    )
    session.commit()
    session.refresh(document)
    return _respond(document, result,
                    note=f"{body.count} blank page(s) added.")


# --------------------------------------------------------- flatten comments

@router.post("/annotations/flatten", response_model=VersionResponse)
def flatten_annotations(document_id: str, request: Request,
                        user: CurrentUser, session: DbSession) -> VersionResponse:
    """Write the stored annotations into the document itself.

    Annotations live in the database so that marking up a document never
    rewrites it. The cost is that a downloaded copy has none of them, which
    surprises people. This produces a copy that does, as a new version, and
    leaves the editable annotations untouched.
    """
    from docintel.db.models import Annotation
    from docintel.pdf import annots

    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, None))

    rows = session.query(Annotation).filter(
        Annotation.document_id == document.id).all()
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This document has no annotations to write into it.",
        )

    payload = [
        {"kind": r.kind.value if hasattr(r.kind, "value") else str(r.kind),
         "page": r.page, "rect": r.rect or {}, "quads": r.quads or [],
         "colour": r.colour, "opacity": r.opacity, "body": r.body}
        for r in rows
    ]

    output = _guard(lambda: annots.flatten(data, payload))
    result = docsvc.add_version(
        session, document, output, "annotated",
        actor=user, action="pdf.annotations_flattened",
        detail=f"{len(payload)} annotation(s)",
    )
    session.commit()
    session.refresh(document)

    return _respond(
        document, result,
        note=(f"{len(payload)} annotation(s) written into the page. They are "
              "part of the document now and can no longer be edited there; "
              "the editable copies remain in the Comments tab."),
    )


# ------------------------------------------------------ document properties

class MetadataRequest(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)
    author: Optional[str] = Field(default=None, max_length=300)
    subject: Optional[str] = Field(default=None, max_length=500)
    keywords: Optional[str] = Field(default=None, max_length=500)
    source_version: Optional[int] = Field(default=None, ge=1)


class SanitiseRequest(BaseModel):
    remove_metadata: bool = True
    remove_javascript: bool = True
    remove_embedded_files: bool = True
    remove_outline: bool = False
    source_version: Optional[int] = Field(default=None, ge=1)


class OutlineEntry(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    page: int = Field(ge=1)
    depth: int = Field(default=0, ge=0, le=8)


class OutlineRequest(BaseModel):
    entries: List[OutlineEntry] = Field(max_length=2000)
    source_version: Optional[int] = Field(default=None, ge=1)


@router.get("/properties")
def get_properties(document_id: str, user: CurrentUser, session: DbSession,
                   version: Optional[int] = None) -> dict:
    """Metadata, page geometry, and what hidden data the file carries."""
    from docintel.pdf import properties

    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, version))
    result = _guard(lambda: properties.read_properties(data))
    result["document_id"] = document.id
    return result


@router.post("/properties", response_model=VersionResponse)
def set_properties(document_id: str, body: MetadataRequest, request: Request,
                   user: CurrentUser, session: DbSession) -> VersionResponse:
    from docintel.pdf import properties

    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))

    values = {k: v for k, v in body.model_dump(exclude={"source_version"}).items()
              if v is not None}
    if not values:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="No properties were supplied.")

    output = _guard(lambda: properties.set_metadata(data, values))
    result = docsvc.add_version(
        session, document, output, "properties",
        actor=user, action="pdf.properties_set",
        detail=", ".join(sorted(values)),
    )
    session.commit()
    session.refresh(document)
    return _respond(document, result, note="Document properties updated.")


@router.post("/sanitise", response_model=VersionResponse)
def sanitise_document(document_id: str, body: SanitiseRequest, request: Request,
                      user: CurrentUser, session: DbSession) -> VersionResponse:
    """Strip hidden data, reporting exactly what was removed."""
    from docintel.pdf import properties

    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))

    output, removed = _guard(lambda: properties.sanitise(
        data,
        remove_metadata=body.remove_metadata,
        remove_javascript=body.remove_javascript,
        remove_embedded_files=body.remove_embedded_files,
        remove_outline=body.remove_outline,
    ))

    result = docsvc.add_version(
        session, document, output, "sanitised",
        actor=user, action="pdf.sanitised",
        detail=f"{len(removed)} item(s)",
    )
    session.commit()
    session.refresh(document)

    if removed:
        note = ("Removed: " + "; ".join(removed) + ". Earlier versions still "
                "contain it — delete them if the original must not survive.")
    else:
        note = ("Nothing was removed: this document carried none of the hidden "
                "data that was checked for.")
    return _respond(document, result, note=note)


@router.get("/outline")
def get_outline(document_id: str, user: CurrentUser, session: DbSession,
                version: Optional[int] = None) -> dict:
    from docintel.pdf import properties

    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, version))
    return {"document_id": document.id,
            "entries": _guard(lambda: properties.read_outline(data))}


@router.post("/outline", response_model=VersionResponse)
def set_document_outline(document_id: str, body: OutlineRequest, request: Request,
                         user: CurrentUser, session: DbSession) -> VersionResponse:
    """Replace the bookmarks with the given list."""
    from docintel.pdf import properties

    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))

    entries = [e.model_dump() for e in body.entries]
    output = _guard(lambda: properties.set_outline(data, entries))

    result = docsvc.add_version(
        session, document, output, "bookmarks",
        actor=user, action="pdf.outline_set",
        detail=f"{len(entries)} bookmark(s)",
    )
    session.commit()
    session.refresh(document)
    return _respond(document, result, note=f"{len(entries)} bookmark(s) saved.")


# ------------------------------------------------------------------ links

class LinkRequest(BaseModel):
    page: int = Field(ge=1)
    rect: dict
    url: str = Field(min_length=4, max_length=2000)
    source_version: Optional[int] = Field(default=None, ge=1)


class RemoveLinksRequest(BaseModel):
    page: Optional[int] = Field(default=None, ge=1)
    index: Optional[int] = Field(default=None, ge=0)
    source_version: Optional[int] = Field(default=None, ge=1)


@router.get("/links")
def get_links(document_id: str, user: CurrentUser, session: DbSession,
              version: Optional[int] = None) -> dict:
    """Every link in the document, with where it sits and where it points."""
    from docintel.pdf import links as link_tools

    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, version))
    found = _guard(lambda: link_tools.list_links(data))
    return {"document_id": document.id, "count": len(found), "links": found}


@router.post("/links", response_model=VersionResponse)
def add_link(document_id: str, body: LinkRequest, request: Request,
             user: CurrentUser, session: DbSession) -> VersionResponse:
    """Add a clickable area pointing at a URL."""
    from docintel.pdf import links as link_tools

    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))
    output = _guard(lambda: link_tools.add_link(
        data, page=body.page, rect=body.rect, url=body.url))

    result = docsvc.add_version(
        session, document, output, "link-added",
        actor=user, action="pdf.link_added", detail=f"page {body.page}",
    )
    session.commit()
    session.refresh(document)
    return _respond(document, result, note=f"Link added to page {body.page}.")


@router.post("/links/remove", response_model=VersionResponse)
def remove_links(document_id: str, body: RemoveLinksRequest, request: Request,
                 user: CurrentUser, session: DbSession) -> VersionResponse:
    """Remove one link, or every link when nothing is named."""
    from docintel.pdf import links as link_tools

    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))
    output, removed = _guard(lambda: link_tools.remove_links(
        data, page=body.page, index=body.index))

    result = docsvc.add_version(
        session, document, output, "links-removed",
        actor=user, action="pdf.links_removed", detail=f"{removed} link(s)",
    )
    session.commit()
    session.refresh(document)
    return _respond(document, result, note=f"{removed} link(s) removed.")


# ------------------------------------------------------------ attachments

@router.get("/attachments")
def get_attachments(document_id: str, user: CurrentUser, session: DbSession,
                    version: Optional[int] = None) -> dict:
    """Files carried inside the document."""
    from docintel.pdf import attachments as attach_tools

    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, version))
    found = _guard(lambda: attach_tools.list_attachments(data))
    return {"document_id": document.id, "count": len(found), "attachments": found}


@router.get("/attachments/{name}")
def download_attachment(document_id: str, name: str, user: CurrentUser,
                        session: DbSession,
                        version: Optional[int] = None) -> Response:
    """Hand back an attachment's bytes.

    Served as an octet-stream download rather than inline: the whole point of
    the security scanner flagging embedded files is that their contents are
    not to be trusted, and a browser should not try to render one.
    """
    from docintel.pdf import attachments as attach_tools

    document = require_document(session, user, document_id)
    data = _guard(lambda: docsvc.read_version(session, document, version))
    payload, clean = _guard(lambda: attach_tools.extract(data, name))

    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{clean}"'},
    )


@router.post("/attachments/remove", response_model=VersionResponse)
def remove_attachment(document_id: str, request: Request, user: CurrentUser,
                      session: DbSession,
                      name: Optional[str] = None) -> VersionResponse:
    """Remove one attachment, or all of them."""
    from docintel.pdf import attachments as attach_tools

    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, None))
    output, removed = _guard(lambda: attach_tools.remove(data, name))

    result = docsvc.add_version(
        session, document, output, "attachments-removed",
        actor=user, action="pdf.attachments_removed", detail=f"{removed} file(s)",
    )
    session.commit()
    session.refresh(document)
    return _respond(document, result, note=f"{removed} attachment(s) removed.")


@router.post("/attachments", response_model=VersionResponse,
             status_code=status.HTTP_201_CREATED)
async def add_attachment(document_id: str, request: Request,
                         user: CurrentUser, session: DbSession,
                         file: UploadFile = File(...),
                         description: str = Form("")) -> VersionResponse:
    """Embed a file inside the document."""
    from docintel.pdf import attachments as attach_tools

    document = require_document(session, user, document_id, write=True)

    payload = await file.read(settings.max_upload_bytes + 1)
    await file.close()
    if len(payload) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.max_upload_mb} MB limit.",
        )

    data = _guard(lambda: docsvc.read_version(session, document, None))
    output = _guard(lambda: attach_tools.attach(
        data, file.filename or "attachment", payload, description))

    result = docsvc.add_version(
        session, document, output, "attachment",
        actor=user, action="pdf.attachment_added",
        detail=attach_tools.safe_name(file.filename or "attachment"),
    )
    session.commit()
    session.refresh(document)
    return _respond(document, result, note="File attached to the document.")


# --------------------------------------------------------- bates numbering

class BatesRequest(BaseModel):
    prefix: str = Field(default="", max_length=40)
    suffix: str = Field(default="", max_length=40)
    start_at: int = Field(default=1, ge=0)
    digits: int = Field(default=6, ge=1, le=12)
    position: Literal["bottom-left", "bottom-center", "bottom-right",
                      "top-left", "top-center", "top-right"] = "bottom-right"
    font_size: float = Field(default=9, ge=5, le=48)
    source_version: Optional[int] = Field(default=None, ge=1)


@router.post("/bates", response_model=VersionResponse)
def bates_number(document_id: str, body: BatesRequest, request: Request,
                 user: CurrentUser, session: DbSession) -> VersionResponse:
    """Stamp sequential Bates numbers.

    A Bates number is a zero-padded sequence with an optional prefix, used so
    every page of a disclosure has a unique reference that can be cited. It is
    page numbering with a strict format, so it runs through the same stamping
    path rather than duplicating it.
    """
    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))

    template = f"{body.prefix}{{page:0{body.digits}d}}{body.suffix}"
    output = _guard(lambda: engine.page_numbers(
        data, position=body.position, start_at=body.start_at,
        format=template, font_size=body.font_size,
    ))

    first = template.format(page=body.start_at)
    result = docsvc.add_version(
        session, document, output, "bates",
        actor=user, action="pdf.bates", detail=f"from {first}",
    )
    session.commit()
    session.refresh(document)
    return _respond(document, result,
                    note=f"Bates numbering applied, starting at {first}.")


# ------------------------------------------------------- scan enhancement

class EnhanceRequest(BaseModel):
    pages: Optional[List[int]] = None
    deskew: bool = True
    despeckle: bool = True
    contrast: bool = True
    binarise: bool = False
    dpi: int = Field(default=200, ge=72, le=400)
    # Enhancement rasterises: any real text layer is replaced by a picture of
    # itself. Refused on a document that has one unless explicitly confirmed.
    confirm_rasterise: bool = False
    source_version: Optional[int] = Field(default=None, ge=1)


@router.post("/enhance", response_model=VersionResponse)
def enhance_scan(document_id: str, body: EnhanceRequest, request: Request,
                 user: CurrentUser, session: DbSession) -> VersionResponse:
    """Deskew, despeckle and clean up a scanned document."""
    from docintel.pdf import enhance as enhance_tools
    from docintel.pdf import ocr as ocr_tools

    document = require_document(session, user, document_id, write=True)
    data = _guard(lambda: docsvc.read_version(session, document, body.source_version))

    assessment = ocr_tools.assess(data)
    if assessment.classification == "native" and not body.confirm_rasterise:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "This document already has a real text layer. Enhancing it "
                "turns every page into an image, so the text stops being "
                "selectable, searchable and readable by the AI features. "
                "Set confirm_rasterise to proceed anyway."
            ),
        )

    output, report = _guard(lambda: enhance_tools.enhance(
        data, pages=body.pages, deskew=body.deskew, despeckle=body.despeckle,
        contrast=body.contrast, binarise=body.binarise, dpi=body.dpi,
    ))

    result = docsvc.add_version(
        session, document, output, "enhanced",
        actor=user, action="pdf.enhanced", detail=f"{len(report)} page(s)",
    )
    session.commit()
    session.refresh(document)

    skewed = [r for r in report if r["skew_corrected"]]
    note = f"{len(report)} page(s) cleaned."
    if skewed:
        worst = max(abs(r["skew_corrected"]) for r in skewed)
        note += f" Straightened up to {worst:.2f}°."
    note += (" Pages are now images: run OCR to give the document a text layer "
             "again.")
    return _respond(document, result, note=note)
