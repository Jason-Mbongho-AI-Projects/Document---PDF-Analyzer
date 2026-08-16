"""Authentication and token handling."""
import time

import jwt
import pytest

from docintel.config import settings
from docintel.core.security import (
    MAX_PASSWORD_BYTES, TokenError, create_access_token,
    decode_access_token, hash_password, verify_password,
)


# ------------------------------------------------------------- hashing

def test_password_round_trip():
    digest = hash_password("correct-horse-battery")
    assert digest != "correct-horse-battery"
    assert verify_password("correct-horse-battery", digest)
    assert not verify_password("wrong", digest)


def test_hashes_are_salted():
    assert hash_password("same-password") != hash_password("same-password")


def test_overlong_password_is_rejected_not_truncated():
    """bcrypt truncates at 72 bytes; two long passwords must not collide."""
    with pytest.raises(ValueError):
        hash_password("x" * (MAX_PASSWORD_BYTES + 1))


def test_verify_rejects_garbage_hash():
    assert not verify_password("anything", "not-a-bcrypt-hash")


# -------------------------------------------------------------- tokens

def test_token_round_trip():
    token = create_access_token("user-123")
    assert decode_access_token(token) == "user-123"


def test_expired_token_is_rejected():
    token = create_access_token("user-123", ttl_minutes=-1)
    with pytest.raises(TokenError):
        decode_access_token(token)


def test_token_signed_with_another_key_is_rejected():
    forged = jwt.encode({"sub": "user-123", "exp": 9999999999, "typ": "access"},
                        "attacker-key", algorithm="HS256")
    with pytest.raises(TokenError):
        decode_access_token(forged)


def test_alg_none_token_is_rejected():
    """The classic JWT bypass: unsigned token claiming alg=none."""
    forged = jwt.encode({"sub": "user-123", "exp": 9999999999, "typ": "access"},
                        key="", algorithm="none")
    with pytest.raises(TokenError):
        decode_access_token(forged)


def test_wrong_token_type_is_rejected():
    token = jwt.encode({"sub": "u", "exp": 9999999999, "typ": "refresh"},
                       settings.secret_key, algorithm="HS256")
    with pytest.raises(TokenError):
        decode_access_token(token)


# ------------------------------------------------------------ endpoints

def test_register_returns_token_and_creates_workspace(client):
    response = client.post("/api/v1/auth/register",
                           json={"email": "new@example.com", "password": "a-good-password"})
    assert response.status_code == 201
    token = response.json()["access_token"]

    workspaces = client.get("/api/v1/workspaces",
                            headers={"Authorization": f"Bearer {token}"}).json()
    assert len(workspaces) == 1
    assert workspaces[0]["role"] == "owner"


def test_duplicate_registration_is_rejected(client):
    payload = {"email": "dupe@example.com", "password": "a-good-password"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    assert client.post("/api/v1/auth/register", json=payload).status_code == 409


def test_short_password_is_rejected(client):
    response = client.post("/api/v1/auth/register",
                           json={"email": "x@example.com", "password": "short"})
    assert response.status_code == 422


def test_login_succeeds_and_me_returns_the_user(client):
    client.post("/api/v1/auth/register",
                json={"email": "login@example.com", "password": "a-good-password"})

    response = client.post("/api/v1/auth/login",
                           json={"email": "login@example.com", "password": "a-good-password"})
    assert response.status_code == 200

    token = response.json()["access_token"]
    me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "login@example.com"


def test_login_with_wrong_password_fails(client):
    client.post("/api/v1/auth/register",
                json={"email": "wrong@example.com", "password": "a-good-password"})
    response = client.post("/api/v1/auth/login",
                           json={"email": "wrong@example.com", "password": "not-the-password"})
    assert response.status_code == 401


def test_login_for_unknown_user_is_indistinguishable(client):
    client.post("/api/v1/auth/register",
                json={"email": "known@example.com", "password": "a-good-password"})

    unknown = client.post("/api/v1/auth/login",
                          json={"email": "nobody@example.com", "password": "a-good-password"})
    known_bad = client.post("/api/v1/auth/login",
                            json={"email": "known@example.com", "password": "bad-password-here"})

    assert unknown.status_code == known_bad.status_code == 401
    assert unknown.json() == known_bad.json()


def test_email_is_normalised_to_lowercase(client):
    client.post("/api/v1/auth/register",
                json={"email": "Mixed@Example.com", "password": "a-good-password"})
    response = client.post("/api/v1/auth/login",
                           json={"email": "mixed@example.com", "password": "a-good-password"})
    assert response.status_code == 200


# ------------------------------------------------------- unauthenticated

@pytest.mark.parametrize("method,url", [
    ("get", "/api/v1/auth/me"),
    ("get", "/api/v1/workspaces"),
    ("post", "/api/v1/workspaces"),
    ("get", "/api/v1/documents?workspace_id=x"),
    ("get", "/api/v1/documents/abc"),
    ("get", "/api/v1/documents/abc/download"),
    ("get", "/api/v1/documents/abc/security"),
    ("get", "/api/v1/jobs?workspace_id=x"),
])
def test_protected_endpoints_require_a_token(client, method, url):
    response = getattr(client, method)(url)
    assert response.status_code == 401


def test_malformed_bearer_token_is_rejected(client):
    for value in ("Bearer", "Bearer ", "Bearer not.a.jwt", "Basic abc123"):
        response = client.get("/api/v1/auth/me", headers={"Authorization": value})
        assert response.status_code == 401, value


def test_health_is_public(client):
    assert client.get("/health").status_code == 200
    assert client.get("/health/ready").status_code == 200
