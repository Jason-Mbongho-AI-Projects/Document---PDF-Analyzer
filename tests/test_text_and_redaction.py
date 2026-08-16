"""
Text extraction with coordinates, search, and redaction.

The redaction tests assert the strongest available property: after redaction
the text must be absent from the extracted text AND from the raw file bytes.
"""
import io

import pytest
from reportlab.pdfgen import canvas

import pdf_corpus as corpus
from docintel.pdf import redact, text as text_tools
from docintel.pdf.engine import PDFEngineError


def sensitive_pdf() -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(612, 792))
    pdf.setFont("Helvetica", 12)
    pdf.drawString(72, 720, "Contact: alice@example.com or call 555-0134")
    pdf.drawString(72, 700, "Card 4111 1111 1111 1111 expires 2027")
    pdf.drawString(72, 680, "Server at 192.168.1.50 hosts the archive")
    pdf.drawString(72, 660, "Ordinary sentence that must survive redaction.")
    pdf.save()
    return buffer.getvalue()


def content_streams(data: bytes) -> bytes:
    """All page content streams, decompressed.

    Searching the raw file is not a valid test on its own: streams are usually
    Flate-compressed, so a plaintext search would "pass" even on a completely
    unredacted document. Decompressing first makes the assertion mean
    something.
    """
    import pikepdf

    chunks = []
    with pikepdf.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            contents = page.obj.get("/Contents")
            if contents is None:
                continue
            items = contents if isinstance(contents, pikepdf.Array) else [contents]
            for item in items:
                try:
                    chunks.append(bytes(item.read_bytes()))
                except Exception:
                    continue
    return b"\n".join(chunks)


# --------------------------------------------------------- extraction

def test_extract_returns_words_with_geometry():
    pages = text_tools.extract(corpus.clean_pdf())
    assert len(pages) == 1

    page = pages[0]
    assert "Sample document text." in page.text
    assert [w.text for w in page.words][:2] == ["Sample", "document"]

    word = page.words[0]
    assert word.x1 > word.x0 and word.y1 > word.y0


def test_view_and_pdf_coordinates_are_consistent():
    """view.y must be the page height minus the PDF top edge."""
    page = text_tools.extract(corpus.clean_pdf())[0]
    word = page.words[0]

    view = word.view_rect(page.height)
    assert view["y"] == pytest.approx(page.height - word.y1, abs=0.01)
    assert view["x"] == pytest.approx(word.x0, abs=0.01)
    assert view["height"] == pytest.approx(word.y1 - word.y0, abs=0.01)


def test_as_dict_carries_both_coordinate_systems():
    payload = text_tools.extract(corpus.clean_pdf())[0].as_dict()
    word = payload["words"][0]
    assert set(word["pdf_rect"]) == {"x", "y", "width", "height"}
    assert set(word["view_rect"]) == {"x", "y", "width", "height"}
    assert word["pdf_rect"]["y"] != word["view_rect"]["y"]


def test_extract_specific_pages_only():
    pages = text_tools.extract(corpus.multipage_pdf(5), pages=[2, 4])
    assert [p.page for p in pages] == [2, 4]


def test_extract_out_of_range_page_is_rejected():
    with pytest.raises(PDFEngineError, match="out of range"):
        text_tools.extract(corpus.multipage_pdf(2), pages=[9])


def test_page_with_no_text_returns_empty():
    page = text_tools.extract(corpus.empty_text_pdf())[0]
    assert page.words == []


# ------------------------------------------------------------- search

def test_search_finds_matches_on_every_page():
    matches = text_tools.search(corpus.multipage_pdf(3), "document")
    assert [m.page for m in matches] == [1, 2, 3]
    assert all(m.rects for m in matches)


def test_search_is_case_insensitive_by_default():
    assert text_tools.search(corpus.clean_pdf(), "SAMPLE")
    assert not text_tools.search(corpus.clean_pdf(), "SAMPLE", case_sensitive=True)


def test_whole_word_search():
    assert text_tools.search(corpus.clean_pdf(), "sent")
    assert not text_tools.search(corpus.clean_pdf(), "sent", whole_words=True)


def test_search_returns_context_and_rects():
    match = text_tools.search(corpus.clean_pdf(), "document")[0]
    assert "Sample" in match.context
    assert match.rects and match.rects[0]["width"] > 0


def test_empty_search_is_rejected():
    with pytest.raises(PDFEngineError, match="cannot be empty"):
        text_tools.search(corpus.clean_pdf(), "   ")


# ---------------------------------------------------------- detection

def test_detects_common_sensitive_types():
    kinds = {c.kind for c in redact.detect(sensitive_pdf())}
    assert {"email", "credit_card", "ip_address"} <= kinds


def test_detection_does_not_modify_the_document():
    source = sensitive_pdf()
    redact.detect(source)
    assert text_tools.plain_text(source).count("alice@example.com") == 1


def test_luhn_filters_non_card_numbers():
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(612, 792))
    pdf.setFont("Helvetica", 12)
    pdf.drawString(72, 720, "Reference 1234 5678 9012 3456 is not a card")
    pdf.save()

    cards = [c for c in redact.detect(buffer.getvalue()) if c.kind == "credit_card"]
    assert cards == []


def test_custom_terms_and_regex():
    found = redact.detect(sensitive_pdf(), kinds=[], custom_terms=["Ordinary"])
    assert any(c.text == "Ordinary" for c in found)

    found = redact.detect(sensitive_pdf(), kinds=[], custom_regex=r"expires \d{4}")
    assert any("expires 2027" in c.text for c in found)


def test_unknown_detector_is_rejected():
    with pytest.raises(PDFEngineError, match="Unknown detector"):
        redact.detect(sensitive_pdf(), kinds=["telepathy"])


def test_invalid_regex_is_rejected():
    with pytest.raises(PDFEngineError, match="Invalid regular expression"):
        redact.detect(sensitive_pdf(), custom_regex="([unclosed")


# ---------------------------------------------------------- redaction

def test_redaction_removes_text_from_extraction():
    source = sensitive_pdf()
    targets = redact.detect(source)
    output = redact.apply(source, targets)

    remaining = text_tools.plain_text(output).lower()
    for candidate in targets:
        assert candidate.text.lower() not in remaining


def test_redaction_removes_text_from_the_content_stream():
    """The strongest check: the glyphs are gone from the page's own
    instruction stream, not merely hidden behind a rectangle."""
    source = sensitive_pdf()
    targets = [c for c in redact.detect(source) if c.kind == "email"]
    output = redact.apply(source, targets)

    # Precondition: it really is there before redaction.
    assert b"alice@example.com" in content_streams(source)
    # And genuinely gone after.
    assert b"alice@example.com" not in content_streams(output)


def test_drawing_a_box_alone_would_not_pass_this_test():
    """Guards the guard: covering without removing must be detectable."""
    source = sensitive_pdf()
    targets = [c for c in redact.detect(source) if c.kind == "email"]

    # draw_boxes only, removal disabled by stubbing the rewrite to a no-op.
    import pytest as _pytest
    with _pytest.MonkeyPatch.context() as patch:
        patch.setattr(redact, "_rewrite_instructions",
                      lambda instructions, terms: ([], False))
        with _pytest.raises(PDFEngineError, match="failed verification"):
            redact.apply(source, targets, draw_boxes=True)


def test_redaction_preserves_surrounding_content():
    source = sensitive_pdf()
    targets = [c for c in redact.detect(source) if c.kind == "email"]
    output = redact.apply(source, targets)

    text = text_tools.plain_text(output)
    assert "Ordinary sentence that must survive redaction." in text
    assert "Card" in text


def test_redacted_document_still_opens_and_keeps_its_pages():
    from pypdf import PdfReader
    source = sensitive_pdf()
    output = redact.apply(source, redact.detect(source))
    assert len(PdfReader(io.BytesIO(output)).pages) == 1


def test_verification_rejects_a_failed_redaction(monkeypatch):
    """If removal silently does nothing, apply() must raise, not return."""
    source = sensitive_pdf()
    targets = [c for c in redact.detect(source) if c.kind == "email"]

    # Simulate a stream rewrite that does not actually change anything.
    monkeypatch.setattr(redact, "_rewrite_instructions",
                        lambda instructions, terms: ([], False))

    with pytest.raises(PDFEngineError, match="failed verification"):
        redact.apply(source, targets)


def test_empty_target_list_is_rejected():
    with pytest.raises(PDFEngineError, match="No redaction targets"):
        redact.apply(sensitive_pdf(), [])


def test_redaction_on_out_of_range_page_is_rejected():
    target = redact.Candidate("manual", "anything", page=9, start=0, end=1)
    with pytest.raises(PDFEngineError, match="out of range"):
        redact.apply(sensitive_pdf(), [target])


# ---------------------------------------------------------------- API

@pytest.fixture
def sensitive_doc(alice):
    response = alice.upload(sensitive_pdf(), name="contact.pdf")
    return response.json()["document"]["id"]


def test_text_endpoint_returns_word_geometry(alice, sensitive_doc):
    body = alice.get(f"/api/v1/documents/{sensitive_doc}/text").json()
    page = body["pages"][0]
    assert page["words"]
    assert set(page["words"][0]["view_rect"]) == {"x", "y", "width", "height"}


def test_search_endpoint(alice, sensitive_doc):
    body = alice.get(f"/api/v1/documents/{sensitive_doc}/search?q=archive").json()
    assert body["total"] == 1
    assert body["matches"][0]["rects"]


def test_detect_endpoint_changes_nothing(alice, sensitive_doc):
    before = alice.get(f"/api/v1/documents/{sensitive_doc}/download").content
    body = alice.post(f"/api/v1/documents/{sensitive_doc}/redact/detect", json={}).json()

    assert body["total"] > 0
    assert "Nothing has been changed" in body["note"]
    after = alice.get(f"/api/v1/documents/{sensitive_doc}/download").content
    assert before == after


def test_apply_redaction_endpoint(alice, sensitive_doc):
    candidates = alice.post(
        f"/api/v1/documents/{sensitive_doc}/redact/detect", json={}
    ).json()["candidates"]
    emails = [c for c in candidates if c["kind"] == "email"]

    response = alice.post(f"/api/v1/documents/{sensitive_doc}/redact/apply",
                          json={"targets": emails})
    assert response.status_code == 200
    assert response.json()["verified"] is True

    redacted = alice.get(f"/api/v1/documents/{sensitive_doc}/download").content
    assert b"alice@example.com" not in content_streams(redacted)

    # The source version is untouched, and the response says so.
    original = alice.get(f"/api/v1/documents/{sensitive_doc}/download?version=1").content
    assert b"alice@example.com" in content_streams(original)
    assert "Earlier versions" in response.json()["note"]


def test_redaction_audit_does_not_record_the_redacted_text(alice, sensitive_doc, db):
    from docintel.db.models import AuditLog

    candidates = alice.post(
        f"/api/v1/documents/{sensitive_doc}/redact/detect", json={}
    ).json()["candidates"]
    emails = [c for c in candidates if c["kind"] == "email"]
    alice.post(f"/api/v1/documents/{sensitive_doc}/redact/apply", json={"targets": emails})

    entries = db.query(AuditLog).filter(AuditLog.action == "pdf.redacted").all()
    assert entries
    for entry in entries:
        assert "alice@example.com" not in (entry.detail or "")


def test_redaction_requires_write_access(alice, bob, sensitive_doc):
    alice.post(f"/api/v1/workspaces/{alice.workspace_id}/members",
               json={"email": bob.email, "role": "viewer"})

    # A viewer may detect but not apply.
    assert bob.post(f"/api/v1/documents/{sensitive_doc}/redact/detect",
                    json={}).status_code == 200
    assert bob.post(f"/api/v1/documents/{sensitive_doc}/redact/apply",
                    json={"targets": [{"page": 1, "text": "x"}]}).status_code == 403


def test_other_tenant_cannot_read_text_or_redact(alice, bob, sensitive_doc):
    assert bob.get(f"/api/v1/documents/{sensitive_doc}/text").status_code == 404
    assert bob.get(f"/api/v1/documents/{sensitive_doc}/search?q=a").status_code == 404
    assert bob.post(f"/api/v1/documents/{sensitive_doc}/redact/detect",
                    json={}).status_code == 404
