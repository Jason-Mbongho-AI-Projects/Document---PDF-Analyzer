"""
Tenant isolation.

The single most important property in the platform: no user may reach another
user's document through any route — direct fetch, download, listing, search,
job status, or enumeration. Every endpoint that accepts a document or
workspace id is exercised here from an unauthorized caller.
"""
import pdf_corpus as corpus


def test_bob_cannot_read_alices_document(alice, bob):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]

    response = bob.get(f"/api/v1/documents/{document_id}")
    # 404 not 403 — a 403 would confirm the id exists.
    assert response.status_code == 404


def test_bob_cannot_download_alices_document(alice, bob):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    assert bob.get(f"/api/v1/documents/{document_id}/download").status_code == 404


def test_bob_cannot_read_alices_security_report(alice, bob):
    document_id = alice.upload(corpus.javascript_pdf()).json()["document"]["id"]
    assert bob.get(f"/api/v1/documents/{document_id}/security").status_code == 404


def test_bob_cannot_read_alices_pages(alice, bob):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    assert bob.get(f"/api/v1/documents/{document_id}/pages").status_code == 404


def test_bob_cannot_delete_alices_document(alice, bob):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    assert bob.delete(f"/api/v1/documents/{document_id}").status_code == 404

    # And it is genuinely still there.
    assert alice.get(f"/api/v1/documents/{document_id}").status_code == 200


def test_bob_cannot_archive_alices_document(alice, bob):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    assert bob.post(f"/api/v1/documents/{document_id}/archive").status_code == 404


def test_bob_cannot_list_documents_in_alices_workspace(alice, bob):
    alice.upload(corpus.clean_pdf())
    response = bob.get(f"/api/v1/documents?workspace_id={alice.workspace_id}")
    assert response.status_code == 404


def test_bob_cannot_read_alices_workspace(alice, bob):
    assert bob.get(f"/api/v1/workspaces/{alice.workspace_id}").status_code == 404


def test_bob_cannot_list_alices_workspace_members(alice, bob):
    assert bob.get(f"/api/v1/workspaces/{alice.workspace_id}/members").status_code == 404


def test_bob_cannot_add_himself_to_alices_workspace(alice, bob):
    response = bob.post(
        f"/api/v1/workspaces/{alice.workspace_id}/members",
        json={"email": bob.email, "role": "admin"},
    )
    assert response.status_code == 404


def test_bob_cannot_upload_into_alices_workspace(alice, bob):
    response = bob.upload(corpus.clean_pdf(), workspace_id=alice.workspace_id)
    assert response.status_code == 404


def test_bob_cannot_see_alices_jobs(alice, bob):
    upload = alice.upload(corpus.clean_pdf()).json()
    job_id = upload["jobs"][0]

    assert bob.get(f"/api/v1/jobs/{job_id}").status_code == 404
    assert bob.get(f"/api/v1/jobs?workspace_id={alice.workspace_id}").status_code == 404
    assert bob.post(f"/api/v1/jobs/{job_id}/retry").status_code == 404
    assert bob.post(f"/api/v1/jobs/{job_id}/cancel").status_code == 404


def test_workspace_listing_shows_only_own_workspaces(alice, bob):
    alice_ids = {w["id"] for w in alice.get("/api/v1/workspaces").json()}
    bob_ids = {w["id"] for w in bob.get("/api/v1/workspaces").json()}

    assert alice_ids and bob_ids
    assert alice_ids.isdisjoint(bob_ids)


def test_document_search_cannot_cross_tenants(alice, bob):
    alice.upload(corpus.clean_pdf(), name="alice-secret-merger.pdf")
    bob.upload(corpus.clean_pdf(), name="bob-notes.pdf")

    found = bob.get(f"/api/v1/documents?workspace_id={bob.workspace_id}&search=secret").json()
    assert found["total"] == 0


def test_shared_workspace_grants_access(alice, bob):
    """The isolation must be membership-based, not user-based —
    once invited, Bob can legitimately read the document."""
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    assert bob.get(f"/api/v1/documents/{document_id}").status_code == 404

    invite = alice.post(
        f"/api/v1/workspaces/{alice.workspace_id}/members",
        json={"email": bob.email, "role": "member"},
    )
    assert invite.status_code == 201

    assert bob.get(f"/api/v1/documents/{document_id}").status_code == 200


def test_viewer_role_cannot_write(alice, bob):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    alice.post(
        f"/api/v1/workspaces/{alice.workspace_id}/members",
        json={"email": bob.email, "role": "viewer"},
    )

    assert bob.get(f"/api/v1/documents/{document_id}").status_code == 200
    # Read yes, write no.
    assert bob.delete(f"/api/v1/documents/{document_id}").status_code == 403
    assert bob.post(f"/api/v1/documents/{document_id}/archive").status_code == 403
    assert bob.upload(corpus.clean_pdf(), workspace_id=alice.workspace_id).status_code == 403


def test_member_cannot_manage_membership(alice, bob, client):
    from conftest import Actor
    alice.post(f"/api/v1/workspaces/{alice.workspace_id}/members",
               json={"email": bob.email, "role": "member"})

    carol = Actor(client, "carol@example.com")
    response = bob.post(
        f"/api/v1/workspaces/{alice.workspace_id}/members",
        json={"email": carol.email, "role": "member"},
    )
    assert response.status_code == 403


def test_last_owner_cannot_be_removed(alice):
    users = alice.get(f"/api/v1/workspaces/{alice.workspace_id}/members").json()
    owner_id = users[0]["user_id"]

    response = alice.delete(
        f"/api/v1/workspaces/{alice.workspace_id}/members/{owner_id}"
    )
    assert response.status_code == 409


def test_enumeration_of_random_ids_returns_404(alice):
    for fake in ("0" * 32, "deadbeef" * 4, "does-not-exist"):
        assert alice.get(f"/api/v1/documents/{fake}").status_code == 404
        assert alice.get(f"/api/v1/workspaces/{fake}").status_code == 404
