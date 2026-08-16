"""
Database schema.

One extensible `documents` table serves every file type rather than separate
pdf_documents / user_documents tables. Derived artefacts (versions, pages,
extracted content, analyses) hang off it, so adding OCR or conversion later
means adding rows and job types, not a parallel document model.

Every tenant-scoped row carries workspace_id. Authorization is enforced by
joining through workspace_members — there is no code path that reads a
document without proving membership first.
"""
import enum
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer,
    JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# ----------------------------------------------------------------- enums

class Role(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    VIEWER = "viewer"

    @property
    def can_write(self) -> bool:
        return self in (Role.OWNER, Role.ADMIN, Role.MEMBER)

    @property
    def can_manage(self) -> bool:
        return self in (Role.OWNER, Role.ADMIN)


class DocumentStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class JobState(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobType(str, enum.Enum):
    INGEST = "ingest"
    SECURITY_SCAN = "security_scan"
    ANALYZE = "analyze"


# ----------------------------------------------------------------- tenancy

class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    workspaces: Mapped[list["Workspace"]] = relationship(back_populates="organization")


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    memberships: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    organization: Mapped[Organization] = relationship(back_populates="workspaces")
    members: Mapped[list["WorkspaceMember"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )


class WorkspaceMember(Base, TimestampMixin):
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_member"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False), default=Role.MEMBER, nullable=False)

    workspace: Mapped[Workspace] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


# --------------------------------------------------------------- documents

class Document(Base, TimestampMixin):
    """One row per uploaded file, of any type."""
    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_workspace_created", "workspace_id", "created_at"),
        Index("ix_documents_workspace_hash", "workspace_id", "content_hash"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False
    )
    uploaded_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, native_enum=False), default=DocumentStatus.UPLOADED, nullable=False
    )
    status_detail: Mapped[Optional[str]] = mapped_column(Text)

    page_count: Mapped[Optional[int]] = mapped_column(Integer)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Derived profile: title/author/producer/dimensions/etc. Kept as JSON so
    # new extractors can add keys without a migration.
    doc_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan",
        order_by="DocumentVersion.version",
    )
    analyses: Mapped[list["DocumentAnalysis"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    findings: Mapped[list["SecurityFinding"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentVersion(Base, TimestampMixin):
    """Immutable pointer to stored bytes. Version 1 is the original upload;
    OCR, redaction and conversion append rather than overwrite."""
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_version"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(80), default="original", nullable=False)

    # Opaque key resolved by the storage provider. Never a filesystem path
    # exposed to a client.
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    document: Mapped[Document] = relationship(back_populates="versions")


class DocumentAnalysis(Base, TimestampMixin):
    """Result of one analysis pass, keyed by kind so modes coexist."""
    __tablename__ = "document_analyses"
    __table_args__ = (
        Index("ix_analysis_document_kind", "document_id", "kind"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    document: Mapped[Document] = relationship(back_populates="analyses")


class AnnotationKind(str, enum.Enum):
    HIGHLIGHT = "highlight"
    UNDERLINE = "underline"
    STRIKETHROUGH = "strikethrough"
    NOTE = "note"
    COMMENT = "comment"
    DRAWING = "drawing"
    SHAPE = "shape"
    ARROW = "arrow"
    TEXTBOX = "textbox"
    STAMP = "stamp"


class Annotation(Base, TimestampMixin):
    """A viewer-layer annotation.

    Stored separately from the PDF bytes so annotating never rewrites the
    document, and so comments can be threaded, resolved and queried. Geometry
    is kept in PDF user-space points relative to the page, which keeps a
    highlight aligned with its text at any zoom level.
    """
    __tablename__ = "document_annotations"
    __table_args__ = (
        Index("ix_annotations_document_page", "document_id", "page"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    author_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    kind: Mapped[AnnotationKind] = mapped_column(
        Enum(AnnotationKind, native_enum=False), nullable=False
    )
    page: Mapped[int] = mapped_column(Integer, nullable=False)

    # Bounding box and, for text markup, the individual line rectangles.
    rect: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    quads: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    colour: Mapped[str] = mapped_column(String(20), default="#FFD54F", nullable=False)
    opacity: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # The text the annotation covers, kept for search and for showing context
    # in a comments panel.
    selected_text: Mapped[Optional[str]] = mapped_column(Text)
    body: Mapped[Optional[str]] = mapped_column(Text)

    # Threading and resolution for comments.
    parent_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("document_annotations.id", ondelete="CASCADE"), index=True
    )
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(32))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    extra: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    replies: Mapped[list["Annotation"]] = relationship(
        back_populates="parent", cascade="all, delete-orphan",
        remote_side=None, foreign_keys=[parent_id],
    )
    parent: Mapped[Optional["Annotation"]] = relationship(
        back_populates="replies", remote_side=[id], foreign_keys=[parent_id],
    )


class SecurityFinding(Base, TimestampMixin):
    __tablename__ = "document_security_findings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    finding_id: Mapped[str] = mapped_column(String(60), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    locations: Mapped[str] = mapped_column(Text, default="", nullable=False)

    document: Mapped[Document] = relationship(back_populates="findings")


# -------------------------------------------------------------------- jobs

class Job(Base, TimestampMixin):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        Index("ix_jobs_state_created", "state", "created_at"),
        Index("ix_jobs_workspace", "workspace_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False
    )
    document_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[JobType] = mapped_column(Enum(JobType, native_enum=False), nullable=False)
    state: Mapped[JobState] = mapped_column(
        Enum(JobState, native_enum=False), default=JobState.QUEUED, index=True, nullable=False
    )

    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text)

    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    progress_note: Mapped[Optional[str]] = mapped_column(String(300))

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)

    # Idempotency: a repeated enqueue with the same key returns the existing
    # job instead of duplicating work.
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(120), unique=True, index=True)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[Optional[str]] = mapped_column(String(80))


# ------------------------------------------------------- audit and metering

class AuditLog(Base):
    """Append-only record of who did what.

    Deliberately stores no document content — only identifiers, so an audit
    trail can never become an exfiltration channel.
    """
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_workspace_time", "workspace_id", "created_at"),
        Index("ix_audit_actor_time", "actor_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )

    actor_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    actor_email: Mapped[Optional[str]] = mapped_column(String(320))
    workspace_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    document_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)

    action: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    result: Mapped[str] = mapped_column(String(20), default="success", nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(String(500))
    ip_address: Mapped[Optional[str]] = mapped_column(String(64))


class UsageRecord(Base):
    """Per-operation metering, for cost attribution."""
    __tablename__ = "usage_records"
    __table_args__ = (
        Index("ix_usage_workspace_time", "workspace_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )

    workspace_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    document_id: Mapped[Optional[str]] = mapped_column(String(32))

    operation: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    units: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    unit_kind: Mapped[str] = mapped_column(String(30), default="count", nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String(120))


# --------------------------------------------------------------- signing

class SignatureRequestState(str, enum.Enum):
    DRAFT = "draft"
    SENT = "sent"
    DELIVERED = "delivered"
    VIEWED = "viewed"
    PARTIALLY_SIGNED = "partially_signed"
    COMPLETED = "completed"
    DECLINED = "declined"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class RecipientState(str, enum.Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    VIEWED = "viewed"
    SIGNED = "signed"
    DECLINED = "declined"


class SignatureFieldType(str, enum.Enum):
    SIGNATURE = "signature"
    INITIAL = "initial"
    NAME = "name"
    EMAIL = "email"
    DATE = "date"
    TEXT = "text"
    CHECKBOX = "checkbox"
    DROPDOWN = "dropdown"


class SignatureAsset(Base, TimestampMixin):
    """A saved signature belonging to one user.

    Never public, never shared, never logged. The image bytes live behind the
    storage provider under a key that is only ever resolved after the owning
    user has been authenticated — there is no URL that serves these.
    """
    __tablename__ = "signature_assets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    label: Mapped[str] = mapped_column(String(80), default="Signature", nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="drawn", nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    width: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SignatureRequest(Base, TimestampMixin):
    __tablename__ = "signature_requests"
    __table_args__ = (
        Index("ix_sigreq_workspace_state", "workspace_id", "state"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    created_by: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text)
    state: Mapped[SignatureRequestState] = mapped_column(
        Enum(SignatureRequestState, native_enum=False),
        default=SignatureRequestState.DRAFT, index=True, nullable=False,
    )
    sequential: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Hash of the document version being signed, captured when the request is
    # sent. Any later change to the document is detectable against this.
    document_hash: Mapped[Optional[str]] = mapped_column(String(64))
    source_version: Mapped[Optional[int]] = mapped_column(Integer)
    signed_version: Mapped[Optional[int]] = mapped_column(Integer)

    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    recipients: Mapped[list["SignatureRecipient"]] = relationship(
        back_populates="request", cascade="all, delete-orphan",
        order_by="SignatureRecipient.order",
    )
    fields: Mapped[list["SignatureFieldPlacement"]] = relationship(
        back_populates="request", cascade="all, delete-orphan",
    )
    events: Mapped[list["SignatureEvent"]] = relationship(
        back_populates="request", cascade="all, delete-orphan",
        order_by="SignatureEvent.created_at",
    )


class SignatureRecipient(Base, TimestampMixin):
    __tablename__ = "signature_recipients"
    __table_args__ = (
        UniqueConstraint("request_id", "email", name="uq_recipient_email"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("signature_requests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(200))
    order: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    state: Mapped[RecipientState] = mapped_column(
        Enum(RecipientState, native_enum=False), default=RecipientState.PENDING, nullable=False
    )

    # Unguessable per-recipient token. This is the bearer credential for the
    # signing link, so it is generated with secrets and never derived from
    # anything guessable such as the email address.
    access_token: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    viewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    signed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    declined_reason: Mapped[Optional[str]] = mapped_column(String(500))

    request: Mapped[SignatureRequest] = relationship(back_populates="recipients")


class SignatureFieldPlacement(Base, TimestampMixin):
    __tablename__ = "signature_fields"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("signature_requests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    recipient_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("signature_recipients.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[SignatureFieldType] = mapped_column(
        Enum(SignatureFieldType, native_enum=False), nullable=False
    )
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    width: Mapped[float] = mapped_column(Float, nullable=False)
    height: Mapped[float] = mapped_column(Float, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    label: Mapped[Optional[str]] = mapped_column(String(120))

    value: Mapped[Optional[str]] = mapped_column(Text)
    filled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    request: Mapped[SignatureRequest] = relationship(back_populates="fields")


class SignatureEvent(Base):
    """Append-only evidence trail for a signature request."""
    __tablename__ = "signature_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True, nullable=False
    )
    request_id: Mapped[str] = mapped_column(
        ForeignKey("signature_requests.id", ondelete="CASCADE"), index=True, nullable=False
    )
    recipient_id: Mapped[Optional[str]] = mapped_column(String(32))
    actor: Mapped[Optional[str]] = mapped_column(String(320))
    event: Mapped[str] = mapped_column(String(60), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(String(500))
    ip_address: Mapped[Optional[str]] = mapped_column(String(64))
    document_hash: Mapped[Optional[str]] = mapped_column(String(64))

    request: Mapped[SignatureRequest] = relationship(back_populates="events")
