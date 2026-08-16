"""
Editing, forms, rendering and annotation endpoints.

Outputs are downloaded and re-opened, so a passing test means a real,
usable PDF or image came back — not just a 200.
"""
import io

import pytest
from PIL import Image
from pypdf import PdfReader

import pdf_corpus as corpus


@pytest.fixture
def doc(alice):
    """A 5-page document owned by alice."""
    response = alice.upload(corpus.multipage_pdf(5), name="report.pdf")
    assert response.status_code == 201
    return response.json()["document"]["id"]


def download(actor, document_id, version=None):
    url = f"/api/v1/documents/{document_id}/download"
    if version:
        url += f"?version={version}"
    response = actor.get(url)
    assert response.status_code == 200, response.text
    return response.content


def pages_of(data: bytes) -> int:
    return len(PdfReader(io.BytesIO(data)).pages)


# ------------------------------------------------- page organisation

def test_rotate_creates_a_new_version_and_keeps_the_original(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/pages/rotate",
                          json={"pages": [1, 2], "degrees": 90})
    assert response.status_code == 200
    assert response.json()["version"] == 2

    rotated = PdfReader(io.BytesIO(download(alice, doc)))
    assert rotated.pages[0].get("/Rotate") == 90

    original = PdfReader(io.BytesIO(download(alice, doc, version=1)))
    assert original.pages[0].get("/Rotate", 0) == 0


def test_delete_pages_and_original_still_downloadable(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/pages/delete",
                          json={"pages": [2, 3]})
    assert response.status_code == 200
    assert "still contains these pages" in response.json()["note"]

    assert pages_of(download(alice, doc)) == 3
    assert pages_of(download(alice, doc, version=1)) == 5


def test_reorder(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/pages/reorder",
                          json={"order": [5, 4, 3, 2, 1]})
    assert response.status_code == 200
    assert pages_of(download(alice, doc)) == 5


def test_reorder_with_missing_pages_is_rejected(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/pages/reorder",
                          json={"order": [1, 2]})
    assert response.status_code == 400
    assert "every page" in response.json()["detail"]


def test_duplicate_pages(alice, doc):
    assert alice.post(f"/api/v1/documents/{doc}/pages/duplicate",
                      json={"pages": [1]}).status_code == 200
    assert pages_of(download(alice, doc)) == 6


def test_crop(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/pages/crop",
                          json={"pages": [1], "left": 100, "bottom": 100,
                                "right": 400, "top": 600})
    assert response.status_code == 200
    page = PdfReader(io.BytesIO(download(alice, doc))).pages[0]
    assert float(page.mediabox.width) == 300.0


def test_extract_returns_a_new_pdf_without_touching_the_source(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/pages/extract",
                          json={"pages": [2, 3]})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert pages_of(response.content) == 2

    # Source unchanged: still one version, still 5 pages.
    assert pages_of(download(alice, doc)) == 5


def test_split_creates_versions_per_range(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/pages/split",
                          json={"ranges": [[1, 2], [3, 5]]})
    assert response.status_code == 200

    parts = response.json()["parts"]
    assert [p["pages"] for p in parts] == ["1-2", "3-5"]
    assert pages_of(download(alice, doc, version=parts[0]["version"])) == 2
    assert pages_of(download(alice, doc, version=parts[1]["version"])) == 3


def test_out_of_range_page_returns_400(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/pages/rotate",
                          json={"pages": [99], "degrees": 90})
    assert response.status_code == 400
    assert "out of range" in response.json()["detail"]


# ------------------------------------------------------- composition

def test_watermark(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/watermark",
                          json={"text": "CONFIDENTIAL", "opacity": 0.2})
    assert response.status_code == 200
    assert pages_of(download(alice, doc)) == 5


def test_page_numbers_are_present_on_every_page(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/page-numbers",
                          json={"position": "bottom-right"})
    assert response.status_code == 200

    reader = PdfReader(io.BytesIO(download(alice, doc)))
    rendered = [(p.extract_text() or "").strip().splitlines()[-1] for p in reader.pages]
    assert rendered == ["1", "2", "3", "4", "5"]


def test_header_and_footer(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/header-footer",
                          json={"header": "ACME Ltd", "footer": "Internal"})
    assert response.status_code == 200

    text = PdfReader(io.BytesIO(download(alice, doc))).pages[0].extract_text()
    assert "ACME Ltd" in text and "Internal" in text


# -------------------------------------------------- security and size

def test_compress_reports_measured_bytes(alice):
    heavy = alice.upload(corpus.multipage_pdf(30), name="heavy.pdf").json()["document"]["id"]
    alice.post(f"/api/v1/documents/{heavy}/watermark", json={"text": "DRAFT"})

    response = alice.post(f"/api/v1/documents/{heavy}/compress",
                          json={"preset": "maximum-compression"})
    assert response.status_code == 200

    body = response.json()
    actual = len(download(alice, heavy))
    # The reported size must equal the bytes actually stored.
    assert body["compressed_bytes"] == actual
    assert body["size_bytes"] == actual
    assert body["reduction_percent"] >= 0


def test_protect_then_download_requires_password(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/protect",
                          json={"user_password": "s3cret-open", "allow_copy": False})
    assert response.status_code == 200
    assert "not cryptographic controls" in response.json()["note"]

    data = download(alice, doc)
    reader = PdfReader(io.BytesIO(data))
    assert reader.is_encrypted
    assert reader.decrypt("s3cret-open") != 0
    assert len(reader.pages) == 5


def test_unlock_with_correct_password(alice, doc):
    alice.post(f"/api/v1/documents/{doc}/protect", json={"user_password": "s3cret-open"})
    response = alice.post(f"/api/v1/documents/{doc}/unlock",
                          json={"password": "s3cret-open"})
    assert response.status_code == 200
    assert not PdfReader(io.BytesIO(download(alice, doc))).is_encrypted


def test_unlock_with_wrong_password_is_refused(alice, doc):
    alice.post(f"/api/v1/documents/{doc}/protect", json={"user_password": "s3cret-open"})
    response = alice.post(f"/api/v1/documents/{doc}/unlock", json={"password": "guess"})
    assert response.status_code == 423
    assert "incorrect" in response.json()["detail"].lower()


# ------------------------------------------------------------- forms

def test_inspect_form_lists_fields_with_geometry(alice):
    form_id = alice.upload(corpus.fillable_form_pdf(), name="form.pdf").json()["document"]["id"]
    response = alice.get(f"/api/v1/documents/{form_id}/form")
    assert response.status_code == 200

    body = response.json()
    assert body["has_form"] and body["fillable"] and not body["is_xfa"]
    assert set(body["required_fields"]) == {"email", "agree"}

    by_name = {f["name"]: f for f in body["fields"]}
    assert by_name["comments"]["kind"] == "multiline"
    assert by_name["country"]["options"] == ["UK", "US", "DE"]
    assert by_name["reference"]["read_only"] is True
    # Geometry a viewer needs to position the field.
    assert by_name["email"]["page"] == 1
    assert len(by_name["email"]["rect"]) == 4


def test_fill_form_persists_values(alice):
    form_id = alice.upload(corpus.fillable_form_pdf(), name="form.pdf").json()["document"]["id"]
    response = alice.post(f"/api/v1/documents/{form_id}/form/fill",
                          json={"values": {"email": "ada@example.com",
                                           "full_name": "Ada Lovelace",
                                           "agree": "/Yes"}})
    assert response.status_code == 200
    assert response.json()["note"] is None       # nothing required left empty

    after = alice.get(f"/api/v1/documents/{form_id}/form").json()
    values = {f["name"]: f["value"] for f in after["fields"]}
    assert values["email"] == "ada@example.com"
    assert values["full_name"] == "Ada Lovelace"


def test_fill_reports_missing_required_fields(alice):
    form_id = alice.upload(corpus.fillable_form_pdf(), name="form.pdf").json()["document"]["id"]
    response = alice.post(f"/api/v1/documents/{form_id}/form/fill",
                          json={"values": {"full_name": "Ada"}})
    assert response.status_code == 200
    note = response.json()["note"]
    assert "email" in note and "agree" in note


def test_fill_rejects_unknown_field(alice):
    form_id = alice.upload(corpus.fillable_form_pdf(), name="form.pdf").json()["document"]["id"]
    response = alice.post(f"/api/v1/documents/{form_id}/form/fill",
                          json={"values": {"not_a_field": "x"}})
    assert response.status_code == 400
    assert "Unknown form field" in response.json()["detail"]


def test_flatten_removes_interactivity(alice):
    form_id = alice.upload(corpus.fillable_form_pdf(), name="form.pdf").json()["document"]["id"]
    alice.post(f"/api/v1/documents/{form_id}/form/fill",
               json={"values": {"email": "a@b.com"}, "flatten": True})
    assert alice.get(f"/api/v1/documents/{form_id}/form").json()["has_form"] is False


def test_document_without_a_form_says_so(alice, doc):
    body = alice.get(f"/api/v1/documents/{doc}/form").json()
    assert body["has_form"] is False
    assert "no interactive form fields" in body["note"]


def test_xfa_form_is_reported_as_unsupported(alice):
    xfa_id = alice.upload(corpus.xfa_form_pdf(), name="xfa.pdf").json()["document"]["id"]
    body = alice.get(f"/api/v1/documents/{xfa_id}/form").json()
    assert body["is_xfa"] is True
    assert body["fillable"] is False
    assert "not supported" in body["note"]


# --------------------------------------------------------- rendering

def test_render_page_returns_an_image(alice, doc):
    response = alice.get(f"/api/v1/documents/{doc}/render/1?scale=1.0")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"

    image = Image.open(io.BytesIO(response.content))
    assert image.width > 100 and image.height > 100


def test_render_out_of_range_page_is_rejected(alice, doc):
    assert alice.get(f"/api/v1/documents/{doc}/render/99").status_code == 400


def test_snapshot_captures_only_the_requested_region(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/snapshot",
                          json={"page": 1, "left": 72, "top": 60,
                                "right": 372, "bottom": 210, "scale": 2.0})
    assert response.status_code == 200

    image = Image.open(io.BytesIO(response.content))
    # 300x150 points at scale 2 -> 600x300 px, not a full page.
    assert 560 <= image.width <= 620
    assert 280 <= image.height <= 320


def test_snapshot_as_jpeg(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/snapshot",
                          json={"page": 1, "left": 0, "top": 0,
                                "right": 200, "bottom": 200, "format": "jpg"})
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.content)).format == "JPEG"


def test_snapshot_rejects_an_inverted_region(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/snapshot",
                          json={"page": 1, "left": 300, "top": 300,
                                "right": 100, "bottom": 100})
    assert response.status_code == 400


# ------------------------------------------------------- annotations

def test_create_and_list_highlight(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/annotations", json={
        "kind": "highlight", "page": 2,
        "rect": {"x": 72, "y": 700, "width": 200, "height": 14},
        "quads": [{"x": 72, "y": 700, "width": 200, "height": 14}],
        "selected_text": "Sample document text.",
        "colour": "#FFD54F",
    })
    assert response.status_code == 201
    annotation = response.json()
    assert annotation["kind"] == "highlight"
    assert annotation["page"] == 2

    listing = alice.get(f"/api/v1/documents/{doc}/annotations").json()
    assert len(listing) == 1


def test_annotations_persist_across_requests_and_filter_by_page(alice, doc):
    for page in (1, 2, 2):
        alice.post(f"/api/v1/documents/{doc}/annotations",
                   json={"kind": "highlight", "page": page,
                         "rect": {"x": 1, "y": 1, "width": 10, "height": 10}})

    assert len(alice.get(f"/api/v1/documents/{doc}/annotations?page=2").json()) == 2
    assert len(alice.get(f"/api/v1/documents/{doc}/annotations").json()) == 3


def test_annotating_does_not_modify_the_pdf(alice, doc):
    before = download(alice, doc)
    alice.post(f"/api/v1/documents/{doc}/annotations",
               json={"kind": "highlight", "page": 1,
                     "rect": {"x": 1, "y": 1, "width": 10, "height": 10}})
    assert download(alice, doc) == before


def test_comment_threading_and_resolution(alice, doc):
    parent = alice.post(f"/api/v1/documents/{doc}/annotations", json={
        "kind": "comment", "page": 3, "body": "This number looks wrong.",
        "rect": {"x": 10, "y": 10, "width": 5, "height": 5},
    }).json()

    reply = alice.post(f"/api/v1/documents/{doc}/annotations", json={
        "kind": "comment", "page": 3, "body": "Agreed, checking.",
        "parent_id": parent["id"],
        "rect": {"x": 10, "y": 10, "width": 5, "height": 5},
    })
    assert reply.status_code == 201
    assert reply.json()["parent_id"] == parent["id"]

    resolved = alice.post(f"/api/v1/documents/{doc}/annotations/{parent['id']}/resolve")
    assert resolved.status_code == 200 and resolved.json()["is_resolved"] is True

    open_only = alice.get(
        f"/api/v1/documents/{doc}/annotations?include_resolved=false"
    ).json()
    assert parent["id"] not in [a["id"] for a in open_only]

    reopened = alice.post(f"/api/v1/documents/{doc}/annotations/{parent['id']}/reopen")
    assert reopened.json()["is_resolved"] is False


def test_update_and_delete_annotation(alice, doc):
    created = alice.post(f"/api/v1/documents/{doc}/annotations", json={
        "kind": "note", "page": 1, "body": "first",
        "rect": {"x": 1, "y": 1, "width": 5, "height": 5},
    }).json()

    patched = alice.client.patch(
        f"/api/v1/documents/{doc}/annotations/{created['id']}",
        headers=alice.headers, json={"body": "revised", "colour": "#4FC3F7"},
    )
    assert patched.status_code == 200
    assert patched.json()["body"] == "revised"

    assert alice.delete(
        f"/api/v1/documents/{doc}/annotations/{created['id']}"
    ).status_code == 204
    assert alice.get(f"/api/v1/documents/{doc}/annotations").json() == []


def test_annotation_beyond_page_count_is_rejected(alice, doc):
    response = alice.post(f"/api/v1/documents/{doc}/annotations",
                          json={"kind": "highlight", "page": 99,
                                "rect": {"x": 1, "y": 1, "width": 5, "height": 5}})
    assert response.status_code == 400


# ------------------------------------------------- authorization

@pytest.mark.parametrize("method,path,payload", [
    ("post", "pages/rotate", {"pages": [1], "degrees": 90}),
    ("post", "pages/delete", {"pages": [1]}),
    ("post", "pages/reorder", {"order": [1]}),
    ("post", "watermark", {"text": "X"}),
    ("post", "compress", {"preset": "balanced"}),
    ("post", "protect", {"user_password": "abcd"}),
    ("post", "snapshot", {"page": 1, "left": 0, "top": 0, "right": 10, "bottom": 10}),
    ("post", "annotations", {"kind": "highlight", "page": 1,
                             "rect": {"x": 1, "y": 1, "width": 2, "height": 2}}),
])
def test_editing_another_tenants_document_is_impossible(alice, bob, doc, method, path, payload):
    response = getattr(bob, method)(f"/api/v1/documents/{doc}/{path}", json=payload)
    assert response.status_code == 404


def test_viewer_cannot_edit(alice, bob, doc):
    alice.post(f"/api/v1/workspaces/{alice.workspace_id}/members",
               json={"email": bob.email, "role": "viewer"})

    # Can read...
    assert bob.get(f"/api/v1/documents/{doc}/render/1").status_code == 200
    assert bob.get(f"/api/v1/documents/{doc}/form").status_code == 200
    # ...but not write.
    assert bob.post(f"/api/v1/documents/{doc}/pages/rotate",
                    json={"pages": [1], "degrees": 90}).status_code == 403
    assert bob.post(f"/api/v1/documents/{doc}/annotations",
                    json={"kind": "highlight", "page": 1,
                          "rect": {"x": 1, "y": 1, "width": 2, "height": 2}}).status_code == 403
