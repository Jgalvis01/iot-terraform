"""Sincronización bodega -> central.

La tabla ``outbox`` es la cola local durable: cada cambio de inventario se guarda en
la misma transacción que el evento. Si se cae el broker o la conexión con la central,
los eventos se quedan aquí y el outbox relay los envía en orden al reconectar.

Después de una reconexión el relay encola además un ``INVENTORY_SNAPSHOT`` con el
estado completo de la bodega, de modo que la central queda sincronizada aunque se
haya perdido algún evento (p.ej. uno que acabó en DLQ).
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.modules.inventory.models import InventoryEntity
from app.modules.products.models import ProductEntity
from app.outbox import OutboxEntity, enqueue_event

SNAPSHOT_CHUNK = 500
SNAPSHOT_EVENT = "INVENTORY_SNAPSHOT"


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def snapshot_routing_key() -> str:
    return f"inventory.snapshot.{settings.wh_lower}"


def outbox_stats(db: Session) -> dict:
    pending_filter = OutboxEntity.published_at.is_(None)
    pending = db.execute(select(func.count(OutboxEntity.id)).where(pending_filter)).scalar_one()
    oldest = db.execute(select(func.min(OutboxEntity.created_at)).where(pending_filter)).scalar_one()
    last_published = db.execute(select(func.max(OutboxEntity.published_at))).scalar_one()
    return {"outbox_pending": int(pending or 0), "oldest_pending_at": _iso(oldest),
            "last_published_at": _iso(last_published)}


def snapshot_pending(db: Session) -> bool:
    return db.execute(select(OutboxEntity.id).where(
        OutboxEntity.published_at.is_(None), OutboxEntity.routing_key == snapshot_routing_key()
    ).limit(1)).first() is not None


def enqueue_snapshot(db: Session, reason: str, skip_if_pending: bool = False) -> dict | None:
    """Encola el inventario completo en el outbox (no hace commit). None si no se encoló."""
    if not settings.EVENTS_ENABLED:
        return None
    if skip_if_pending and snapshot_pending(db):
        return None
    rows = db.execute(
        select(ProductEntity.sku, InventoryEntity.quantity, InventoryEntity.reserved_quantity,
               InventoryEntity.updated_at)
        .join(InventoryEntity, InventoryEntity.product_id == ProductEntity.id)
        .order_by(ProductEntity.sku)
    ).all()
    items = [{"sku": sku, "quantity": int(q or 0), "reserved_quantity": int(r or 0), "updated_at": _iso(u)}
             for sku, q, r, u in rows]
    chunks = [items[i:i + SNAPSHOT_CHUNK] for i in range(0, len(items), SNAPSHOT_CHUNK)] or [[]]
    snapshot_id = f"SNAP-{uuid.uuid4().hex[:12]}"
    for idx, chunk in enumerate(chunks, start=1):
        enqueue_event(db, SNAPSHOT_EVENT, snapshot_routing_key(), {
            "warehouse_id": settings.WAREHOUSE_ID, "snapshot_id": snapshot_id, "reason": reason,
            "chunk": idx, "chunks": len(chunks), "total_items": len(items), "items": chunk,
        })
    return {"snapshot_id": snapshot_id, "items": len(items), "chunks": len(chunks), "reason": reason}
