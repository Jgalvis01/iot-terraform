from sqlalchemy import select, func

from app.config import settings
from app.modules.inventory.models import InventoryMovementEntity
from app.modules.transfers.models import ProcessedCommandEntity
from app.outbox import OutboxEntity
from wh_unit_helpers import move


def cmd(client, tid, step, **body):
    return client.post(f"/transfers/{tid}/{step}", json=body)


def test_reserve_commit_out_flow(client, product):
    move(client, product["id"], "RECEIPT", 10)
    r = cmd(client, "TR-1", "reserve", sku="P-001", quantity=4)
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["quantity"], r.json()["reserved_quantity"]) == ("applied", 6, 4)
    r = cmd(client, "TR-1", "commit-out", sku="P-001", quantity=4)
    assert (r.json()["status"], r.json()["quantity"], r.json()["reserved_quantity"]) == ("applied", 6, 0)


def test_reserve_then_release_compensation(client, product):
    move(client, product["id"], "RECEIPT", 10)
    cmd(client, "TR-2", "reserve", sku="P-001", quantity=7)
    r = cmd(client, "TR-2", "release", sku="P-001", quantity=7)
    assert (r.json()["quantity"], r.json()["reserved_quantity"]) == (10, 0)


def test_commands_idempotent(client, session_factory, product):
    move(client, product["id"], "RECEIPT", 10)
    first = cmd(client, "TR-3", "reserve", sku="P-001", quantity=4)
    second = cmd(client, "TR-3", "reserve", sku="P-001", quantity=4)
    assert first.json()["status"] == "applied" and second.json()["status"] == "already_applied"
    assert second.json()["reserved_quantity"] == 4 and second.json()["quantity"] == 6
    with session_factory() as s:
        assert s.get(ProcessedCommandEntity, "TR-3:reserve") is not None
        n = s.execute(select(func.count()).select_from(InventoryMovementEntity)
                      .where(InventoryMovementEntity.reference == "TR-3")).scalar()
    assert n == 1


def test_insufficient_stock_409_and_not_marked_processed(client, session_factory, product):
    move(client, product["id"], "RECEIPT", 3)
    r = cmd(client, "TR-4", "reserve", sku="P-001", quantity=5)
    assert r.status_code == 409 and r.json()["code"] == "INSUFFICIENT_STOCK"
    with session_factory() as s:
        assert s.get(ProcessedCommandEntity, "TR-4:reserve") is None
    move(client, product["id"], "RECEIPT", 5)
    assert cmd(client, "TR-4", "reserve", sku="P-001", quantity=5).json()["status"] == "applied"


def test_unknown_product_404(client):
    assert cmd(client, "TR-5", "reserve", sku="NOPE", quantity=1).status_code == 404
    assert cmd(client, "TR-5", "release", sku="NOPE", quantity=1).status_code == 404
    assert cmd(client, "TR-5", "commit-out", sku="NOPE", quantity=1).status_code == 404


def test_receive_creates_product_and_is_idempotent(client, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "EVENTS_ENABLED", True)
    body = dict(sku="P-777", quantity=6, name="Nuevo", unit="unidad", category="X")
    r1 = cmd(client, "TR-6", "receive", **body)
    r2 = cmd(client, "TR-6", "receive", **body)
    assert r1.json()["status"] == "applied" and r1.json()["quantity"] == 6
    assert r2.json()["status"] == "already_applied" and r2.json()["quantity"] == 6
    assert client.get("/products/by-sku/P-777").status_code == 200
    with session_factory() as s:
        keys = [r.routing_key for r in s.execute(select(OutboxEntity).order_by(OutboxEntity.id)).scalars()]
    assert keys == ["product.created.wh-001", "inventory.updated.wh-001"]


def test_receive_on_existing_product_adds(client, product):
    move(client, product["id"], "RECEIPT", 2)
    r = cmd(client, "TR-7", "receive", sku="P-001", quantity=3, name="Caja", unit="unidad")
    assert r.json()["quantity"] == 5
