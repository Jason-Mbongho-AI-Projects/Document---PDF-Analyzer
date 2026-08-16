"""
Open-access mode.

Two properties matter and are asserted here:

  1. Open mode really does let an unauthenticated caller in — otherwise the
     development convenience does not work.
  2. Open mode disables AUTHENTICATION ONLY. Authorization, object-level
     checks and tenant isolation must behave exactly as before, so switching
     it off cannot be hiding a broken permission check.

Plus the guard that stops it ever reaching production.
"""
import importlib

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import pdf_corpus as corpus
from docintel.config import Settings, settings
from docintel.db.models import User
from docintel.main import app


@pytest.fixture
def open_mode(monkeypatch):
    """Flip the running app into open access for one test."""
    monkeypatch.setattr(settings, "auth_mode", "open")
    yield
    # monkeypatch restores the attribute automatically.


# ------------------------------------------------------- production guard

def build(**overrides) -> Settings:
    """Settings built in isolation.

    _env_file=None keeps the developer's own .env out of these assertions —
    otherwise a local DOCINTEL_AUTH_MODE=open would make the "secure by
    default" test pass or fail depending on whose machine it runs on.
    """
    return Settings(_env_file=None, **overrides)


def test_open_mode_is_refused_in_production():
    """A misconfigured deploy must fail to start, not start insecurely."""
    with pytest.raises(ValidationError) as caught:
        build(environment="production", auth_mode="open", secret_key="x" * 48)

    assert "not permitted" in str(caught.value)


def test_open_mode_is_allowed_outside_production():
    for environment in ("development", "test"):
        assert build(environment=environment, auth_mode="open").auth_open is True


def test_required_is_the_default():
    """A fresh configuration is closed unless deliberately opened."""
    assert build(environment="development").auth_mode == "required"


# ------------------------------------------------------------ mode probe

def test_mode_endpoint_is_public(client):
    response = client.get("/api/v1/auth/mode")
    assert response.status_code == 200
    assert response.json()["mode"] == "required"
    assert response.json()["open_access"] is False
    assert response.json()["warning"] is None


def test_mode_endpoint_reports_open_access(client, open_mode):
    body = client.get("/api/v1/auth/mode").json()
    assert body["open_access"] is True
    assert "Authentication is disabled" in body["warning"]


# --------------------------------------------------- required mode holds

def test_required_mode_rejects_anonymous(client):
    assert client.get("/api/v1/workspaces").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401


# ------------------------------------------------------ open mode works

def test_open_mode_admits_anonymous_callers(client, open_mode):
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == settings.dev_user_email


def test_open_mode_provisions_a_workspace(client, open_mode):
    spaces = client.get("/api/v1/workspaces").json()
    assert len(spaces) == 1
    assert spaces[0]["role"] == "owner"


def test_open_mode_supports_the_full_document_flow(client, open_mode):
    workspace = client.get("/api/v1/workspaces").json()[0]["id"]

    upload = client.post(
        "/api/v1/documents",
        data={"workspace_id": workspace},
        files={"file": ("demo.pdf", corpus.multipage_pdf(2), "application/pdf")},
    )
    assert upload.status_code == 201

    document_id = upload.json()["document"]["id"]
    assert client.get(f"/api/v1/documents/{document_id}").status_code == 200
    assert client.get(f"/api/v1/documents/{document_id}/download").status_code == 200


def test_open_mode_reuses_one_dev_user(client, open_mode, db):
    for _ in range(3):
        client.get("/api/v1/auth/me")

    users = db.query(User).filter(User.email == settings.dev_user_email).all()
    assert len(users) == 1


def test_dev_user_password_is_unusable(client, open_mode, db):
    """Once auth is switched back on, nobody can sign in as the dev account."""
    client.get("/api/v1/auth/me")          # provision it

    for attempt in ("password", "", "dev", settings.dev_user_email):
        response = client.post("/api/v1/auth/login", json={
            "email": settings.dev_user_email, "password": attempt,
        })
        assert response.status_code != 200, f"signed in with {attempt!r}"
        assert "access_token" not in response.text


# --------------------------------- authorization still applies in open mode

def test_open_mode_still_enforces_tenant_isolation(client, alice, open_mode):
    """The critical property: open access must not become open data.

    Alice's document belongs to Alice's workspace. The anonymous dev user is
    in a different workspace and must still be refused.
    """
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]

    # Anonymous (dev user) — no Authorization header at all.
    assert client.get(f"/api/v1/documents/{document_id}").status_code == 404
    assert client.get(f"/api/v1/documents/{document_id}/download").status_code == 404
    assert client.get(f"/api/v1/documents/{document_id}/text").status_code == 404
    assert client.post(f"/api/v1/documents/{document_id}/pages/rotate",
                       json={"pages": [1], "degrees": 90}).status_code == 404


def test_open_mode_cannot_list_another_workspace(client, alice, open_mode):
    response = client.get(f"/api/v1/documents?workspace_id={alice.workspace_id}")
    assert response.status_code == 404


def test_a_supplied_token_still_wins_in_open_mode(client, alice, open_mode):
    """Signing in while open access is on gives you your own identity."""
    me = client.get("/api/v1/auth/me", headers=alice.headers).json()
    assert me["email"] == alice.email

    anonymous = client.get("/api/v1/auth/me").json()
    assert anonymous["email"] == settings.dev_user_email


def test_an_invalid_token_is_still_rejected_in_open_mode(client, open_mode):
    """Open mode must not turn a bad token into a silent anonymous session."""
    response = client.get("/api/v1/auth/me",
                          headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401
