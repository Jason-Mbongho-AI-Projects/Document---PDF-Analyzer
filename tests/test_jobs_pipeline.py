"""
Job queue and the end-to-end ingest pipeline.

These drive the real worker function against the real handlers and real
stored bytes — no mocked processing.
"""
import pdf_corpus as corpus
from docintel.db.models import Document, DocumentStatus, Job, JobState, JobType
from docintel.db.session import SessionLocal
from docintel.jobs.queue import DatabaseQueue
from docintel.jobs.worker import run_once


def drain(limit: int = 20) -> int:
    """Run queued jobs to completion, as the worker process would."""
    queue = DatabaseQueue()
    processed = 0
    while processed < limit and run_once(queue, "test-worker"):
        processed += 1
    return processed


# --------------------------------------------------------------- queueing

def test_upload_enqueues_ingest_and_security_scan(alice, db):
    upload = alice.upload(corpus.clean_pdf()).json()
    document_id = upload["document"]["id"]

    jobs = db.query(Job).filter(Job.document_id == document_id).all()
    assert {j.type for j in jobs} == {JobType.INGEST, JobType.SECURITY_SCAN}
    assert all(j.state == JobState.QUEUED for j in jobs)


def test_enqueue_is_idempotent(alice, db):
    from docintel.jobs.queue import queue
    upload = alice.upload(corpus.clean_pdf()).json()
    document_id = upload["document"]["id"]

    with SessionLocal() as session:
        first = queue.enqueue(
            session, workspace_id=alice.workspace_id, job_type=JobType.INGEST,
            document_id=document_id, idempotency_key=f"ingest:{document_id}",
        )
        session.commit()
        assert first.id == upload["jobs"][0]

    total = db.query(Job).filter(Job.document_id == document_id).count()
    assert total == 2  # not 3


def test_claim_is_exclusive(alice):
    alice.upload(corpus.clean_pdf())
    queue = DatabaseQueue()

    with SessionLocal() as s1, SessionLocal() as s2:
        first = queue.claim(s1, "worker-a")
        second = queue.claim(s2, "worker-b")

    assert first is not None and second is not None
    assert first.id != second.id           # never the same job twice


def test_claim_returns_none_when_empty():
    queue = DatabaseQueue()
    with SessionLocal() as session:
        assert queue.claim(session, "idle-worker") is None


# ------------------------------------------------------- ingest handler

def test_ingest_populates_the_document_profile(alice, db):
    document_id = alice.upload(corpus.multipage_pdf(3)).json()["document"]["id"]
    drain()

    document = db.get(Document, document_id)
    db.refresh(document)

    assert document.status == DocumentStatus.READY
    assert document.page_count == 3

    profile = document.doc_metadata["profile"]
    assert profile["page_count"] == 3
    assert profile["classification"] == "native"
    assert profile["title"] == "Corpus Document"
    assert profile["author"] == "Test Harness"


def test_ingest_classifies_a_page_with_no_text_layer(alice, db):
    document_id = alice.upload(corpus.empty_text_pdf()).json()["document"]["id"]
    drain()

    document = db.get(Document, document_id)
    db.refresh(document)
    profile = document.doc_metadata["profile"]

    # Honest wording: we know there is no text layer, not that it is a scan.
    assert profile["classification"] == "no_text_layer"
    assert profile["pages_with_text"] == 0


def test_pages_endpoint_reports_dimensions(alice):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    drain()

    pages = alice.get(f"/api/v1/documents/{document_id}/pages").json()
    assert pages["total"] == 1
    first = pages["items"][0]
    assert first["orientation"] == "portrait"
    assert first["width"] == 612.0 and first["height"] == 792.0
    assert first["text_layer"] == "native"


# ---------------------------------------------------- security handler

def test_security_scan_persists_findings(alice):
    document_id = alice.upload(corpus.javascript_pdf()).json()["document"]["id"]

    before = alice.get(f"/api/v1/documents/{document_id}/security").json()
    assert before["scanned"] is False

    drain()

    after = alice.get(f"/api/v1/documents/{document_id}/security").json()
    assert after["scanned"] is True
    assert after["risk_level"] == "high"
    assert any(f["finding_id"] == "javascript" for f in after["findings"])


def test_clean_document_scan_reports_no_indicators(alice):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    drain()

    report = alice.get(f"/api/v1/documents/{document_id}/security").json()
    assert report["risk_level"] == "none"
    assert report["findings"] == []
    assert "not a guarantee" in report["headline"]


def test_findings_are_ordered_most_severe_first(alice):
    document_id = alice.upload(corpus.embedded_file_pdf("payload.exe")).json()["document"]["id"]
    drain()

    findings = alice.get(f"/api/v1/documents/{document_id}/security").json()["findings"]
    assert findings
    severities = [f["severity"] for f in findings]
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    assert severities == sorted(severities, key=lambda s: order[s])


# ------------------------------------------------------------- failures

def test_job_failure_is_recorded_and_retried(alice, db, monkeypatch):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]

    import docintel.jobs.handlers as handlers

    def explode(session, job, progress):
        raise RuntimeError("simulated handler failure")

    monkeypatch.setitem(handlers.HANDLERS, JobType.INGEST, explode)

    # max_attempts runs, then it lands in FAILED rather than looping forever.
    drain(limit=30)

    job = db.query(Job).filter(
        Job.document_id == document_id, Job.type == JobType.INGEST
    ).one()
    db.refresh(job)

    assert job.state == JobState.FAILED
    assert "simulated handler failure" in job.error
    assert job.attempts == job.max_attempts


def test_failed_job_can_be_retried_through_the_api(alice, db, monkeypatch):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]

    import docintel.jobs.handlers as handlers
    original = handlers.HANDLERS[JobType.INGEST]

    def explode(session, job, progress):
        raise RuntimeError("boom")

    monkeypatch.setitem(handlers.HANDLERS, JobType.INGEST, explode)
    drain(limit=30)

    job = db.query(Job).filter(
        Job.document_id == document_id, Job.type == JobType.INGEST
    ).one()
    assert job.state == JobState.FAILED

    monkeypatch.setitem(handlers.HANDLERS, JobType.INGEST, original)
    assert alice.post(f"/api/v1/jobs/{job.id}/retry").status_code == 200

    drain()
    db.refresh(job)
    assert job.state == JobState.COMPLETED


def test_completed_job_cannot_be_cancelled(alice, db):
    document_id = alice.upload(corpus.clean_pdf()).json()["document"]["id"]
    drain()

    job = db.query(Job).filter(Job.document_id == document_id).first()
    assert alice.post(f"/api/v1/jobs/{job.id}/cancel").status_code == 409


def test_job_progress_is_reported(alice, db):
    document_id = alice.upload(corpus.multipage_pdf(3)).json()["document"]["id"]
    drain()

    job = db.query(Job).filter(
        Job.document_id == document_id, Job.type == JobType.INGEST
    ).one()
    db.refresh(job)
    assert job.progress == 1.0
    assert job.state == JobState.COMPLETED
    assert job.result["page_count"] == 3


def test_corrupt_pdf_fails_the_job_without_killing_the_worker(alice, db):
    """A hostile file must not take the worker down."""
    corrupt = b"%PDF-1.7\nbroken" + b"\x00" * 100
    response = alice.upload(corrupt, name="corrupt.pdf")
    assert response.status_code == 201     # passes magic-byte validation

    drain(limit=30)

    document_id = response.json()["document"]["id"]
    jobs = db.query(Job).filter(Job.document_id == document_id).all()
    for job in jobs:
        db.refresh(job)

    # The worker survived and reported honestly; nothing hung.
    states = {j.type: j.state for j in jobs}
    assert states[JobType.INGEST] == JobState.FAILED
    # Security scanning still produces a report for an unparseable file.
    assert states[JobType.SECURITY_SCAN] == JobState.COMPLETED
