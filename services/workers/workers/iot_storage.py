from prometheus_client import Counter
from sqlalchemy.exc import IntegrityError, OperationalError

from wcommon.errors import TransientError
from wcommon.idempotency import already_processed, mark_processed
from wcommon.logging import log
from wcommon.metrics import MESSAGES_DUPLICATED

from workers.iot_common import parse_reading
from workers.models import SensorReading, SensorState, utcnow

CONSUMER = "iot_storage"
OUT_OF_ORDER = Counter("iot_out_of_order_total", "Lecturas descartadas por sequence_number fuera de orden",
                       ["warehouse_id"])
STORED = Counter("iot_readings_stored_total", "Lecturas IoT almacenadas", ["warehouse_id", "sensor_type"])


class ReadingStorage:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def handle(self, msg: dict) -> str:
        r = parse_reading(msg)
        try:
            with self.session_factory() as session, session.begin():
                if already_processed(session, r["event_id"], CONSUMER):
                    MESSAGES_DUPLICATED.labels(CONSUMER).inc()
                    log("iot_reading_duplicate", event_id=r["event_id"], sensor_id=r["sensor_id"])
                    return "duplicate"
                state = session.get(SensorState, (r["warehouse_id"], r["sensor_id"]), with_for_update=True)
                if state is not None and r["sequence_number"] <= state.last_sequence_number:
                    OUT_OF_ORDER.labels(r["warehouse_id"]).inc()
                    mark_processed(session, r["event_id"], CONSUMER)
                    log("iot_reading_out_of_order", level="WARNING", event_id=r["event_id"],
                        warehouse_id=r["warehouse_id"], sensor_id=r["sensor_id"],
                        sequence_number=r["sequence_number"], last_sequence_number=state.last_sequence_number)
                    return "out_of_order"
                if state is None:
                    session.add(SensorState(warehouse_id=r["warehouse_id"], sensor_id=r["sensor_id"],
                                            last_sequence_number=r["sequence_number"],
                                            last_measured_at=r["measured_at"], updated_at=utcnow()))
                else:
                    state.last_sequence_number = r["sequence_number"]
                    state.last_measured_at = r["measured_at"]
                    state.updated_at = utcnow()
                session.add(SensorReading(**r, received_at=utcnow()))
                mark_processed(session, r["event_id"], CONSUMER)
        except IntegrityError as exc:
            raise TransientError(f"conflicto concurrente: {exc.orig!r}") from exc
        except OperationalError as exc:
            raise TransientError(f"BD no disponible: {exc.orig!r}") from exc
        STORED.labels(r["warehouse_id"], r["sensor_type"] or "unknown").inc()
        return "stored"


def main() -> None:
    from workers.runtime import bootstrap, run
    session_factory = bootstrap("iot-storage-worker")
    run("q.iot.storage", ReadingStorage(session_factory).handle, CONSUMER, prefetch=50)


if __name__ == "__main__":
    main()
