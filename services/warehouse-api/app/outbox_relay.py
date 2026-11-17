import signal
import time
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.outbox import OutboxEntity
from wcommon import rabbit
from wcommon.events import build_event
from wcommon.logging import configure_logging, log

BATCH_SIZE = 100
POLL_SECONDS = 1.0
MAX_BACKOFF = 30.0
HEARTBEAT_SECONDS = 15.0
_running = True


# lee los eventos pendientes del outbox y los manda a rabbitmq
def relay_once(session: Session, publish_fn: Callable[[str, dict], None], batch_size: int = BATCH_SIZE) -> int:
    rows = session.execute(
        select(OutboxEntity).where(OutboxEntity.published_at.is_(None))
        .order_by(OutboxEntity.id).limit(batch_size).with_for_update(skip_locked=True)
    ).scalars().all()
    published = 0
    for row in rows:
        try:
            publish_fn(row.routing_key, row.payload)
        except Exception:
            row.attempts = (row.attempts or 0) + 1
            session.commit()
            raise
        row.published_at = datetime.now(timezone.utc)
        row.attempts = (row.attempts or 0) + 1
        published += 1
        log("outbox_published", event_id=row.event_id, routing_key=row.routing_key,
            correlation_id_event=row.payload.get("correlation_id"))
    session.commit()
    return published


def _stop(*_):
    global _running
    _running = False


def run() -> None:
    configure_logging(f"{settings.SERVICE_NAME}-outbox")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log("outbox_relay_started", warehouse_id=settings.WAREHOUSE_ID)
    connection = channel = None
    backoff = 1.0
    last_heartbeat = 0.0
    while _running:
        try:
            Base.metadata.create_all(bind=engine)
            if channel is None or channel.is_closed:
                connection = rabbit.connect(max_wait_seconds=0)
                channel = rabbit.open_publisher_channel(connection)
                log("outbox_rabbit_connected")

            def publish_fn(routing_key: str, message: dict) -> None:
                rabbit.publish(channel, rabbit.EVENTS_EXCHANGE, routing_key, message)

            with SessionLocal() as session:
                n = relay_once(session, publish_fn)
            if time.monotonic() - last_heartbeat >= HEARTBEAT_SECONDS:
                wh = settings.WAREHOUSE_ID
                publish_fn(f"warehouse.heartbeat.{wh.lower()}", build_event(
                    "WAREHOUSE_HEARTBEAT", f"warehouse-{wh.split('-')[-1]}", {"warehouse_id": wh}))
                last_heartbeat = time.monotonic()
            backoff = 1.0
            if n < BATCH_SIZE:
                time.sleep(POLL_SECONDS)
        except Exception as exc:
            log("outbox_relay_error", level="WARNING", error=repr(exc), retry_in_seconds=backoff)
            try:
                if connection is not None and connection.is_open:
                    connection.close()
            except Exception:
                pass
            connection = channel = None
            time.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)
    try:
        if connection is not None and connection.is_open:
            connection.close()
    except Exception:
        pass
    log("outbox_relay_stopped")


if __name__ == "__main__":
    run()
