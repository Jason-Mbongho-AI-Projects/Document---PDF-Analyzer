"""
Editing the text of a PDF.

The claim being tested is strong — that text is genuinely replaced or removed
rather than covered over — so the checks read the result back rather than
trusting the operation. Placement is checked too: a replacement that lands on
the wrong baseline or at the wrong size is visibly wrong even when the
extracted characters are right.
"""
import io

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from docintel.pdf import textedit
from docintel.pdf.engine import PDFEngineError
from docintel.pdf.text import extract
from docintel.pdf.textedit import Edit, Style

SIZE = 14.0


def make_pdf(lines=None, size: float = SIZE) -> bytes:
    lines = lines or [
        "Invoice for Acme Corporation",
        "Amount due: 4200 USD",
        "Contact: billing@acme.example",
    ]
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont("Helvetica", size)
    y = 700
    for line in lines:
        pdf.drawString(72, y, line)
        y -= 30
    pdf.save()
    return buffer.getvalue()


def page_text(data: bytes, page: int = 1) -> str:
    return " ".join(p.text for p in extract(data) if p.page == page)


# --- finding ---------------------------------------------------------------

def test_finds_a_phrase_with_its_geometry():
    spots = textedit.find_text(make_pdf(), "Acme Corporation")

    assert len(spots) == 1
    assert spots[0].page == 1
    assert spots[0].width > 0 and spots[0].height > 0


def test_finding_is_case_insensitive():
    assert textedit.find_text(make_pdf(), "acme CORPORATION")


def test_missing_text_is_simply_not_found():
    assert textedit.find_text(make_pdf(), "Initech") == []


# --- replacing -------------------------------------------------------------

def test_replacement_removes_the_old_and_adds_the_new():
    out, _ = textedit.apply_edits(
        make_pdf(), [Edit(page=1, find="Acme Corporation", replace="Globex Limited")])

    text = page_text(out)
    assert "Globex Limited" in text
    assert "Acme Corporation" not in text


def test_the_old_text_is_gone_from_the_file_not_merely_hidden():
    """Covering text with a box leaves it extractable. This must not."""
    out, _ = textedit.apply_edits(
        make_pdf(), [Edit(page=1, find="Acme Corporation", replace="Globex Limited")])

    for page in extract(out):
        assert "Acme" not in page.text


def test_untouched_lines_are_left_alone():
    out, _ = textedit.apply_edits(
        make_pdf(), [Edit(page=1, find="Acme Corporation", replace="Globex Limited")])

    text = page_text(out)
    assert "Amount due" in text
    assert "billing@acme.example" in text


def test_replacement_lands_on_the_original_baseline():
    """Off by a descender is small in numbers and obvious on the page.

    Both strings carry a descender, so their reported boxes start at the same
    place and the comparison measures placement rather than glyph shape.
    """
    original = textedit.find_text(make_pdf(), "Acme Corporation")[0]
    out, _ = textedit.apply_edits(
        make_pdf(), [Edit(page=1, find="Acme Corporation", replace="Globex Property")])

    placed = textedit.find_text(out, "Globex Property")
    assert placed, "the replacement was not found in the output"

    assert abs(placed[0].y - original.y) < 1.0
    assert abs(placed[0].x - original.x) < 1.0


def test_the_original_point_size_is_recovered():
    """Inferred from width, so a run without descenders is measured correctly."""
    inferred = textedit._infer_size(
        textedit.find_text(make_pdf(), "Invoice for")[0], "Helvetica")

    assert abs(inferred - SIZE) < 0.5


def test_trailing_punctuation_does_not_prevent_a_match():
    """Selecting "Amount due" from "Amount due:" is the ordinary case."""
    assert textedit.find_text(make_pdf(), "Amount due")


def test_a_named_occurrence_leaves_the_others():
    data = make_pdf(["total total total"])
    out, _ = textedit.apply_edits(
        data, [Edit(page=1, find="total", replace="sum", occurrence=0)],
        verify=True)

    assert page_text(out).count("total") == 2
    assert "sum" in page_text(out)


# --- deleting --------------------------------------------------------------

def test_deleting_removes_the_text():
    out, _ = textedit.apply_edits(make_pdf(), [Edit(page=1, find="4200")])

    assert "4200" not in page_text(out)
    assert "Amount due" in page_text(out)


# --- adding ----------------------------------------------------------------

def test_added_text_is_readable():
    out = textedit.add_text(make_pdf(), 1, 72, 610, "PAID IN FULL")

    assert "PAID IN FULL" in page_text(out)


def test_adding_to_a_page_that_does_not_exist_is_refused():
    with pytest.raises(PDFEngineError, match="does not exist"):
        textedit.add_text(make_pdf(), 9, 72, 610, "nowhere")


def test_empty_text_is_refused():
    with pytest.raises(PDFEngineError, match="cannot be empty"):
        textedit.add_text(make_pdf(), 1, 72, 610, "   ")


# --- formatting ------------------------------------------------------------

def test_font_choice_is_honoured_and_reported():
    _, report = textedit.apply_edits(
        make_pdf(),
        [Edit(page=1, find="4200", replace="18500",
              style=Style(font="Times", bold=True))])

    assert report[0]["font"] == "Times-Bold"


def test_an_explicit_size_overrides_the_inferred_one():
    _, report = textedit.apply_edits(
        make_pdf(),
        [Edit(page=1, find="4200", replace="99", style=Style(size=30))])

    assert report[0]["size"] == 30


def test_an_explicit_size_is_never_silently_shrunk_to_fit():
    """A size the user typed is an instruction; warn instead of overriding."""
    _, report = textedit.apply_edits(
        make_pdf(),
        [Edit(page=1, find="4200", replace="18500", style=Style(size=30))])

    assert report[0]["size"] == 30
    assert report[0].get("shrunk") is not True
    assert report[0]["overflows"] is True


def test_a_wider_replacement_is_shrunk_to_fit():
    """PDF text does not reflow, so a longer replacement must be made to fit."""
    _, report = textedit.apply_edits(
        make_pdf(), [Edit(page=1, find="4200", replace="18500")])

    assert report[0]["shrunk"] is True
    assert report[0]["size"] < SIZE
    assert report[0]["overflows"] is False


def test_fitting_can_be_turned_off_and_then_warns():
    _, report = textedit.apply_edits(
        make_pdf(),
        [Edit(page=1, find="4200", replace="18500", fit_to_width=False)])

    assert report[0]["overflows"] is True
    assert "reflow" in report[0]["note"]


def test_an_unfittable_replacement_warns_rather_than_becoming_unreadable():
    """There is a floor: 6pt text in a 14pt line helps nobody."""
    _, report = textedit.apply_edits(
        make_pdf(),
        [Edit(page=1, find="4200", replace="4200000000000000000000")])

    assert report[0]["overflows"] is True
    assert report[0].get("shrunk") is not True


def test_following_text_keeps_its_position():
    """Blanking by character count shortens the line and drags text left.

    "4200" is four digits; four spaces are about half as wide, so the " USD"
    after it used to slide backwards into the replacement.
    """
    out, _ = textedit.apply_edits(
        make_pdf(), [Edit(page=1, find="4200", replace="8300")])

    before = textedit.find_text(make_pdf(), "USD")[0]
    after = textedit.find_text(out, "USD")[0]

    assert abs(after.x - before.x) < 2.0


def test_a_similar_length_replacement_is_not_flagged():
    _, report = textedit.apply_edits(
        make_pdf(), [Edit(page=1, find="4200", replace="8300")])

    assert report[0]["overflows"] is False


# --- refusals --------------------------------------------------------------

def test_text_that_is_not_there_is_reported_clearly():
    with pytest.raises(PDFEngineError, match="was not found"):
        textedit.apply_edits(make_pdf(), [Edit(page=1, find="Initech", replace="x")])


def test_an_out_of_range_occurrence_is_refused():
    with pytest.raises(PDFEngineError, match="does not exist"):
        textedit.apply_edits(
            make_pdf(),
            [Edit(page=1, find="Acme Corporation", replace="x", occurrence=5)])


def test_no_edits_is_refused():
    with pytest.raises(PDFEngineError, match="No edits"):
        textedit.apply_edits(make_pdf(), [])


def test_editing_one_page_does_not_touch_another():
    """Pages often share a content stream; stamping must not leak across.

    Without giving each page its own stream first, an overlay applied to page
    one can appear on every page that references the same stream object.
    """
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont("Helvetica", SIZE)
    pdf.drawString(72, 700, "Alpha heading")
    pdf.showPage()
    pdf.setFont("Helvetica", SIZE)
    pdf.drawString(72, 700, "Beta heading")
    pdf.showPage()
    pdf.save()

    out, _ = textedit.apply_edits(
        buffer.getvalue(),
        [Edit(page=1, find="Alpha heading", replace="Gamma heading")])

    pages = {p.page: p.text for p in extract(out)}
    assert "Gamma heading" in pages[1]
    assert "Gamma" not in pages[2]
    assert "Beta heading" in pages[2]


def test_the_source_document_is_not_mutated():
    data = make_pdf()
    before = bytes(data)
    textedit.apply_edits(data, [Edit(page=1, find="4200", replace="1")])

    assert data == before


# --- through the API -------------------------------------------------------

def test_edit_endpoint_creates_a_verified_version(alice):
    document = alice.upload(make_pdf(), "invoice.pdf").json()["document"]["id"]

    response = alice.post(
        f"/api/v1/documents/{document}/text/edit",
        json={"edits": [{"page": 1, "find": "Acme Corporation",
                         "replace": "Globex Limited"}]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"] == 2
    assert "verified" in body["note"]

    latest = alice.get(f"/api/v1/documents/{document}/download").content
    assert "Globex Limited" in page_text(latest)
    assert "Acme Corporation" not in page_text(latest)


def test_the_earlier_version_still_has_the_original_text(alice):
    """Editing appends; it must never destroy what was there."""
    document = alice.upload(make_pdf(), "invoice.pdf").json()["document"]["id"]
    alice.post(f"/api/v1/documents/{document}/text/edit",
               json={"edits": [{"page": 1, "find": "Acme Corporation",
                                "replace": "Globex Limited"}]})

    first = alice.get(f"/api/v1/documents/{document}/download?version=1").content
    assert "Acme Corporation" in page_text(first)


def test_find_endpoint_reports_positions(alice):
    document = alice.upload(make_pdf(), "invoice.pdf").json()["document"]["id"]

    response = alice.get(
        f"/api/v1/documents/{document}/text/find?q=Acme%20Corporation")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["occurrences"][0]["width"] > 0


def test_add_endpoint_creates_a_version(alice):
    document = alice.upload(make_pdf(), "invoice.pdf").json()["document"]["id"]

    response = alice.post(
        f"/api/v1/documents/{document}/text/add",
        json={"page": 1, "x": 72, "y": 600, "text": "APPROVED",
              "style": {"font": "Times", "size": 18, "colour": "#B91C1C"}},
    )

    assert response.status_code == 200, response.text
    latest = alice.get(f"/api/v1/documents/{document}/download").content
    assert "APPROVED" in page_text(latest)


def test_editing_missing_text_is_a_clean_error(alice):
    document = alice.upload(make_pdf(), "invoice.pdf").json()["document"]["id"]

    response = alice.post(
        f"/api/v1/documents/{document}/text/edit",
        json={"edits": [{"page": 1, "find": "Initech", "replace": "x"}]},
    )

    assert response.status_code == 400
    assert "not found" in response.json()["detail"]
