"""Document comparison."""
import io

import pytest
from reportlab.pdfgen import canvas

from docintel.pdf.compare import compare, interpret


def build(pages) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(612, 792))
    for lines in pages:
        pdf.setFont("Helvetica", 12)
        y = 720
        for line in lines:
            pdf.drawString(72, y, line)
            y -= 20
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


ORIGINAL = build([
    ["Payment must be received within 60 days.", "Governing law is England and Wales."],
    ["The agreement expires on 31 December 2026."],
    ["This page is unchanged entirely."],
])

REVISED = build([
    ["Payment must be received within 30 days.", "Governing law is England and Wales."],
    ["A brand new inserted page appears here."],
    ["The agreement expires on 31 December 2027."],
    ["This page is unchanged entirely."],
])


def test_identical_documents_report_no_differences():
    result = compare(ORIGINAL, ORIGINAL)
    assert result.identical
    assert "No textual differences" in result.summary
    assert result.changed_pages == []


def test_detects_a_changed_page():
    result = compare(ORIGINAL, REVISED)
    assert not result.identical
    assert 1 in result.changed_pages


def test_detects_an_inserted_page():
    result = compare(ORIGINAL, REVISED)
    assert result.added_pages == [2]
    assert result.new_page_count == result.old_page_count + 1


def test_insertion_does_not_cascade_into_false_changes():
    """The page after an insert is the same text and must stay 'unchanged'."""
    result = compare(ORIGINAL, REVISED)
    unchanged = [p for p in result.pages if p.status == "unchanged"]
    assert any(p.old_page == 3 and p.new_page == 4 for p in unchanged)


def test_pages_are_paired_by_content_not_index():
    """The 2026 page and the 2027 page are the same clause, one page apart."""
    result = compare(ORIGINAL, REVISED)
    pair = next(p for p in result.pages if p.old_page == 2)
    assert pair.new_page == 3
    assert pair.status == "changed"
    assert pair.similarity > 0.9


def test_extracts_number_changes():
    result = compare(ORIGINAL, REVISED)
    pairs = [(n["old"], n["new"]) for n in result.numbers_changed]
    assert ("60", "30") in pairs


def test_extracts_date_changes():
    result = compare(ORIGINAL, REVISED)
    pairs = [(d["old"], d["new"]) for d in result.dates_changed]
    assert ("31 December 2026", "31 December 2027") in pairs


def test_removed_page_is_reported():
    shorter = build([["Only one page here."]])
    longer = build([["Only one page here."], ["A second page that will be removed."]])

    result = compare(longer, shorter)
    assert result.removed_pages == [2]


def test_change_records_both_sides():
    result = compare(ORIGINAL, REVISED)
    page = next(p for p in result.pages if p.old_page == 1)
    change = next(c for c in page.changes if c.kind == "changed")

    assert "60 days" in change.old
    assert "30 days" in change.new


def test_serialisation_is_complete():
    payload = compare(ORIGINAL, REVISED).as_dict()
    for key in ("identical", "summary", "added_pages", "removed_pages",
                "changed_pages", "numbers_changed", "dates_changed", "pages"):
        assert key in payload


# --------------------------------------------------- interpretation layer

class Recorder:
    name = "recorder"
    available = True

    def __init__(self):
        self.messages = None

    def complete(self, messages, *, temperature=0.2, max_tokens=1200):
        from docintel.ai.provider import Completion
        self.messages = messages
        return Completion(text="The payment window was halved.", model="recorder")

    def stream(self, messages, **kwargs):
        yield "x"


def test_interpretation_is_derived_from_the_computed_diff():
    """The model sees the diff, never the raw documents, so it cannot invent
    a change the structural comparison did not find."""
    provider = Recorder()
    result = compare(ORIGINAL, REVISED)

    text = interpret(result, provider=provider)
    assert text == "The payment window was halved."

    sent = provider.messages[1].content
    assert "OLD:" in sent and "NEW:" in sent
    assert "60 days" in sent
    # Fenced as untrusted data.
    import prompt_guard
    assert prompt_guard.FENCE in sent


def test_interpreting_identical_documents_calls_no_model():
    provider = Recorder()
    result = compare(ORIGINAL, ORIGINAL)
    assert "no differences" in interpret(result, provider=provider).lower()
    assert provider.messages is None


# ------------------------------------------------------------------- api

def test_compare_endpoint(alice):
    a = alice.upload(ORIGINAL, name="v1.pdf").json()["document"]["id"]
    b = alice.upload(REVISED, name="v2.pdf").json()["document"]["id"]

    response = alice.post(f"/api/v1/documents/{a}/compare",
                          json={"against_document_id": b})
    assert response.status_code == 200

    body = response.json()
    assert body["identical"] is False
    assert body["added_pages"] == [2]
    assert any(n["old"] == "60" and n["new"] == "30" for n in body["numbers_changed"])
    assert body["original"]["filename"] == "v1.pdf"
    assert "computed mechanically" in body["note"]


def test_compare_cannot_reach_another_tenants_document(alice, bob):
    mine = bob.upload(ORIGINAL, name="mine.pdf").json()["document"]["id"]
    theirs = alice.upload(REVISED, name="theirs.pdf").json()["document"]["id"]

    # Bob can see `mine` but not `theirs`; the comparison must not leak it.
    response = bob.post(f"/api/v1/documents/{mine}/compare",
                        json={"against_document_id": theirs})
    assert response.status_code == 404


def test_compare_without_interpretation_calls_no_model(alice):
    from docintel.ai.provider import set_provider

    class Exploding:
        name = "boom"
        available = True
        def complete(self, *a, **k): raise AssertionError("model was called")
        def stream(self, *a, **k): raise AssertionError("model was called")

    set_provider(Exploding())
    try:
        a = alice.upload(ORIGINAL, name="v1.pdf").json()["document"]["id"]
        b = alice.upload(REVISED, name="v2.pdf").json()["document"]["id"]
        response = alice.post(f"/api/v1/documents/{a}/compare",
                              json={"against_document_id": b})
        assert response.status_code == 200
        assert response.json()["interpretation"] is None
    finally:
        set_provider(None)
