from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.sync import enqueue_snapshot, outbox_stats
from wcommon.logging import log

router = APIRouter(prefix="/sync", tags=["Sync"])


@router.get("/status")
def sync_status(db: Session = Depends(get_db)):
    """Estado de la cola local (outbox): eventos pendientes de enviar a la central."""
    return {"warehouse_id": settings.WAREHOUSE_ID, "events_enabled": settings.EVENTS_ENABLED,
            **outbox_stats(db)}


@router.post("/snapshot", status_code=202)
def request_snapshot(reason: str = Query("manual", max_length=32), db: Session = Depends(get_db)):
    """Encola el inventario completo para resincronizar la central (lo usa la central o un operador)."""
    result = enqueue_snapshot(db, reason)
    if result is None:
        return JSONResponse(status_code=409, content={
            "detail": "Eventos desactivados en esta bodega (EVENTS_ENABLED=false)", "code": "EVENTS_DISABLED"})
    db.commit()
    log("warehouse_snapshot_enqueued", warehouse_id=settings.WAREHOUSE_ID, **result)
    return {"warehouse_id": settings.WAREHOUSE_ID, **result}
