"""
Form builder: turn a flat PDF into a fillable AcroForm.

Fields are written as real widget annotations attached to the document's
/AcroForm, not drawn boxes. The difference matters: the output opens as a
fillable form in Acrobat, Preview and any conforming reader, and round-trips
through this platform's own form filler.

Every build is verified before it is returned — the result is re-parsed with
the same inspector the fill endpoint uses, and if the fields are not present
and fillable the operation raises rather than handing back a PDF that merely
has rectangles on it.
"""
import io
import re
from dataclasses import dataclass, field as dataclass_field
from typing import Dict, List, Optional, Sequence

from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject, BooleanObject, DictionaryObject, FloatObject, NameObject,
    NumberObject, TextStringObject,
)

from docintel.pdf.engine import PDFEngineError

# AcroForm field flags (PDF 32000-1 tables 226-228).
FLAG_READ_ONLY = 1 << 0
FLAG_REQUIRED = 1 << 1
FLAG_MULTILINE = 1 << 12
FLAG_RADIO = 1 << 15
FLAG_COMBO = 1 << 17

SUPPORTED = {
    "text", "multiline", "checkbox", "radio", "dropdown", "date", "signature",
}

NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


@dataclass
class FieldSpec:
    """One field to create. Coordinates are in view space (top-left origin),
    matching what a browser overlay produces."""
    name: str
    type: str
    page: int
    x: float
    y: float
    width: float
    height: float
    required: bool = False
    read_only: bool = False
    default: str = ""
    tooltip: str = ""
    options: List[str] = dataclass_field(default_factory=list)
    max_length: Optional[int] = None
    font_size: float = 10.0


def _validate(specs: Sequence[FieldSpec], page_count: int) -> None:
    if not specs:
        raise PDFEngineError("No fields were supplied.")

    seen: set = set()
    for spec in specs:
        if spec.type not in SUPPORTED:
            raise PDFEngineError(
                f"Unsupported field type '{spec.type}'. Supported: "
                f"{', '.join(sorted(SUPPORTED))}."
            )
        if not NAME_PATTERN.match(spec.name):
            raise PDFEngineError(
                f"Field name '{spec.name}' is invalid. Use letters, digits, "
                "dot, dash or underscore, starting with a letter."
            )
        if spec.name in seen:
            raise PDFEngineError(f"Duplicate field name '{spec.name}'.")
        seen.add(spec.name)

        if spec.page < 1 or spec.page > page_count:
            raise PDFEngineError(
                f"Field '{spec.name}' is on page {spec.page}, but the document "
                f"has {page_count} page(s)."
            )
        if spec.width <= 0 or spec.height <= 0:
            raise PDFEngineError(f"Field '{spec.name}' has no size.")
        if spec.type in ("dropdown", "radio") and len(spec.options) < 2:
            raise PDFEngineError(
                f"Field '{spec.name}' is a {spec.type} and needs at least two options."
            )


def _flags(spec: FieldSpec) -> int:
    flags = 0
    if spec.required:
        flags |= FLAG_REQUIRED
    if spec.read_only:
        flags |= FLAG_READ_ONLY
    if spec.type == "multiline":
        flags |= FLAG_MULTILINE
    if spec.type == "dropdown":
        flags |= FLAG_COMBO
    if spec.type == "radio":
        flags |= FLAG_RADIO
    return flags


def _rect(spec: FieldSpec, page_height: float) -> ArrayObject:
    # View space has its origin at the top-left; PDF space at the bottom-left.
    lower = page_height - spec.y - spec.height
    return ArrayObject([
        FloatObject(round(spec.x, 2)),
        FloatObject(round(lower, 2)),
        FloatObject(round(spec.x + spec.width, 2)),
        FloatObject(round(lower + spec.height, 2)),
    ])


def build(data: bytes, specs: Sequence[FieldSpec], *,
          verify: bool = True) -> bytes:
    """Add fillable fields to a PDF."""
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise PDFEngineError(f"The document could not be opened: {exc}") from exc

    _validate(specs, len(reader.pages))

    writer = PdfWriter(clone_from=io.BytesIO(data))
    add = getattr(writer, "add_object", None) or writer._add_object

    # A default resource dictionary is required, otherwise readers have no
    # font with which to render field values.
    helvetica = add(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    }))
    resources = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/Helv"): helvetica}),
    })

    field_refs: List = []

    for spec in specs:
        page = writer.pages[spec.page - 1]
        page_height = float(page.mediabox.height)

        widget = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/Rect"): _rect(spec, page_height),
            NameObject("/T"): TextStringObject(spec.name),
            NameObject("/Ff"): NumberObject(_flags(spec)),
            # Print flag: without it the field is invisible when printed.
            NameObject("/F"): NumberObject(4),
            NameObject("/DA"): TextStringObject(f"/Helv {spec.font_size:g} Tf 0 g"),
            NameObject("/MK"): DictionaryObject({
                NameObject("/BC"): ArrayObject([FloatObject(0.6)] * 3),
                NameObject("/BG"): ArrayObject([FloatObject(0.97)] * 3),
            }),
            NameObject("/BS"): DictionaryObject({
                NameObject("/W"): NumberObject(1),
                NameObject("/S"): NameObject("/S"),
            }),
        })

        if spec.tooltip:
            widget[NameObject("/TU")] = TextStringObject(spec.tooltip[:200])

        if spec.type in ("text", "multiline", "date"):
            widget[NameObject("/FT")] = NameObject("/Tx")
            if spec.max_length:
                widget[NameObject("/MaxLen")] = NumberObject(int(spec.max_length))
            if spec.default:
                widget[NameObject("/V")] = TextStringObject(spec.default)
                widget[NameObject("/DV")] = TextStringObject(spec.default)

        elif spec.type in ("checkbox", "radio"):
            widget[NameObject("/FT")] = NameObject("/Btn")
            on_state = NameObject("/Yes")
            widget[NameObject("/V")] = (
                on_state if spec.default in ("Yes", "/Yes", "true", "on")
                else NameObject("/Off")
            )
            widget[NameObject("/AS")] = widget[NameObject("/V")]
            # An appearance dictionary with both states, so readers know what
            # "on" looks like.
            widget[NameObject("/AP")] = DictionaryObject({
                NameObject("/N"): DictionaryObject({
                    on_state: add(_zero_stream(writer)),
                    NameObject("/Off"): add(_zero_stream(writer)),
                }),
            })

        elif spec.type == "dropdown":
            widget[NameObject("/FT")] = NameObject("/Ch")
            widget[NameObject("/Opt")] = ArrayObject(
                [TextStringObject(o) for o in spec.options]
            )
            chosen = spec.default if spec.default in spec.options else spec.options[0]
            widget[NameObject("/V")] = TextStringObject(chosen)
            widget[NameObject("/DV")] = TextStringObject(chosen)

        elif spec.type == "signature":
            widget[NameObject("/FT")] = NameObject("/Sig")

        reference = add(widget)
        field_refs.append(reference)

        existing = page.get(NameObject("/Annots"))
        annots = ArrayObject(list(existing) if existing else [])
        annots.append(reference)
        page[NameObject("/Annots")] = annots

    # Attach (or extend) the document's AcroForm.
    root = writer._root_object
    acroform = root.get(NameObject("/AcroForm"))

    if acroform is None:
        root[NameObject("/AcroForm")] = add(DictionaryObject({
            NameObject("/Fields"): ArrayObject(field_refs),
            NameObject("/DR"): resources,
            NameObject("/DA"): TextStringObject("/Helv 10 Tf 0 g"),
            # Ask readers to generate appearances for the values we set.
            NameObject("/NeedAppearances"): BooleanObject(True),
        }))
    else:
        acroform = acroform.get_object()
        current = acroform.get(NameObject("/Fields"))
        merged = ArrayObject(list(current) if current else [])
        merged.extend(field_refs)
        acroform[NameObject("/Fields")] = merged
        acroform[NameObject("/NeedAppearances")] = BooleanObject(True)
        if NameObject("/DR") not in acroform:
            acroform[NameObject("/DR")] = resources

    buffer = io.BytesIO()
    writer.write(buffer)
    output = buffer.getvalue()

    if verify:
        _verify(output, specs)
    return output


def _zero_stream(writer):
    """A minimal empty appearance stream for checkbox states."""
    from pypdf.generic import DecodedStreamObject

    stream = DecodedStreamObject()
    stream.set_data(b"")
    stream[NameObject("/Type")] = NameObject("/XObject")
    stream[NameObject("/Subtype")] = NameObject("/Form")
    stream[NameObject("/BBox")] = ArrayObject(
        [FloatObject(0), FloatObject(0), FloatObject(1), FloatObject(1)]
    )
    return stream


def _verify(output: bytes, specs: Sequence[FieldSpec]) -> None:
    """Re-read the result with the same inspector the fill endpoint uses.

    A form that does not come back as fillable is a failed build, not a
    successful one with cosmetic boxes.
    """
    from docintel.pdf import forms

    try:
        report = forms.inspect(output)
    except Exception as exc:
        raise PDFEngineError(
            f"The generated form could not be re-read: {exc}"
        ) from exc

    if not report.fillable:
        raise PDFEngineError(
            "The generated document did not come back as a fillable form. "
            "It has not been saved."
        )

    produced = {f.name for f in report.fields}
    missing = sorted({s.name for s in specs} - produced)
    if missing:
        raise PDFEngineError(
            f"These fields were not present in the generated form: "
            f"{', '.join(missing)}. It has not been saved."
        )


def describe(data: bytes) -> Dict[str, object]:
    """Summarise the fields already on a document, for the builder UI."""
    from docintel.pdf import forms

    report = forms.inspect(data)
    return {
        "has_form": report.has_form,
        "fillable": report.fillable,
        "note": report.note,
        "fields": [
            {
                "name": f.name, "kind": f.kind, "required": f.required,
                "read_only": f.read_only, "page": f.page, "rect": f.rect,
                "options": f.options,
            }
            for f in report.fields
        ],
    }
