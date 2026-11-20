import pytest
from sqlalchemy import create_engine, text

from wcommon.events import build_event

from workers.db import init_db, make_engine, make_session_factory
from workers.models import GlobalInventory, Transfer, utcnow
from workers.projection import Projection
from workers.saga import TransferSaga


@pytest.fixture
def sf(pg_url):
    engine = make_engine(pg_url)
    init_db(engine)
    yield make_session_factory(engine)
    with engine.begin() as c:
        c.execute(text("TRUNCATE global_inventory, warehouse_status, processed_events, transfers"))
    engine.dispose()


def ev(qty, at):
    e = build_event("INVENTORY_UPDATED", "wh-001", {"warehouse_id": "WH-001", "sku": "P-9", "product_id": 1,
                                                    "quantity": qty, "reserved_quantity": 0})
    e["occurred_at"] = at
    return e


def test_projection_on_postgres(sf):
    p = Projection(sf)
    e = ev(5, "2026-09-28T15:00:10Z")
    assert p.handle(e) == "applied"
    assert p.handle(e) == "duplicate"
    assert p.handle(ev(1, "2026-09-28T15:00:00+00:00")) == "out_of_order_discarded"
    with sf() as s:
        assert s.get(GlobalInventory, ("P-9", "WH-001")).quantity == 5


class OkWarehouses:
    def __getattr__(self, name):
        return lambda *a, **k: {"status": "applied", "name": "n", "unit": "u", "category": "c"}


def test_saga_on_postgres_history_jsonb(sf):
    now = utcnow()
    with sf() as s, s.begin():
        s.add(Transfer(transfer_id="TR-pg1", sku="P-9", source_warehouse="WH-001", destination_warehouse="WH-002",
                       quantity=1, status="REQUESTED", attempts=0, history=[{"status": "REQUESTED"}],
                       created_at=now, updated_at=now))
    saga = TransferSaga(sf, OkWarehouses())
    assert saga.process("TR-pg1") == "COMPLETED"
    with sf() as s:
        t = s.get(Transfer, "TR-pg1")
        assert len(t.history) == 7 and t.history[-1]["status"] == "COMPLETED"
