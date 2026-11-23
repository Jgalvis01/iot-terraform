from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Integer, String, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base
from app.modules.shared.utils import schemas_utils
from wcommon.events import build_event

JSON_TYPE = JSON().with_variant(JSONB(), "postgresql")


class OutboxEntity(Base):
    __tablename__ = "outbox"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    event_id = Column(String, unique=True, nullable=False)
    routing_key = Column(String, nullable=False)
    payload = Column(JSON_TYPE, nullable=False)
    created_at = Column(DateTime(timezone=True), default=schemas_utils.current_utc_time, nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)


def enqueue_event(db: Session, event_type: str, routing_key: str, payload: dict,
                  occurred_at: datetime | None = None) -> dict | None:
    if not settings.EVENTS_ENABLED:
        return None
    event = build_event(event_type, settings.event_source, payload, occurred_at=occurred_at)
    db.add(OutboxEntity(event_id=event["event_id"], routing_key=routing_key, payload=event))
    return event
