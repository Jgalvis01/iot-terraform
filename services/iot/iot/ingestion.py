import json
import os
import queue
import re
import time
from collections import deque
from datetime import datetime

from prometheus_client import Counter, Gauge

RECEIVED = Counter("iot_ingestion_received_total", "Mensajes MQTT recibidos")
REJECTED = Counter("iot_ingestion_rejected_total", "Mensajes rechazados por validacion")
PUBLISHED = Counter("iot_ingestion_published_total", "Eventos publicados a RabbitMQ")
PENDING = Gauge("iot_ingestion_pending_events", "Eventos en buffer esperando a RabbitMQ")

WH_RE = re.compile(r"^WH-\d{3}$")
TOPIC_FILTER = "warehouses/+/sensors/+"


class ValidationError(ValueError):
    pass


def _nonempty_str(d: dict, key: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ValidationError(f"campo '{key}' ausente o invalido")
    return v


def validate_reading(data) -> dict:
    if not isinstance(data, dict):
        raise ValidationError("el mensaje no es un objeto JSON")
    event_id = _nonempty_str(data, "event_id")
    sensor_id = _nonempty_str(data, "sensor_id")
    sensor_type = _nonempty_str(data, "sensor_type")
    unit = _nonempty_str(data, "unit")
    wh = _nonempty_str(data, "warehouse_id")
    if not WH_RE.match(wh):
        raise ValidationError("warehouse_id con formato invalido")
    seq = data.get("sequence_number")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise ValidationError("sequence_number debe ser entero >= 0")
    value = data.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("value debe ser numerico")
    ts = _nonempty_str(data, "timestamp")
    try:
        datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError("timestamp no es ISO8601")
    return {"_event_id": event_id, "warehouse_id": wh, "sensor_id": sensor_id, "sensor_type": sensor_type,
            "value": value, "unit": unit, "sequence_number": seq, "measured_at": ts}


def routing_key_for(warehouse_id: str) -> str:
    return f"iot.reading.{warehouse_id.lower()}"


def to_event(data: dict) -> tuple[str, dict]:
    from wcommon.events import build_event
    payload = validate_reading(data)
    event_id = payload.pop("_event_id")
    event = build_event("SENSOR_READING", "iot-ingestion", payload, event_id=event_id)
    return routing_key_for(payload["warehouse_id"]), event


class Forwarder:

    def __init__(self, publish_fn, max_pending: int = 50000, log=None):
        self.publish_fn = publish_fn
        self.pending: deque = deque(maxlen=max_pending)
        self.log = log or (lambda *a, **k: print(json.dumps({"event": a[0], **k})))

    def handle(self, raw: bytes | str) -> bool:
        RECEIVED.inc()
        try:
            data = json.loads(raw)
            item = to_event(data)
        except (ValueError, UnicodeDecodeError) as exc:
            REJECTED.inc()
            self.log("reading_rejected", level="WARNING", reason=str(exc))
            return False
        self.pending.append(item)
        PENDING.set(len(self.pending))
        return True

    def flush(self) -> bool:
        while self.pending:
            rk, event = self.pending[0]
            try:
                self.publish_fn(rk, event)
            except Exception as exc:
                self.log("publish_failed", level="WARNING", error=repr(exc), pending=len(self.pending))
                PENDING.set(len(self.pending))
                return False
            self.pending.popleft()
            PUBLISHED.inc()
        PENDING.set(0)
        return True


def main() -> None:
    import paho.mqtt.client as mqtt
    from wcommon import rabbit
    from wcommon.logging import configure_logging, log
    from wcommon.metrics import start_metrics_server

    configure_logging("iot-ingestion")
    start_metrics_server(int(os.getenv("METRICS_PORT", "9100")))
    inbox: queue.Queue = queue.Queue(maxsize=100000)

    def logf(event, level="INFO", **kw):
        log(event, level=level, **kw)

    state = {"conn": None, "ch": None}

    def publish_fn(rk, event):
        if state["ch"] is None or state["ch"].is_closed:
            raise ConnectionError("canal RabbitMQ no disponible")
        rabbit.publish(state["ch"], rabbit.EVENTS_EXCHANGE, rk, event)

    fw = Forwarder(publish_fn, int(os.getenv("MAX_PENDING", "50000")), log=logf)

    host = os.getenv("MQTT_HOST", "mosquitto")
    port = int(os.getenv("MQTT_PORT", "1883"))
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=os.getenv("MQTT_CLIENT_ID", "iot-ingestion"),
                         clean_session=False)

    def on_connect(c, userdata, flags, reason_code, properties=None):
        log("mqtt_connected", host=host, port=port)
        c.subscribe(TOPIC_FILTER, qos=1)

    def on_message(c, userdata, msg):
        try:
            inbox.put_nowait(msg.payload)
        except queue.Full:
            REJECTED.inc()

    client.on_connect = on_connect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=10)
    client.connect_async(host, port, keepalive=30)
    client.loop_start()

    def ensure_rabbit() -> bool:
        if state["ch"] is not None and not state["ch"].is_closed:
            return True
        try:
            state["conn"] = rabbit.connect(max_wait_seconds=10)
            state["ch"] = rabbit.open_publisher_channel(state["conn"])
            log("rabbitmq_connected")
            return True
        except Exception as exc:
            state["ch"] = None
            log("rabbitmq_unavailable", level="WARNING", error=repr(exc), pending=len(fw.pending))
            return False

    log("ingestion_started", topic=TOPIC_FILTER)
    while True:
        try:
            raw = inbox.get(timeout=1)
            fw.handle(raw)
            while True:
                try:
                    fw.handle(inbox.get_nowait())
                except queue.Empty:
                    break
        except queue.Empty:
            pass
        if fw.pending or state["ch"] is None:
            if ensure_rabbit() and not fw.flush():
                state["ch"] = None
                time.sleep(1)
            elif state["ch"] is None:
                time.sleep(2)
        else:
            try:
                state["conn"].process_data_events(time_limit=0)
            except Exception:
                state["ch"] = None


if __name__ == "__main__":
    main()
