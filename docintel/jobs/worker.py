"""
Background worker.

Runs as its own process, so long operations do not depend on a browser
request staying open. Start with:

    python -m docintel.jobs.worker

Each job is retried up to max_attempts; a job that exhausts them is marked
FAILED with the error recorded. Failures never take the worker down.
"""
import logging
import os
import signal
import socket
import time
import traceback
from typing import Optional

from docintel.config import settings
from docintel.db.models import Job, JobState, utcnow
from docintel.db.session import session_scope
from docintel.jobs.handlers import HANDLERS
from docintel.jobs.queue import DatabaseQueue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("docintel.worker")

_shutdown = False


def _request_shutdown(signum, _frame):
    global _shutdown
    logger.info("signal %s received, finishing current job then stopping", signum)
    _shutdown = True


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def run_once(queue: DatabaseQueue, identity: str) -> bool:
    """Claim and run at most one job. Returns True if work was done."""
    with session_scope() as session:
        job: Optional[Job] = queue.claim(session, identity)
        if job is None:
            return False
        job_id, job_type = job.id, job.type

    logger.info("job %s (%s) claimed", job_id, job_type.value)

    def progress(fraction: float, note: str) -> None:
        with session_scope() as session:
            current = session.get(Job, job_id)
            if current is not None:
                current.progress = max(0.0, min(fraction, 1.0))
                current.progress_note = note[:300]

    try:
        handler = HANDLERS.get(job_type)
        if handler is None:
            raise ValueError(f"no handler registered for job type {job_type.value}")

        with session_scope() as session:
            current = session.get(Job, job_id)
            result = handler(session, current, progress)
            current.state = JobState.COMPLETED
            current.result = result or {}
            current.progress = 1.0
            current.progress_note = "done"
            current.finished_at = utcnow()
            current.error = None

        logger.info("job %s completed", job_id)

    except Exception as exc:
        # Log the type and message; the traceback goes to the worker log only,
        # never to an API response.
        logger.error("job %s failed: %s: %s", job_id, type(exc).__name__, exc)
        logger.debug(traceback.format_exc())

        with session_scope() as session:
            current = session.get(Job, job_id)
            if current is None:
                return True
            if current.attempts >= current.max_attempts:
                current.state = JobState.FAILED
                current.finished_at = utcnow()
                current.progress_note = "failed"
            else:
                current.state = JobState.QUEUED
                current.locked_at = None
                current.locked_by = None
            current.error = f"{type(exc).__name__}: {exc}"[:2000]

    return True


def main() -> None:
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGTERM, _request_shutdown)

    queue = DatabaseQueue()
    identity = worker_id()
    logger.info("worker %s started (queue=%s db=%s)",
                identity, settings.queue_driver,
                "sqlite" if settings.is_sqlite else "postgres")

    last_reap = 0.0
    while not _shutdown:
        now = time.monotonic()
        if now - last_reap > 60:
            with session_scope() as session:
                reclaimed = queue.reap_stalled(session)
            if reclaimed:
                logger.warning("returned %d stalled job(s) to the queue", reclaimed)
            last_reap = now

        try:
            did_work = run_once(queue, identity)
        except Exception:
            logger.exception("worker loop error")
            did_work = False

        if not did_work:
            time.sleep(settings.worker_poll_seconds)

    logger.info("worker %s stopped", identity)


if __name__ == "__main__":
    main()
