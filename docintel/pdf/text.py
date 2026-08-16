"""
Text extraction with coordinates.

PDFium gives per-character bounding boxes, which is what everything
position-aware is built on: browser text selection, highlight alignment,
in-document search, and redaction that knows exactly which glyphs to remove.

Coordinates are reported in two systems, because both are needed:

  pdf_rect     origin bottom-left, y increasing upward — the PDF convention,
               used by the engine when it writes back into a document
  view_rect    origin top-left, y increasing downward — the browser/canvas
               convention, used directly by a viewer overlay

Converting once here means neither the frontend nor the redaction code has to
remember which way up a page is.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

import pypdfium2 as pdfium

from docintel.pdf.engine import PDFEngineError, PasswordRequired

# A run of characters is split into words on whitespace, and also when the
# horizontal gap exceeds this multiple of the character height — some PDFs
# encode spaces as positioning rather than as space characters.
GAP_RATIO = 0.28


@dataclass
class Word:
    text: str
    page: int
    start: int              # character index within the page
    end: int                # exclusive
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def pdf_rect(self) -> Dict[str, float]:
        return {"x": self.x0, "y": self.y0,
                "width": self.x1 - self.x0, "height": self.y1 - self.y0}

    def view_rect(self, page_height: float) -> Dict[str, float]:
        return {"x": self.x0, "y": page_height - self.y1,
                "width": self.x1 - self.x0, "height": self.y1 - self.y0}


@dataclass
class PageText:
    page: int
    width: float
    height: float
    text: str
    words: List[Word] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "page": self.page,
            "width": round(self.width, 2),
            "height": round(self.height, 2),
            "text": self.text,
            "words": [
                {
                    "text": w.text,
                    "start": w.start,
                    "end": w.end,
                    "pdf_rect": {k: round(v, 2) for k, v in w.pdf_rect.items()},
                    "view_rect": {k: round(v, 2)
                                  for k, v in w.view_rect(self.height).items()},
                }
                for w in self.words
            ],
        }


@dataclass
class Match:
    page: int
    start: int
    end: int
    text: str
    context: str
    rects: List[Dict[str, float]]        # view coordinates, one per line


def _open(data: bytes, password: Optional[str] = None) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(data, password=password)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            raise PasswordRequired("This PDF is password protected.") from exc
        raise PDFEngineError(f"The document could not be read: {exc}") from exc
    except Exception as exc:
        raise PDFEngineError(f"The document could not be read: {exc}") from exc


def _group_words(textpage, page_number: int) -> Tuple[str, List[Word]]:
    """Walk the character stream, grouping glyphs into words with boxes."""
    count = textpage.count_chars()
    if count == 0:
        return "", []

    full_text = textpage.get_text_range() or ""
    words: List[Word] = []

    current: List[str] = []
    start_index = 0
    box: Optional[List[float]] = None
    previous_right: Optional[float] = None

    def flush(end_index: int) -> None:
        nonlocal current, box, previous_right
        if current and box:
            text = "".join(current)
            if text.strip():
                words.append(Word(
                    text=text, page=page_number,
                    start=start_index, end=end_index,
                    x0=box[0], y0=box[1], x1=box[2], y1=box[3],
                ))
        current, box, previous_right = [], None, None

    for index in range(count):
        try:
            char = textpage.get_text_range(index, 1)
        except Exception:
            continue
        if not char:
            continue

        if char.isspace() or unicodedata.category(char[0]) in ("Zs", "Zl", "Zp", "Cc"):
            flush(index)
            continue

        try:
            left, bottom, right, top = textpage.get_charbox(index)
        except Exception:
            continue

        # A wide horizontal gap or a change of line starts a new word even
        # without an explicit space character.
        if box is not None:
            height = max(box[3] - box[1], 1.0)
            gap = left - previous_right if previous_right is not None else 0.0
            new_line = abs(bottom - box[1]) > height * 0.6
            if new_line or gap > height * GAP_RATIO:
                flush(index)

        if box is None:
            start_index = index
            box = [left, bottom, right, top]
        else:
            box[0] = min(box[0], left)
            box[1] = min(box[1], bottom)
            box[2] = max(box[2], right)
            box[3] = max(box[3], top)

        current.append(char)
        previous_right = right

    flush(count)
    return full_text, words


def extract(data: bytes, pages: Optional[List[int]] = None,
            password: Optional[str] = None) -> List[PageText]:
    """Extract text and word geometry, one page at a time."""
    pdf = _open(data, password)
    try:
        total = len(pdf)
        targets = pages or list(range(1, total + 1))

        results: List[PageText] = []
        for number in targets:
            if number < 1 or number > total:
                raise PDFEngineError(f"Page {number} is out of range (1..{total}).")

            page = pdf[number - 1]
            textpage = page.get_textpage()
            try:
                text, words = _group_words(textpage, number)
            finally:
                textpage.close()

            results.append(PageText(
                page=number,
                width=page.get_width(),
                height=page.get_height(),
                text=text,
                words=words,
            ))
        return results
    finally:
        pdf.close()


def plain_text(data: bytes, password: Optional[str] = None) -> str:
    return "\n\n".join(p.text for p in extract(data, password=password))


def search(data: bytes, query: str, *, case_sensitive: bool = False,
           whole_words: bool = False, max_results: int = 500,
           password: Optional[str] = None) -> List[Match]:
    """Find a string, returning highlight rectangles in view coordinates."""
    if not query.strip():
        raise PDFEngineError("Search text cannot be empty.")

    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.escape(query)
    if whole_words:
        pattern = rf"\b{pattern}\b"
    expression = re.compile(pattern, flags)

    matches: List[Match] = []
    for page in extract(data, password=password):
        for found in expression.finditer(page.text):
            if len(matches) >= max_results:
                return matches

            start, end = found.start(), found.end()
            covered = [w for w in page.words if w.start < end and w.end > start]

            # Group the covering words into per-line rectangles so a match
            # that wraps produces one box per line, like a real selection.
            rects: List[Dict[str, float]] = []
            for word in covered:
                rect = word.view_rect(page.height)
                merged = False
                for existing in rects:
                    if abs(existing["y"] - rect["y"]) < rect["height"] * 0.6:
                        right = max(existing["x"] + existing["width"],
                                    rect["x"] + rect["width"])
                        existing["x"] = min(existing["x"], rect["x"])
                        existing["width"] = right - existing["x"]
                        existing["height"] = max(existing["height"], rect["height"])
                        merged = True
                        break
                if not merged:
                    rects.append(dict(rect))

            left = max(start - 40, 0)
            right = min(end + 40, len(page.text))

            matches.append(Match(
                page=page.page, start=start, end=end,
                text=page.text[start:end],
                context=" ".join(page.text[left:right].split()),
                rects=[{k: round(v, 2) for k, v in r.items()} for r in rects],
            ))

    return matches
