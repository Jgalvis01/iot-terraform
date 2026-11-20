import threading
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text

import app.database as appdb
from wh_it_helpers import recreate_db
from app.config import settings
from app.errors import ApiError
from app.modules.inventory.inventory_service import InventoryService
from app.modules.inventory.schemas import MovementCreate
from app.modules.transfers.schemas import StepCommand
from app.modules.transfers.transfers_service import TransferService
from app.outbox import OutboxEntity
from app.outbox_relay import relay_once

ROOT = Path(__file__).resolve().parents[3]


def mk_product(client, sku="P-001", qty=0):
    pid = client.post("/products", json={"sku": sku, "name": "n", "unit": "u"}).json()["id"]
    if qty:
        client.post("/inventory/movements", json={"product_id": pid, "type": "RECEIPT", "quantity": qty})
    return pid


def run_parallel(fns):
    barrier = threading.Barrier(len(fns))
    results = [None] * len(fns)

    def wrap(i, fn):
        barrier.wait()
        try:
            results[i] = ("ok", fn())
        except Exception as exc:
            results[i] = ("err", exc)

    threads = [threading.Thread(target=wrap, args=(i, f)) for i, f in enumerate(fns)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return results


def test_concurrent_shipments_only_one_passes(client, session_factory):
    pid = mk_product(client, qty=10)

    def shipment():
        with session_factory() as s:
            return InventoryService(s).process_movement(
                MovementCreate(product_id=pid, type="SHIPMENT", quantity=8))

    results = run_parallel([shipment, shipment])
    assert sorted(r[0] for r in results) == ["err", "ok"]
    err = next(r[1] for r in results if r[0] == "err")
    assert isinstance(err, ApiError) and err.code == "INSUFFICIENT_STOCK"
    assert client.get(f"/inventory/{pid}").json()["quantity"] == 2
    assert len(client.get(f"/inventory/{pid}/movements").json()) == 2


def test_many_concurrent_receipts_no_lost_updates(client, session_factory):
    pid = mk_product(client)

    def receipt():
        with session_factory() as s:
            InventoryService(s).process_movement(MovementCreate(product_id=pid, type="RECEIPT", quantity=1))

    results = run_parallel([receipt] * 8)
    assert all(r[0] == "ok" for r in results)
    assert client.get(f"/inventory/{pid}").json()["quantity"] == 8


def test_same_saga_command_concurrent_applies_once(client, session_factory):
    pid = mk_product(client, qty=10)

    def reserve():
        with session_factory() as s:
            return TransferService(s).execute("TR-C", "reserve", StepCommand(sku="P-001", quantity=4))["status"]

    results = run_parallel([reserve] * 4)
    assert sorted(r[1] for r in results) == ["already_applied"] * 3 + ["applied"]
    inv = client.get(f"/inventory/{pid}").json()
    assert (inv["quantity"], inv["reserved_quantity"]) == (6, 4)


def test_concurrent_receive_creating_same_product(client, session_factory):
    def receive(tid):
        def fn():
            from app.modules.transfers.schemas import ReceiveCommand
            with session_factory() as s:
                return TransferService(s).execute(
                    tid, "receive", ReceiveCommand(sku="P-NEW", quantity=2, name="n", unit="u"))["status"]
        return fn

    results = run_parallel([receive("TR-A"), receive("TR-B")])
    assert [r[0] for r in results] == ["ok", "ok"]
    assert client.get("/inventory/by-sku/P-NEW").json()["quantity"] == 4


def test_outbox_atomic_and_relay_skip_locked(client, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "EVENTS_ENABLED", True)
    pid = mk_product(client, qty=5)
    with session_factory() as s:
        assert len(s.execute(select(OutboxEntity)).scalars().all()) == 2

    locker = session_factory()
    locked = locker.execute(select(OutboxEntity).with_for_update()).scalars().all()
    assert len(locked) == 2
    sent = []
    with session_factory() as s2:
        assert relay_once(s2, lambda rk, m: sent.append(rk)) == 0
    locker.rollback()
    locker.close()
    with session_factory() as s3:
        assert relay_once(s3, lambda rk, m: sent.append(rk)) == 2
    assert sent == ["product.created.wh-001", "inventory.updated.wh-001"]


def test_failed_business_rule_rolls_back_everything(client, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "EVENTS_ENABLED", True)
    pid = mk_product(client, qty=1)
    r = client.post("/transfers/TR-F/reserve", json={"sku": "P-001", "quantity": 5})
    assert r.status_code == 409
    with session_factory() as s:
        assert s.execute(text("SELECT count(*) FROM processed_commands")).scalar() == 0
        assert s.execute(text("SELECT count(*) FROM outbox")).scalar() == 2
        assert s.execute(text("SELECT count(*) FROM inventory_movements")).scalar() == 1


def test_schema_sql_and_seed_sql_apply_cleanly():
    url = recreate_db("wh_schema_test")
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    raw = engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute((ROOT / "db" / "schema.sql").read_text())
        cur.execute((ROOT / "db" / "seed.sql").read_text())
        cur.execute((ROOT / "db" / "seed.sql").read_text())
        cur.execute("SELECT count(*), sum(quantity) FROM inventory")
        assert cur.fetchone() == (5, 500)
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        tables = {r[0] for r in cur.fetchall()}
        assert tables == set(appdb.Base.metadata.tables)
        for name, table in appdb.Base.metadata.tables.items():
            cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name=%s", (name,))
            assert {r[0] for r in cur.fetchall()} == {c.name for c in table.columns}, name
    finally:
        raw.close()
        engine.dispose()


def test_full_flow_by_sku_with_cache(client, fake_redis):
    mk_product(client, qty=5)
    a = client.get("/inventory/by-sku/P-001").json()
    assert fake_redis.get("warehouse:WH-001:inventory:sku:P-001") is not None
    assert client.get("/inventory/by-sku/P-001").json() == a
    assert client.delete(f"/products/{a['product_id']}").status_code == 409
