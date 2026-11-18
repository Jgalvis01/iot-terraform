from dataclasses import dataclass

from prometheus_client import Counter
from sqlalchemy import select

from wcommon.errors import PermanentError, TransientError
from wcommon.events import build_event, validate_event
from wcommon.logging import log
from wcommon.rabbit import EVENTS_EXCHANGE, MAX_RETRIES

from workers.models import Transfer, utcnow
from workers.warehouse_client import WarehouseRejected

TERMINAL = frozenset({"COMPLETED", "CANCELLED", "FAILED"})
FAIL_STEPS = frozenset({"none", "before_reserve", "after_reserve", "destination_down"})

TRANSFER_TRANSITIONS = Counter("transfer_transitions_total", "Transiciones de la saga", ["status"])


@dataclass
class Outcome:
    status: str | None
    reason: str | None = None
    error: Exception | None = None


class TransferSaga:
    def __init__(self, session_factory, warehouses, publisher=None, fail_injection: str = "none",
                 max_retries: int = MAX_RETRIES, source: str = "central-orchestrator"):
        self.session_factory = session_factory
        self.wh = warehouses
        self.publisher = publisher
        self.fail_injection = (fail_injection or "none").lower()
        self.max_retries = max_retries
        self.source = source
        self._steps = {
            "REQUESTED": self._requested,
            "RESERVING_SOURCE": self._reserving_source,
            "SOURCE_RESERVED": self._source_reserved,
            "IN_TRANSIT": self._in_transit,
            "DESTINATION_PENDING": self._destination_pending,
            "DESTINATION_RECEIVED": self._destination_received,
            "COMPENSATING": self._compensating,
        }

    def handle(self, msg: dict) -> None:
        validate_event(msg)
        if msg["event_type"] != "PROCESS_TRANSFER":
            raise PermanentError(f"Comando inesperado en q.transfers: {msg['event_type']}")
        transfer_id = msg["payload"].get("transfer_id")
        if not transfer_id:
            raise PermanentError("PROCESS_TRANSFER sin transfer_id")
        self.process(transfer_id, retries=int(msg.get("_retries", 0)))

    # avanza la transferencia segun el estado en que este
    def process(self, transfer_id: str, retries: int = 0) -> str:
        for _ in range(len(self._steps) + 2):
            with self.session_factory() as session, session.begin():
                t = session.execute(select(Transfer).where(Transfer.transfer_id == transfer_id)
                                    .with_for_update()).scalar_one_or_none()
                if t is None:
                    raise PermanentError(f"Transferencia {transfer_id} no existe")
                if t.status in TERMINAL:
                    log("transfer_already_terminal", transfer_id=transfer_id, status=t.status)
                    return t.status
                step = self._steps.get(t.status)
                if step is None:
                    raise PermanentError(f"Estado desconocido {t.status} en {transfer_id}")
                previous = t.status
                outcome = step(t, retries)
                if outcome.status:
                    self._transition(t, outcome.status, outcome.reason)
                elif outcome.error is not None:
                    t.attempts = (t.attempts or 0) + 1
                    t.updated_at = utcnow()
                    t.history = list(t.history or []) + [{
                        "status": t.status, "at": _iso(t.updated_at), "event": "attempt_failed",
                        "retries": retries, "error": str(outcome.error)[:300]}]
                snapshot = _snapshot(t)
            if outcome.status:
                TRANSFER_TRANSITIONS.labels(outcome.status).inc()
                log("transfer_transition", transfer_id=transfer_id, sku=snapshot["sku"],
                    from_status=previous, to_status=outcome.status, reason=outcome.reason)
                self._publish_status(snapshot)
            if outcome.error is not None:
                log("transfer_step_error", level="WARNING", transfer_id=transfer_id, status=snapshot["status"],
                    retries=retries, error=str(outcome.error))
                raise outcome.error
            if snapshot["status"] in TERMINAL:
                return snapshot["status"]
        raise PermanentError(f"Saga {transfer_id} no convergió")

    def _fail_step(self, t: Transfer) -> str:
        value = (t.fail_step or self.fail_injection or "none").lower()
        return value if value in FAIL_STEPS else "none"

    def _requested(self, t, retries):
        return Outcome("RESERVING_SOURCE")

    def _reserving_source(self, t, retries):
        try:
            if self._fail_step(t) == "before_reserve":
                raise TransientError("injected failure: before_reserve")
            self.wh.reserve(t.source_warehouse, t.transfer_id, t.sku, t.quantity)
            return Outcome("SOURCE_RESERVED")
        except WarehouseRejected as exc:
            return Outcome("FAILED", f"source rejected reserve: {exc}")
        except TransientError as exc:
            if retries >= self.max_retries:
                return Outcome("FAILED", f"source unavailable: {exc}", error=exc)
            return Outcome(None, error=exc)

    def _source_reserved(self, t, retries):
        if self._fail_step(t) == "after_reserve":
            return Outcome("COMPENSATING", "injected failure: after_reserve")
        return Outcome("IN_TRANSIT")

    def _in_transit(self, t, retries):
        return Outcome("DESTINATION_PENDING")

    def _destination_pending(self, t, retries):
        try:
            if self._fail_step(t) == "destination_down":
                raise TransientError("injected failure: destination_down")
            product = self.wh.get_product(t.source_warehouse, t.sku)
            self.wh.receive(t.destination_warehouse, t.transfer_id, t.sku, t.quantity,
                            name=product.get("name") or t.sku, unit=product.get("unit") or "unit",
                            category=product.get("category") or "general")
            return Outcome("DESTINATION_RECEIVED")
        except WarehouseRejected as exc:
            return Outcome("COMPENSATING", f"destination step rejected: {exc}")
        except TransientError as exc:
            if retries >= self.max_retries:
                return Outcome("COMPENSATING", f"destination unavailable after {retries} retries: {exc}")
            return Outcome(None, error=exc)

    def _destination_received(self, t, retries):
        try:
            self.wh.commit_out(t.source_warehouse, t.transfer_id, t.sku, t.quantity)
            return Outcome("COMPLETED")
        except WarehouseRejected as exc:
            return Outcome(None, error=PermanentError(f"commit-out rejected, manual intervention: {exc}"))
        except TransientError as exc:
            return Outcome(None, error=exc)

    # si algo fallo se libera la reserva de la bodega origen
    def _compensating(self, t, retries):
        try:
            self.wh.release(t.source_warehouse, t.transfer_id, t.sku, t.quantity)
            return Outcome("CANCELLED")
        except WarehouseRejected as exc:
            return Outcome(None, error=PermanentError(f"release rejected, manual intervention: {exc}"))
        except TransientError as exc:
            return Outcome(None, error=exc)

    def _transition(self, t: Transfer, status: str, reason: str | None) -> None:
        t.status = status
        if reason:
            t.reason = reason
        t.updated_at = utcnow()
        entry = {"status": status, "at": _iso(t.updated_at)}
        if reason:
            entry["reason"] = reason
        t.history = list(t.history or []) + [entry]

    def _publish_status(self, snap: dict) -> None:
        if self.publisher is None:
            return
        event = build_event("TRANSFER_STATUS_CHANGED", self.source, {
            "transfer_id": snap["transfer_id"], "sku": snap["sku"],
            "source_warehouse": snap["source_warehouse"],
            "destination_warehouse": snap["destination_warehouse"],
            "quantity": snap["quantity"], "status": snap["status"], "reason": snap["reason"],
        }, correlation_id=snap["correlation_id"])
        try:
            self.publisher.publish(EVENTS_EXCHANGE, f"transfer.status.{snap['status'].lower()}", event)
        except Exception as exc:
            log("transfer_status_publish_failed", level="WARNING", transfer_id=snap["transfer_id"],
                status=snap["status"], error=repr(exc))


def _iso(dt) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _snapshot(t: Transfer) -> dict:
    return {"transfer_id": t.transfer_id, "sku": t.sku, "source_warehouse": t.source_warehouse,
            "destination_warehouse": t.destination_warehouse, "quantity": t.quantity,
            "status": t.status, "reason": t.reason, "correlation_id": t.correlation_id}
