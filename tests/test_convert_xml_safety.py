"""
Exports must survive characters that XML cannot carry.

DOCX, XLSX and PPTX are ZIP archives full of XML, and the libraries that
write them reject a string containing a forbidden character outright — the
whole export fails, with a message about XML compatibility that tells the
user nothing about their document.

Extracted PDF text contains such characters more often than one would expect:
a glyph that fails to map to Unicode commonly comes back as U+FFFE. A single
one of those in a fifty-page report used to make Word and PowerPoint export
impossible while every other format worked.
"""
import io

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from docintel.pdf.convert import convert, xml_safe

# Everything XML forbids: C0 controls, the noncharacters, a lone surrogate.
FORBIDDEN = "\x00\x01\x08\x0b\x0c\x1f\ufffe\uffff"


def test_forbidden_characters_are_removed():
    assert xml_safe("defense\ufffeonly") == "defenseonly"
    assert xml_safe(f"a{FORBIDDEN}b") == "ab"


def test_ordinary_text_is_untouched():
    """Tabs, newlines and returns are legal and must survive."""
    for value in ("plain", "tab\tsep", "line\nbreak", "carriage\rreturn",
                  "accented café", "emoji 🎯", "chinese 中文"):
        assert xml_safe(value) == value


def test_none_becomes_empty_rather_than_the_word_none():
    assert xml_safe(None) == ""


def make_pdf_with(text: str) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont("Helvetica", 13)
    pdf.drawString(72, 700, text)
    pdf.drawString(72, 670, "Ordinary second line.")
    pdf.save()
    return buffer.getvalue()


@pytest.mark.parametrize("target", ["docx", "pptx", "txt", "html", "json"])
def test_every_export_survives_a_forbidden_character(target, monkeypatch):
    """The character is injected at extraction, where it really comes from."""
    from docintel.pdf import convert as convert_module

    data = make_pdf_with("Quarterly report")
    original = convert_module.extract

    def poisoned(payload, *args, **kwargs):
        pages = original(payload, *args, **kwargs)
        for page in pages:
            page.text = page.text.replace("Quarterly", "Quarter\ufffely")
        return pages

    monkeypatch.setattr(convert_module, "extract", poisoned)

    result = convert(data, target)
    assert result.data, f"{target} produced nothing"


def test_the_docx_still_contains_the_surrounding_text(monkeypatch):
    """Dropping the bad character must not drop the sentence with it."""
    from docx import Document

    from docintel.pdf import convert as convert_module

    data = make_pdf_with("Quarterly report")
    original = convert_module.extract

    def poisoned(payload, *args, **kwargs):
        pages = original(payload, *args, **kwargs)
        for page in pages:
            page.text = page.text.replace("Quarterly", "Quarter\ufffely")
        return pages

    monkeypatch.setattr(convert_module, "extract", poisoned)

    document = Document(io.BytesIO(convert(data, "docx").data))
    body = " ".join(p.text for p in document.paragraphs)

    assert "Quarterly report" in body          # the character simply vanishes
    assert "Ordinary second line." in body
    assert "\ufffe" not in body


def test_spreadsheet_cells_are_sanitised():
    """XLSX has no table in the fixture above, so its path is checked here.

    Every cell reaches openpyxl through _coerce, and openpyxl rejects
    XML-invalid text exactly as python-docx does.
    """
    from docintel.pdf.convert import _coerce

    assert _coerce("total￾") == "total"
    assert _coerce("  1234  ") == 1234          # numbers still coerce
    assert _coerce(None) == ""
