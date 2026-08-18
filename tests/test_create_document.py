"""Making a document in the app, rather than uploading one."""
import pypdfium2 as pdfium
import pytest

import pdf_corpus as corpus


def create(actor, **body):
    payload = {"workspace_id": actor.workspace_id}
    payload.update(body)
    return actor.post("/api/v1/documents/create", json=payload)


def text_of(data: bytes) -> str:
    document = pdfium.PdfDocument(data)
    return "\n".join(page.get_textpage().get_text_range() for page in document)


def test_creates_a_pdf_from_typed_text(alice):
    response = create(alice, filename="Notes.pdf", title="Board meeting",
                      content="First item.\n\nSecond item.")
    assert response.status_code == 201, response.text
    document = response.json()["document"]
    assert document["filename"] == "Notes.pdf"
    assert document["mime_type"] == "application/pdf"
    assert document["size_bytes"] > 0


def test_the_created_file_opens_and_holds_the_words(alice):
    document_id = create(
        alice, title="Board meeting",
        content="Revenue is up.\n\nCosts are flat.").json()["document"]["id"]
    data = alice.get(f"/api/v1/documents/{document_id}/download").content
    assert data.startswith(b"%PDF-")
    text = text_of(data)
    assert "Board meeting" in text
    assert "Revenue is up." in text
    assert "Costs are flat." in text


def test_markdown_headings_and_bullets_survive(alice):
    document_id = create(alice, content="# Agenda\n\n- Budget\n- Hiring").json()["document"]["id"]
    text = text_of(alice.get(f"/api/v1/documents/{document_id}/download").content)
    assert "Agenda" in text
    assert "Budget" in text and "Hiring" in text


def test_a_blank_document_has_the_pages_asked_for(alice):
    document_id = create(alice, filename="blank.pdf",
                         blank_pages=4).json()["document"]["id"]
    data = alice.get(f"/api/v1/documents/{document_id}/download").content
    assert len(pdfium.PdfDocument(data)) == 4


def test_a4_is_a_different_size_from_letter(alice):
    sizes = {}
    for page_size in ("letter", "a4"):
        document_id = create(alice, content="Body text.",
                             page_size=page_size).json()["document"]["id"]
        data = alice.get(f"/api/v1/documents/{document_id}/download").content
        sizes[page_size] = pdfium.PdfDocument(data)[0].get_size()
    assert sizes["letter"] != sizes["a4"]
    # A4 is the taller and narrower of the two.
    assert sizes["a4"][1] > sizes["letter"][1]
    assert sizes["a4"][0] < sizes["letter"][0]


def test_a_missing_extension_is_added(alice):
    response = create(alice, filename="Untitled report", content="Body.")
    assert response.json()["document"]["filename"] == "Untitled report.pdf"


def test_a_traversal_filename_is_sanitised(alice):
    response = create(alice, filename="../../etc/passwd.pdf", content="Body.")
    stored = response.json()["document"]["filename"]
    assert ".." not in stored and "/" not in stored


def test_creating_queues_the_same_work_as_an_upload(alice):
    """A created document must be searchable and scanned like any other."""
    assert len(create(alice, content="Body.").json()["jobs"]) == 2


def test_it_appears_in_the_library(alice):
    create(alice, filename="Findable.pdf", content="Body.")
    listing = alice.get(
        "/api/v1/documents", params={"workspace_id": alice.workspace_id}).json()
    names = [d["filename"] for d in listing["items"]]
    assert "Findable.pdf" in names


def test_another_workspace_cannot_be_written_to(alice, bob):
    response = alice.post("/api/v1/documents/create", json={
        "workspace_id": bob.workspace_id, "content": "Body."})
    assert response.status_code in (403, 404)


def test_anonymous_creation_is_refused(client, alice):
    response = client.post("/api/v1/documents/create", json={
        "workspace_id": alice.workspace_id, "content": "Body."})
    assert response.status_code in (401, 403)


@pytest.mark.parametrize("blank_pages", [0, 201])
def test_page_counts_outside_the_range_are_refused(alice, blank_pages):
    assert create(alice, blank_pages=blank_pages).status_code == 422


def test_an_edited_created_document_still_versions(alice):
    """Nothing about creation should bypass the normal version history."""
    document_id = create(alice, content="Body.").json()["document"]["id"]
    history = alice.get(f"/api/v1/documents/{document_id}/versions").json()
    assert history["current"] == 1
    assert [v["version"] for v in history["versions"]] == [1]
    assert history["versions"][0]["label"] == "created"
