"""
Assembling pages from more than one source: insert, replace, blank.

Combining whole documents was already possible. What was missing is the finer
work Acrobat groups under Organize — dropping one document's pages into the
middle of another, swapping a page out for a corrected one, or adding a blank
sheet. All of it is page-level composition, so it lives together here.

Nothing mutates its inputs: each function returns new bytes and the caller
appends a version.
"""
from __future__ import annotations

import io
from typing import List, Optional, Sequence

from pypdf import PageObject, PdfReader, PdfWriter

from docintel.pdf.engine import PDFEngineError

# US Letter, in points. Used when a blank page is added to an empty document
# and there is no existing page to copy dimensions from.
DEFAULT_PAGE = (612.0, 792.0)


def _read(data: bytes) -> PdfReader:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise PDFEngineError(
                "This document is password protected. Unlock it before "
                "changing its pages."
            )
        if not reader.pages:
            raise PDFEngineError("This document has no pages.")
        return reader
    except PDFEngineError:
        raise
    except Exception as exc:
        raise PDFEngineError(f"The document could not be read: {exc}") from exc


def _write(writer: PdfWriter) -> bytes:
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def insert_pages(data: bytes, source: bytes, *, after: int,
                 pages: Optional[Sequence[int]] = None) -> bytes:
    """Insert pages from `source` into `data` after page `after`.

    `after=0` puts them at the very front, which is the natural way to say
    "before page one" and avoids a separate flag for it.
    """
    target = _read(data)
    incoming = _read(source)
    total = len(target.pages)

    if after < 0 or after > total:
        raise PDFEngineError(
            f"Cannot insert after page {after}; the document has {total} page(s). "
            "Use 0 to insert at the beginning."
        )

    selected = _select(incoming, pages)

    writer = PdfWriter()
    for page in target.pages[:after]:
        writer.add_page(page)
    for page in selected:
        writer.add_page(page)
    for page in target.pages[after:]:
        writer.add_page(page)

    return _write(writer)


def replace_pages(data: bytes, source: bytes, *, targets: Sequence[int],
                  pages: Optional[Sequence[int]] = None) -> bytes:
    """Swap the given pages of `data` for pages taken from `source`.

    The replacement runs in order: the first incoming page takes the place of
    the first target, and so on. Counts must match, because guessing which
    page was meant to go where is how the wrong page ends up in a contract.
    """
    target = _read(data)
    incoming = _read(source)
    total = len(target.pages)

    ordered = sorted(set(targets))
    if not ordered:
        raise PDFEngineError("No pages were named for replacement.")
    outside = [p for p in ordered if p < 1 or p > total]
    if outside:
        raise PDFEngineError(
            f"Page(s) {', '.join(map(str, outside))} do not exist; the document "
            f"has {total}."
        )

    selected = _select(incoming, pages)
    if len(selected) != len(ordered):
        raise PDFEngineError(
            f"{len(ordered)} page(s) are being replaced but {len(selected)} "
            "replacement page(s) were supplied. The counts must match."
        )

    swap = dict(zip(ordered, selected))

    writer = PdfWriter()
    for number, page in enumerate(target.pages, start=1):
        writer.add_page(swap.get(number, page))

    return _write(writer)


def insert_blank(data: bytes, *, after: int, count: int = 1,
                 width: Optional[float] = None,
                 height: Optional[float] = None) -> bytes:
    """Add blank pages, matching the neighbouring page's size by default."""
    reader = _read(data)
    total = len(reader.pages)

    if after < 0 or after > total:
        raise PDFEngineError(
            f"Cannot insert after page {after}; the document has {total} page(s)."
        )
    if count < 1 or count > 100:
        raise PDFEngineError("Between 1 and 100 blank pages can be added at once.")

    if width is None or height is None:
        # Match the page it follows, or the first page when inserting at the
        # front, so a blank sheet does not arrive in a different size.
        reference = reader.pages[max(after - 1, 0)]
        width = float(reference.mediabox.width)
        height = float(reference.mediabox.height)

    writer = PdfWriter()
    for page in reader.pages[:after]:
        writer.add_page(page)
    for _ in range(count):
        writer.add_page(PageObject.create_blank_page(width=width, height=height))
    for page in reader.pages[after:]:
        writer.add_page(page)

    return _write(writer)


def _select(reader: PdfReader, pages: Optional[Sequence[int]]) -> List[PageObject]:
    """Pick pages from a source document, in the order requested."""
    total = len(reader.pages)
    if pages is None:
        return list(reader.pages)

    chosen = []
    for number in pages:
        if number < 1 or number > total:
            raise PDFEngineError(
                f"The source document has {total} page(s); page {number} was "
                "requested."
            )
        chosen.append(reader.pages[number - 1])

    if not chosen:
        raise PDFEngineError("No source pages were selected.")
    return chosen
