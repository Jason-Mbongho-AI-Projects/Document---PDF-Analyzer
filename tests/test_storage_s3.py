"""
S3 storage driver tests.

These run against a fake client rather than AWS: the value being proved is
that the provider honours the StorageProvider contract, and specifically that
it tells "no such object" apart from "the call failed". Getting that wrong is
how a permissions error becomes a silently missing document.

The fake implements only the five S3 calls the provider makes, and raises
botocore-shaped errors so the error-classification path is exercised for real.
"""
import io

import pytest

from docintel.storage import StorageError, StorageProvider
from docintel.storage.s3 import S3StorageProvider


class FakeClientError(Exception):
    """Shaped like botocore.exceptions.ClientError."""

    def __init__(self, code: str, status: int = 400):
        super().__init__(code)
        self.response = {
            "Error": {"Code": code},
            "ResponseMetadata": {"HTTPStatusCode": status},
        }


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.calls: list[str] = []
        self.fail_with: Exception | None = None

    def _maybe_fail(self):
        if self.fail_with:
            raise self.fail_with

    def put_object(self, Bucket, Key, Body):
        self.calls.append("put")
        self._maybe_fail()
        self.objects[Key] = Body
        return {}

    def get_object(self, Bucket, Key):
        self.calls.append("get")
        self._maybe_fail()
        if Key not in self.objects:
            raise FakeClientError("NoSuchKey", 404)
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):
        self.calls.append("head")
        self._maybe_fail()
        if Key not in self.objects:
            raise FakeClientError("404", 404)
        return {"ContentLength": len(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.calls.append("delete")
        self._maybe_fail()
        self.objects.pop(Key, None)
        return {}

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        self.calls.append("list")
        self._maybe_fail()

        # S3's continuation token marks the last key returned, not an index
        # into the result set. That distinction matters here: this provider
        # deletes each page before requesting the next, so a positional token
        # would skip keys as the listing shrank underneath it. Emulating the
        # real key-based behaviour is what makes this test meaningful.
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        if ContinuationToken:
            keys = [k for k in keys if k > ContinuationToken]

        page = keys[:2]     # small page so the continuation path is exercised
        truncated = len(keys) > 2
        return {
            "Contents": [{"Key": k} for k in page],
            "IsTruncated": truncated,
            "NextContinuationToken": page[-1] if truncated and page else None,
        }

    def delete_objects(self, Bucket, Delete):
        self.calls.append("delete_objects")
        self._maybe_fail()
        for item in Delete["Objects"]:
            self.objects.pop(item["Key"], None)
        return {}


@pytest.fixture
def provider():
    fake = FakeS3()
    return S3StorageProvider(bucket="test-bucket", client=fake), fake


KEY = "ws1/doc1/v1-original"


def test_implements_the_storage_interface(provider):
    store, _ = provider
    assert isinstance(store, StorageProvider)


def test_round_trips_bytes(provider):
    store, _ = provider
    written = store.put(KEY, b"%PDF-1.7 hello")

    assert written == len(b"%PDF-1.7 hello")
    assert store.get(KEY) == b"%PDF-1.7 hello"


def test_open_returns_a_readable_stream(provider):
    store, _ = provider
    store.put(KEY, b"stream me")

    with store.open(KEY) as handle:
        assert handle.read() == b"stream me"


def test_missing_object_raises_not_found(provider):
    store, _ = provider
    with pytest.raises(StorageError, match="object not found"):
        store.get("ws1/doc1/v9-original")


def test_exists_reports_presence(provider):
    store, _ = provider
    assert store.exists(KEY) is False
    store.put(KEY, b"x")
    assert store.exists(KEY) is True


def test_delete_is_idempotent(provider):
    store, _ = provider
    store.put(KEY, b"x")
    store.delete(KEY)
    store.delete(KEY)          # already gone — must not raise
    assert store.exists(KEY) is False


def test_rejects_a_key_that_fails_validation(provider):
    store, fake = provider
    with pytest.raises(StorageError, match="invalid storage key"):
        store.put("../../etc/passwd", b"x")
    assert fake.calls == []    # nothing reached the wire


def test_exists_is_false_for_an_invalid_key(provider):
    store, _ = provider
    assert store.exists("not a key") is False


def test_refuses_to_delete_an_empty_prefix(provider):
    store, fake = provider
    store.put(KEY, b"x")

    for prefix in ("", "/", "..."):
        with pytest.raises(StorageError, match="refusing to delete an empty prefix"):
            store.delete_prefix(prefix)

    assert store.exists(KEY) is True


def test_delete_prefix_removes_everything_under_it(provider):
    store, _ = provider
    for version in range(1, 6):
        store.put(f"ws1/doc1/v{version}-original", b"x")
    store.put("ws2/doc9/v1-original", b"keep me")

    removed = store.delete_prefix("ws1/doc1")

    assert removed == 5
    assert store.exists("ws2/doc9/v1-original") is True


def test_delete_prefix_pages_through_a_truncated_listing(provider):
    store, fake = provider
    for version in range(1, 8):
        store.put(f"ws1/doc1/v{version}-original", b"x")

    removed = store.delete_prefix("ws1/doc1")

    assert removed == 7
    assert fake.calls.count("list") > 1     # proves it followed the token


def test_a_real_failure_is_not_mistaken_for_a_missing_object(provider):
    """AccessDenied must raise, not read as 'the file is not there'.

    Treating a permissions failure as absence is how a document that exists
    gets reported as deleted, so this distinction is the whole point of the
    error classification.
    """
    store, fake = provider
    fake.fail_with = FakeClientError("AccessDenied", 403)

    with pytest.raises(StorageError, match="could not read object"):
        store.get(KEY)

    with pytest.raises(StorageError, match="could not stat object"):
        store.exists(KEY)

    with pytest.raises(StorageError, match="could not delete object"):
        store.delete(KEY)


def test_put_failure_surfaces(provider):
    store, fake = provider
    fake.fail_with = FakeClientError("SlowDown", 503)

    with pytest.raises(StorageError, match="could not store object"):
        store.put(KEY, b"x")


def test_requires_a_bucket():
    with pytest.raises(StorageError, match="DOCINTEL_S3_BUCKET"):
        S3StorageProvider(bucket="", client=FakeS3())
