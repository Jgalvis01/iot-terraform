import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from wcommon.events import build_event
from wcommon.logging import log

from app.database import get_db, iso
from app.models import ReportJob, utcnow
from app.command_outbox import QUEUED, publish_or_queue
from app.publisher import get_publisher
from app.routers.transfers import PUBLISH_FAILURES
from app.warehouse_report import build_summary

router = APIRouter(tags=["reports"])
REPORT_TYPES = {"inventory_snapshot", "warehouses_summary"}


class ReportIn(BaseModel):
    report_type: str = "inventory_snapshot"


def job_to_dict(j: ReportJob) -> dict:
    return {"job_id": j.job_id, "report_type": j.report_type, "status": j.status, "s3_key": j.s3_key,
            "error": j.error, "created_at": iso(j.created_at), "updated_at": iso(j.updated_at)}


@router.post("/reports", status_code=202)
def create_report(body: ReportIn, db: Session = Depends(get_db), publisher=Depends(get_publisher)):
    if body.report_type not in REPORT_TYPES:
        raise HTTPException(422, f"report_type no soportado; valores: {sorted(REPORT_TYPES)}")
    now = utcnow()
    job = ReportJob(job_id=f"JOB-{uuid.uuid4().hex[:8]}", report_type=body.report_type,
                    status="PROCESSING", created_at=now, updated_at=now)
    db.add(job)
    db.commit()
    command = build_event("GENERATE_REPORT", "central-api", {"job_id": job.job_id, "report_type": job.report_type})
    try:
        delivery = publish_or_queue(db, publisher, "report.generate", command)
    except Exception as exc:
        db.rollback()
        PUBLISH_FAILURES.labels("GENERATE_REPORT").inc()
        db.delete(job)
        db.commit()
        log("report_rejected_broker_down", level="ERROR", job_id=job.job_id, error=repr(exc))
        return JSONResponse(status_code=503, content={"detail": "Broker y cola de respaldo no disponibles; reintente.",
                                                      "code": "BROKER_UNAVAILABLE"},
                            headers={"Retry-After": "5"})
    if delivery == QUEUED:
        PUBLISH_FAILURES.labels("GENERATE_REPORT").inc()
    log("report_requested", job_id=job.job_id, report_type=job.report_type, delivery=delivery)
    return {"job_id": job.job_id, "status": "PROCESSING", "delivery": delivery}


@router.get("/reports/warehouses")
def warehouses_report(low_stock_threshold: int | None = Query(None, ge=0), db: Session = Depends(get_db)):
    """Reporte general de bodegas: estado, sincronización, inventario, bajo stock y transferencias."""
    return build_summary(db, low_stock_threshold)


@router.get("/reports/jobs/{job_id}")
def get_report_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(ReportJob, job_id)
    if job is None:
        raise HTTPException(404, "Job no encontrado")
    return job_to_dict(job)
