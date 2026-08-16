"""
Audit logging and usage metering.

Records identifiers and outcomes only. Document text never enters an audit
row — an audit trail that quotes content becomes a second copy of the data
you were trying to protect.
"""
from typing import Optional

from sqlalchemy.orm import Session

from docintel.db.models import AuditLog, UsageRecord, User

MAX_DETAIL = 500


def record(
    session: Session,
    *,
    action: str,
    actor: Optional[User] = None,
    workspace_id: Optional[str] = None,
    document_id: Optional[str] = None,
    result: str = "success",
    detail: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> AuditLog:
    entry = AuditLog(
        action=action,
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        workspace_id=workspace_id,
        document_id=document_id,
        result=result,
        detail=(detail or "")[:MAX_DETAIL] or None,
        ip_address=ip_address,
    )
    session.add(entry)
    return entry


def meter(
    session: Session,
    *,
    workspace_id: str,
    operation: str,
    units: float,
    unit_kind: str = "count",
    user_id: Optional[str] = None,
    document_id: Optional[str] = None,
    model: Optional[str] = None,
) -> UsageRecord:
    entry = UsageRecord(
        workspace_id=workspace_id,
        user_id=user_id,
        document_id=document_id,
        operation=operation,
        units=units,
        unit_kind=unit_kind,
        model=model,
    )
    session.add(entry)
    return entry
