from sqlalchemy import select

from app.config import settings
from app.outbox import OutboxEntity
from wh_unit_helpers import move


def stock(client, pid):
    return client.get(f"/inventory/{pid}").json()


def test_create_product_creates_zero_inventory_with_sku(client, product):
    inv = stock(client, product["id"])
    assert inv["sku"] == "P-001" and inv["quantity"] == 0 and inv["warehouse_id"] == "WH-001"


def test_duplicate_sku_conflict(client, product):
    r = client.post("/products", json={"sku": "P-001", "name": "x", "unit": "u"})
    assert r.status_code == 409


def test_product_by_sku_put_and_404(client, product):
    assert client.get("/products/by-sku/P-001").json()["id"] == product["id"]
    assert client.get("/products/by-sku/NOPE").status_code == 404
    r = client.put(f"/products/{product['id']}", json={"name": "Nueva"})
    assert r.status_code == 200 and r.json()["name"] == "Nueva" and r.json()["sku"] == "P-001"


def test_delete_blocked_when_stock_or_reserved(client, product):
    pid = product["id"]
    move(client, pid, "RECEIPT", 5)
    assert client.delete(f"/products/{pid}").status_code == 409
    move(client, pid, "SHIPMENT", 5)
    move(client, pid, "RECEIPT", 2)
    move(client, pid, "RESERVATION", 2)
    assert client.delete(f"/products/{pid}").status_code == 409
    move(client, pid, "RELEASE", 2)
    move(client, pid, "ADJUSTMENT", 0)
    assert client.delete(f"/products/{pid}").status_code == 204
    assert client.get(f"/products/{pid}").status_code == 404


def test_negative_stock_rejected_and_state_unchanged(client, product):
    pid = product["id"]
    move(client, pid, "RECEIPT", 10)
    r = move(client, pid, "SHIPMENT", 11)
    assert r.status_code == 400 and r.json()["code"] == "INSUFFICIENT_STOCK"
    assert stock(client, pid)["quantity"] == 10
    assert client.get(f"/inventory/{pid}/movements").json().__len__() == 1


def test_movement_by_sku_and_invalid_type(client, product):
    r = client.post("/inventory/movements", json={"sku": "P-001", "type": "RECEIPT", "quantity": 4})
    assert r.status_code == 201 and r.json()["sku"] == "P-001"
    assert client.post("/inventory/movements", json={"sku": "P-001", "type": "BOGUS", "quantity": 1}).status_code == 422
    assert client.post("/inventory/movements", json={"type": "RECEIPT", "quantity": 1}).status_code == 422
    assert client.post("/inventory/movements", json={"sku": "ZZZ", "type": "RECEIPT", "quantity": 1}).status_code == 404


def test_adjustment_sets_absolute(client, product):
    pid = product["id"]
    move(client, pid, "RECEIPT", 10)
    move(client, pid, "ADJUSTMENT", 3)
    assert stock(client, pid)["quantity"] == 3
    assert client.post("/inventory/movements", json={"product_id": pid, "type": "ADJUSTMENT", "quantity": -1}).status_code == 422


def test_reserve_and_release_via_movements(client, product):
    pid = product["id"]
    move(client, pid, "RECEIPT", 10)
    move(client, pid, "RESERVATION", 4)
    assert (stock(client, pid)["quantity"], stock(client, pid)["reserved_quantity"]) == (6, 4)
    assert move(client, pid, "RELEASE", 5).status_code == 400
    move(client, pid, "RELEASE", 4)
    assert (stock(client, pid)["quantity"], stock(client, pid)["reserved_quantity"]) == (10, 0)


def test_movements_by_date(client, product):
    from datetime import datetime, timezone
    move(client, product["id"], "RECEIPT", 1)
    today = datetime.now(timezone.utc).date().isoformat()
    assert len(client.get(f"/inventory/movements?date={today}").json()) == 1
    assert client.get("/inventory/movements?date=2000-01-01").json() == []


def test_health_ok_and_redis_down(client, monkeypatch):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["checks"] == {"db": "ok", "redis": "ok"}
    monkeypatch.setattr(settings, "CACHE_ENABLED", False)
    assert client.get("/health").json()["checks"]["redis"] == "disabled"


def test_correlation_id_header_roundtrip(client):
    r = client.get("/health", headers={"X-Correlation-ID": "COR-test123"})
    assert r.headers["X-Correlation-ID"] == "COR-test123"


def test_metrics_endpoint(client):
    assert "cache_hits_total" in client.get("/metrics").text


def test_no_outbox_when_events_disabled(client, session_factory, product):
    move(client, product["id"], "RECEIPT", 1)
    with session_factory() as s:
        assert s.execute(select(OutboxEntity)).first() is None


def test_outbox_events_in_same_tx_with_correlation(client, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "EVENTS_ENABLED", True)
    h = {"X-Correlation-ID": "COR-abc"}
    pid = client.post("/products", json={"sku": "P-9", "name": "n", "unit": "u"}, headers=h).json()["id"]
    client.post("/inventory/movements", json={"product_id": pid, "type": "RECEIPT", "quantity": 5, "reference": "OC-1"}, headers=h)
    with session_factory() as s:
        rows = s.execute(select(OutboxEntity).order_by(OutboxEntity.id)).scalars().all()
    assert [r.routing_key for r in rows] == ["product.created.wh-001", "inventory.updated.wh-001"]
    ev = rows[1].payload
    assert ev["event_type"] == "INVENTORY_UPDATED" and ev["correlation_id"] == "COR-abc"
    assert ev["source"] == "warehouse-001"
    assert ev["payload"] == {"warehouse_id": "WH-001", "sku": "P-9", "product_id": pid, "quantity": 5,
                             "reserved_quantity": 0, "movement_type": "RECEIPT", "movement_quantity": 5,
                             "reference": "OC-1"}


def test_failed_movement_writes_no_outbox(client, session_factory, monkeypatch, product):
    monkeypatch.setattr(settings, "EVENTS_ENABLED", True)
    assert move(client, product["id"], "SHIPMENT", 1).status_code == 400
    with session_factory() as s:
        assert s.execute(select(OutboxEntity)).first() is None
