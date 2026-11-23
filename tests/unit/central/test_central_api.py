import os
from datetime import timedelta

import httpx
import pytest

os.environ["DATABASE_URL"] = "sqlite://"

from fastapi.testclient import TestClient

import app.config
import app.database
import app.publisher
from app.models import Base, GlobalInventory, Transfer, WarehouseStatus, utcnow
from app.publisher import PublishError, get_publisher


class FakeCommandPublisher:
    def __init__(self):
        self.sent = []
        self.down = False

    def publish_command(self, routing_key, message):
        if self.down:
            raise PublishError("down")
        self.sent.append((routing_key, message))


def reset_globals():
    app.config.get_settings.cache_clear()
    app.database._engine = None
    app.database._SessionLocal = None
    app.publisher._publisher = None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    monkeypatch.setenv("WAREHOUSES", "WH-001=http://wh1,WH-002=http://wh2,WH-003=http://wh3")
    monkeypatch.setenv("STALE_AFTER_SECONDS", "60")
    reset_globals()
    import main
    pub = FakeCommandPublisher()
    main.app.dependency_overrides[get_publisher] = lambda: pub
    with TestClient(main.app) as c:
        c.pub = pub
        yield c
    main.app.dependency_overrides.clear()
    Base.metadata.drop_all(app.database.get_engine())
    reset_globals()


def session():
    return app.database._SessionLocal()


BODY = {"sku": "P-001", "source_warehouse": "wh-001", "destination_warehouse": "WH-002", "quantity": 5}


def test_create_transfer_persists_and_publishes(client):
    r = client.post("/transfers", json=BODY, headers={"X-Correlation-ID": "COR-abc", "X-Fail-Step": "after_reserve"})
    assert r.status_code == 202
    data = r.json()
    assert data["status"] == "PROCESSING" and data["transfer_id"].startswith("TR-")
    assert r.headers["X-Correlation-ID"] == "COR-abc"
    rk, msg = client.pub.sent[0]
    assert rk == "transfer.process"
    assert msg["event_type"] == "PROCESS_TRANSFER"
    assert msg["payload"] == {"transfer_id": data["transfer_id"]}
    assert msg["correlation_id"] == "COR-abc"
    t = client.get(f"/transfers/{data['transfer_id']}").json()
    assert t["status"] == "REQUESTED" and t["fail_step"] == "after_reserve"
    assert t["source_warehouse"] == "WH-001" and t["history"][0]["status"] == "REQUESTED"
    assert len(client.get("/transfers").json()) == 1


def test_idempotency_key(client):
    h = {"Idempotency-Key": "k-1"}
    a = client.post("/transfers", json=BODY, headers=h).json()
    b = client.post("/transfers", json=BODY, headers=h).json()
    assert a["transfer_id"] == b["transfer_id"] and b["idempotent_replay"] is True
    assert len(client.get("/transfers").json()) == 1
    r = client.post("/transfers", json={**BODY, "quantity": 6}, headers=h)
    assert r.status_code == 409


def test_broker_down_queues_command_and_relay_flushes(client):
    from app.command_outbox import relay_commands
    from app.models import CommandOutbox
    client.pub.down = True
    r = client.post("/transfers", json=BODY, headers={"Idempotency-Key": "k-2"})
    assert r.status_code == 202 and r.json()["delivery"] == "queued"
    tid = r.json()["transfer_id"]
    assert client.get(f"/transfers/{tid}").json()["status"] == "REQUESTED"
    assert client.get("/health").json()["pending_commands"] == 1
    with session() as s:
        row = s.query(CommandOutbox).one()
        assert row.routing_key == "transfer.process" and row.payload["payload"] == {"transfer_id": tid}
        assert row.published_at is None

    def boom(rk, msg):
        raise ConnectionError("broker sigue caído")

    with session() as s:
        with pytest.raises(ConnectionError):
            relay_commands(s, boom)
    with session() as s:
        assert s.query(CommandOutbox).one().attempts == 1
    sent = []
    with session() as s:
        assert relay_commands(s, lambda rk, m: sent.append((rk, m["payload"]))) == 1
        assert relay_commands(s, lambda rk, m: sent.append("again")) == 0
    assert sent == [("transfer.process", {"transfer_id": tid})]
    assert client.get("/health").json()["pending_commands"] == 0
    client.pub.down = False
    again = client.post("/transfers", json=BODY, headers={"Idempotency-Key": "k-2"}).json()
    assert again["transfer_id"] == tid and again["idempotent_replay"] is True


def test_broker_and_queue_down_returns_503_without_orphan(client, monkeypatch):
    import app.routers.transfers as tr

    def fail(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(tr, "publish_or_queue", fail)
    r = client.post("/transfers", json=BODY)
    assert r.status_code == 503 and r.json()["code"] == "BROKER_UNAVAILABLE"
    with session() as s:
        assert s.query(Transfer).count() == 0


@pytest.mark.parametrize("body,headers", [
    ({**BODY, "source_warehouse": "WH-999"}, {}),
    ({**BODY, "destination_warehouse": "WH-001"}, {}),
    ({**BODY, "quantity": 0}, {}),
    (BODY, {"X-Fail-Step": "bogus"}),
])
def test_transfer_validation(client, body, headers):
    assert client.post("/transfers", json=body, headers=headers).status_code == 422
    assert client.pub.sent == []


def test_get_unknown_transfer_404(client):
    assert client.get("/transfers/TR-nope").status_code == 404


def test_readmodel_with_stale(client):
    now = utcnow()
    with session() as s, s.begin():
        s.add(GlobalInventory(sku="P-001", warehouse_id="WH-001", quantity=10, reserved_quantity=2, last_event_at=now))
        s.add(GlobalInventory(sku="P-001", warehouse_id="WH-002", quantity=4, reserved_quantity=0,
                              last_event_at=now - timedelta(minutes=10)))
        s.add(WarehouseStatus(warehouse_id="WH-001", last_event_at=now))
        s.add(WarehouseStatus(warehouse_id="WH-002", last_event_at=now - timedelta(minutes=10)))
    d = client.get("/global/inventory/P-001").json()
    assert d["mode"] == "readmodel" and d["total"] == 14
    stale = {w["warehouse_id"]: w["stale"] for w in d["warehouses"]}
    assert stale == {"WH-001": False, "WH-002": True}
    assert d["stale_warehouses"] == ["WH-002", "WH-003"]
    lst = client.get("/global/inventory").json()
    assert lst["items"][0]["sku"] == "P-001" and lst["items"][0]["total"] == 14
    assert client.get("/global/inventory/P-404").status_code == 404


def test_fanout_reports_unavailable(client, monkeypatch):
    def handler(request: httpx.Request):
        if request.url.host == "wh1":
            return httpx.Response(200, json={"sku": "P-001", "product_id": 1, "warehouse_id": "WH-001",
                                             "quantity": 7, "reserved_quantity": 1, "updated_at": "x"})
        if request.url.host == "wh2":
            raise httpx.ConnectTimeout("timeout")
        return httpx.Response(404, json={"detail": "not found"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    r = client.get("/global/inventory/P-001?mode=fanout")
    assert r.status_code == 200
    d = r.json()
    assert d["total"] == 7 and d["unavailable_warehouses"] == ["WH-002"]
    assert d["not_found_warehouses"] == ["WH-003"] and d["partial"] is True


def test_reports_and_audit(client):
    r = client.post("/reports", json={"report_type": "inventory_snapshot"})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    assert client.pub.sent[-1][0] == "report.generate"
    assert client.pub.sent[-1][1]["payload"] == {"job_id": job_id, "report_type": "inventory_snapshot"}
    assert client.get(f"/reports/jobs/{job_id}").json()["status"] == "PROCESSING"
    assert client.post("/reports", json={"report_type": "nope"}).status_code == 422
    client.pub.down = True
    queued = client.post("/reports", json={})
    assert queued.status_code == 202 and queued.json()["delivery"] == "queued"
    assert client.get("/audit?limit=5").json() == []


def test_health_and_metrics(client):
    h = client.get("/health")
    assert h.status_code == 200 and h.json()["checks"]["db"] == "ok"
    assert "http_requests_total" in client.get("/metrics").text
