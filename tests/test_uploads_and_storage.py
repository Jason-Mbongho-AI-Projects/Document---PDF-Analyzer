"""Upload validation, storage safety, and the document lifecycle."""
import pytest

import pdf_corpus as corpus
from docintel.core.uploads import sanitize_filename, validate_upload
from docintel.storage import LocalStorageProvider, StorageError, build_key


# ------------------------------------------------------- filename safety

@pytest.mark.parametrize("hostile,forbidden", [
    ("../../../../etc/passwd", ".."),
    ("..\\..\\windows\\system32\\cmd.exe", ".."),
    ("/absolute/path/file.pdf", "/"),
    ("C:\\Users\\victim\\file.pdf", "\\"),
])
def test_sanitize_strips_path_components(hostile, forbidden):
    safe = sanitize_filename(hostile)
    assert forbidden not in safe
    assert "/" not in safe and "\\" not in safe


def test_sanitize_removes_null_bytes():
    assert "\x00" not in sanitize_filename("evil\x00.pdf")


def test_sanitize_handles_windows_reserved_names():
    assert sanitize_filename("CON.pdf").upper() != "CON.PDF"


def test_sanitize_falls_back_for_empty_input():
    assert sanitize_filename("") == "document.pdf"
    assert sanitize_filename("...") == "document.pdf"


def test_sanitize_caps_length():
    assert len(sanitize_filename("a" * 500 + ".pdf")) <= 200


# ---------------------------------------------------- content validation

def test_valid_pdf_passes():
    result = validate_upload("report.pdf", "application/pdf", corpus.clean_pdf())
    assert result.ok
    assert result.detected_type == "pdf"


def test_executable_is_rejected_despite_pdf_name():
    """Magic bytes are authoritative, not the extension."""
    result = validate_upload("evil.pdf", "application/pdf", b"MZ\x90\x00 this is a PE binary")
    assert not result.ok
    assert "executable" in result.message


def test_linux_executable_is_rejected():
    payload = b"\x7fELF\x02\x01\x01" + b"\x00" * 60
    result = validate_upload("tool.pdf", "application/pdf", payload)
    assert not result.ok
    assert "executable" in result.message


def test_unrecognised_binary_is_rejected():
    """Unknown bytes are refused rather than guessed at."""
    assert not validate_upload("thing.pdf", "application/pdf", bytes(range(256)) * 4).ok


def test_legacy_office_file_is_refused_with_advice():
    """.doc and .xls are OLE containers we do not read; say what to do instead."""
    ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64
    result = validate_upload("old.doc", "application/msword", ole)
    assert not result.ok
    assert ".docx" in result.message


def test_html_is_accepted_as_a_document():
    """Markup is content, not an attack: it is converted, never executed."""
    payload = b"<html><body><h1>Notice</h1><p>Body text.</p></body></html>"
    result = validate_upload("page.html", "text/html", payload)
    assert result.ok
    assert result.detected_type == "html"


def test_extension_is_corrected_to_match_content():
    """A PDF named .exe must not be stored, or served back, as an .exe."""
    result = validate_upload("script.exe", "application/pdf", corpus.clean_pdf())
    assert result.ok
    assert result.safe_filename == "script.pdf"


def test_declared_mime_does_not_override_content():
    """The browser's guess is a hint; the bytes decide."""
    result = validate_upload("doc.pdf", "text/html", corpus.clean_pdf())
    assert result.ok
    assert result.detected_type == "pdf"


@pytest.mark.parametrize("name,builder,expected", [
    ("notes.txt", lambda: b"Plain notes.\nSecond line.", "txt"),
    ("rows.csv", lambda: b"region,total\nnorth,4\n", "csv"),
    ("report.docx", corpus.small_docx, "docx"),
    ("book.xlsx", corpus.small_xlsx, "xlsx"),
    ("scan.png", corpus.small_png, "png"),
])
def test_other_formats_are_accepted(name, builder, expected):
    result = validate_upload(name, None, builder())
    assert result.ok, result.message
    assert result.detected_type == expected


def test_empty_file_is_rejected():
    assert not validate_upload("empty.pdf", "application/pdf", b"").ok


def test_oversized_file_is_rejected():
    from docintel.config import settings
    payload = b"%PDF-1.7" + b"0" * (settings.max_upload_bytes + 10)
    result = validate_upload("big.pdf", "application/pdf", payload)
    assert not result.ok
    assert "exceeds" in result.message


# -------------------------------------------------------- storage safety

@pytest.mark.parametrize("bad_key", [
    "../escape",
    "workspace/../../etc/passwd",
    "/absolute/key",
    "",
    "nodir",
    "work space/doc/v1",
])
def test_storage_rejects_unsafe_keys(tmp_path, bad_key):
    provider = LocalStorageProvider(tmp_path)
    with pytest.raises(StorageError):
        provider.put(bad_key, b"data")


def test_storage_round_trip(tmp_path):
    provider = LocalStorageProvider(tmp_path)
    key = build_key("ws123", "doc456", 1)

    provider.put(key, b"hello")
    assert provider.exists(key)
    assert provider.get(key) == b"hello"

    provider.delete(key)
    assert not provider.exists(key)


def test_storage_refuses_to_delete_its_own_root(tmp_path):
    provider = LocalStorageProvider(tmp_path)
    with pytest.raises(StorageError):
        provider.delete_prefix("")


def test_delete_prefix_removes_every_version(tmp_path):
    provider = LocalStorageProvider(tmp_path)
    for version in (1, 2, 3):
        provider.put(build_key("ws", "doc", version), b"x")

    assert provider.delete_prefix("ws/doc") == 3
    assert not provider.exists(build_key("ws", "doc", 1))


# ------------------------------------------------------ upload endpoint

def test_upload_creates_document_and_queues_jobs(alice):
    response = alice.upload(corpus.clean_pdf(), name="quarterly.pdf")
    assert response.status_code == 201

    body = response.json()
    assert body["document"]["filename"] == "quarterly.pdf"
    assert body["document"]["status"] == "processing"
    assert len(body["jobs"]) == 2


def test_upload_rejects_an_executable(alice):
    response = alice.upload(b"MZ\x90\x00" + b"\x00" * 64, name="fake.pdf")
    assert response.status_code == 400
    assert "executable" in response.json()["detail"]


def test_upload_converts_a_word_document(alice):
    """Anything convertible becomes a PDF at the door, so every tool works."""
    response = alice.upload(corpus.small_docx(), name="report.docx")
    assert response.status_code == 201, response.text
    document = response.json()["document"]
    assert document["filename"] == "report.pdf"
    assert document["mime_type"] == "application/pdf"


def test_upload_converts_plain_text(alice):
    response = alice.upload(b"A short memo.\nWith two lines.", name="memo.txt")
    assert response.status_code == 201, response.text
    assert response.json()["document"]["filename"] == "memo.pdf"


def test_upload_converts_an_image(alice):
    response = alice.upload(corpus.small_png(), name="scan.png")
    assert response.status_code == 201, response.text
    assert response.json()["document"]["filename"] == "scan.pdf"


def test_converted_upload_remembers_what_it_came_from(alice):
    from docintel.db.models import Document
    from docintel.db.session import session_scope

    document_id = alice.upload(
        corpus.small_docx(), name="q3.docx").json()["document"]["id"]
    with session_scope() as session:
        metadata = session.get(Document, document_id).doc_metadata
    assert metadata["converted_from"] == "docx"
    assert metadata["original_filename"] == "q3.docx"


def test_a_converted_upload_is_a_readable_pdf(alice):
    """Conversion is only worth anything if the result opens and keeps the words."""
    import pypdfium2 as pdfium

    document_id = alice.upload(
        corpus.small_docx(), name="q3.docx").json()["document"]["id"]
    data = alice.get(f"/api/v1/documents/{document_id}/download").content
    assert data.startswith(b"%PDF-")
    text = pdfium.PdfDocument(data)[0].get_textpage().get_text_range()
    assert corpus.DOCX_SENTENCE in text


def test_upload_sanitises_traversal_filename(alice):
    response = alice.upload(corpus.clean_pdf(), name="../../../etc/passwd.pdf")
    assert response.status_code == 201
    stored = response.json()["document"]["filename"]
    assert ".." not in stored and "/" not in stored


def test_download_returns_the_original_bytes(alice):
    data = corpus.clean_pdf()
    document_id = alice.upload(data).json()["document"]["id"]

    response = alice.get(f"/api/v1/documents/{document_id}/download")
    assert response.status_code == 200
    assert response.content == data
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in response.headers["content-disposition"]


def test_download_never_exposes_a_filesystem_path(alice):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]

    detail = alice.get(f"/api/v1/documents/{document_id}").json()
    body = str(detail)
    assert "storage_key" not in body
    assert ".storage" not in body


def test_delete_removes_stored_bytes(alice):
    from docintel.storage import storage
    upload = alice.upload(corpus.clean_pdf()).json()
    document_id = upload["document"]["id"]
    workspace_id = upload["document"]["workspace_id"]

    assert alice.delete(f"/api/v1/documents/{document_id}").status_code == 204
    assert alice.get(f"/api/v1/documents/{document_id}").status_code == 404
    assert not storage.exists(build_key(workspace_id, document_id, 1))


def test_archive_and_restore(alice):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]

    assert alice.post(f"/api/v1/documents/{document_id}/archive").status_code == 200
    listing = alice.get(f"/api/v1/documents?workspace_id={alice.workspace_id}").json()
    assert listing["total"] == 0

    assert alice.post(f"/api/v1/documents/{document_id}/restore").status_code == 200
    listing = alice.get(f"/api/v1/documents?workspace_id={alice.workspace_id}").json()
    assert listing["total"] == 1


def test_listing_paginates(alice):
    for i in range(5):
        alice.upload(corpus.clean_pdf(), name=f"doc{i}.pdf")

    page = alice.get(
        f"/api/v1/documents?workspace_id={alice.workspace_id}&limit=2&offset=0"
    ).json()
    assert page["total"] == 5
    assert len(page["items"]) == 2
