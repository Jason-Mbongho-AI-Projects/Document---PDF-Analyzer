"""
The Acrobat-parity batch: page assembly, flattening, properties, bookmarks.

These close gaps found by comparing the API against Acrobat's tool set. The
one worth reading twice is flattening: annotations are stored in the database
and never written to the file, so a downloaded document silently had none of
its comments. Everything else here is composition or structure.
"""
import io

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from docintel.pdf import annots, assemble, properties
from docintel.pdf.engine import PDFEngineError
from docintel.pdf.text import extract


def make_pdf(label: str = "DOC", pages: int = 3) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    for number in range(1, pages + 1):
        pdf.setFont("Helvetica", 16)
        pdf.drawString(72, 700, f"{label} page {number}")
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def texts(data: bytes):
    return [p.text.strip().replace("\n", " ") for p in extract(data)]


# --- inserting -------------------------------------------------------------

def test_insert_places_pages_in_the_middle():
    out = assemble.insert_pages(make_pdf("MAIN", 3), make_pdf("OTHER", 2), after=1)

    assert texts(out) == ["MAIN page 1", "OTHER page 1", "OTHER page 2",
                          "MAIN page 2", "MAIN page 3"]


def test_insert_after_zero_puts_pages_first():
    out = assemble.insert_pages(make_pdf("MAIN", 2), make_pdf("OTHER", 1), after=0)

    assert texts(out)[0] == "OTHER page 1"


def test_insert_can_take_selected_source_pages_in_order():
    out = assemble.insert_pages(make_pdf("MAIN", 1), make_pdf("OTHER", 3),
                                after=1, pages=[3, 1])

    assert texts(out) == ["MAIN page 1", "OTHER page 3", "OTHER page 1"]


def test_insert_past_the_end_is_refused():
    with pytest.raises(PDFEngineError, match="Cannot insert after page"):
        assemble.insert_pages(make_pdf(pages=2), make_pdf(), after=9)


# --- replacing -------------------------------------------------------------

def test_replace_swaps_the_named_page():
    out = assemble.replace_pages(make_pdf("MAIN", 3), make_pdf("OTHER", 1),
                                 targets=[2], pages=[1])

    assert texts(out) == ["MAIN page 1", "OTHER page 1", "MAIN page 3"]


def test_replace_requires_matching_counts():
    """Guessing which page was meant where is how the wrong page ships."""
    with pytest.raises(PDFEngineError, match="counts must match"):
        assemble.replace_pages(make_pdf("MAIN", 3), make_pdf("OTHER", 3),
                               targets=[1, 2], pages=[1])


def test_replace_rejects_a_page_that_does_not_exist():
    with pytest.raises(PDFEngineError, match="do not exist"):
        assemble.replace_pages(make_pdf(pages=2), make_pdf(pages=1),
                               targets=[7], pages=[1])


# --- blank pages -----------------------------------------------------------

def test_blank_pages_are_added_and_match_the_page_size():
    source = make_pdf(pages=2)
    out = assemble.insert_blank(source, after=1, count=2)

    pages = extract(out)
    assert len(pages) == 4
    assert abs(pages[1].width - pages[0].width) < 0.5
    assert abs(pages[1].height - pages[0].height) < 0.5
    assert pages[1].text.strip() == ""


def test_an_absurd_number_of_blank_pages_is_refused():
    with pytest.raises(PDFEngineError, match="1 and 100"):
        assemble.insert_blank(make_pdf(), after=0, count=500)


# --- flattening annotations ------------------------------------------------

def annotation(kind: str, **extra) -> dict:
    base = {"kind": kind, "page": 1, "colour": "#FFD54F", "opacity": 1.0,
            "rect": {"x": 72, "y": 80, "width": 200, "height": 16},
            "quads": [], "body": None}
    base.update(extra)
    return base


def test_flattening_changes_the_page():
    """The marks have to actually reach the file, which is the whole point."""
    source = make_pdf(pages=1)
    out = annots.flatten(source, [annotation("highlight")])

    assert out != source
    assert len(extract(out)) == 1


def test_every_annotation_kind_can_be_drawn():
    source = make_pdf(pages=1)
    for kind in ("highlight", "underline", "strikethrough", "shape",
                 "arrow", "drawing", "note", "comment", "textbox", "stamp"):
        out = annots.flatten(source, [annotation(kind, body="a remark")])
        assert out.startswith(b"%PDF"), kind


def test_flattening_leaves_the_text_readable():
    """A highlight that paints over its text has destroyed the document."""
    source = make_pdf("READABLE", 1)
    out = annots.flatten(source, [annotation("highlight")])

    assert "READABLE page 1" in " ".join(p.text for p in extract(out))


def test_flattening_only_touches_the_annotated_page():
    out = annots.flatten(make_pdf("DOC", 3), [annotation("shape", page=2)])

    assert texts(out) == ["DOC page 1", "DOC page 2", "DOC page 3"]


def test_flattening_nothing_is_refused():
    with pytest.raises(PDFEngineError, match="no annotations"):
        annots.flatten(make_pdf(), [])


# --- properties ------------------------------------------------------------

def test_properties_report_pages_and_geometry():
    result = properties.read_properties(make_pdf(pages=3))

    assert result["page_count"] == 3
    assert result["pages"][0]["width"] == 612.0
    assert result["encrypted"] is False


def test_metadata_can_be_set_and_read_back():
    out = properties.set_metadata(make_pdf(), {"title": "Board Pack",
                                               "author": "Finance"})
    meta = properties.read_properties(out)["metadata"]

    assert meta["title"] == "Board Pack"
    assert meta["author"] == "Finance"


def test_an_unknown_property_is_refused_by_name():
    with pytest.raises(PDFEngineError, match="Unknown propert"):
        properties.set_metadata(make_pdf(), {"colour": "blue"})


def test_sanitise_removes_metadata_and_says_what_it_removed():
    withmeta = properties.set_metadata(make_pdf(), {"title": "Confidential"})
    clean, removed = properties.sanitise(withmeta)

    assert removed, "nothing was reported as removed"
    assert properties.read_properties(clean)["metadata"]["title"] == ""


def test_sanitise_reports_honestly_when_there_was_nothing_to_remove():
    """A reassuring message either way would be worthless."""
    once, _ = properties.sanitise(make_pdf())
    _, second = properties.sanitise(once)

    assert second == []


# --- bookmarks -------------------------------------------------------------

def test_bookmarks_round_trip_with_their_nesting():
    out = properties.set_outline(make_pdf(pages=3), [
        {"title": "Summary", "page": 1, "depth": 0},
        {"title": "Detail", "page": 2, "depth": 1},
        {"title": "Appendix", "page": 3, "depth": 0},
    ])

    entries = properties.read_outline(out)
    assert [e["title"] for e in entries] == ["Summary", "Detail", "Appendix"]
    assert [e["page"] for e in entries] == [1, 2, 3]
    assert entries[1]["depth"] == 1


def test_setting_bookmarks_replaces_rather_than_appends():
    once = properties.set_outline(make_pdf(pages=2),
                                  [{"title": "First", "page": 1, "depth": 0}])
    twice = properties.set_outline(once,
                                   [{"title": "Second", "page": 2, "depth": 0}])

    assert [e["title"] for e in properties.read_outline(twice)] == ["Second"]


def test_a_bookmark_pointing_off_the_end_is_refused():
    with pytest.raises(PDFEngineError, match="points at page"):
        properties.set_outline(make_pdf(pages=2),
                               [{"title": "Nowhere", "page": 9, "depth": 0}])


# --- through the API -------------------------------------------------------

def test_insert_endpoint_uses_another_document(alice):
    main = alice.upload(make_pdf("MAIN", 2), "main.pdf").json()["document"]["id"]
    other = alice.upload(make_pdf("OTHER", 1), "other.pdf").json()["document"]["id"]

    response = alice.post(f"/api/v1/documents/{main}/pages/insert",
                          json={"source_document_id": other, "after": 1})

    assert response.status_code == 200, response.text
    latest = alice.get(f"/api/v1/documents/{main}/download").content
    assert texts(latest) == ["MAIN page 1", "OTHER page 1", "MAIN page 2"]


def test_cannot_insert_from_another_tenants_document(alice, bob):
    """Naming a document must not become a way to read it."""
    mine = alice.upload(make_pdf("MINE", 1), "mine.pdf").json()["document"]["id"]
    theirs = bob.upload(make_pdf("SECRET", 1), "secret.pdf").json()["document"]["id"]

    response = alice.post(f"/api/v1/documents/{mine}/pages/insert",
                          json={"source_document_id": theirs, "after": 1})

    assert response.status_code == 404


def test_flatten_endpoint_needs_annotations(alice):
    document = alice.upload(make_pdf(), "doc.pdf").json()["document"]["id"]

    response = alice.post(f"/api/v1/documents/{document}/annotations/flatten")

    assert response.status_code == 400
    assert "no annotations" in response.json()["detail"]


def test_flatten_endpoint_writes_stored_annotations(alice):
    document = alice.upload(make_pdf(), "doc.pdf").json()["document"]["id"]
    created = alice.post(f"/api/v1/documents/{document}/annotations", json={
        "kind": "highlight", "page": 1,
        "rect": {"x": 72, "y": 80, "width": 200, "height": 16},
        "quads": [{"x": 72, "y": 80, "width": 200, "height": 16}],
        "colour": "#FFD54F",
    })
    assert created.status_code in (200, 201), created.text

    response = alice.post(f"/api/v1/documents/{document}/annotations/flatten")

    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    assert "written into the page" in response.json()["note"]


def test_path_annotations_are_stored_as_points(alice):
    """Arrows and freehand are runs of positions, not rectangles.

    They used to be sent as quads and rejected: a quad must have width and
    height, and a point has neither, so the mark silently never appeared.
    """
    document = alice.upload(make_pdf(), "doc.pdf").json()["document"]["id"]

    response = alice.post(f"/api/v1/documents/{document}/annotations", json={
        "kind": "arrow", "page": 1, "colour": "#1D4ED8",
        "points": [{"x": 100, "y": 120}, {"x": 260, "y": 210}],
    })

    assert response.status_code in (200, 201), response.text
    stored = response.json()
    assert len(stored["quads"]) == 2
    assert stored["quads"][0]["x"] == 100


def test_a_freehand_stroke_keeps_its_order(alice):
    document = alice.upload(make_pdf(), "doc.pdf").json()["document"]["id"]
    path = [{"x": 10 + i * 5, "y": 40 + i} for i in range(12)]

    response = alice.post(f"/api/v1/documents/{document}/annotations", json={
        "kind": "drawing", "page": 1, "colour": "#BE123C", "points": path,
    })

    quads = response.json()["quads"]
    assert [q["x"] for q in quads] == [p["x"] for p in path]


def test_a_rectangle_annotation_still_requires_a_size(alice):
    """Loosening points must not loosen areas: a zero-size highlight is a bug."""
    document = alice.upload(make_pdf(), "doc.pdf").json()["document"]["id"]

    response = alice.post(f"/api/v1/documents/{document}/annotations", json={
        "kind": "highlight", "page": 1,
        "rect": {"x": 10, "y": 10, "width": 0, "height": 0},
    })

    assert response.status_code == 422


def test_drawn_marks_flatten_into_the_file(alice):
    """The whole point of drawing them is that they can leave the app."""
    document = alice.upload(make_pdf(), "doc.pdf").json()["document"]["id"]
    alice.post(f"/api/v1/documents/{document}/annotations", json={
        "kind": "arrow", "page": 1, "colour": "#1D4ED8",
        "points": [{"x": 100, "y": 120}, {"x": 260, "y": 210}]})
    alice.post(f"/api/v1/documents/{document}/annotations", json={
        "kind": "drawing", "page": 1, "colour": "#BE123C",
        "points": [{"x": 300 + i * 4, "y": 300} for i in range(10)]})

    response = alice.post(f"/api/v1/documents/{document}/annotations/flatten")

    assert response.status_code == 200, response.text
    before = alice.get(f"/api/v1/documents/{document}/download?version=1").content
    after = alice.get(f"/api/v1/documents/{document}/download").content
    assert after != before


def test_properties_endpoint_reports_hidden_data(alice):
    document = alice.upload(make_pdf(), "doc.pdf").json()["document"]["id"]

    body = alice.get(f"/api/v1/documents/{document}/properties").json()

    assert body["page_count"] == 3
    assert "hidden_data" in body
    assert set(body["metadata"]) >= {"title", "author"}


def test_sanitise_endpoint_creates_a_version(alice):
    document = alice.upload(make_pdf(), "doc.pdf").json()["document"]["id"]
    alice.post(f"/api/v1/documents/{document}/properties",
               json={"title": "Internal only"})

    response = alice.post(f"/api/v1/documents/{document}/sanitise", json={})

    assert response.status_code == 200, response.text
    latest = alice.get(f"/api/v1/documents/{document}/download").content
    assert properties.read_properties(latest)["metadata"]["title"] == ""


def test_outline_endpoint_round_trips(alice):
    document = alice.upload(make_pdf(pages=2), "doc.pdf").json()["document"]["id"]

    saved = alice.post(f"/api/v1/documents/{document}/outline", json={
        "entries": [{"title": "Start", "page": 1, "depth": 0},
                    {"title": "Middle", "page": 2, "depth": 1}]})
    assert saved.status_code == 200, saved.text

    entries = alice.get(f"/api/v1/documents/{document}/outline").json()["entries"]
    assert [e["title"] for e in entries] == ["Start", "Middle"]
