"""Translation and OCR availability."""
import io

import pytest
from pypdf import PdfReader
from reportlab.pdfgen import canvas

import pdf_corpus as corpus
from docintel.ai import translate as T
from docintel.ai.provider import Completion, LLMError, LLMProvider, set_provider
from docintel.pdf import ocr


class Scripted(LLMProvider):
    name = "scripted"
    available = True

    def __init__(self, reply="TRANSLATED"):
        self.reply = reply
        self.calls = []

    def complete(self, messages, *, temperature=0.2, max_tokens=1200):
        self.calls.append(messages)
        return Completion(text=self.reply, model="scripted",
                          prompt_tokens=5, completion_tokens=5)

    def stream(self, messages, **kwargs):
        yield self.reply


@pytest.fixture(autouse=True)
def _isolate():
    set_provider(Scripted())
    yield
    set_provider(None)


def contract(pages=3) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(612, 792))
    for index in range(pages):
        pdf.setFont("Helvetica", 12)
        pdf.drawString(72, 720, "Acme Corporation Service Agreement")
        pdf.drawString(72, 700, f"Section {index + 1}: Payment Terms apply here.")
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


# --------------------------------------------------------- translation

def test_translates_every_page():
    provider = Scripted("Texte traduit")
    result = T.translate(contract(3), "French", provider=provider)

    assert len(result.pages) == 3
    assert all(p["translated"] == "Texte traduit" for p in result.pages)
    assert result.target_language == "French"


def test_only_requested_pages_are_translated():
    provider = Scripted("x")
    result = T.translate(contract(4), "German", pages=[2, 3], provider=provider)
    assert [p["page"] for p in result.pages] == [2, 3]


def test_glossary_is_built_from_recurring_terms():
    terms = T.build_glossary(
        __import__("docintel.pdf.text", fromlist=["extract"]).extract(contract(4)),
        minimum=3,
    )
    assert any("Acme" in t for t in terms)


def test_glossary_is_sent_with_every_page_for_consistency():
    """The same term must not be translated three different ways."""
    provider = Scripted("Acme Corporation Service Agreement = Contrat Acme")
    T.translate(contract(3), "French", provider=provider)

    # First call builds the glossary; the rest carry it.
    page_calls = provider.calls[1:]
    assert page_calls
    for call in page_calls:
        assert "Use these translations consistently" in call[1].content


def test_glossary_tolerates_a_reformatted_term():
    """A model that echoes a term with different spacing/casing must still
    have its translation honoured, not silently dropped."""
    terms = ["Acme Corporation Service Agreement"]
    provider = Scripted("  acme   corporation   service   agreement = Contrat Acme ")
    glossary = T.translate_glossary(terms, "French", provider)
    assert glossary == {"Acme Corporation Service Agreement": "Contrat Acme"}


def test_document_text_is_fenced_as_data():
    import prompt_guard
    provider = Scripted("t")
    T.translate(contract(1), "Spanish", provider=provider)
    assert prompt_guard.FENCE in provider.calls[-1][1].content


def test_missing_language_is_rejected():
    with pytest.raises(LLMError, match="target language is required"):
        T.translate(contract(1), "  ", provider=Scripted())


def test_document_without_text_is_reported_not_guessed():
    with pytest.raises(LLMError, match="no extractable text"):
        T.translate(corpus.empty_text_pdf(), "French", provider=Scripted())


def test_result_states_that_layout_is_not_preserved():
    result = T.translate(contract(1), "French", provider=Scripted())
    assert result.fidelity == "text-only"
    assert "layout" in result.note.lower()
    assert "original document is unchanged" in result.note


def test_translation_renders_to_an_openable_pdf():
    result = T.translate(contract(2), "French", provider=Scripted("Bonjour le monde"))
    output = T.to_pdf(result, title="Traduction")

    reader = PdfReader(io.BytesIO(output))
    assert "Bonjour le monde" in (reader.pages[0].extract_text() or "")


# ---------------------------------------------------------------- ocr

def test_assess_detects_a_native_text_layer():
    assessment = ocr.assess(corpus.multipage_pdf(3))
    assert assessment.classification == "native"
    assert assessment.pages_needing_ocr == []
    assert "not needed" in assessment.summary


def test_assess_detects_a_page_without_text():
    assessment = ocr.assess(corpus.empty_text_pdf())
    assert assessment.classification == "no_text_layer"
    assert assessment.pages_needing_ocr == [1]
    assert "scanned" in assessment.summary


def test_assessment_works_without_any_ocr_engine():
    """Detecting the need for OCR must not itself require OCR."""
    ocr.set_provider(ocr.UnavailableProvider())
    try:
        assessment = ocr.assess(corpus.empty_text_pdf())
        assert assessment.pages_needing_ocr == [1]
    finally:
        ocr.set_provider(None)


def test_unavailable_provider_explains_how_to_enable_it():
    provider = ocr.UnavailableProvider()
    assert provider.available is False
    assert "Tesseract" in provider.reason

    from docintel.pdf.engine import PDFEngineError
    with pytest.raises(PDFEngineError, match="Tesseract"):
        provider.recognise(corpus.clean_pdf())


def test_provider_selection_falls_back_honestly():
    ocr.set_provider(None)
    provider = ocr.get_provider()
    # Whichever is chosen, an unavailable one must carry a reason.
    assert provider.available or provider.reason
