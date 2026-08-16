"""
Insights, key quotes, summary caching and export.

These are the original app's analysis features, carried into the platform.
"""
import csv
import io

import pytest
from reportlab.pdfgen import canvas

import pdf_corpus as corpus
from docintel.ai import insights as insight_tools
from docintel.ai.provider import Completion, LLMError, LLMProvider, set_provider


class Scripted(LLMProvider):
    name = "scripted"
    available = True

    def __init__(self, reply=""):
        self.reply = reply
        self.calls = []

    def complete(self, messages, *, temperature=0.2, max_tokens=1200):
        self.calls.append(messages)
        return Completion(text=self.reply, model="scripted",
                          prompt_tokens=3, completion_tokens=3)

    def stream(self, messages, **kwargs):
        yield self.reply


@pytest.fixture(autouse=True)
def _isolate():
    set_provider(Scripted("SUMMARY"))
    yield
    set_provider(None)


def prose_pdf() -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(612, 792))
    pdf.setFont("Helvetica", 12)
    pdf.drawString(72, 720, "Revenue grew fourteen percent this quarter.")
    pdf.drawString(72, 700, "The outcome was excellent and shareholders benefit.")
    pdf.showPage()
    pdf.setFont("Helvetica", 12)
    pdf.drawString(72, 720, "Supplier concentration remains a serious risk.")
    pdf.save()
    return buffer.getvalue()


# --------------------------------------------------------------- insights

def test_insights_need_no_model():
    """Statistical measures must not depend on an AI provider."""
    provider = Scripted()
    set_provider(provider)

    result = insight_tools.compute(prose_pdf())
    assert result.keywords
    assert provider.calls == []          # nothing was sent anywhere


def test_insights_report_counts_and_measures():
    result = insight_tools.compute(prose_pdf())
    assert result.page_count == 2
    assert result.word_count > 10
    assert "sentiment" in result.sentiment
    assert "reading_level" in result.readability


def test_insights_state_the_limits_of_each_measure():
    """A word-count sentiment score must not look like a classifier."""
    result = insight_tools.compute(prose_pdf())
    assert "fixed positive/negative list" in result.method_notes["sentiment"]
    assert "Not a semantic topic model" in result.method_notes["keywords"]
    assert "Flesch" in result.method_notes["readability"]


def test_insights_on_a_document_without_text_are_refused():
    with pytest.raises(LLMError, match="no extractable text"):
        insight_tools.compute(corpus.empty_text_pdf())


def test_metrics_reuse_the_original_calculation():
    result = insight_tools.metrics("a" * 1000, "a" * 100, 4.0, 5)
    assert result["compression_ratio"] == 10.0
    assert result["chunks_processed"] == 5


# ----------------------------------------------------------------- quotes

def test_quotes_are_verified_against_the_document():
    """A paraphrase is not a quotation and must be discarded."""
    provider = Scripted(
        "Revenue grew fourteen percent this quarter.\n"
        "The company had a really great three months."      # invented
    )
    quotes = insight_tools.key_quotes(prose_pdf(), provider=provider)

    texts = [q.text for q in quotes]
    assert "Revenue grew fourteen percent this quarter." in texts
    assert not any("really great three months" in t for t in texts)


def test_quotes_carry_the_page_they_came_from():
    provider = Scripted("Supplier concentration remains a serious risk.")
    quotes = insight_tools.key_quotes(prose_pdf(), provider=provider)
    assert quotes and quotes[0].page == 2


def test_quote_extraction_fences_the_document():
    import prompt_guard
    provider = Scripted("Revenue grew fourteen percent this quarter.")
    insight_tools.key_quotes(prose_pdf(), provider=provider)
    assert prompt_guard.FENCE in provider.calls[0][1].content


def test_all_paraphrased_quotes_yields_an_empty_list_not_fiction():
    provider = Scripted("Something the document never said at all.")
    assert insight_tools.key_quotes(prose_pdf(), provider=provider) == []


# -------------------------------------------------------------------- api

@pytest.fixture
def doc(alice):
    return alice.upload(prose_pdf(), name="q3.pdf").json()["document"]["id"]


def test_insights_endpoint(alice, doc):
    body = alice.get(f"/api/v1/documents/{doc}/ai/insights").json()
    assert body["keywords"]
    assert body["page_count"] == 2
    assert "No AI model was used" in body["note"]
    assert "method_notes" in body


def test_quotes_endpoint_and_caching(alice, doc):
    provider = Scripted("Revenue grew fourteen percent this quarter.")
    set_provider(provider)

    first = alice.post(f"/api/v1/documents/{doc}/ai/quotes").json()
    assert first["cached"] is False
    assert first["quotes"][0]["page"] == 1

    calls = len(provider.calls)
    second = alice.post(f"/api/v1/documents/{doc}/ai/quotes").json()
    assert second["cached"] is True
    assert len(provider.calls) == calls        # no second model call


def test_summary_is_cached_and_can_be_refreshed(alice, doc):
    provider = Scripted("The quarter was strong.")
    set_provider(provider)

    first = alice.post(f"/api/v1/documents/{doc}/ai/summarize",
                       json={"mode": "brief"}).json()
    assert first["cached"] is False
    calls = len(provider.calls)

    second = alice.post(f"/api/v1/documents/{doc}/ai/summarize",
                        json={"mode": "brief"}).json()
    assert second["cached"] is True
    assert len(provider.calls) == calls

    third = alice.post(f"/api/v1/documents/{doc}/ai/summarize",
                       json={"mode": "brief", "refresh": True}).json()
    assert third["cached"] is False
    assert len(provider.calls) > calls


def test_different_modes_cache_separately(alice, doc):
    set_provider(Scripted("s"))
    alice.post(f"/api/v1/documents/{doc}/ai/summarize", json={"mode": "brief"})

    other = alice.post(f"/api/v1/documents/{doc}/ai/summarize",
                       json={"mode": "executive"}).json()
    assert other["cached"] is False


# ------------------------------------------------------------- exporting

def test_export_summary_as_text(alice, doc):
    set_provider(Scripted("The quarter was strong."))
    alice.post(f"/api/v1/documents/{doc}/ai/summarize", json={"mode": "detailed"})

    response = alice.get(f"/api/v1/documents/{doc}/ai/summary/export?mode=detailed")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert "q3_summary.txt" in response.headers["content-disposition"]

    body = response.text
    assert "PDF SUMMARY REPORT" in body
    assert "The quarter was strong." in body
    assert "q3.pdf" in body


def test_export_sections_as_csv(alice, doc):
    set_provider(Scripted("Section text."))
    alice.post(f"/api/v1/documents/{doc}/ai/summarize", json={"mode": "brief"})

    response = alice.get(
        f"/api/v1/documents/{doc}/ai/summary/export?mode=brief&format=csv")
    assert response.status_code == 200

    rows = list(csv.reader(io.StringIO(response.text)))
    assert rows[0] == ["Section", "Pages", "Summary characters", "Status", "Summary"]
    assert len(rows) > 1
    assert rows[1][3] == "Success"


def test_export_before_a_summary_exists_says_so(alice, doc):
    response = alice.get(f"/api/v1/documents/{doc}/ai/summary/export?mode=brief")
    assert response.status_code == 404
    assert "has been generated" in response.json()["detail"]


def test_export_rejects_an_unknown_format(alice, doc):
    set_provider(Scripted("s"))
    alice.post(f"/api/v1/documents/{doc}/ai/summarize", json={"mode": "brief"})
    assert alice.get(
        f"/api/v1/documents/{doc}/ai/summary/export?mode=brief&format=docx"
    ).status_code == 400


def test_other_tenant_cannot_read_insights_or_export(alice, bob, doc):
    assert bob.get(f"/api/v1/documents/{doc}/ai/insights").status_code == 404
    assert bob.post(f"/api/v1/documents/{doc}/ai/quotes").status_code == 404
    assert bob.get(
        f"/api/v1/documents/{doc}/ai/summary/export?mode=brief").status_code == 404
