import sys
from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

SERVICE_DIR = Path(__file__).resolve().parents[3] / "services" / "warehouse-api"
sys.path.insert(0, str(SERVICE_DIR))

import app.models
from app import cache as cache_mod
from app.config import settings
from app.database import Base, get_db
from main import app


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite+pysqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, autoflush=False)
    engine.dispose()


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


@pytest.fixture()
def product(client):
    r = client.post("/products", json={"sku": "P-001", "name": "Caja", "unit": "unidad", "category": "Empaque"})
    assert r.status_code == 201, r.text
    return r.json()
