import json
import os
import random
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from prometheus_client import Counter, Gauge

SENSOR_TYPES = ("temperature", "humidity", "door", "motion", "energy")
UNITS = {"temperature": "C", "humidity": "%", "door": "s", "motion": "bool", "energy": "kW"}

SENT = Counter("iot_sim_messages_sent_total", "Mensajes publicados al broker")
BUFFERED = Gauge("iot_sim_buffered_messages", "Mensajes en buffer local por desconexion")
DUPLICATES = Counter("iot_sim_duplicates_injected_total", "Duplicados inyectados")
OUT_OF_ORDER = Counter("iot_sim_out_of_order_injected_total", "Mensajes enviados fuera de orden")


def build_sensors(count: int, warehouses: list[str], sequence_start: int = 0) -> list[dict]:
    sensors = []
    for i in range(count):
        sensors.append({
            "sensor_id": f"S-{i + 1:04d}",
            "warehouse_id": warehouses[i % len(warehouses)],
            "sensor_type": SENSOR_TYPES[i % len(SENSOR_TYPES)],
            "sequence": sequence_start,
        })
    return sensors


def sample_value(sensor_type: str, rng: random.Random, anomaly_rate: float = 0.03):
    anomaly = rng.random() < anomaly_rate
    if sensor_type == "temperature":
        return round(rng.uniform(9.0, 14.0) if anomaly else rng.gauss(5.0, 1.2), 2)
    if sensor_type == "humidity":
        return round(rng.uniform(71.0, 90.0) if anomaly else rng.gauss(55.0, 6.0), 2)
    if sensor_type == "door":
        return rng.randint(120, 600) if anomaly else (rng.randint(5, 40) if rng.random() < 0.2 else 0)
    if sensor_type == "motion":
        return 1 if rng.random() < 0.3 else 0
    if sensor_type == "energy":
        return round(rng.uniform(25.0, 40.0) if anomaly else max(0.0, rng.gauss(12.0, 2.5)), 2)
    raise ValueError(sensor_type)


def make_message(sensor: dict, rng: random.Random, now: datetime | None = None) -> dict:
    sensor["sequence"] += 1
    now = now or datetime.now(timezone.utc)
    return {
        "event_id": f"EVT-{uuid.UUID(int=rng.getrandbits(128), version=4)}",
        "sequence_number": sensor["sequence"],
        "warehouse_id": sensor["warehouse_id"],
        "sensor_id": sensor["sensor_id"],
        "sensor_type": sensor["sensor_type"],
        "value": sample_value(sensor["sensor_type"], rng),
        "unit": UNITS[sensor["sensor_type"]],
        "timestamp": now.isoformat().replace("+00:00", "Z"),
    }


def topic_for(msg: dict) -> str:
    return f"warehouses/{msg['warehouse_id']}/sensors/{msg['sensor_id']}"


class Emitter:

    def __init__(self, client, rng: random.Random, duplicate_rate=0.05, out_of_order_rate=0.05,
                 buffer_size=10000, log=None):
        self.client = client
        self.rng = rng
        self.duplicate_rate = duplicate_rate
        self.out_of_order_rate = out_of_order_rate
        self.buffer: deque = deque(maxlen=buffer_size)
        self.connected = False
        self.held: dict[str, dict] = {}
        self.log = log or (lambda event, **f: print(json.dumps({"event": event, **f})))

    def _raw_send(self, msg: dict) -> None:
        if not self.connected:
            self._buffer(msg)
            return
        info = self.client.publish(topic_for(msg), json.dumps(msg), qos=1)
        if info is not None and getattr(info, "rc", 0) != 0:
            self._buffer(msg)
            return
        SENT.inc()

    def _buffer(self, msg: dict) -> None:
        self.buffer.append(msg)
        BUFFERED.set(len(self.buffer))

    def emit(self, msg: dict) -> None:
        held = self.held.pop(msg["sensor_id"], None)
        if held is not None:
            self._raw_send(msg)
            self._raw_send(held)
            OUT_OF_ORDER.inc()
        elif self.rng.random() < self.out_of_order_rate:
            self.held[msg["sensor_id"]] = msg
            return
        else:
            self._raw_send(msg)
        if self.rng.random() < self.duplicate_rate:
            self._raw_send(msg)
            DUPLICATES.inc()

    def on_connect(self) -> None:
        self.connected = True
        self.flush()

    def on_disconnect(self) -> None:
        self.connected = False

    def flush(self) -> int:
        n = 0
        while self.buffer and self.connected:
            msg = self.buffer.popleft()
            before = len(self.buffer)
            self._raw_send(msg)
            if len(self.buffer) > before:
                self.buffer.rotate(1)
                break
            n += 1
        BUFFERED.set(len(self.buffer))
        if n:
            self.log("buffer_flushed", count=n, remaining=len(self.buffer))
        return n


def main() -> None:
    import paho.mqtt.client as mqtt
    from wcommon.logging import configure_logging, log
    from wcommon.metrics import start_metrics_server

    configure_logging("iot-simulator")
    host = os.getenv("MQTT_HOST", "mosquitto")
    port = int(os.getenv("MQTT_PORT", "1883"))
    n = int(os.getenv("SENSORS", "100"))
    warehouses = [w.strip() for w in os.getenv("WAREHOUSES", "WH-001,WH-002,WH-003,WH-004,WH-005").split(",") if w.strip()]
    interval = float(os.getenv("INTERVAL_SECONDS", "2"))
    seed = os.getenv("RANDOM_SEED")
    rng = random.Random(int(seed)) if seed else random.Random()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"iot-sim-{uuid.uuid4().hex[:8]}")
    emitter = Emitter(client, rng, float(os.getenv("DUPLICATE_RATE", "0.05")),
                      float(os.getenv("OUT_OF_ORDER_RATE", "0.05")),
                      int(os.getenv("BUFFER_SIZE", "10000")),
                      log=lambda event, **f: log(event, **f))

    def on_connect(c, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            log("mqtt_connected", host=host, port=port, buffered=len(emitter.buffer))
            emitter.on_connect()

    def on_disconnect(c, userdata, flags, reason_code, properties=None):
        log("mqtt_disconnected", level="WARNING", reason=str(reason_code))
        emitter.on_disconnect()

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.reconnect_delay_set(min_delay=1, max_delay=10)
    client.connect_async(host, port, keepalive=30)
    client.loop_start()
    start_metrics_server(int(os.getenv("METRICS_PORT", "9100")))

    sensors = build_sensors(n, warehouses, sequence_start=int(time.time() * 1000))
    log("simulator_started", sensors=n, warehouses=warehouses, interval=interval)
    while True:
        started = time.monotonic()
        for s in sensors:
            emitter.emit(make_message(s, rng))
            time.sleep(interval / max(len(sensors), 1))
        spent = time.monotonic() - started
        if spent < interval:
            time.sleep(interval - spent)


if __name__ == "__main__":
    main()
