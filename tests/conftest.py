"""
Test fixtures.

Each test gets a throwaway SQLite database and storage root, configured
before docintel is imported so the app binds to them rather than the
developer's real database.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="docintel-tests-"))
os.environ["DOCINTEL_ENVIRONMENT"] = "test"
os.environ["DOCINTEL_DATABASE_URL"] = f"sqlite:///{(_TMP / 'test.db').as_posix()}"
os.environ["DOCINTEL_STORAGE_ROOT"] = str(_TMP / "storage")
os.environ["DOCINTEL_SECRET_KEY"] = "test-secret-key-not-used-in-production-abcdefghij"
# Keep the suite fast; production uses the configured default.
os.environ["DOCINTEL_BCRYPT_ROUNDS"] = "4"
# Pin auth ON regardless of the developer's .env. Without this, a local
# DOCINTEL_AUTH_MODE=open silently disables authentication for the whole
# suite and every authorization test passes for the wrong reason.
os.environ["DOCINTEL_AUTH_MODE"] = "required"

from fastapi.testclient import TestClient  # noqa: E402

from docintel.db.models import Base  # noqa: E402
from docintel.db.session import SessionLocal, engine  # noqa: E402
from docintel.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_tables(_schema):
    """Truncate between tests so ids and counts never leak across cases."""
    yield
    with SessionLocal() as session:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


class Actor:
    """A registered user with their token, default workspace and helpers."""

    def __init__(self, client: TestClient, email: str, password: str = "correct-horse-battery"):
        self.client = client
        self.email = email
        self.password = password

        response = client.post("/api/v1/auth/register",
                               json={"email": email, "password": password})
        assert response.status_code == 201, response.text
        self.token = response.json()["access_token"]

        workspaces = client.get("/api/v1/workspaces", headers=self.headers).json()
        self.workspace_id = workspaces[0]["id"]

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def get(self, url, **kwargs):
        return self.client.get(url, headers=self.headers, **kwargs)

    def post(self, url, **kwargs):
        return self.client.post(url, headers=self.headers, **kwargs)

    def delete(self, url, **kwargs):
        return self.client.delete(url, headers=self.headers, **kwargs)

    def upload(self, data: bytes, name: str = "doc.pdf",
               mime: str = "application/pdf", workspace_id: str = None):
        return self.client.post(
            "/api/v1/documents",
            headers=self.headers,
            data={"workspace_id": workspace_id or self.workspace_id},
            files={"file": (name, data, mime)},
        )


@pytest.fixture
def alice(client) -> Actor:
    return Actor(client, "alice@example.com")


@pytest.fixture
def bob(client) -> Actor:
    return Actor(client, "bob@example.com")
