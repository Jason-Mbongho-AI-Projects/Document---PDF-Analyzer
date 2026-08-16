"""Workspace and membership endpoints."""
from typing import List

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from docintel.api.schemas import (
    MemberAdd, MemberResponse, WorkspaceCreate, WorkspaceResponse, WorkspaceWithRole,
)
from docintel.core import audit
from docintel.core.deps import CurrentUser, DbSession, client_ip, require_workspace
from docintel.db.models import Organization, Role, User, Workspace, WorkspaceMember

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get("", response_model=List[WorkspaceWithRole])
def list_workspaces(user: CurrentUser, session: DbSession) -> List[WorkspaceWithRole]:
    """Only workspaces the caller belongs to. There is no 'list all'."""
    rows = session.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .order_by(Workspace.created_at)
    ).all()

    return [
        WorkspaceWithRole(
            id=ws.id, name=ws.name, description=ws.description,
            organization_id=ws.organization_id, created_at=ws.created_at,
            role=Role(role).value,
        )
        for ws, role in rows
    ]


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
def create_workspace(
    body: WorkspaceCreate, request: Request, user: CurrentUser, session: DbSession
) -> Workspace:
    # Reuse the org the caller already belongs to, otherwise create one.
    existing = session.execute(
        select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.user_id == user.id)
        .limit(1)
    ).scalar_one_or_none()

    if existing is not None:
        organization_id = existing.organization_id
    else:
        org = Organization(name=f"{user.email.split('@')[0]}'s organization")
        session.add(org)
        session.flush()
        organization_id = org.id

    workspace = Workspace(
        organization_id=organization_id,
        name=body.name,
        description=body.description,
    )
    session.add(workspace)
    session.flush()
    session.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=Role.OWNER))

    audit.record(session, action="workspace.create", actor=user,
                 workspace_id=workspace.id, ip_address=client_ip(request))
    session.commit()
    return workspace


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
def get_workspace(workspace_id: str, user: CurrentUser, session: DbSession) -> Workspace:
    return require_workspace(session, user, workspace_id)


@router.get("/{workspace_id}/members", response_model=List[MemberResponse])
def list_members(workspace_id: str, user: CurrentUser, session: DbSession) -> List[MemberResponse]:
    require_workspace(session, user, workspace_id)

    rows = session.execute(
        select(WorkspaceMember, User)
        .join(User, User.id == WorkspaceMember.user_id)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.created_at)
    ).all()

    return [
        MemberResponse(user_id=m.user_id, email=u.email,
                       role=Role(m.role).value, created_at=m.created_at)
        for m, u in rows
    ]


@router.post("/{workspace_id}/members", response_model=MemberResponse,
             status_code=status.HTTP_201_CREATED)
def add_member(
    workspace_id: str, body: MemberAdd, request: Request,
    user: CurrentUser, session: DbSession,
) -> MemberResponse:
    require_workspace(session, user, workspace_id, manage=True)

    target = session.scalar(select(User).where(User.email == body.email.lower().strip()))
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such user")

    already = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == target.id,
        )
    )
    if already is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="User is already a member")

    member = WorkspaceMember(workspace_id=workspace_id, user_id=target.id, role=Role(body.role))
    session.add(member)

    audit.record(session, action="workspace.member_add", actor=user,
                 workspace_id=workspace_id, detail=f"added {target.id} as {body.role}",
                 ip_address=client_ip(request))
    session.commit()

    return MemberResponse(user_id=target.id, email=target.email,
                          role=member.role.value, created_at=member.created_at)


@router.delete("/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    workspace_id: str, user_id: str, request: Request,
    user: CurrentUser, session: DbSession,
) -> None:
    require_workspace(session, user, workspace_id, manage=True)

    member = session.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == user_id,
        )
    )
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not a member")

    if member.role == Role.OWNER:
        owners = session.scalars(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.role == Role.OWNER,
            )
        ).all()
        if len(owners) <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot remove the last owner of a workspace",
            )

    session.delete(member)
    audit.record(session, action="workspace.member_remove", actor=user,
                 workspace_id=workspace_id, detail=f"removed {user_id}",
                 ip_address=client_ip(request))
    session.commit()
