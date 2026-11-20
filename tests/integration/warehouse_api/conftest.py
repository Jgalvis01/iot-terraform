import sys
from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

SERVICE_DIR = Path(__file__).resolve().parents[3] / "services" / "warehouse-api"
sys.path.insert(0, str(SERVICE_DIR))

import app.models
from app import cache as cache_mod
from app.config import settings
from app.database import Base, get_db
from main import app

from wh_it_helpers import recreate_db


@pytest.fixture(scope="session")
def pg_engine():
    engine = create_engine(recreate_db("wh_test"), pool_size=10)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def session_factory(pg_engine):
    with pg_engine.begin() as c:
        c.execute(text("TRUNCATE products, inventory, inventory_movements, outbox, processed_commands "
                       "RESTART IDENTITY CASCADE"))
    return sessionmaker(bind=pg_engine, autoflush=False)


@pytest.fixture()
def fake_redis(monkeypatch):
    client = fakeredis.FakeRedis()
    monkeypatch.setattr(cache_mod.cache, "_client", client)
    monkeypatch.setattr(settings, "CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "WAREHOUSE_ID", "WH-001")
    return client


@pytest.fixture()
def client(session_factory, fake_redis):
    def _get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
