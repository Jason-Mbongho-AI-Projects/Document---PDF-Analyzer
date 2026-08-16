"""
Document version service.

Every PDF operation produces a NEW version. The original is never overwritten
— that is the guarantee the editor, redaction, signing and translation
features all depend on.

Storage is content-addressed by hash: if an operation happens to produce bytes
identical to an existing version, the new version row points at the existing
object rather than storing a second copy.
"""
from dataclasses import dataclass
from typing import Callable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from docintel.core import audit
from docintel.db.models import Document, DocumentVersion, User
from docintel.pdf.engine import PDFEngineError
from docintel.storage import build_key, content_hash, storage


@dataclass
class VersionResult:
    version: DocumentVersion
    reused_existing_bytes: bool
    size_bytes: int


def latest_version(session: Session, document: Document) -> DocumentVersion:
    version = session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version.desc())
        .limit(1)
    ).first()
    if version is None:
        raise PDFEngineError("This document has no stored content.")
    return version


def get_version(session: Session, document: Document,
                number: Optional[int] = None) -> DocumentVersion:
    if number is None:
        return latest_version(session, document)

    version = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version == number,
        )
    )
    if version is None:
        raise PDFEngineError(f"Version {number} does not exist for this document.")
    return version


def read_version(session: Session, document: Document,
                 number: Optional[int] = None) -> bytes:
    return storage.get(get_version(session, document, number).storage_key)


def add_version(
    session: Session,
    document: Document,
    data: bytes,
    label: str,
    *,
    actor: Optional[User] = None,
    action: Optional[str] = None,
    detail: Optional[str] = None,
) -> VersionResult:
    """Append a derived version. Never mutates an existing one."""
    if not data:
        raise PDFEngineError("The operation produced no output.")

    highest = session.scalar(
        select(func.max(DocumentVersion.version))
        .where(DocumentVersion.document_id == document.id)
    ) or 0
    number = highest + 1

    digest = content_hash(data)

    # Content addressing: reuse the object if these exact bytes already exist
    # for this document.
    twin = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.content_hash == digest,
        )
    )

    if twin is not None:
        key = twin.storage_key
        reused = True
    else:
        key = build_key(document.workspace_id, document.id, number, label)
        storage.put(key, data)
        reused = False

    version = DocumentVersion(
        document_id=document.id,
        version=number,
        label=label,
        storage_key=key,
        size_bytes=len(data),
        content_hash=digest,
    )
    session.add(version)

    # Page count can change with almost any structural operation.
    try:
        from docintel.pdf.engine import get_engine
        document.page_count = get_engine().page_count(data)
    except Exception:
        pass

    if action:
        audit.record(
            session, action=action, actor=actor,
            workspace_id=document.workspace_id, document_id=document.id,
            detail=(detail or f"version {number} ({label})")[:500],
        )

    session.flush()
    return VersionResult(version=version, reused_existing_bytes=reused,
                         size_bytes=len(data))


def apply_operation(
    session: Session,
    document: Document,
    operation: Callable[[bytes], bytes],
    *,
    label: str,
    action: str,
    actor: Optional[User] = None,
    source_version: Optional[int] = None,
    detail: Optional[str] = None,
) -> VersionResult:
    """Run an engine operation over a version and store the result as a new one."""
    source = read_version(session, document, source_version)
    output = operation(source)
    return add_version(session, document, output, label,
                       actor=actor, action=action, detail=detail)
