from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db, iso
from app.models import AuditLog

router = APIRouter(tags=["audit"])


@router.get("/audit")
def list_audit(limit: int = Query(50, ge=1, le=500), event_type: str | None = None,
               correlation_id: str | None = None, db: Session = Depends(get_db)):
    stmt = select(AuditLog).order_by(AuditLog.received_at.desc()).limit(limit)
    if event_type:
        stmt = stmt.where(AuditLog.event_type == event_type.upper())
    if correlation_id:
        stmt = stmt.where(AuditLog.correlation_id == correlation_id)
    return [{"event_id": a.event_id, "event_type": a.event_type, "source": a.source,
             "correlation_id": a.correlation_id, "payload": a.payload, "received_at": iso(a.received_at)}
            for a in db.execute(stmt).scalars()]
