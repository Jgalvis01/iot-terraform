from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, String, select
from sqlalchemy.orm import declarative_base

_Base = declarative_base()


class ProcessedEvent(_Base):
    __tablename__ = "processed_events"
    event_id = Column(String, primary_key=True)
    consumer = Column(String, primary_key=True)
    processed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


def ensure_tables(engine) -> None:
    _Base.metadata.create_all(bind=engine)


def already_processed(session, event_id: str, consumer: str) -> bool:
    stmt = select(ProcessedEvent).where(ProcessedEvent.event_id == event_id,
                                        ProcessedEvent.consumer == consumer)
    return session.execute(stmt).first() is not None


def mark_processed(session, event_id: str, consumer: str) -> None:
    session.add(ProcessedEvent(event_id=event_id, consumer=consumer))
