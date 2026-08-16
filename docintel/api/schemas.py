"""Request and response models. These define the API contract."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------- auth

class RegisterRequest(BaseModel):
    email: EmailStr
    # 72 bytes is bcrypt's hard limit; rejecting is safer than truncating.
    password: str = Field(min_length=10, max_length=72)
    full_name: Optional[str] = Field(default=None, max_length=200)
    organization_name: Optional[str] = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


class UserResponse(ORMModel):
    id: str
    email: str
    full_name: Optional[str]
    is_active: bool
    created_at: datetime


# -------------------------------------------------------------- workspaces

class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)


class WorkspaceResponse(ORMModel):
    id: str
    name: str
    description: Optional[str]
    organization_id: str
    created_at: datetime


class WorkspaceWithRole(WorkspaceResponse):
    role: str


class MemberAdd(BaseModel):
    email: EmailStr
    role: str = Field(default="member", pattern="^(owner|admin|member|viewer)$")


class MemberResponse(BaseModel):
    user_id: str
    email: str
    role: str
    created_at: datetime


# --------------------------------------------------------------- documents

class DocumentResponse(ORMModel):
    id: str
    workspace_id: str
    filename: str
    mime_type: str
    size_bytes: int
    content_hash: str
    status: str
    status_detail: Optional[str]
    page_count: Optional[int]
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class DocumentDetail(DocumentResponse):
    doc_metadata: Dict[str, Any] = Field(default_factory=dict)
    version_count: int = 0


class DocumentUploadResponse(BaseModel):
    document: DocumentResponse
    jobs: List[str]


class PageInfo(BaseModel):
    page: int
    width: float
    height: float
    orientation: str
    characters: int
    text_layer: str


class SecurityFindingResponse(ORMModel):
    finding_id: str
    title: str
    severity: str
    detail: str
    locations: str


class SecurityReportResponse(BaseModel):
    document_id: str
    scanned: bool
    risk_level: Optional[str] = None
    risk_label: Optional[str] = None
    headline: Optional[str] = None
    encrypted: Optional[bool] = None
    signed: Optional[bool] = None
    has_forms: Optional[bool] = None
    url_count: Optional[int] = None
    findings: List[SecurityFindingResponse] = Field(default_factory=list)


# -------------------------------------------------------------------- jobs

class JobResponse(ORMModel):
    id: str
    workspace_id: str
    document_id: Optional[str]
    type: str
    state: str
    progress: float
    progress_note: Optional[str]
    attempts: int
    max_attempts: int
    error: Optional[str]
    result: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]


class Page(BaseModel):
    """Envelope for list endpoints."""
    items: List[Any]
    total: int
    limit: int
    offset: int
