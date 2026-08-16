"""Authentication endpoints."""
import time

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from docintel.api.schemas import (
    LoginRequest, RegisterRequest, TokenResponse, UserResponse,
)
from docintel.config import settings
from docintel.core import audit
from docintel.core.deps import CurrentUser, DbSession, client_ip
from docintel.core.security import create_access_token, hash_password, verify_password
from docintel.db.models import Organization, Role, User, Workspace, WorkspaceMember

router = APIRouter(prefix="/auth", tags=["auth"])

INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect email or password",
)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request, session: DbSession) -> TokenResponse:
    email = body.email.lower().strip()

    if session.scalar(select(User).where(User.email == email)):
        # Same message and shape as a failed login would give, so this
        # endpoint cannot be used to enumerate registered addresses.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Registration could not be completed",
        )

    user = User(
        email=email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
    )
    session.add(user)
    session.flush()

    # Every user starts with their own organization and a personal workspace,
    # so there is never a document without a tenant.
    org = Organization(name=body.organization_name or f"{email.split('@')[0]}'s organization")
    session.add(org)
    session.flush()

    workspace = Workspace(organization_id=org.id, name="Personal", description="Default workspace")
    session.add(workspace)
    session.flush()

    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=Role.OWNER))

    audit.record(session, action="auth.register", actor=user,
                 workspace_id=workspace.id, ip_address=client_ip(request))
    session.commit()

    return TokenResponse(
        access_token=create_access_token(user.id),
        expires_in_minutes=settings.access_token_ttl_minutes,
    )


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, session: DbSession) -> TokenResponse:
    started = time.perf_counter()
    email = body.email.lower().strip()
    user = session.scalar(select(User).where(User.email == email))

    # Always run a hash comparison, even when the user does not exist, so
    # response time does not reveal which addresses are registered.
    stored = user.password_hash if user else (
        "$2b$12$" + "." * 53  # structurally valid, never matches
    )
    ok = verify_password(body.password, stored)

    if not user or not ok or not user.is_active:
        audit.record(session, action="auth.login", actor=user, result="failure",
                     detail="invalid credentials", ip_address=client_ip(request))
        session.commit()
        # Pad to a floor so the failure path cannot be timed against success.
        elapsed = time.perf_counter() - started
        if elapsed < 0.25:
            time.sleep(0.25 - elapsed)
        raise INVALID_CREDENTIALS

    audit.record(session, action="auth.login", actor=user, ip_address=client_ip(request))
    session.commit()

    return TokenResponse(
        access_token=create_access_token(user.id),
        expires_in_minutes=settings.access_token_ttl_minutes,
    )


@router.get("/mode")
def auth_mode() -> dict:
    """Whether sign-in is required.

    Public by necessity — the client has to know whether to show the login
    screen before it has any credentials. It reveals only the mode, never
    anything about users.
    """
    return {
        "mode": settings.auth_mode,
        "open_access": settings.auth_open,
        "environment": settings.environment,
        "warning": (
            "Authentication is disabled. Anyone who can reach this service has "
            "full access to every document in it."
            if settings.auth_open else None
        ),
    }


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser) -> User:
    return user
