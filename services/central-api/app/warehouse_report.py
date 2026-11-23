import os

from sqlalchemy import select
from sqlalchemy.orm import Session

from wcommon.reports import warehouses_summary

from app.command_outbox import pending_count
from app.config import get_settings
from app.models import GlobalInventory, Transfer, WarehouseStatus, WarehouseSync, utcnow


def low_stock_threshold() -> int:
    return int(os.getenv("LOW_STOCK_THRESHOLD", "5"))


def sync_rows(db: Session) -> dict[str, dict]:
    return {s.warehouse_id: {"last_heartbeat_at": s.last_heartbeat_at, "outbox_pending": s.outbox_pending,
                             "oldest_pending_at": s.oldest_pending_at, "last_snapshot_at": s.last_snapshot_at,
                             "last_snapshot_id": s.last_snapshot_id}
            for s in db.execute(select(WarehouseSync)).scalars()}


def build_summary(db: Session, threshold: int | None = None) -> dict:
    settings = get_settings()
    inventory = [{"sku": r.sku, "warehouse_id": r.warehouse_id, "quantity": r.quantity,
                  "reserved_quantity": r.reserved_quantity}
                 for r in db.execute(select(GlobalInventory)).scalars()]
    last = {s.warehouse_id: s.last_event_at for s in db.execute(select(WarehouseStatus)).scalars()}
    transfers = [{"source_warehouse": src, "destination_warehouse": dst, "status": st, "quantity": q}
                 for src, dst, st, q in db.execute(select(Transfer.source_warehouse, Transfer.destination_warehouse,
                                                          Transfer.status, Transfer.quantity)).all()]
    return warehouses_summary(
        configured=list(settings.warehouses), inventory=inventory, last_event_at=last, sync=sync_rows(db),
        transfers=transfers, now=utcnow(), stale_after_seconds=settings.stale_after_seconds,
        low_stock_threshold=low_stock_threshold() if threshold is None else threshold,
        pending_central_commands=pending_count(db),
    )
