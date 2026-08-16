"""
Storage provider abstraction.

Callers deal in opaque storage keys, never filesystem paths. A key is never
echoed to a client, and the local driver refuses any key that would escape
its root, so a crafted key cannot be turned into a path traversal.

Content is addressed by hash and namespaced by workspace, which gives
per-tenant separation and lets identical uploads share bytes later without
changing the interface.
"""
import hashlib
import os
import re
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Iterator

from docintel.config import settings

# Storage keys we generate ourselves; this validates them on the way back in.
SAFE_KEY = re.compile(r"^[a-z0-9]+(?:/[a-zA-Z0-9._-]+)+$")


class StorageError(RuntimeError):
    pass


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_key(workspace_id: str, document_id: str, version: int, kind: str = "original") -> str:
    """Namespace: <workspace>/<document>/v<version>-<kind>"""
    safe_kind = re.sub(r"[^a-zA-Z0-9_-]", "", kind) or "file"
    return f"{workspace_id}/{document_id}/v{version}-{safe_kind}"


class StorageProvider(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes) -> int:
        """Store bytes, returning the number written."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        ...

    @abstractmethod
    def open(self, key: str) -> BinaryIO:
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> int:
        """Remove everything under a prefix. Used by retention and deletion."""


class LocalStorageProvider(StorageProvider):
    def __init__(self, root: Path | None = None):
        self.root = Path(root or settings.storage_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        if not SAFE_KEY.match(key):
            raise StorageError("invalid storage key")

        candidate = (self.root / key).resolve()
        # Definitive containment check — symlinks and .. are both caught here
        # rather than by inspecting the key string.
        if not candidate.is_relative_to(self.root):
            raise StorageError("storage key escapes the storage root")
        return candidate

    def put(self, key: str, data: bytes) -> int:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file then rename, so a crash cannot leave a
        # half-written object visible under the real key.
        temp = path.with_suffix(path.suffix + ".partial")
        with open(temp, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
        return len(data)

    def get(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.exists():
            raise StorageError("object not found")
        return path.read_bytes()

    def open(self, key: str) -> BinaryIO:
        path = self._resolve(key)
        if not path.exists():
            raise StorageError("object not found")
        return open(path, "rb")

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        if path.exists():
            path.unlink()

    def exists(self, key: str) -> bool:
        try:
            return self._resolve(key).exists()
        except StorageError:
            return False

    def delete_prefix(self, prefix: str) -> int:
        safe = re.sub(r"[^a-zA-Z0-9/_-]", "", prefix).strip("/")
        if not safe:
            raise StorageError("refusing to delete an empty prefix")

        target = (self.root / safe).resolve()
        if not target.is_relative_to(self.root) or target == self.root:
            raise StorageError("refusing to delete outside the storage root")

        if not target.exists():
            return 0
        count = sum(1 for _ in target.rglob("*") if _.is_file())
        shutil.rmtree(target)
        return count


def get_storage() -> StorageProvider:
    if settings.storage_driver == "local":
        return LocalStorageProvider()
    if settings.storage_driver == "s3":
        # Imported here, not at module scope: the S3 driver pulls in boto3, and
        # a local clone must not need it to start.
        from docintel.storage.s3 import S3StorageProvider

        return S3StorageProvider()
    raise StorageError(
        f"storage driver '{settings.storage_driver}' is not recognised. "
        "Available drivers: local, s3."
    )


storage = get_storage()
