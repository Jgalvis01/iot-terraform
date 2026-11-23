import uuid
from datetime import datetime, timezone

from wcommon.correlation import get_correlation_id
from wcommon.errors import PermanentError

REQUIRED_FIELDS = ("event_id", "event_type", "event_version", "occurred_at", "source", "payload")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def to_iso(dt: datetime) -> str:
    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def build_event(event_type: str, source: str, payload: dict, event_version: int = 1,
                correlation_id: str | None = None, event_id: str | None = None,
                occurred_at: datetime | None = None) -> dict:
    return {
        "event_id": event_id or f"EVT-{uuid.uuid4()}",
        "event_type": event_type,
        "event_version": event_version,
        "occurred_at": to_iso(occurred_at) if occurred_at is not None else utc_now_iso(),
        "correlation_id": correlation_id or get_correlation_id(),
        "source": source,
        "payload": payload,
    }


def validate_event(msg: dict) -> None:
    if not isinstance(msg, dict):
        raise PermanentError("El mensaje no es un objeto JSON")
    missing = [f for f in REQUIRED_FIELDS if f not in msg]
    if missing:
        raise PermanentError(f"Evento inválido, faltan campos: {missing}")
    if not isinstance(msg["payload"], dict):
        raise PermanentError("payload debe ser un objeto")
