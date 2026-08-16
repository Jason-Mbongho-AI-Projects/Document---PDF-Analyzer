"""
Concrete PDF engine built on pypdf, pikepdf and reportlab.

Page indices in this module's public API are 1-based, matching what a user
sees. Conversion to 0-based happens at the boundary.
"""
import io
from typing import List, Optional, Sequence, Tuple

import pikepdf
import pypdf
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError, DependencyError
from pypdf.generic import DecodedStreamObject, NameObject
from reportlab.lib.colors import Color
from reportlab.lib.pagesizes import A3, A4, LEGAL, LETTER, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from docintel.pdf.engine import (
    CompressionResult, PageGeometry, PDFEngine, PDFEngineError, PasswordRequired,
)

PAGE_SIZES = {
    "letter": LETTER,
    "legal": LEGAL,
    "a4": A4,
    "a3": A3,
}

CORNERS = {
    "top-left", "top-center", "top-right",
    "bottom-left", "bottom-center", "bottom-right",
}


def _read(data: bytes, password: Optional[str] = None) -> PdfReader:
    try:
        reader = PdfReader(io.BytesIO(data))
    except PdfReadError as exc:
        raise PDFEngineError(f"The file is not a readable PDF: {exc}") from exc
    except Exception as exc:
        raise PDFEngineError(f"The file could not be opened: {exc}") from exc

    # is_encrypted itself can raise on some AES documents, so probe defensively
    # and treat an unreadable header as "encrypted" rather than as corrupt.
    try:
        encrypted = reader.is_encrypted
    except Exception:
        encrypted = True

    if encrypted:
        if password is None:
            raise PasswordRequired("This PDF is password protected.")
        try:
            if reader.decrypt(password) == 0:
                raise PasswordRequired("The supplied password is incorrect.")
        except DependencyError as exc:
            raise PDFEngineError(f"Unsupported encryption: {exc}") from exc
        except PasswordRequired:
            raise
        except Exception as exc:
            raise PasswordRequired(f"The document could not be decrypted: {exc}") from exc

    return reader


def _write(writer: PdfWriter) -> bytes:
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _validate_pages(pages: Sequence[int], total: int) -> List[int]:
    """Normalise a 1-based page selection, rejecting anything out of range."""
    if not pages:
        raise PDFEngineError("No pages were selected.")

    seen: List[int] = []
    for page in pages:
        if not isinstance(page, int) or page < 1 or page > total:
            raise PDFEngineError(
                f"Page {page} is out of range; the document has {total} page(s)."
            )
        if page not in seen:
            seen.append(page)
    return seen


class PyPdfEngine(PDFEngine):

    # ------------------------------------------------------- inspection

    def page_count(self, data: bytes) -> int:
        return len(_read(data).pages)

    def geometry(self, data: bytes) -> List[PageGeometry]:
        reader = _read(data)
        result = []
        for index, page in enumerate(reader.pages, start=1):
            box = page.mediabox
            result.append(PageGeometry(
                number=index,
                width=round(float(box.width), 2),
                height=round(float(box.height), 2),
                rotation=int(page.get("/Rotate", 0) or 0) % 360,
            ))
        return result

    def is_encrypted(self, data: bytes) -> bool:
        """Detect encryption via QPDF, which reads the header of every
        revision including AES-256 without needing to decrypt it."""
        try:
            with pikepdf.open(io.BytesIO(data)) as pdf:
                return bool(pdf.is_encrypted)
        except pikepdf.PasswordError:
            return True
        except Exception:
            try:
                return bool(PdfReader(io.BytesIO(data)).is_encrypted)
            except Exception:
                return False

    # ------------------------------------------------ page organisation

    def reorder(self, data: bytes, order: Sequence[int]) -> bytes:
        reader = _read(data)
        total = len(reader.pages)

        normalised = _validate_pages(order, total)
        if len(normalised) != total:
            raise PDFEngineError(
                f"A reorder must list every page exactly once "
                f"({len(normalised)} given, {total} expected)."
            )

        writer = PdfWriter()
        for page_number in normalised:
            writer.add_page(reader.pages[page_number - 1])
        return _write(writer)

    def rotate(self, data: bytes, pages: Sequence[int], degrees: int) -> bytes:
        if degrees % 90 != 0:
            raise PDFEngineError("Rotation must be a multiple of 90 degrees.")

        reader = _read(data)
        targets = set(_validate_pages(pages, len(reader.pages)))

        writer = PdfWriter()
        for index, page in enumerate(reader.pages, start=1):
            if index in targets:
                page.rotate(degrees)
            writer.add_page(page)
        return _write(writer)

    def delete_pages(self, data: bytes, pages: Sequence[int]) -> bytes:
        reader = _read(data)
        total = len(reader.pages)
        targets = set(_validate_pages(pages, total))

        if len(targets) == total:
            raise PDFEngineError("A document must keep at least one page.")

        writer = PdfWriter()
        for index, page in enumerate(reader.pages, start=1):
            if index not in targets:
                writer.add_page(page)
        return _write(writer)

    def extract_pages(self, data: bytes, pages: Sequence[int]) -> bytes:
        reader = _read(data)
        selected = _validate_pages(pages, len(reader.pages))

        writer = PdfWriter()
        for page_number in selected:
            writer.add_page(reader.pages[page_number - 1])
        return _write(writer)

    def duplicate_pages(self, data: bytes, pages: Sequence[int]) -> bytes:
        reader = _read(data)
        targets = set(_validate_pages(pages, len(reader.pages)))

        writer = PdfWriter()
        for index, page in enumerate(reader.pages, start=1):
            writer.add_page(page)
            if index in targets:
                writer.add_page(page)
        return _write(writer)

    def insert(self, data: bytes, other: bytes, at: int) -> bytes:
        base = _read(data)
        incoming = _read(other)
        total = len(base.pages)

        # `at` is a 1-based insertion point; total + 1 appends.
        if at < 1 or at > total + 1:
            raise PDFEngineError(
                f"Insertion point {at} is out of range (1..{total + 1})."
            )

        writer = PdfWriter()
        for index, page in enumerate(base.pages, start=1):
            if index == at:
                for new_page in incoming.pages:
                    writer.add_page(new_page)
            writer.add_page(page)

        if at == total + 1:
            for new_page in incoming.pages:
                writer.add_page(new_page)

        return _write(writer)

    def crop(self, data: bytes, pages: Sequence[int],
             box: Tuple[float, float, float, float]) -> bytes:
        left, bottom, right, top = box
        if right <= left or top <= bottom:
            raise PDFEngineError("Crop box must have positive width and height.")

        reader = _read(data)
        targets = set(_validate_pages(pages, len(reader.pages)))

        writer = PdfWriter()
        for index, page in enumerate(reader.pages, start=1):
            if index in targets:
                media = page.mediabox
                # Clamp to the existing page so a crop can never enlarge it.
                new_left = max(float(media.left), left)
                new_bottom = max(float(media.bottom), bottom)
                new_right = min(float(media.right), right)
                new_top = min(float(media.top), top)

                if new_right <= new_left or new_top <= new_bottom:
                    raise PDFEngineError(
                        f"Crop box does not overlap page {index}."
                    )

                page.mediabox.lower_left = (new_left, new_bottom)
                page.mediabox.upper_right = (new_right, new_top)
                page.cropbox.lower_left = (new_left, new_bottom)
                page.cropbox.upper_right = (new_right, new_top)
            writer.add_page(page)
        return _write(writer)

    def merge(self, documents: Sequence[bytes]) -> bytes:
        if not documents:
            raise PDFEngineError("No documents were supplied to merge.")

        writer = PdfWriter()
        for position, document in enumerate(documents, start=1):
            try:
                reader = _read(document)
            except PDFEngineError as exc:
                raise PDFEngineError(f"Document {position} could not be merged: {exc}")
            for page in reader.pages:
                writer.add_page(page)
        return _write(writer)

    def split_ranges(self, data: bytes,
                     ranges: Sequence[Tuple[int, int]]) -> List[bytes]:
        reader = _read(data)
        total = len(reader.pages)
        if not ranges:
            raise PDFEngineError("No page ranges were supplied.")

        outputs: List[bytes] = []
        for start, end in ranges:
            if start < 1 or end > total or start > end:
                raise PDFEngineError(
                    f"Range {start}-{end} is invalid for a {total}-page document."
                )
            writer = PdfWriter()
            for number in range(start, end + 1):
                writer.add_page(reader.pages[number - 1])
            outputs.append(_write(writer))
        return outputs

    # ------------------------------------------------------- composition

    @staticmethod
    def _overlay(width: float, height: float, draw) -> pypdf.PageObject:
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=(width, height))
        draw(pdf)
        pdf.save()
        buffer.seek(0)
        return PdfReader(buffer).pages[0]

    @staticmethod
    def _privatise_contents(writer: PdfWriter) -> None:
        """Give every page its own /Contents object.

        Many generators point several pages at a single shared content stream.
        merge_page() appends to whatever object the page references, so with
        sharing in place stamping page 1 also stamps pages 2..n, and the last
        overlay wins on all of them. Copying each stream into a distinct
        object first makes per-page stamping actually per-page.
        """
        add = getattr(writer, "add_object", None) or writer._add_object

        for page in writer.pages:
            contents = page.get_contents()
            if contents is None:
                continue
            stream = DecodedStreamObject()
            stream.set_data(contents.get_data())
            page[NameObject("/Contents")] = add(stream)

    def _compose(self, data: bytes, should_stamp, make_overlay,
                 over: bool = True) -> bytes:
        """Stamp an overlay onto selected pages."""
        writer = PdfWriter(clone_from=io.BytesIO(data))
        self._privatise_contents(writer)

        for index, page in enumerate(writer.pages, start=1):
            if not should_stamp(index):
                continue
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            page.merge_page(make_overlay(index, width, height), over=over)

        return _write(writer)

    @staticmethod
    def _resolve_font(name: str) -> str:
        """Fall back to Helvetica rather than failing on an unknown font."""
        try:
            pdfmetrics.getFont(name)
            return name
        except Exception:
            return "Helvetica"

    def watermark_text(self, data: bytes, text: str, **options) -> bytes:
        if not text.strip():
            raise PDFEngineError("Watermark text cannot be empty.")

        opacity = float(options.get("opacity", 0.15))
        rotation = float(options.get("rotation", 45))
        font_size = float(options.get("font_size", 48))
        font = self._resolve_font(options.get("font", "Helvetica-Bold"))
        colour = options.get("colour", (0.5, 0.5, 0.5))
        behind = bool(options.get("behind", False))
        pages = options.get("pages")

        reader = _read(data)
        total = len(reader.pages)
        targets = set(_validate_pages(pages, total)) if pages else set(range(1, total + 1))

        def make(index: int, width: float, height: float):
            def draw(pdf):
                pdf.saveState()
                pdf.setFillColor(Color(*colour, alpha=opacity))
                pdf.setFont(font, font_size)
                pdf.translate(width / 2, height / 2)
                pdf.rotate(rotation)
                pdf.drawCentredString(0, 0, text)
                pdf.restoreState()
            return self._overlay(width, height, draw)

        # over=False places the mark beneath existing content.
        return self._compose(data, lambda i: i in targets, make, over=not behind)

    def page_numbers(self, data: bytes, **options) -> bytes:
        position = options.get("position", "bottom-center")
        if position not in CORNERS:
            raise PDFEngineError(
                f"Unknown position '{position}'. Expected one of {sorted(CORNERS)}."
            )

        start_at = int(options.get("start_at", 1))
        template = options.get("format", "{page}")
        font_size = float(options.get("font_size", 10))
        font = self._resolve_font(options.get("font", "Helvetica"))
        margin = float(options.get("margin", 36))
        pages = options.get("pages")

        reader = _read(data)
        total = len(reader.pages)
        targets = _validate_pages(pages, total) if pages else list(range(1, total + 1))
        target_set = set(targets)

        try:
            template.format(page=1, total=total)
        except (KeyError, IndexError) as exc:
            raise PDFEngineError(
                "Page number format may only use {page} and {total}."
            ) from exc

        # Precompute each target page's label so numbering stays sequential
        # over a sparse page selection.
        labels = {
            page_number: template.format(page=start_at + offset,
                                         total=len(targets) + start_at - 1)
            for offset, page_number in enumerate(targets)
        }

        def make(index: int, width: float, height: float):
            text = labels[index]

            def draw(pdf):
                pdf.setFont(font, font_size)
                y = margin if position.startswith("bottom") else height - margin
                if position.endswith("left"):
                    pdf.drawString(margin, y, text)
                elif position.endswith("right"):
                    pdf.drawRightString(width - margin, y, text)
                else:
                    pdf.drawCentredString(width / 2, y, text)
            return self._overlay(width, height, draw)

        return self._compose(data, lambda i: i in target_set, make)

    def header_footer(self, data: bytes, header: str = "",
                      footer: str = "", **options) -> bytes:
        if not header and not footer:
            raise PDFEngineError("Provide header text, footer text, or both.")

        font_size = float(options.get("font_size", 9))
        font = self._resolve_font(options.get("font", "Helvetica"))
        margin = float(options.get("margin", 28))
        align = options.get("align", "center")
        pages = options.get("pages")

        reader = _read(data)
        total = len(reader.pages)
        targets = set(_validate_pages(pages, total)) if pages else set(range(1, total + 1))

        def make(index: int, width: float, height: float):
            def draw(pdf):
                pdf.setFont(font, font_size)
                for text, y in ((header, height - margin), (footer, margin)):
                    if not text:
                        continue
                    if align == "left":
                        pdf.drawString(margin, y, text)
                    elif align == "right":
                        pdf.drawRightString(width - margin, y, text)
                    else:
                        pdf.drawCentredString(width / 2, y, text)
            return self._overlay(width, height, draw)

        return self._compose(data, lambda i: i in targets, make)

    def blank_document(self, pages: int = 1, size: str = "letter",
                       orientation: str = "portrait") -> bytes:
        if pages < 1 or pages > 500:
            raise PDFEngineError("A new document must have between 1 and 500 pages.")

        key = (size or "letter").lower()
        if key not in PAGE_SIZES:
            raise PDFEngineError(
                f"Unknown page size '{size}'. Expected one of {sorted(PAGE_SIZES)}."
            )

        dimensions = PAGE_SIZES[key]
        if orientation == "landscape":
            dimensions = landscape(dimensions)

        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer, pagesize=dimensions)
        for _ in range(pages):
            pdf.showPage()
        pdf.save()
        return buffer.getvalue()

    # -------------------------------------------------- security / size

    def protect(self, data: bytes, user_password: str,
                owner_password: Optional[str] = None, **permissions) -> bytes:
        """Encrypt with AES-256 (revision 6).

        pikepdf/QPDF is used rather than pypdf here: it implements AES-256
        natively, whereas pypdf would pull in an extra crypto dependency for
        the same result.
        """
        if not user_password:
            raise PDFEngineError("An open password is required.")

        allow = pikepdf.Permissions(
            accessibility=True,
            extract=bool(permissions.get("allow_copy", True)),
            modify_annotation=bool(permissions.get("allow_annotate", True)),
            modify_assembly=bool(permissions.get("allow_modify", False)),
            modify_form=bool(permissions.get("allow_annotate", True)),
            modify_other=bool(permissions.get("allow_modify", False)),
            print_lowres=bool(permissions.get("allow_print", True)),
            print_highres=bool(permissions.get("allow_print", True)),
        )

        try:
            with pikepdf.open(io.BytesIO(data)) as pdf:
                buffer = io.BytesIO()
                pdf.save(buffer, encryption=pikepdf.Encryption(
                    user=user_password,
                    owner=owner_password or user_password,
                    R=6,                      # AES-256
                    allow=allow,
                ))
                return buffer.getvalue()
        except pikepdf.PasswordError as exc:
            raise PasswordRequired("This PDF is already password protected.") from exc
        except Exception as exc:
            raise PDFEngineError(f"The document could not be protected: {exc}") from exc

    def unlock(self, data: bytes, password: str) -> bytes:
        """Remove encryption from a document the caller can already open.

        This is not a bypass. The correct password must be supplied; without
        it the operation fails. No recovery or cracking is attempted.
        """
        if not password:
            raise PasswordRequired("A password is required to unlock this document.")

        try:
            with pikepdf.open(io.BytesIO(data), password=password) as pdf:
                buffer = io.BytesIO()
                pdf.save(buffer)      # saved with no encryption
                return buffer.getvalue()
        except pikepdf.PasswordError as exc:
            raise PasswordRequired("The supplied password is incorrect.") from exc
        except Exception as exc:
            raise PDFEngineError(f"The document could not be unlocked: {exc}") from exc

    def compress(self, data: bytes, preset: str = "balanced") -> CompressionResult:
        presets = {
            "maximum-quality": dict(streams=True, objects=False),
            "balanced": dict(streams=True, objects=True),
            "maximum-compression": dict(streams=True, objects=True),
        }
        if preset not in presets:
            raise PDFEngineError(
                f"Unknown preset '{preset}'. Expected one of {sorted(presets)}."
            )

        try:
            with pikepdf.open(io.BytesIO(data)) as pdf:
                buffer = io.BytesIO()
                pdf.save(
                    buffer,
                    compress_streams=True,
                    object_stream_mode=(
                        pikepdf.ObjectStreamMode.generate
                        if presets[preset]["objects"]
                        else pikepdf.ObjectStreamMode.preserve
                    ),
                    linearize=(preset != "maximum-compression"),
                    recompress_flate=(preset == "maximum-compression"),
                )
                compressed = buffer.getvalue()
        except pikepdf.PasswordError as exc:
            raise PasswordRequired("This PDF is password protected.") from exc
        except Exception as exc:
            raise PDFEngineError(f"The document could not be compressed: {exc}") from exc

        # If the "compressed" output is larger, keep the original. Reporting a
        # negative saving as a saving would be a lie.
        if len(compressed) >= len(data):
            return CompressionResult(len(data), len(data), data)

        return CompressionResult(len(data), len(compressed), compressed)


PERMISSION_CAVEAT = (
    "Permission flags (print, copy, modify) are instructions to a conforming "
    "PDF reader, not cryptographic controls. A reader that chooses to ignore "
    "them can still print or extract content once the document is decrypted. "
    "Only the open password actually restricts access to the content."
)
