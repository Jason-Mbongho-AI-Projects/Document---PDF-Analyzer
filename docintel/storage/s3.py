"""
S3-compatible object storage.

Built and tested but not configured: `DOCINTEL_STORAGE_DRIVER` still defaults
to `local`, and nothing here runs until that is changed deliberately.

boto3 is imported inside the constructor rather than at module scope so that
the dependency is only required by deployments that actually select this
driver. A local clone with no AWS packages installed imports this module
happily; it fails, with a message naming the missing package, only if someone
tries to construct the provider.

Anything S3-compatible works — MinIO, Cloudflare R2, Backblaze B2 — via
`DOCINTEL_S3_ENDPOINT_URL`. Credentials are never read from settings; they come
from boto3's own chain (environment, shared config, instance role), so a
deployment on EC2 or ECS needs no secrets in the application config at all.
"""
from __future__ import annotations

import io
import re
from typing import BinaryIO

from docintel.config import settings
from docintel.storage import SAFE_KEY, StorageError, StorageProvider


class S3StorageProvider(StorageProvider):
    def __init__(
        self,
        bucket: str | None = None,
        region: str | None = None,
        endpoint_url: str | None = None,
        client=None,
    ):
        self.bucket = bucket or settings.s3_bucket
        if not self.bucket:
            raise StorageError(
                "storage driver 's3' requires DOCINTEL_S3_BUCKET to be set."
            )

        if client is not None:
            # Injected client — used by the tests, and by anyone who needs to
            # hand in a pre-configured session.
            self.client = client
            return

        try:
            import boto3
        except ModuleNotFoundError as exc:      # pragma: no cover - env-specific
            raise StorageError(
                "storage driver 's3' requires boto3. Install it with: "
                "pip install boto3"
            ) from exc

        self.client = boto3.client(
            "s3",
            region_name=region or settings.s3_region or None,
            endpoint_url=endpoint_url or settings.s3_endpoint_url or None,
        )

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _check(key: str) -> str:
        # Same validation as the local driver. S3 has no directory traversal to
        # exploit, but a key that fails here is a bug or an attack either way,
        # and the two drivers must not disagree about what a valid key is.
        if not SAFE_KEY.match(key):
            raise StorageError("invalid storage key")
        return key

    def _is_missing(self, exc: Exception) -> bool:
        """True when the error means 'no such object', not a real failure.

        A missing object must surface as StorageError('object not found') and
        never be confused with a permissions or connectivity problem, which
        would otherwise look identical to the caller and get treated as a
        deleted file.
        """
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
        status = (
            getattr(exc, "response", {})
            .get("ResponseMetadata", {})
            .get("HTTPStatusCode")
        )
        return code in {"NoSuchKey", "404", "NotFound"} or status == 404

    # -- interface -------------------------------------------------------

    def put(self, key: str, data: bytes) -> int:
        self._check(key)
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        except Exception as exc:
            raise StorageError(f"could not store object: {exc}") from exc
        return len(data)

    def get(self, key: str) -> bytes:
        self._check(key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if self._is_missing(exc):
                raise StorageError("object not found") from exc
            raise StorageError(f"could not read object: {exc}") from exc
        return response["Body"].read()

    def open(self, key: str) -> BinaryIO:
        # S3 has no seekable stream without extra machinery, and every caller
        # here reads the whole object anyway (PDFs are parsed in full). Reading
        # into a buffer keeps the contract identical to the local driver.
        return io.BytesIO(self.get(key))

    def delete(self, key: str) -> None:
        self._check(key)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if self._is_missing(exc):
                return          # deleting what is already gone is a success
            raise StorageError(f"could not delete object: {exc}") from exc

    def exists(self, key: str) -> bool:
        try:
            self._check(key)
        except StorageError:
            return False
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:
            if self._is_missing(exc):
                return False
            raise StorageError(f"could not stat object: {exc}") from exc

    def delete_prefix(self, prefix: str) -> int:
        safe = re.sub(r"[^a-zA-Z0-9/_-]", "", prefix).strip("/")
        if not safe:
            # Guarding this is the difference between deleting one workspace
            # and emptying the bucket.
            raise StorageError("refusing to delete an empty prefix")

        removed = 0
        token: str | None = None
        try:
            while True:
                kwargs = {"Bucket": self.bucket, "Prefix": f"{safe}/"}
                if token:
                    kwargs["ContinuationToken"] = token
                page = self.client.list_objects_v2(**kwargs)

                keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                if keys:
                    # delete_objects takes 1000 at a time, which is also the
                    # page size list_objects_v2 returns, so one call per page.
                    self.client.delete_objects(
                        Bucket=self.bucket, Delete={"Objects": keys},
                    )
                    removed += len(keys)

                if not page.get("IsTruncated"):
                    break
                token = page.get("NextContinuationToken")
                if not token:
                    break
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"could not delete prefix: {exc}") from exc

        return removed
