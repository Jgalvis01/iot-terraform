"""Cola de respaldo de comandos de la central.

Si el broker no está disponible cuando la central emite un comando (transferencia,
reporte), el comando se guarda en ``command_outbox`` (PostgreSQL) y la petición
se acepta igualmente (202). El servicio ``command-relay`` los publica en orden
cuando el broker vuelve. Así la central no deja de atender aunque se caiga el broker.
"""
from datetime import datetime, timezone
from typing import Callable

from prometheus_client import Counter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from wcommon.logging import log

from app.models import CommandOutbox
from app.publisher import PublishError

PUBLISHED, QUEUED = "published", "queued"
COMMANDS_QUEUED = Counter("central_commands_queued_total", "Comandos guardados en la cola de respaldo", ["command"])


def publish_or_queue(db: Session, publisher, routing_key: str, message: dict) -> str:
    try:
        publisher.publish_command(routing_key, message)
        return PUBLISHED
    except PublishError as exc:
        db.add(CommandOutbox(event_id=message["event_id"], routing_key=routing_key, payload=message,
                             attempts=0, last_error=str(exc)[:500]))
        db.commit()
        COMMANDS_QUEUED.labels(message.get("event_type", "?")).inc()
        log("command_queued_broker_down", level="WARNING", routing_key=routing_key,
            event_id=message["event_id"], event_type=message.get("event_type"), error=str(exc))
        return QUEUED


def pending_count(db: Session) -> int:
    return int(db.execute(select(func.count(CommandOutbox.id))
                          .where(CommandOutbox.published_at.is_(None))).scalar_one() or 0)


def relay_commands(session: Session, publish_fn: Callable[[str, dict], None], batch_size: int = 100) -> int:
    rows = session.execute(
        select(CommandOutbox).where(CommandOutbox.published_at.is_(None))
        .order_by(CommandOutbox.id).limit(batch_size).with_for_update(skip_locked=True)
    ).scalars().all()
    published = 0
    for row in rows:
        row.attempts = (row.attempts or 0) + 1
        try:
            publish_fn(row.routing_key, row.payload)
        except Exception as exc:
            row.last_error = repr(exc)[:500]
            session.commit()
            raise
        row.published_at = datetime.now(timezone.utc)
        published += 1
        log("command_relayed", event_id=row.event_id, routing_key=row.routing_key, attempts=row.attempts)
    session.commit()
    return published
