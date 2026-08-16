"""
Request dependencies: authentication and object-level authorization.

The rule this module exists to enforce: no handler ever loads a Document by
id alone. Access goes through require_document(), which joins the document to
the caller's workspace membership in a single query. A document the caller
cannot see returns 404, not 403 — a 403 would confirm the id exists and turn
the endpoint into an enumeration oracle.
"""
import secrets
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from docintel.config import settings
from docintel.core.security import TokenError, decode_access_token
from docintel.db.models import Document, Role, User, Workspace, WorkspaceMember
from docintel.db.session import get_session

bearer = HTTPBearer(auto_error=False)

CREDENTIALS_ERROR = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)
NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
FORBIDDEN = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="You do not have permission to perform this action",
)


def get_or_create_dev_user(session: Session) -> User:
    """The stand-in identity used when auth_mode is "open".

    A real row with a real workspace, deliberately: every authorization check
    downstream then behaves exactly as it will once authentication is switched
    back on, so open mode cannot mask a broken permission check.
    """
    from docintel.db.models import Organization, Role, Workspace, WorkspaceMember
    from docintel.core.security import hash_password

    email = settings.dev_user_email
    user = session.scalar(select(User).where(User.email == email))

    if user is None:
        user = User(
            email=email,
            # Unusable-by-design: open mode never checks it, and once auth is
            # switched back on nobody can sign in as this account.
            password_hash=hash_password(secrets.token_urlsafe(32)),
            full_name="Development User",
        )
        session.add(user)
        session.flush()

    membership = session.scalar(
        select(WorkspaceMember).where(WorkspaceMember.user_id == user.id)
    )
    if membership is None:
        org = Organization(name="Development")
        session.add(org)
        session.flush()

        workspace = Workspace(
            organization_id=org.id,
            name="Development",
            description="Default workspace for open-access mode",
        )
        session.add(workspace)
        session.flush()
        session.add(WorkspaceMember(
            workspace_id=workspace.id, user_id=user.id, role=Role.OWNER,
        ))

    session.commit()
    return user


def get_current_user(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer)],
    session: Annotated[Session, Depends(get_session)],
) -> User:
    # A token is always honoured when one is supplied, in either mode, so the
    # real sign-in flow stays exercisable while open access is on.
    if credentials is not None and credentials.credentials:
        try:
            user_id = decode_access_token(credentials.credentials)
        except TokenError:
            raise CREDENTIALS_ERROR

        user = session.get(User, user_id)
        if user is None or not user.is_active:
            raise CREDENTIALS_ERROR
        return user

    if settings.auth_open:
        return get_or_create_dev_user(session)

    raise CREDENTIALS_ERROR


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[Session, Depends(get_session)]


def membership_for(session: Session, user: User, workspace_id: str) -> Optional[WorkspaceMember]:
    return session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user.id,
        )
    )


def require_workspace(
    session: Session,
    user: User,
    workspace_id: str,
    *,
    write: bool = False,
    manage: bool = False,
) -> Workspace:
    """Resolve a workspace the caller is a member of, or 404."""
    membership = membership_for(session, user, workspace_id)
    if membership is None:
        # Deliberately 404: never confirm that a workspace id exists.
        raise NOT_FOUND

    if manage and not membership.role.can_manage:
        raise FORBIDDEN
    if write and not membership.role.can_write:
        raise FORBIDDEN

    workspace = session.get(Workspace, workspace_id)
    if workspace is None:
        raise NOT_FOUND
    return workspace


def require_document(
    session: Session,
    user: User,
    document_id: str,
    *,
    write: bool = False,
) -> Document:
    """Load a document only if the caller is a member of its workspace.

    Single query joining through workspace_members, so there is no window in
    which an unauthorized document is in memory.
    """
    row = session.execute(
        select(Document, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Document.workspace_id)
        .where(
            Document.id == document_id,
            WorkspaceMember.user_id == user.id,
        )
    ).first()

    if row is None:
        raise NOT_FOUND

    document, role = row
    if write and not Role(role).can_write:
        raise FORBIDDEN
    return document


def client_ip(request: Request) -> Optional[str]:
    if request.client:
        return request.client.host
    return None
