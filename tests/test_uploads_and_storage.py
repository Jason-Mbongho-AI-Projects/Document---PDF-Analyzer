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
    assert result.detected_type == "application/pdf"


def test_non_pdf_content_is_rejected_despite_pdf_name():
    """Magic bytes are authoritative, not the extension."""
    result = validate_upload("evil.pdf", "application/pdf", b"MZ\x90\x00 this is a PE binary")
    assert not result.ok
    assert "not a PDF" in result.message


def test_html_disguised_as_pdf_is_rejected():
    payload = b"<html><script>alert(1)</script></html>"
    assert not validate_upload("page.pdf", "application/pdf", payload).ok


def test_wrong_extension_is_rejected():
    assert not validate_upload("script.exe", "application/pdf", corpus.clean_pdf()).ok


def test_wrong_declared_mime_is_rejected():
    result = validate_upload("doc.pdf", "text/html", corpus.clean_pdf())
    assert not result.ok


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


def test_upload_rejects_non_pdf(alice):
    response = alice.upload(b"not a pdf at all", name="fake.pdf")
    assert response.status_code == 400


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
