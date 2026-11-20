import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for p in (ROOT / "services" / "workers", ROOT / "services" / "central-api", ROOT / "libs" / "wcommon"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

TEST_PG_URL = os.getenv("CENTRAL_TEST_PG_URL", "postgresql+psycopg2://postgres:postgres@localhost:55433/postgres")
TEST_RMQ_URL = os.getenv("CENTRAL_TEST_RMQ_URL", "amqp://guest:guest@localhost:55672/%2F")
os.environ.setdefault("RETRY_DELAY_MS", "300")

import pika
import pytest
from sqlalchemy import create_engine, text


@pytest.fixture(scope="session")
def pg_url():
    try:
        with create_engine(TEST_PG_URL).connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:
        pytest.skip(f"PostgreSQL de test no disponible: {exc}")
    return TEST_PG_URL


@pytest.fixture(scope="session")
def rmq_url(monkeypatch_session):
    try:
        pika.BlockingConnection(pika.URLParameters(TEST_RMQ_URL)).close()
    except Exception as exc:
        pytest.skip(f"RabbitMQ de test no disponible: {exc}")
    import wcommon.rabbit as rabbit
    monkeypatch_session.setattr(rabbit, "RETRY_DELAY_MS", int(os.environ["RETRY_DELAY_MS"]))
    monkeypatch_session.setenv("RABBITMQ_URL", TEST_RMQ_URL)
    return TEST_RMQ_URL


@pytest.fixture(scope="session")
def monkeypatch_session():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture
def channel(rmq_url):
    from wcommon.rabbit import declare_topology
    conn = pika.BlockingConnection(pika.URLParameters(rmq_url))
    ch = conn.channel()
    declare_topology(ch)
    yield ch
    conn.close()
