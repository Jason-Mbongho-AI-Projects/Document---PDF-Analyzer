"""
Write recognised text back into the PDF as a searchable layer.

OCR that only returns a transcript leaves the document exactly as unusable as
it was: search finds nothing, the AI features see an empty document, and the
text cannot be selected. The recognised words have to go back onto the page.

They are drawn in text render mode 3 — present in the content stream, part of
the text layer, and not painted. The scan itself remains the only thing
visible, so the page looks untouched while becoming selectable and searchable.

Positions come from Tesseract in pixels of the rendered image; they are scaled
to PDF points against each page's own mediabox, so a document mixing page
sizes stays aligned.
"""
from __future__ import annotations

import io
from typing import Dict, List, Optional, Sequence

from pypdf import PdfReader

from docintel.pdf.engine import PDFEngineError
from docintel.pdf.operations import PyPdfEngine

# Below this the box is noise rather than a word, and a font size of zero
# raises in reportlab.
MIN_BOX = 1.0


def _fit_font(canvas, word: str, width_pt: float, height_pt: float) -> float:
    """Font size that makes `word` fill its box without overflowing it.

    Tesseract reports the box the word occupies in the image. Drawing at a
    size derived from the box height alone leaves wide words spilling past
    their neighbours, which matters because selection and search use these
    positions.
    """
    size = max(height_pt * 0.85, 1.0)
    natural = canvas.stringWidth(word, "Helvetica", size)
    if natural > 0 and width_pt > 0:
        size = min(size, size * (width_pt / natural))
    return max(size, 1.0)


def build(
    data: bytes,
    pages: Sequence[Dict[str, object]],
    engine: Optional[PyPdfEngine] = None,
) -> bytes:
    """Return `data` with an invisible text layer added for the given pages.

    `pages` are OcrResult.pages entries, each carrying `words` and
    `image_size`. Pages without word geometry are left untouched rather than
    guessed at.
    """
    engine = engine or PyPdfEngine()

    by_number: Dict[int, Dict[str, object]] = {}
    for page in pages:
        words = page.get("words") or []
        size = page.get("image_size") or []
        if words and len(size) == 2 and all(size):
            by_number[int(page["page"])] = page

    if not by_number:
        raise PDFEngineError(
            "No word positions were produced, so no text layer can be written."
        )

    def make_overlay(index: int, width: float, height: float):
        page = by_number[index]
        image_width, image_height = page["image_size"]        # type: ignore[misc]
        scale_x = width / float(image_width)
        scale_y = height / float(image_height)

        def draw(pdf):
            # Mode 3 renders nothing while keeping the glyphs in the text
            # layer — this single call is what makes the layer invisible. No
            # alpha is set alongside it: that would leave a transparency state
            # in the merged content stream for no additional benefit.
            text_object = pdf.beginText()
            text_object.setTextRenderMode(3)

            for word in page["words"]:                        # type: ignore[index]
                box_width = float(word["width"]) * scale_x
                box_height = float(word["height"]) * scale_y
                if box_width < MIN_BOX or box_height < MIN_BOX:
                    continue

                size = _fit_font(pdf, str(word["text"]), box_width, box_height)
                x = float(word["left"]) * scale_x
                # PDF space has its origin bottom-left; Tesseract measures from
                # the top. The baseline sits just inside the bottom of the box.
                y = height - (float(word["top"]) + float(word["height"])) * scale_y
                y += box_height * 0.15

                text_object.setFont("Helvetica", size)
                text_object.setTextOrigin(x, y)
                text_object.textOut(str(word["text"]))

            pdf.drawText(text_object)

        return engine._overlay(width, height, draw)

    return engine._compose(
        data,
        should_stamp=lambda index: index in by_number,
        make_overlay=make_overlay,
        over=True,
    )


def verify(data: bytes, pages: Sequence[int]) -> List[int]:
    """Return the pages that still have no extractable text.

    A text layer that cannot be read back is worse than none: the document
    would be reported as searchable while every downstream feature still sees
    an empty page. Callers use this to refuse a bad result.
    """
    from docintel.pdf.text import extract

    extracted = {p.page: p.text for p in extract(data)}
    return [n for n in pages if len((extracted.get(n) or "").strip()) < 20]
