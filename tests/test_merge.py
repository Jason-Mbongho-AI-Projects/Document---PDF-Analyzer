"""
Combining documents.

The engine could merge from the beginning; there was no endpoint, so the
feature did not exist. What matters beyond "it produces a PDF" is that the
caller's order is honoured exactly, the sources survive untouched, and that
combining cannot be turned into a way to read another tenant's document.
"""
import io

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from docintel.pdf.text import extract


def make_pdf(label: str, pages: int = 1) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    for number in range(1, pages + 1):
        pdf.drawString(100, 700, f"{label} page {number}")
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


@pytest.fixture
def documents(alice):
    alpha = alice.upload(make_pdf("ALPHA", 2), "alpha.pdf").json()["document"]["id"]
    beta = alice.upload(make_pdf("BETA", 3), "beta.pdf").json()["document"]["id"]
    return alpha, beta


def text_of(actor, document_id: str) -> str:
    response = actor.get(f"/api/v1/documents/{document_id}/download")
    assert response.status_code == 200
    return " ".join(p.text for p in extract(response.content))


def page_count(actor, document_id: str) -> int:
    response = actor.get(f"/api/v1/documents/{document_id}/download")
    return len(extract(response.content))


def test_combines_every_page(alice, documents):
    alpha, beta = documents

    response = alice.post("/api/v1/documents/merge",
                          json={"document_ids": [alpha, beta]})

    assert response.status_code == 201, response.text
    assert page_count(alice, response.json()["document"]["id"]) == 5


def test_order_is_the_order_given(alice, documents):
    """The list is the sequence, not a hint — reversing it reverses the file."""
    alpha, beta = documents

    forward = alice.post("/api/v1/documents/merge",
                         json={"document_ids": [alpha, beta]}).json()["document"]["id"]
    backward = alice.post("/api/v1/documents/merge",
                          json={"document_ids": [beta, alpha]}).json()["document"]["id"]

    forward_text = text_of(alice, forward)
    backward_text = text_of(alice, backward)

    assert forward_text.index("ALPHA page 1") < forward_text.index("BETA page 1")
    assert backward_text.index("BETA page 1") < backward_text.index("ALPHA page 1")


def test_sources_are_left_alone(alice, documents):
    """Combining creates a document; it must not consume its inputs."""
    alpha, beta = documents
    alice.post("/api/v1/documents/merge", json={"document_ids": [alpha, beta]})

    assert page_count(alice, alpha) == 2
    assert page_count(alice, beta) == 3


def test_records_what_it_was_built_from(alice, documents):
    alpha, beta = documents
    combined = alice.post("/api/v1/documents/merge",
                          json={"document_ids": [alpha, beta]}).json()["document"]["id"]

    detail = alice.get(f"/api/v1/documents/{combined}").json()
    assert detail["doc_metadata"]["combined_from"] == [alpha, beta]


def test_custom_filename_gains_a_pdf_extension(alice, documents):
    alpha, beta = documents
    response = alice.post("/api/v1/documents/merge",
                          json={"document_ids": [alpha, beta],
                                "filename": "board pack"})

    assert response.json()["document"]["filename"] == "board pack.pdf"


def test_a_single_document_is_refused(alice, documents):
    """Combining one document is a mistake, not an operation."""
    alpha, _ = documents

    assert alice.post("/api/v1/documents/merge",
                      json={"document_ids": [alpha]}).status_code == 422


def test_an_unknown_document_is_not_found(alice, documents):
    alpha, _ = documents
    response = alice.post("/api/v1/documents/merge",
                          json={"document_ids": [alpha, "0" * 32]})

    assert response.status_code == 404


def test_cannot_combine_another_tenants_document(alice, bob, documents):
    """The obvious way to abuse this: name a document you cannot read.

    Every source is authorised individually, and an unauthorised one must be
    indistinguishable from one that does not exist.
    """
    alpha, _ = documents
    theirs = bob.upload(make_pdf("SECRET", 1), "secret.pdf").json()["document"]["id"]

    response = alice.post("/api/v1/documents/merge",
                          json={"document_ids": [alpha, theirs]})

    assert response.status_code == 404
    # And nothing was created from the attempt.
    listing = alice.get(f"/api/v1/documents?workspace_id={alice.workspace_id}").json()
    assert all("SECRET" not in d["filename"] for d in listing["items"])
