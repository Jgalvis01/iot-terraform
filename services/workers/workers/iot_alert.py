import os

from prometheus_client import Counter
from sqlalchemy.exc import IntegrityError, OperationalError

from wcommon.errors import TransientError
from wcommon.events import build_event
from wcommon.idempotency import already_processed, mark_processed
from wcommon.logging import log
from wcommon.metrics import MESSAGES_DUPLICATED
from wcommon.rabbit import EVENTS_EXCHANGE

from workers.iot_common import parse_reading
from workers.models import SensorAlert, utcnow

CONSUMER = "iot_alert"
ALERTS = Counter("iot_alerts_total", "Alertas IoT generadas", ["warehouse_id", "sensor_type"])

SENSOR_ALIASES = {"temperature": "temperature", "temp": "temperature", "temperatura": "temperature",
                  "humidity": "humidity", "humedad": "humidity",
                  "door": "door", "opening": "door", "apertura": "door"}


def thresholds_from_env() -> dict[str, float]:
    return {"temperature": float(os.getenv("TEMP_MAX_C", "8")),
            "humidity": float(os.getenv("HUMIDITY_MAX_PCT", "70")),
            "door": float(os.getenv("DOOR_OPEN_MAX_SECONDS", "60"))}


def evaluate(sensor_type: str | None, value: float, thresholds: dict[str, float]) -> float | None:
    kind = SENSOR_ALIASES.get((sensor_type or "").lower())
    if kind is None or kind not in thresholds:
        return None
    return thresholds[kind] if value > thresholds[kind] else None


class AlertEvaluator:
    def __init__(self, session_factory, publisher, thresholds: dict[str, float] | None = None):
        self.session_factory = session_factory
        self.publisher = publisher
        self.thresholds = thresholds or thresholds_from_env()

    def handle(self, msg: dict) -> str:
        r = parse_reading(msg)
        try:
            with self.session_factory() as session, session.begin():
                if already_processed(session, r["event_id"], CONSUMER):
                    MESSAGES_DUPLICATED.labels(CONSUMER).inc()
                    return "duplicate"
                threshold = evaluate(r["sensor_type"], r["value"], self.thresholds)
                if threshold is None:
                    mark_processed(session, r["event_id"], CONSUMER)
                    return "ok"
                alert_id = f"ALR-{r['event_id']}"
                measured = r["measured_at"].isoformat().replace("+00:00", "Z") if r["measured_at"] else None
                event = build_event("SENSOR_ALERT", "central-iot-alert-worker", {
                    "warehouse_id": r["warehouse_id"], "sensor_id": r["sensor_id"],
                    "sensor_type": r["sensor_type"], "value": r["value"], "threshold": threshold,
                    "measured_at": measured}, event_id=alert_id, correlation_id=msg.get("correlation_id"))
                self.publisher.publish(EVENTS_EXCHANGE, f"iot.alert.{r['warehouse_id'].lower()}", event)
                session.add(SensorAlert(alert_id=alert_id, reading_event_id=r["event_id"],
                                        warehouse_id=r["warehouse_id"], sensor_id=r["sensor_id"],
                                        sensor_type=r["sensor_type"], value=r["value"], threshold=threshold,
                                        measured_at=r["measured_at"], created_at=utcnow()))
                mark_processed(session, r["event_id"], CONSUMER)
        except IntegrityError as exc:
            raise TransientError(f"conflicto concurrente: {exc.orig!r}") from exc
        except OperationalError as exc:
            raise TransientError(f"BD no disponible: {exc.orig!r}") from exc
        ALERTS.labels(r["warehouse_id"], r["sensor_type"]).inc()
        log("iot_alert_raised", level="WARNING", event_id=alert_id, warehouse_id=r["warehouse_id"],
            sensor_id=r["sensor_id"], value=r["value"], threshold=threshold)
        return "alert"


def main() -> None:
    from workers.publisher import RabbitPublisher
    from workers.runtime import bootstrap, run
    session_factory = bootstrap("iot-alert-worker")
    run("q.iot.alerts", AlertEvaluator(session_factory, RabbitPublisher()).handle, CONSUMER, prefetch=50)


if __name__ == "__main__":
    main()
