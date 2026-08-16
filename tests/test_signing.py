"""
Signing: saved signatures, Fill & Sign, and the multi-party request workflow.

The workflow tests drive a complete two-signer journey through the real
endpoints — create, send, view, sign, sign, complete, finalise — and then
assert on the audit trail and the produced document.
"""
import io

import pytest
from PIL import Image
from pypdf import PdfReader

import pdf_corpus as corpus
from docintel.signing import service as signing


# --------------------------------------------------------- saved assets

def test_typed_signature_renders_a_png():
    png = signing.render_typed_signature("Ada Lovelace")
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    assert image.width > 100


def test_typed_signature_requires_a_name():
    with pytest.raises(signing.SigningError, match="name is required"):
        signing.render_typed_signature("   ")


def test_uploaded_image_is_normalised_and_bounded():
    big = Image.new("RGBA", (4000, 2000), (0, 0, 0, 255))
    buffer = io.BytesIO()
    big.save(buffer, "PNG")

    data, width, height = signing.normalise_signature_image(buffer.getvalue())
    assert max(width, height) <= 800
    assert Image.open(io.BytesIO(data)).format == "PNG"


def test_non_image_upload_is_rejected():
    with pytest.raises(signing.SigningError, match="not a readable image"):
        signing.normalise_signature_image(b"this is not an image")


def test_create_and_fetch_own_signature(alice):
    response = alice.client.post(
        "/api/v1/signatures", headers=alice.headers,
        data={"kind": "typed", "typed_name": "Ada Lovelace", "label": "Main"},
    )
    assert response.status_code == 201
    asset_id = response.json()["id"]

    image = alice.get(f"/api/v1/signatures/{asset_id}/image")
    assert image.status_code == 200
    assert image.headers["cache-control"] == "private, no-store"
    assert Image.open(io.BytesIO(image.content)).format == "PNG"


def test_one_user_cannot_fetch_anothers_signature(alice, bob):
    """Signature assets are per-user, not per-workspace."""
    asset_id = alice.client.post(
        "/api/v1/signatures", headers=alice.headers,
        data={"kind": "typed", "typed_name": "Ada"},
    ).json()["id"]

    # Even inside a shared workspace.
    alice.post(f"/api/v1/workspaces/{alice.workspace_id}/members",
               json={"email": bob.email, "role": "member"})

    assert bob.get(f"/api/v1/signatures/{asset_id}/image").status_code == 404
    assert bob.get("/api/v1/signatures").json() == []


def test_signature_creation_is_audited_without_the_image(alice, db):
    from docintel.db.models import AuditLog

    alice.client.post("/api/v1/signatures", headers=alice.headers,
                      data={"kind": "typed", "typed_name": "Ada Lovelace"})

    entries = db.query(AuditLog).filter(
        AuditLog.action == "signature.asset_created").all()
    assert entries
    for entry in entries:
        assert "Ada Lovelace" not in (entry.detail or "")


def test_delete_signature(alice):
    asset_id = alice.client.post(
        "/api/v1/signatures", headers=alice.headers,
        data={"kind": "typed", "typed_name": "Ada"},
    ).json()["id"]

    assert alice.delete(f"/api/v1/signatures/{asset_id}").status_code == 204
    assert alice.get(f"/api/v1/signatures/{asset_id}/image").status_code == 404


# ------------------------------------------------------------ fill & sign

def test_self_sign_stamps_and_creates_a_version(alice):
    document_id = alice.upload(corpus.multipage_pdf(3)).json()["document"]["id"]
    asset_id = alice.client.post(
        "/api/v1/signatures", headers=alice.headers,
        data={"kind": "typed", "typed_name": "Ada Lovelace"},
    ).json()["id"]

    response = alice.post(f"/api/v1/documents/{document_id}/sign/self", json={
        "placements": [
            {"page": 1, "x": 72, "y": 600, "width": 180, "height": 50,
             "kind": "signature", "asset_id": asset_id},
            {"page": 2, "x": 72, "y": 300, "width": 160, "height": 20,
             "kind": "date", "text": "2026-08-15"},
            {"page": 3, "x": 72, "y": 200, "width": 20, "height": 20,
             "kind": "check"},
        ],
    })
    assert response.status_code == 200
    assert response.json()["version"] == 2
    assert "not a cryptographic digital signature" in response.json()["legal_notice"]

    signed = alice.get(f"/api/v1/documents/{document_id}/download").content
    reader = PdfReader(io.BytesIO(signed))
    assert len(reader.pages) == 3
    assert "2026-08-15" in (reader.pages[1].extract_text() or "")

    # The original is intact.
    original = alice.get(f"/api/v1/documents/{document_id}/download?version=1").content
    assert "2026-08-15" not in (
        PdfReader(io.BytesIO(original)).pages[1].extract_text() or ""
    )


def test_self_sign_cannot_use_another_users_signature(alice, bob):
    asset_id = alice.client.post(
        "/api/v1/signatures", headers=alice.headers,
        data={"kind": "typed", "typed_name": "Ada"},
    ).json()["id"]

    document_id = bob.upload(corpus.clean_pdf()).json()["document"]["id"]
    response = bob.post(f"/api/v1/documents/{document_id}/sign/self", json={
        "placements": [{"page": 1, "x": 10, "y": 10, "width": 50, "height": 20,
                        "kind": "signature", "asset_id": asset_id}],
    })
    assert response.status_code == 404


# ------------------------------------------------- request workflow

@pytest.fixture
def prepared(alice):
    """A sent two-signer request, returning ids and signing tokens."""
    document_id = alice.upload(corpus.multipage_pdf(2), name="nda.pdf") \
        .json()["document"]["id"]

    created = alice.post(
        f"/api/v1/documents/{document_id}/signature-requests",
        json={
            "title": "Mutual NDA",
            "message": "Please sign.",
            "recipients": [
                {"email": "first@example.com", "name": "First", "order": 1},
                {"email": "second@example.com", "name": "Second", "order": 2},
            ],
            "fields": [
                {"type": "signature", "page": 1, "x": 72, "y": 600,
                 "width": 180, "height": 48, "recipient_email": "first@example.com",
                 "label": "Signature 1"},
                {"type": "date", "page": 1, "x": 300, "y": 600,
                 "width": 120, "height": 24, "recipient_email": "first@example.com",
                 "label": "Date 1"},
                {"type": "signature", "page": 2, "x": 72, "y": 600,
                 "width": 180, "height": 48, "recipient_email": "second@example.com",
                 "label": "Signature 2"},
            ],
        },
    ).json()

    sent = alice.post(f"/api/v1/signature-requests/{created['id']}/send").json()
    tokens = [r["signing_path"].split("/")[-1] for r in sent["recipients"]]
    return {"document_id": document_id, "request": sent, "tokens": tokens}


def test_request_starts_as_draft_then_sends(alice):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    created = alice.post(
        f"/api/v1/documents/{document_id}/signature-requests",
        json={
            "title": "T", "recipients": [{"email": "a@example.com"}],
            "fields": [{"type": "signature", "page": 1, "x": 1, "y": 1,
                        "width": 10, "height": 10, "recipient_email": "a@example.com"}],
        },
    ).json()
    assert created["state"] == "draft"
    assert created["document_hash"] is None

    sent = alice.post(f"/api/v1/signature-requests/{created['id']}/send").json()
    assert sent["state"] == "sent"
    # The hash of what is being signed is frozen at send time.
    assert sent["document_hash"]


def test_send_requires_recipients_and_fields(alice):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    response = alice.post(
        f"/api/v1/documents/{document_id}/signature-requests",
        json={"title": "T", "recipients": [{"email": "a@example.com"}], "fields": []},
    )
    assert response.status_code == 422        # schema requires >= 1 field


def test_duplicate_recipient_is_rejected(alice):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    response = alice.post(
        f"/api/v1/documents/{document_id}/signature-requests",
        json={
            "title": "T",
            "recipients": [{"email": "a@example.com"}, {"email": "A@Example.com"}],
            "fields": [{"type": "signature", "page": 1, "x": 1, "y": 1,
                        "width": 10, "height": 10}],
        },
    )
    assert response.status_code == 400
    assert "more than once" in response.json()["detail"]


def test_field_for_unknown_recipient_is_rejected(alice):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    response = alice.post(
        f"/api/v1/documents/{document_id}/signature-requests",
        json={
            "title": "T", "recipients": [{"email": "a@example.com"}],
            "fields": [{"type": "signature", "page": 1, "x": 1, "y": 1,
                        "width": 10, "height": 10,
                        "recipient_email": "nobody@example.com"}],
        },
    )
    assert response.status_code == 400
    assert "unknown recipient" in response.json()["detail"]


def test_recipient_opens_with_token_and_sees_only_their_fields(client, prepared):
    view = client.get(f"/api/v1/sign/{prepared['tokens'][0]}").json()

    assert view["recipient"]["email"] == "first@example.com"
    assert len(view["fields"]) == 2          # not the second signer's field
    assert view["your_turn"] is True
    assert "not a cryptographic digital signature" in view["legal_notice"]


def test_invalid_token_is_refused(client):
    assert client.get("/api/v1/sign/not-a-real-token").status_code == 400


def test_recipient_can_fetch_only_the_document_being_signed(client, prepared):
    response = client.get(f"/api/v1/sign/{prepared['tokens'][0]}/document")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_full_two_signer_journey(client, alice, prepared):
    tokens = prepared["tokens"]
    request_id = prepared["request"]["id"]

    # First signer.
    first = client.get(f"/api/v1/sign/{tokens[0]}").json()
    values = {f["id"]: ("Ada Lovelace" if f["type"] == "signature" else "2026-08-15")
              for f in first["fields"]}
    result = client.post(f"/api/v1/sign/{tokens[0]}/submit", json={"values": values}).json()

    assert result["completed"] is False
    assert result["state"] == "partially_signed"
    assert result["remaining"] == 1

    # Second signer.
    second = client.get(f"/api/v1/sign/{tokens[1]}").json()
    values = {f["id"]: "Grace Hopper" for f in second["fields"]}
    result = client.post(f"/api/v1/sign/{tokens[1]}/submit", json={"values": values}).json()

    assert result["completed"] is True
    assert result["state"] == "completed"

    # Sender finalises: the marks are stamped into a new version.
    final = alice.post(f"/api/v1/signature-requests/{request_id}/finalise").json()
    assert final["signed_version"] == 2

    signed = alice.get(
        f"/api/v1/documents/{prepared['document_id']}/download").content
    reader = PdfReader(io.BytesIO(signed))
    assert "Ada Lovelace" in (reader.pages[0].extract_text() or "")
    assert "Grace Hopper" in (reader.pages[1].extract_text() or "")


def test_signing_twice_is_refused(client, prepared):
    token = prepared["tokens"][0]
    view = client.get(f"/api/v1/sign/{token}").json()
    values = {f["id"]: "x" for f in view["fields"]}

    assert client.post(f"/api/v1/sign/{token}/submit",
                       json={"values": values}).status_code == 200
    again = client.post(f"/api/v1/sign/{token}/submit", json={"values": values})
    assert again.status_code == 400
    assert "already signed" in again.json()["detail"]


def test_missing_required_field_is_refused(client, prepared):
    token = prepared["tokens"][0]
    client.get(f"/api/v1/sign/{token}")
    response = client.post(f"/api/v1/sign/{token}/submit", json={"values": {}})
    assert response.status_code == 400
    assert "required" in response.json()["detail"]


def test_recipient_cannot_fill_another_recipients_field(client, prepared):
    first = client.get(f"/api/v1/sign/{prepared['tokens'][0]}").json()
    second = client.get(f"/api/v1/sign/{prepared['tokens'][1]}").json()

    response = client.post(
        f"/api/v1/sign/{prepared['tokens'][0]}/submit",
        json={"values": {second["fields"][0]["id"]: "forged"}},
    )
    assert response.status_code == 400
    assert "not assigned to you" in response.json()["detail"]


def test_decline_stops_the_request(client, alice, prepared):
    response = client.post(f"/api/v1/sign/{prepared['tokens'][1]}/decline",
                           json={"reason": "Wrong counterparty"})
    assert response.json()["state"] == "declined"

    # And a declined request cannot then be signed by anyone else.
    view = client.get(f"/api/v1/sign/{prepared['tokens'][0]}")
    assert view.status_code == 400


def test_sequential_order_is_enforced(alice, client):
    document_id = alice.upload(corpus.multipage_pdf(2)).json()["document"]["id"]
    created = alice.post(
        f"/api/v1/documents/{document_id}/signature-requests",
        json={
            "title": "Ordered", "sequential": True,
            "recipients": [
                {"email": "one@example.com", "order": 1},
                {"email": "two@example.com", "order": 2},
            ],
            "fields": [
                {"type": "signature", "page": 1, "x": 1, "y": 1, "width": 10,
                 "height": 10, "recipient_email": "one@example.com"},
                {"type": "signature", "page": 2, "x": 1, "y": 1, "width": 10,
                 "height": 10, "recipient_email": "two@example.com"},
            ],
        },
    ).json()
    sent = alice.post(f"/api/v1/signature-requests/{created['id']}/send").json()
    tokens = [r["signing_path"].split("/")[-1] for r in sent["recipients"]]

    second = client.get(f"/api/v1/sign/{tokens[1]}").json()
    assert second["your_turn"] is False

    blocked = client.post(f"/api/v1/sign/{tokens[1]}/submit",
                          json={"values": {second["fields"][0]["id"]: "x"}})
    assert blocked.status_code == 400
    assert "not your turn" in blocked.json()["detail"]


def test_completed_request_cannot_be_cancelled(client, alice, prepared):
    for token in prepared["tokens"]:
        view = client.get(f"/api/v1/sign/{token}").json()
        client.post(f"/api/v1/sign/{token}/submit",
                    json={"values": {f["id"]: "x" for f in view["fields"]}})

    response = alice.post(
        f"/api/v1/signature-requests/{prepared['request']['id']}/cancel")
    assert response.status_code == 400


# --------------------------------------------------------- audit trail

def test_audit_trail_records_the_journey(client, alice, prepared):
    token = prepared["tokens"][0]
    view = client.get(f"/api/v1/sign/{token}").json()
    client.post(f"/api/v1/sign/{token}/submit",
                json={"values": {f["id"]: "x" for f in view["fields"]}})

    trail = alice.get(
        f"/api/v1/signature-requests/{prepared['request']['id']}/audit").json()

    events = [e["event"] for e in trail["events"]]
    assert "request.created" in events
    assert "request.sent" in events
    assert "recipient.viewed" in events
    assert "recipient.signed" in events

    # Every event is pinned to the hash of what was being signed.
    signed_event = next(e for e in trail["events"] if e["event"] == "recipient.signed")
    assert signed_event["document_hash"] == trail["document_hash"]
    assert signed_event["actor"] == "first@example.com"


def test_finalise_refuses_if_the_document_changed_after_sending(client, alice, prepared):
    """The signed output must match what the recipients actually saw."""
    for token in prepared["tokens"]:
        view = client.get(f"/api/v1/sign/{token}").json()
        client.post(f"/api/v1/sign/{token}/submit",
                    json={"values": {f["id"]: "x" for f in view["fields"]}})

    # Someone edits the source version out from under the request.
    from docintel.db.models import SignatureRequest
    from docintel.db.session import SessionLocal
    with SessionLocal() as session:
        row = session.get(SignatureRequest, prepared["request"]["id"])
        row.document_hash = "0" * 64
        session.commit()

    response = alice.post(
        f"/api/v1/signature-requests/{prepared['request']['id']}/finalise")
    assert response.status_code == 400
    assert "changed since this request was sent" in response.json()["detail"]


# ------------------------------------------------------- authorization

def test_other_tenant_cannot_see_or_control_the_request(alice, bob, prepared):
    request_id = prepared["request"]["id"]

    assert bob.get(f"/api/v1/signature-requests/{request_id}").status_code == 404
    assert bob.get(f"/api/v1/signature-requests/{request_id}/audit").status_code == 404
    assert bob.post(f"/api/v1/signature-requests/{request_id}/cancel").status_code == 404
    assert bob.post(f"/api/v1/signature-requests/{request_id}/send").status_code == 404


def test_listing_omits_signing_links_by_default(alice, prepared):
    listing = alice.get(
        f"/api/v1/documents/{prepared['document_id']}/signature-requests").json()
    for recipient in listing[0]["recipients"]:
        assert "signing_path" not in recipient


def test_sender_can_opt_in_to_signing_links(alice, prepared):
    """The sender must be able to re-copy a link after a page reload."""
    listing = alice.get(
        f"/api/v1/documents/{prepared['document_id']}"
        "/signature-requests?include_links=true").json()

    paths = [r.get("signing_path") for r in listing[0]["recipients"]]
    assert all(paths)
    assert len(set(paths)) == len(paths)


def test_a_viewer_cannot_opt_in_to_signing_links(alice, bob, prepared):
    """Read access is not enough to obtain a credential that can sign."""
    alice.post(f"/api/v1/workspaces/{alice.workspace_id}/members",
               json={"email": bob.email, "role": "viewer"})

    assert bob.get(
        f"/api/v1/documents/{prepared['document_id']}/signature-requests"
    ).status_code == 200
    assert bob.get(
        f"/api/v1/documents/{prepared['document_id']}"
        "/signature-requests?include_links=true"
    ).status_code == 403


def test_tokens_are_unguessable_and_unique(alice, prepared):
    tokens = prepared["tokens"]
    assert len(set(tokens)) == len(tokens)
    for token in tokens:
        assert len(token) >= 40
