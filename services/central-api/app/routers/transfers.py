import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from prometheus_client import Counter
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from wcommon.correlation import get_correlation_id
from wcommon.events import build_event
from wcommon.logging import log

from app.config import get_settings
from app.database import get_db, iso
from app.models import Transfer, utcnow
from app.publisher import PublishError, get_publisher

router = APIRouter(tags=["transfers"])

FAIL_STEPS = {"none", "before_reserve", "after_reserve", "destination_down"}
TRANSFERS_CREATED = Counter("central_transfers_created_total", "Transferencias creadas")
PUBLISH_FAILURES = Counter("central_command_publish_failures_total", "Fallos publicando comandos", ["command"])


class TransferIn(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    source_warehouse: str
    destination_warehouse: str
    quantity: int = Field(gt=0)

    @field_validator("source_warehouse", "destination_warehouse")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()


def transfer_to_dict(t: Transfer) -> dict:
    return {"transfer_id": t.transfer_id, "sku": t.sku, "source_warehouse": t.source_warehouse,
            "destination_warehouse": t.destination_warehouse, "quantity": t.quantity,
            "status": t.status, "reason": t.reason, "attempts": t.attempts,
            "correlation_id": t.correlation_id, "fail_step": t.fail_step,
            "idempotency_key": t.idempotency_key, "history": t.history or [],
            "created_at": iso(t.created_at), "updated_at": iso(t.updated_at)}


def _accepted(t: Transfer, replay: bool = False) -> JSONResponse:
    return JSONResponse(status_code=202, content={
        "transfer_id": t.transfer_id, "status": "PROCESSING", "saga_status": t.status,
        "idempotent_replay": replay})


def _publish_process(publisher, t: Transfer) -> None:
    cmd = build_event("PROCESS_TRANSFER", "central-api", {"transfer_id": t.transfer_id},
                      correlation_id=t.correlation_id)
    publisher.publish_command("transfer.process", cmd)


@router.post("/transfers", status_code=202)
def create_transfer(body: TransferIn, db: Session = Depends(get_db), publisher=Depends(get_publisher),
                    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
                    fail_step: str | None = Header(default=None, alias="X-Fail-Step")):
    warehouses = get_settings().warehouses
    for wh in (body.source_warehouse, body.destination_warehouse):
        if warehouses and wh not in warehouses:
            raise HTTPException(422, f"Bodega desconocida: {wh}. Configuradas: {sorted(warehouses)}")
    if body.source_warehouse == body.destination_warehouse:
        raise HTTPException(422, "source_warehouse y destination_warehouse deben ser distintas")
    if fail_step is not None:
        fail_step = fail_step.strip().lower()
        if fail_step not in FAIL_STEPS:
            raise HTTPException(422, f"X-Fail-Step inválido; valores: {sorted(FAIL_STEPS)}")

    if idempotency_key:
        existing = db.execute(select(Transfer).where(Transfer.idempotency_key == idempotency_key)).scalar_one_or_none()
        if existing is not None:
            return _replay(existing, body, publisher)

    now = utcnow()
    t = Transfer(transfer_id=f"TR-{uuid.uuid4().hex[:8]}", sku=body.sku,
                 source_warehouse=body.source_warehouse, destination_warehouse=body.destination_warehouse,
                 quantity=body.quantity, status="REQUESTED", attempts=0,
                 correlation_id=get_correlation_id(), idempotency_key=idempotency_key, fail_step=fail_step,
                 history=[{"status": "REQUESTED", "at": iso(now)}], created_at=now, updated_at=now)
    db.add(t)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.execute(select(Transfer).where(Transfer.idempotency_key == idempotency_key)).scalar_one()
        return _replay(existing, body, publisher)

    try:
        _publish_process(publisher, t)
    except PublishError as exc:
        PUBLISH_FAILURES.labels("PROCESS_TRANSFER").inc()
        db.delete(t)
        db.commit()
        log("transfer_rejected_broker_down", level="ERROR", transfer_id=t.transfer_id, error=str(exc))
        return JSONResponse(status_code=503, content={
            "detail": "RabbitMQ no disponible; la transferencia no fue creada. Reintente.",
            "code": "BROKER_UNAVAILABLE"}, headers={"Retry-After": "5"})
    TRANSFERS_CREATED.inc()
    log("transfer_requested", transfer_id=t.transfer_id, sku=t.sku, source=t.source_warehouse,
        destination=t.destination_warehouse, quantity=t.quantity, fail_step=fail_step)
    return _accepted(t)


def _replay(existing: Transfer, body: TransferIn, publisher):
    same = (existing.sku, existing.source_warehouse, existing.destination_warehouse, existing.quantity) == \
        (body.sku, body.source_warehouse, body.destination_warehouse, body.quantity)
    if not same:
        raise HTTPException(409, "Idempotency-Key ya usada con otro cuerpo")
    if existing.status == "REQUESTED":
        try:
            _publish_process(publisher, existing)
        except PublishError:
            PUBLISH_FAILURES.labels("PROCESS_TRANSFER").inc()
    log("transfer_idempotent_replay", transfer_id=existing.transfer_id)
    return _accepted(existing, replay=True)


@router.get("/transfers/{transfer_id}")
def get_transfer(transfer_id: str, db: Session = Depends(get_db)):
    t = db.get(Transfer, transfer_id)
    if t is None:
        raise HTTPException(404, "Transferencia no encontrada")
    return transfer_to_dict(t)


@router.get("/transfers")
def list_transfers(status: str | None = None, limit: int = Query(50, ge=1, le=500),
                   db: Session = Depends(get_db)):
    stmt = select(Transfer).order_by(Transfer.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Transfer.status == status.upper())
    return [transfer_to_dict(t) for t in db.execute(stmt).scalars()]
