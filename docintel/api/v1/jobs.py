"""Job status endpoints."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from docintel.api.schemas import JobResponse, Page
from docintel.core import audit
from docintel.core.deps import CurrentUser, DbSession, client_ip, require_workspace
from docintel.db.models import Job, JobState, WorkspaceMember, utcnow

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _visible_job(session, user, job_id: str) -> Job:
    """A job is visible only through membership of its workspace."""
    row = session.execute(
        select(Job)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Job.workspace_id)
        .where(Job.id == job_id, WorkspaceMember.user_id == user.id)
    ).scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return row


@router.get("", response_model=Page)
def list_jobs(
    user: CurrentUser,
    session: DbSession,
    workspace_id: str = Query(...),
    state: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Page:
    require_workspace(session, user, workspace_id)

    conditions = [Job.workspace_id == workspace_id]
    if state:
        try:
            conditions.append(Job.state == JobState(state))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Unknown job state '{state}'")

    total = session.scalar(select(func.count()).select_from(Job).where(*conditions)) or 0
    rows = session.scalars(
        select(Job).where(*conditions)
        .order_by(Job.created_at.desc()).limit(limit).offset(offset)
    ).all()

    return Page(
        items=[JobResponse.model_validate(j).model_dump(mode="json") for j in rows],
        total=total, limit=limit, offset=offset,
    )


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, user: CurrentUser, session: DbSession) -> Job:
    return _visible_job(session, user, job_id)


@router.post("/{job_id}/retry", response_model=JobResponse)
def retry_job(job_id: str, request: Request, user: CurrentUser, session: DbSession) -> Job:
    job = _visible_job(session, user, job_id)
    require_workspace(session, user, job.workspace_id, write=True)

    if job.state not in (JobState.FAILED, JobState.CANCELLED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only failed or cancelled jobs can be retried (state is {job.state.value})",
        )

    job.state = JobState.QUEUED
    job.attempts = 0
    job.error = None
    job.progress = 0.0
    job.progress_note = None
    job.locked_at = None
    job.locked_by = None
    job.finished_at = None

    audit.record(session, action="job.retry", actor=user, workspace_id=job.workspace_id,
                 document_id=job.document_id, detail=job.type.value,
                 ip_address=client_ip(request))
    session.commit()
    session.refresh(job)
    return job


@router.post("/{job_id}/cancel", response_model=JobResponse)
def cancel_job(job_id: str, request: Request, user: CurrentUser, session: DbSession) -> Job:
    job = _visible_job(session, user, job_id)
    require_workspace(session, user, job.workspace_id, write=True)

    if job.state in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is already {job.state.value}",
        )

    job.state = JobState.CANCELLED
    job.finished_at = utcnow()

    audit.record(session, action="job.cancel", actor=user, workspace_id=job.workspace_id,
                 document_id=job.document_id, ip_address=client_ip(request))
    session.commit()
    session.refresh(job)
    return job
