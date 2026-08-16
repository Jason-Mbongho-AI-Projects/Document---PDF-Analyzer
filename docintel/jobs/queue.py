"""
Job queue abstraction with a database-backed default driver.

The default driver needs no Redis or broker, which keeps a local clone
runnable, and is genuinely safe at small scale: claiming uses a conditional
UPDATE so two workers racing for the same row cannot both win. An RQ driver
can be added behind the same interface when throughput demands it.
"""
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from docintel.config import settings
from docintel.db.models import Job, JobState, JobType, utcnow


class JobQueue(ABC):
    @abstractmethod
    def enqueue(self, session: Session, *, workspace_id: str, job_type: JobType,
                document_id: Optional[str] = None, payload: Optional[dict] = None,
                idempotency_key: Optional[str] = None) -> Job:
        ...

    @abstractmethod
    def claim(self, session: Session, worker_id: str) -> Optional[Job]:
        ...


class DatabaseQueue(JobQueue):
    def enqueue(
        self,
        session: Session,
        *,
        workspace_id: str,
        job_type: JobType,
        document_id: Optional[str] = None,
        payload: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> Job:
        if idempotency_key:
            existing = session.scalar(
                select(Job).where(Job.idempotency_key == idempotency_key)
            )
            if existing is not None:
                return existing

        job = Job(
            workspace_id=workspace_id,
            document_id=document_id,
            type=job_type,
            payload=payload or {},
            max_attempts=settings.job_max_attempts,
            idempotency_key=idempotency_key,
        )
        session.add(job)

        try:
            session.flush()
        except IntegrityError:
            # Lost a race on the unique idempotency key — return the winner.
            session.rollback()
            existing = session.scalar(
                select(Job).where(Job.idempotency_key == idempotency_key)
            )
            if existing is None:
                raise
            return existing

        return job

    def claim(self, session: Session, worker_id: str) -> Optional[Job]:
        """Atomically take ownership of one queued job.

        The UPDATE re-asserts state == QUEUED in its WHERE clause, so if
        another worker claimed the row between the SELECT and the UPDATE this
        affects zero rows and we simply try again.
        """
        for _ in range(5):
            candidate = session.scalar(
                select(Job)
                .where(Job.state == JobState.QUEUED)
                .order_by(Job.created_at)
                .limit(1)
            )
            if candidate is None:
                return None

            now = utcnow()
            claimed = session.execute(
                update(Job)
                .where(Job.id == candidate.id, Job.state == JobState.QUEUED)
                .values(
                    state=JobState.PROCESSING,
                    locked_at=now,
                    locked_by=worker_id,
                    started_at=now,
                    attempts=Job.attempts + 1,
                )
            )
            session.commit()

            if claimed.rowcount == 1:
                session.refresh(candidate)
                return candidate

        return None

    def reap_stalled(self, session: Session) -> int:
        """Return jobs whose worker died back to the queue."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.job_timeout_seconds)
        result = session.execute(
            update(Job)
            .where(
                Job.state == JobState.PROCESSING,
                Job.locked_at < cutoff,
                Job.attempts < Job.max_attempts,
            )
            .values(state=JobState.QUEUED, locked_at=None, locked_by=None)
        )
        session.commit()
        return result.rowcount


def get_queue() -> JobQueue:
    # `queue_driver` is Literal["database"], so settings validation rejects
    # anything else before this runs. The indirection stays because it is the
    # seam a second driver would be added at, but there is no unreachable
    # error branch pretending to guard a choice that cannot be made.
    return DatabaseQueue()


queue = get_queue()
