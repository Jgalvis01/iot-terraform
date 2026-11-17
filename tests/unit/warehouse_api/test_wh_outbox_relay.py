import pytest
from sqlalchemy import select

from app.config import settings
from app.outbox import OutboxEntity
from app.outbox_relay import relay_once
from wh_unit_helpers import move


@pytest.fixture()
def with_events(client, monkeypatch):
    monkeypatch.setattr(settings, "EVENTS_ENABLED", True)
    pid = client.post("/products", json={"sku": "P-1", "name": "n", "unit": "u"}).json()["id"]
    move(client, pid, "RECEIPT", 3)
    return pid


def test_relay_publishes_and_marks(session_factory, with_events):
    sent = []
    with session_factory() as s:
        n = relay_once(s, lambda rk, msg: sent.append((rk, msg["event_type"])))
    assert n == 2 and sent == [("product.created.wh-001", "PRODUCT_CREATED"),
                               ("inventory.updated.wh-001", "INVENTORY_UPDATED")]
    with session_factory() as s:
        assert all(r.published_at is not None for r in s.execute(select(OutboxEntity)).scalars())
        assert relay_once(s, lambda *_: sent.append("again")) == 0


def test_relay_keeps_rows_when_rabbit_down(session_factory, with_events):
    def boom(rk, msg):
        raise ConnectionError("rabbit down")

    with session_factory() as s:
        with pytest.raises(ConnectionError):
            relay_once(s, boom)
    with session_factory() as s:
        rows = s.execute(select(OutboxEntity).order_by(OutboxEntity.id)).scalars().all()
        assert all(r.published_at is None for r in rows)
        assert rows[0].attempts == 1
        sent = []
        assert relay_once(s, lambda rk, m: sent.append(rk)) == 2
