"""
PDF editing, organisation, composition and security endpoints.

Every operation reads a version, produces new bytes, and appends a new
version. Nothing here overwrites the original — `version` in each response is
the newly created one, and the source remains downloadable.
"""
from typing import List, Literal, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from docintel.api.schemas import DocumentResponse
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
