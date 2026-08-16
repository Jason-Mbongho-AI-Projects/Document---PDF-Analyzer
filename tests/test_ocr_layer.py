"""
Writing recognised text back into a PDF as a searchable layer.

Most of these need no OCR engine: the layer is built from word boxes, so
synthetic boxes exercise every property that matters. That keeps the important
guarantees — the text is extractable, the page still looks identical, and a
layer that cannot be read back is refused — testable on a machine with no
Tesseract at all.
"""
import io

import pytest
from PIL import Image, ImageChops
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from docintel.pdf import ocr, ocr_layer
from docintel.pdf.engine import PDFEngineError
from docintel.pdf.render import render_page
from docintel.pdf.text import extract

# 150 dpi Letter, the shape render_page produces at scale 3.0 for these tests.
IMAGE_SIZE = [1275, 1650]


def scanned_pdf(lines=("HELLO WORLD",)) -> bytes:
    """A page that is only an image — no text layer whatsoever."""
    image = Image.new("RGB", tuple(IMAGE_SIZE), "white")
    from PIL import ImageDraw
    draw = ImageDraw.Draw(image)
    y = 200
    for line in lines:
        draw.text((150, y), line, fill="black")
        y += 60

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.drawImage(ImageReader(image), 0, 0, width=letter[0], height=letter[1])
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def page_result(words, page=1):
    """An OcrResult-shaped page carrying synthetic word geometry."""
    return {
        "page": page,
        "text": " ".join(w["text"] for w in words),
        "words": words,
        "image_size": list(IMAGE_SIZE),
    }


def word(text, left=150, top=200, width=300, height=40):
    return {"text": text, "left": left, "top": top,
            "width": width, "height": height, "conf": 96.0}


def test_a_scan_starts_with_no_text():
    """Guard the premise: if this ever has text, the rest proves nothing."""
    pages = extract(scanned_pdf())
    assert sum(len(p.text.strip()) for p in pages) == 0


def test_written_text_becomes_extractable():
    data = scanned_pdf()
    out = ocr_layer.build(data, [page_result([word("TROPONIN")])])

    text = " ".join(p.text for p in extract(out))
    assert "TROPONIN" in text.upper()


def test_several_words_all_survive():
    words = [
        word("CONFIDENTIAL", left=150, top=200),
        word("MEDICAL", left=150, top=280),
        word("RECORD", left=150, top=360),
    ]
    out = ocr_layer.build(scanned_pdf(), [page_result(words)])

    text = " ".join(p.text for p in extract(out)).upper()
    for expected in ("CONFIDENTIAL", "MEDICAL", "RECORD"):
        assert expected in text


def test_the_layer_is_invisible():
    """The page must look exactly as it did — this is the whole point.

    A visible text layer would double-print over the scan.
    """
    data = scanned_pdf()
    out = ocr_layer.build(data, [page_result([word("INVISIBLE")])])

    before = Image.open(io.BytesIO(
        render_page(data, 1, scale=2.0, fmt="png").data)).convert("L")
    after = Image.open(io.BytesIO(
        render_page(out, 1, scale=2.0, fmt="png").data)).convert("L")

    assert ImageChops.difference(before, after).getbbox() is None


def test_words_land_near_their_box():
    """Position matters: search highlights and selection use these boxes."""
    out = ocr_layer.build(
        scanned_pdf(), [page_result([word("ANCHOR", left=150, top=200,
                                          width=300, height=40)])])

    found = [w for page in extract(out) for w in page.words
             if "ANCHOR" in w.text.upper()]
    assert found, "the word was not extracted at all"

    page = extract(out)[0]
    # top=200 of 1650px maps to ~12% down the page.
    expected_y = page.height * (200 / IMAGE_SIZE[1])
    actual_y = found[0].view_rect(page.height)["y"]
    assert abs(actual_y - expected_y) < page.height * 0.05


def test_pages_without_geometry_are_left_alone():
    data = scanned_pdf()
    out = ocr_layer.build(data, [
        page_result([word("KEPT")], page=1),
        {"page": 1, "text": "ignored", "words": [], "image_size": IMAGE_SIZE},
    ])

    assert "KEPT" in " ".join(p.text for p in extract(out)).upper()


def test_refuses_when_there_is_no_geometry_at_all():
    with pytest.raises(PDFEngineError, match="no text layer can be written"):
        ocr_layer.build(scanned_pdf(), [
            {"page": 1, "text": "x", "words": [], "image_size": IMAGE_SIZE},
        ])


def test_zero_sized_boxes_are_skipped_rather_than_crashing():
    words = [word("REAL"), word("GHOST", width=0, height=0)]
    out = ocr_layer.build(scanned_pdf(), [page_result(words)])

    assert "REAL" in " ".join(p.text for p in extract(out)).upper()


def test_verify_flags_a_page_that_stayed_empty():
    """An unreadable layer must be detectable, so it can be refused."""
    assert ocr_layer.verify(scanned_pdf(), [1]) == [1]


def test_verify_passes_a_page_that_now_has_text():
    words = [word("A" * 30, width=600)]
    out = ocr_layer.build(scanned_pdf(), [page_result(words)])

    assert ocr_layer.verify(out, [1]) == []


def test_original_is_not_mutated():
    data = scanned_pdf()
    before = bytes(data)
    ocr_layer.build(data, [page_result([word("X" * 10)])])

    assert data == before


# --- with a real engine, when one is installed ---------------------------

engine = ocr.get_provider()
needs_tesseract = pytest.mark.skipif(
    not engine.available, reason="no OCR engine installed",
)


@needs_tesseract
def test_end_to_end_recognise_then_write_back():
    data = scanned_pdf(("CONFIDENTIAL RECORD", "Troponin normal"))
    assert ocr.assess(data).classification == "no_text_layer"

    result = ocr.get_provider().recognise(data, language="eng")
    out = ocr_layer.build(data, result.pages)

    assert ocr.assess(out).classification == "native"
    assert ocr_layer.verify(out, [1]) == []
