from wcommon.errors import PermanentError
from wcommon.events import validate_event

from workers.db import parse_ts

REQUIRED = ("warehouse_id", "sensor_id", "value", "sequence_number")


def parse_reading(msg: dict) -> dict:
    validate_event(msg)
    if msg["event_type"] != "SENSOR_READING":
        raise PermanentError(f"evento inesperado: {msg['event_type']}")
    p = msg["payload"]
    missing = [f for f in REQUIRED if p.get(f) is None]
    if missing:
        raise PermanentError(f"SENSOR_READING sin campos {missing}")
    try:
        return {
            "event_id": msg["event_id"],
            "warehouse_id": str(p["warehouse_id"]).upper(),
            "sensor_id": str(p["sensor_id"]),
            "sensor_type": (p.get("sensor_type") or "").lower() or None,
            "value": float(p["value"]),
            "unit": p.get("unit"),
            "sequence_number": int(p["sequence_number"]),
            "measured_at": parse_ts(p.get("measured_at") or p.get("timestamp") or msg["occurred_at"]),
        }
    except (TypeError, ValueError) as exc:
        raise PermanentError(f"SENSOR_READING inválido: {exc!r}") from exc
