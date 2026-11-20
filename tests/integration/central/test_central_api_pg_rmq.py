import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text


@pytest.fixture
def api(pg_url, rmq_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.setenv("WAREHOUSES", "WH-001=http://wh1:8000,WH-002=http://wh2:8000")
    import app.config
    import app.database
    import app.publisher

    def reset():
        app.config.get_settings.cache_clear()
        app.database._engine = None
        app.database._SessionLocal = None
        if app.publisher._publisher is not None:
            app.publisher._publisher._close()
        app.publisher._publisher = None

    reset()
    import main
    with TestClient(main.app) as c:
        yield c
    with create_engine(pg_url).begin() as conn:
        conn.execute(text("TRUNCATE transfers, report_jobs"))
    import pika
    rconn = pika.BlockingConnection(pika.URLParameters(rmq_url))
    for q in ("q.transfers", "q.reports"):
        rconn.channel().queue_purge(q)
    rconn.close()
    reset()


def test_post_transfer_publishes_to_q_transfers(api, channel):
    channel.queue_purge("q.transfers")
    r = api.post("/transfers", json={"sku": "P-001", "source_warehouse": "WH-001",
                                     "destination_warehouse": "WH-002", "quantity": 3},
                 headers={"Idempotency-Key": "itest-1", "X-Correlation-ID": "COR-itest"})
    assert r.status_code == 202, r.text
    tid = r.json()["transfer_id"]
    method, props, body = channel.basic_get("q.transfers", auto_ack=True)
    assert method is not None, "no llegó el comando a q.transfers"
    msg = json.loads(body)
    assert msg["event_type"] == "PROCESS_TRANSFER" and msg["payload"] == {"transfer_id": tid}
    assert msg["correlation_id"] == "COR-itest" and props.delivery_mode == 2
    t = api.get(f"/transfers/{tid}").json()
    assert t["status"] == "REQUESTED" and t["history"][0]["status"] == "REQUESTED"
    r2 = api.post("/transfers", json={"sku": "P-001", "source_warehouse": "WH-001",
                                      "destination_warehouse": "WH-002", "quantity": 3},
                  headers={"Idempotency-Key": "itest-1"})
    assert r2.json()["transfer_id"] == tid


def test_post_transfer_broker_down_returns_503(pg_url, rmq_url, api, monkeypatch):
    import app.publisher
    from app.publisher import CommandPublisher
    monkeypatch.setattr(app.publisher, "_publisher", CommandPublisher("amqp://guest:guest@localhost:1/%2F"))
    r = api.post("/transfers", json={"sku": "P-001", "source_warehouse": "WH-001",
                                     "destination_warehouse": "WH-002", "quantity": 3})
    assert r.status_code == 503
    with create_engine(pg_url).connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM transfers")).scalar() == 0


def test_post_report_publishes_to_q_reports(api, channel):
    channel.queue_purge("q.reports")
    r = api.post("/reports", json={"report_type": "inventory_snapshot"})
    assert r.status_code == 202
    method, _, body = channel.basic_get("q.reports", auto_ack=True)
    assert json.loads(body)["payload"]["job_id"] == r.json()["job_id"]
