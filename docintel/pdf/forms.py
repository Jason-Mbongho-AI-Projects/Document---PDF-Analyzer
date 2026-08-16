"""
PDF form detection and filling (pypdf, BSD-3-Clause).

Fills existing AcroForm fields. XFA forms are detected and reported as
unsupported rather than silently producing a document that looks filled but
is not — an XFA form's values live in an XML payload that a plain AcroForm
write does not touch.
"""
import io
from dataclasses import dataclass, field as dataclass_field
from typing import Dict, List, Optional

from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject

from docintel.pdf.engine import PDFEngineError

# PDF field type codes to something a UI can render.
FIELD_TYPES = {
    "/Tx": "text",
    "/Btn": "button",
    "/Ch": "choice",
    "/Sig": "signature",
}

# AcroForm field flag bits (PDF 32000-1, table 221/226/228).
FLAG_READ_ONLY = 1 << 0
FLAG_REQUIRED = 1 << 1
FLAG_MULTILINE = 1 << 12
FLAG_PASSWORD = 1 << 13
FLAG_RADIO = 1 << 15
FLAG_PUSHBUTTON = 1 << 16
FLAG_COMBO = 1 << 17


@dataclass
class FormField:
    name: str
    kind: str
    value: Optional[str] = None
    default: Optional[str] = None
    required: bool = False
    read_only: bool = False
    multiline: bool = False
    options: List[str] = dataclass_field(default_factory=list)
    tooltip: Optional[str] = None
    page: Optional[int] = None
    rect: Optional[List[float]] = None
    max_length: Optional[int] = None


@dataclass
class FormReport:
    has_form: bool
    is_xfa: bool
    fields: List[FormField]

    @property
    def fillable(self) -> bool:
        return self.has_form and not self.is_xfa

    @property
    def required_names(self) -> List[str]:
        return [f.name for f in self.fields if f.required and not f.read_only]

    @property
    def note(self) -> str:
        if not self.has_form:
            return "This document contains no interactive form fields."
        if self.is_xfa:
            return (
                "This is an XFA form. Its field values are stored in an embedded "
                "XML payload, so filling it through the standard AcroForm "
                "interface would not produce a correctly filled document. "
                "XFA filling is not supported."
            )
        return f"{len(self.fields)} fillable field(s) detected."


def _flags(node) -> int:
    try:
        return int(node.get("/Ff", 0) or 0)
    except Exception:
        return 0


def _classify(node) -> str:
    kind = FIELD_TYPES.get(str(node.get("/FT", "")), "unknown")
    flags = _flags(node)

    if kind == "button":
        if flags & FLAG_PUSHBUTTON:
            return "pushbutton"
        return "radio" if flags & FLAG_RADIO else "checkbox"
    if kind == "choice":
        return "dropdown" if flags & FLAG_COMBO else "listbox"
    if kind == "text" and flags & FLAG_MULTILINE:
        return "multiline"
    return kind


def inspect(data: bytes) -> FormReport:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise PDFEngineError(f"The file could not be opened: {exc}") from exc

    try:
        root = reader.trailer["/Root"]
        acroform = root.get("/AcroForm")
        acroform = acroform.get_object() if acroform is not None else None
    except Exception:
        acroform = None

    if acroform is None:
        return FormReport(has_form=False, is_xfa=False, fields=[])

    if "/XFA" in acroform:
        return FormReport(has_form=True, is_xfa=True, fields=[])

    # Map widget annotations back to page numbers and rectangles so a viewer
    # can position each field. Keyed by fully-qualified field name, walking up
    # /Parent for widgets that inherit their name — matching on object
    # identity is unreliable because pypdf may hand back distinct wrappers.
    page_of: Dict[str, int] = {}
    rect_of: Dict[str, List[float]] = {}

    def qualified_name(annot) -> Optional[str]:
        parts: List[str] = []
        node, guard = annot, 0
        while node is not None and guard < 16:
            title = node.get("/T")
            if title is not None:
                parts.insert(0, str(title))
            parent = node.get("/Parent")
            node = parent.get_object() if parent is not None else None
            guard += 1
        return ".".join(parts) if parts else None

    for index, page in enumerate(reader.pages, start=1):
        for ref in (page.get("/Annots") or []):
            try:
                annot = ref.get_object()
                name = qualified_name(annot)
                if not name:
                    continue
                page_of.setdefault(name, index)
                if "/Rect" in annot:
                    rect_of.setdefault(name, [float(v) for v in annot["/Rect"]])
            except Exception:
                continue

    raw = reader.get_fields() or {}
    fields: List[FormField] = []

    for name, node in raw.items():
        try:
            flags = _flags(node)
            options = []
            if "/Opt" in node:
                for option in node["/Opt"]:
                    option = option.get_object() if hasattr(option, "get_object") else option
                    options.append(str(option[0]) if isinstance(option, list) else str(option))

            value = node.get("/V")
            default = node.get("/DV")

            fields.append(FormField(
                name=str(name),
                kind=_classify(node),
                value=str(value) if value is not None else None,
                default=str(default) if default is not None else None,
                required=bool(flags & FLAG_REQUIRED),
                read_only=bool(flags & FLAG_READ_ONLY),
                multiline=bool(flags & FLAG_MULTILINE),
                options=options,
                tooltip=str(node["/TU"]) if "/TU" in node else None,
                page=page_of.get(str(name)),
                rect=rect_of.get(str(name)),
                max_length=int(node["/MaxLen"]) if "/MaxLen" in node else None,
            ))
        except Exception:
            # A malformed field must not abort the whole inspection.
            fields.append(FormField(name=str(name), kind="unknown"))

    return FormReport(has_form=True, is_xfa=False, fields=fields)


def fill(data: bytes, values: Dict[str, str], flatten: bool = False) -> bytes:
    """Write values into an AcroForm.

    Unknown field names are rejected rather than ignored, so a typo surfaces
    instead of silently producing a half-filled document.
    """
    report = inspect(data)
    if not report.has_form:
        raise PDFEngineError("This document has no interactive form fields.")
    if report.is_xfa:
        raise PDFEngineError(report.note)

    known = {f.name: f for f in report.fields}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise PDFEngineError(f"Unknown form field(s): {', '.join(unknown)}")

    read_only = sorted(n for n in values if known[n].read_only)
    if read_only:
        raise PDFEngineError(f"Field(s) are read-only: {', '.join(read_only)}")

    for name, value in values.items():
        limit = known[name].max_length
        if limit and len(str(value)) > limit:
            raise PDFEngineError(
                f"Value for '{name}' exceeds its {limit}-character limit."
            )

    reader = PdfReader(io.BytesIO(data))
    writer = PdfWriter(clone_from=reader)

    # Ask conforming readers to regenerate widget appearances, otherwise some
    # viewers show the old (empty) rendering despite the value being set.
    try:
        writer.set_need_appearances_writer(True)
    except Exception:
        pass

    stringified = {name: str(value) for name, value in values.items()}
    for page in writer.pages:
        if page.get("/Annots"):
            writer.update_page_form_field_values(page, stringified, auto_regenerate=False)

    if flatten:
        _flatten(writer)

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _flatten(writer: PdfWriter) -> None:
    """Make filled values non-editable by dropping the form definition.

    The visible appearance streams stay on the page, so the document still
    reads correctly; only the interactive layer is removed.
    """
    try:
        root = writer._root_object
        if "/AcroForm" in root:
            del root[NameObject("/AcroForm")]
        for page in writer.pages:
            if "/Annots" in page:
                del page[NameObject("/Annots")]
    except Exception as exc:
        raise PDFEngineError(f"The form could not be flattened: {exc}") from exc


# A checkbox or radio group carries "/Off" when nothing is selected. Treating
# that as a value would let an unticked required box pass validation.
UNSET_BUTTON_VALUES = {"/Off", "Off", ""}

TOGGLE_KINDS = {"checkbox", "radio"}


def _is_set(kind: str, value) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    if kind in TOGGLE_KINDS and text in UNSET_BUTTON_VALUES:
        return False
    return True


def validate_required(data: bytes, values: Dict[str, str]) -> List[str]:
    """Return the names of required fields still without a value."""
    report = inspect(data)
    missing = []
    for field in report.fields:
        if not field.required or field.read_only:
            continue
        if _is_set(field.kind, values.get(field.name)):
            continue
        if _is_set(field.kind, field.value):
            continue
        missing.append(field.name)
    return missing
