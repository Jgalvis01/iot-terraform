import os
import time
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from wcommon.idempotency import ensure_tables
from wcommon.logging import log

from workers.models import Base


def make_engine(url: str | None = None):
    url = url or os.getenv("DATABASE_URL", "postgresql+psycopg2://central:central@central-db:5432/central_read")
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def init_db(engine, max_wait_seconds: int = 60) -> None:
    delay, waited = 1.0, 0.0
    while True:
        try:
            Base.metadata.create_all(bind=engine)
            ensure_tables(engine)
            return
        except Exception as exc:
            if waited >= max_wait_seconds:
                raise
            log("db_init_retry", level="WARNING", error=repr(exc), wait_seconds=delay)
            time.sleep(delay)
            waited += delay
            delay = min(delay * 2, 5)


def make_session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


def parse_ts(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return as_utc(value)
    return as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def insert_ignore(session, model, values: dict) -> None:
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    session.execute(insert(model).values(**values).on_conflict_do_nothing())
