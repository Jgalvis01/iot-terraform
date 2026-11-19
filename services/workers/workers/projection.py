from prometheus_client import Counter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from wcommon.errors import PermanentError, TransientError
from wcommon.events import validate_event
from wcommon.idempotency import already_processed, mark_processed
from wcommon.logging import log
from wcommon.metrics import MESSAGES_DUPLICATED

from workers.db import as_utc, parse_ts
from workers.models import GlobalInventory, WarehouseStatus

CONSUMER = "projection"
HANDLED = {"INVENTORY_UPDATED", "PRODUCT_CREATED"}
OUT_OF_ORDER = Counter("projection_out_of_order_total", "Eventos descartados por llegar fuera de orden",
                       ["warehouse_id"])


class Projection:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    # actualiza el read model de la central con cada evento
    def handle(self, msg: dict) -> str:
        validate_event(msg)
        if msg["event_type"] == "WAREHOUSE_HEARTBEAT":
            return self._heartbeat(msg)
        if msg["event_type"] not in HANDLED:
            return "ignored"
        payload = msg["payload"]
        try:
            sku = payload["sku"]
            warehouse_id = str(payload["warehouse_id"]).upper()
            occurred_at = parse_ts(msg["occurred_at"])
        except (KeyError, ValueError) as exc:
            raise PermanentError(f"payload inválido: {exc!r}") from exc
        try:
            with self.session_factory() as session, session.begin():
                if already_processed(session, msg["event_id"], CONSUMER):
                    MESSAGES_DUPLICATED.labels(CONSUMER).inc()
                    log("projection_duplicate", event_id=msg["event_id"], sku=sku)
                    return "duplicate"
                result = self._apply(session, msg, sku, warehouse_id, occurred_at)
                self._touch_warehouse(session, warehouse_id, occurred_at)
                mark_processed(session, msg["event_id"], CONSUMER)
        except IntegrityError as exc:
            raise TransientError(f"conflicto concurrente: {exc.orig!r}") from exc
        except OperationalError as exc:
            raise TransientError(f"BD no disponible: {exc.orig!r}") from exc
        log("projection_applied" if result == "applied" else "projection_" + result,
            event_id=msg["event_id"], sku=sku, warehouse_id=warehouse_id, event_type=msg["event_type"])
        return result

    def _apply(self, session, msg, sku, warehouse_id, occurred_at) -> str:
        row = session.execute(select(GlobalInventory).where(
            GlobalInventory.sku == sku, GlobalInventory.warehouse_id == warehouse_id)
            .with_for_update()).scalar_one_or_none()
        if msg["event_type"] == "PRODUCT_CREATED":
            if row is None:
                session.add(GlobalInventory(sku=sku, warehouse_id=warehouse_id, quantity=0,
                                            reserved_quantity=0, last_event_id=msg["event_id"],
                                            last_event_at=occurred_at))
                return "applied"
            return "noop"
        payload = msg["payload"]
        try:
            quantity = int(payload["quantity"])
            reserved = int(payload.get("reserved_quantity", 0) or 0)
        except (KeyError, TypeError, ValueError) as exc:
            raise PermanentError(f"INVENTORY_UPDATED inválido: {exc!r}") from exc
        if row is None:
            session.add(GlobalInventory(sku=sku, warehouse_id=warehouse_id, quantity=quantity,
                                        reserved_quantity=reserved, last_event_id=msg["event_id"],
                                        last_event_at=occurred_at))
            return "applied"
        last = as_utc(row.last_event_at)
        if last is not None and occurred_at < last:
            OUT_OF_ORDER.labels(warehouse_id).inc()
            return "out_of_order_discarded"
        row.quantity = quantity
        row.reserved_quantity = reserved
        row.last_event_id = msg["event_id"]
        row.last_event_at = occurred_at
        return "applied"

    def _heartbeat(self, msg: dict) -> str:
        try:
            warehouse_id = str(msg["payload"]["warehouse_id"]).upper()
            occurred_at = parse_ts(msg["occurred_at"])
        except (KeyError, ValueError) as exc:
            raise PermanentError(f"heartbeat inválido: {exc!r}") from exc
        try:
            with self.session_factory() as session, session.begin():
                self._touch_warehouse(session, warehouse_id, occurred_at)
        except (IntegrityError, OperationalError) as exc:
            raise TransientError(f"BD no disponible: {exc!r}") from exc
        return "heartbeat"

    @staticmethod
    def _touch_warehouse(session, warehouse_id, occurred_at) -> None:
        status = session.get(WarehouseStatus, warehouse_id, with_for_update=True)
        if status is None:
            session.add(WarehouseStatus(warehouse_id=warehouse_id, last_event_at=occurred_at))
        elif status.last_event_at is None or as_utc(status.last_event_at) < occurred_at:
            status.last_event_at = occurred_at


def main() -> None:
    from workers.runtime import bootstrap, run
    session_factory = bootstrap("projection-worker")
    run("q.projection", Projection(session_factory).handle, CONSUMER)


if __name__ == "__main__":
    main()
