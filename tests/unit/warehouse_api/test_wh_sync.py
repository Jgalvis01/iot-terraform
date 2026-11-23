"""Cola local (outbox) y resincronización de la bodega con la central."""
import pytest
from sqlalchemy import select

from app.config import settings
from app.outbox import OutboxEntity
from app.outbox_relay import heartbeat_event, relay_once, resync_after_reconnect
from app.sync import SNAPSHOT_CHUNK, enqueue_snapshot, outbox_stats
from wh_unit_helpers import move


@pytest.fixture()
def events_on(monkeypatch):
    monkeypatch.setattr(settings, "EVENTS_ENABLED", True)


def _product(client, sku, qty):
    pid = client.post("/products", json={"sku": sku, "name": sku, "unit": "u"}).json()["id"]
    if qty:
        assert move(client, pid, "RECEIPT", qty).status_code == 201
    return pid


def test_events_accumulate_while_broker_down_then_drain(client, session_factory, events_on):
    _product(client, "P-1", 5)
    _product(client, "P-2", 2)

    def down(rk, msg):
        raise ConnectionError("broker caído")

    with session_factory() as s:
        with pytest.raises(ConnectionError):
            relay_once(s, down)
    st = client.get("/sync/status").json()
    assert st["warehouse_id"] == "WH-001" and st["outbox_pending"] == 4 and st["oldest_pending_at"]
    sent = []
    with session_factory() as s:
        assert relay_once(s, lambda rk, m: sent.append(m["event_type"])) == 4
    assert sent == ["PRODUCT_CREATED", "INVENTORY_UPDATED", "PRODUCT_CREATED", "INVENTORY_UPDATED"]
    st = client.get("/sync/status").json()
    assert st["outbox_pending"] == 0 and st["oldest_pending_at"] is None and st["last_published_at"]


def test_resync_after_reconnect_goes_after_pending_and_is_deduplicated(client, session_factory, events_on):
    _product(client, "P-1", 5)
    with session_factory() as s:
        result = resync_after_reconnect(s, "reconnect")
        assert result["items"] == 1 and result["chunks"] == 1
        # otra réplica del relay reconecta a la vez: no duplica el snapshot pendiente
        assert resync_after_reconnect(s, "reconnect") is None
    sent = []
    with session_factory() as s:
        relay_once(s, lambda rk, m: sent.append((rk, m)))
    keys = [rk for rk, _ in sent]
    assert keys == ["product.created.wh-001", "inventory.updated.wh-001", "inventory.snapshot.wh-001"]
    snap = sent[-1][1]["payload"]
    assert snap["warehouse_id"] == "WH-001" and snap["reason"] == "reconnect" and snap["total_items"] == 1
    assert snap["items"][0]["sku"] == "P-1" and snap["items"][0]["quantity"] == 5
    assert snap["items"][0]["updated_at"].endswith("Z")


def test_snapshot_endpoint_and_chunking(client, session_factory, events_on, monkeypatch):
    import app.sync as sync_mod
    monkeypatch.setattr(sync_mod, "SNAPSHOT_CHUNK", 2)
    for i in range(5):
        _product(client, f"S-{i}", i)
    r = client.post("/sync/snapshot?reason=central")
    assert r.status_code == 202
    body = r.json()
    assert body["items"] == 5 and body["chunks"] == 3 and body["reason"] == "central"
    with session_factory() as s:
        snaps = s.execute(select(OutboxEntity).where(OutboxEntity.routing_key == "inventory.snapshot.wh-001")
                          .order_by(OutboxEntity.id)).scalars().all()
        assert [len(x.payload["payload"]["items"]) for x in snaps] == [2, 2, 1]
        assert {x.payload["payload"]["snapshot_id"] for x in snaps} == {body["snapshot_id"]}
    assert SNAPSHOT_CHUNK == 500


def test_empty_warehouse_snapshot_still_reports(session_factory, events_on):
    with session_factory() as s:
        result = enqueue_snapshot(s, "startup")
        s.commit()
        assert result["items"] == 0 and result["chunks"] == 1
        assert outbox_stats(s)["outbox_pending"] == 1


def test_snapshot_disabled_without_events(client, monkeypatch):
    monkeypatch.setattr(settings, "EVENTS_ENABLED", False)
    r = client.post("/sync/snapshot")
    assert r.status_code == 409 and r.json()["code"] == "EVENTS_DISABLED"


def test_heartbeat_carries_backlog(client, session_factory, events_on):
    _product(client, "P-1", 1)
    with session_factory() as s:
        hb = heartbeat_event(s)
    assert hb["event_type"] == "WAREHOUSE_HEARTBEAT"
    assert hb["payload"]["warehouse_id"] == "WH-001" and hb["payload"]["outbox_pending"] == 2


def test_snapshot_version_matches_last_event(client, session_factory, events_on):
    """La fila y su evento comparten instante: si no, la central descartaría el snapshot como 'viejo'."""
    _product(client, "P-V", 4)
    with session_factory() as s:
        enqueue_snapshot(s, "test")
        s.commit()
    sent = []
    with session_factory() as s:
        relay_once(s, lambda rk, m: sent.append(m))
    last_update = [m for m in sent if m["event_type"] == "INVENTORY_UPDATED"][-1]
    snap_item = [m for m in sent if m["event_type"] == "INVENTORY_SNAPSHOT"][0]["payload"]["items"][0]
    assert snap_item["updated_at"] == last_update["occurred_at"]
