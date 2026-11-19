import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from wcommon.events import build_event
from wcommon.logging import log

from app.database import get_db, iso
from app.models import ReportJob, utcnow
from app.publisher import PublishError, get_publisher
from app.routers.transfers import PUBLISH_FAILURES

router = APIRouter(tags=["reports"])
REPORT_TYPES = {"inventory_snapshot"}


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
    try:
        publisher.publish_command("report.generate", build_event(
            "GENERATE_REPORT", "central-api", {"job_id": job.job_id, "report_type": job.report_type}))
    except PublishError as exc:
        PUBLISH_FAILURES.labels("GENERATE_REPORT").inc()
        db.delete(job)
        db.commit()
        log("report_rejected_broker_down", level="ERROR", job_id=job.job_id, error=str(exc))
        return JSONResponse(status_code=503, content={"detail": "RabbitMQ no disponible; reintente.",
                                                      "code": "BROKER_UNAVAILABLE"},
                            headers={"Retry-After": "5"})
    log("report_requested", job_id=job.job_id, report_type=job.report_type)
    return {"job_id": job.job_id, "status": "PROCESSING"}


@router.get("/reports/jobs/{job_id}")
def get_report_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(ReportJob, job_id)
    if job is None:
        raise HTTPException(404, "Job no encontrado")
    return job_to_dict(job)
