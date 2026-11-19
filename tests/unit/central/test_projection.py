import pytest

from wcommon.errors import PermanentError
from wcommon.events import build_event

from workers.db import as_utc
from workers.models import GlobalInventory, WarehouseStatus
from workers.projection import Projection


def inv_event(qty, at, reserved=0, wh="WH-001", sku="P-001", event_id=None):
    e = build_event("INVENTORY_UPDATED", wh.lower(), {
        "warehouse_id": wh, "sku": sku, "product_id": 1, "quantity": qty, "reserved_quantity": reserved,
        "movement_type": "RECEIPT", "movement_quantity": 1, "reference": "t"}, event_id=event_id)
    e["occurred_at"] = at
    return e


def row(sf, sku="P-001", wh="WH-001"):
    with sf() as s:
        return s.get(GlobalInventory, (sku, wh))


def test_upsert_and_warehouse_status(session_factory):
    p = Projection(session_factory)
    assert p.handle(inv_event(10, "2026-09-28T15:00:00Z", reserved=2)) == "applied"
    r = row(session_factory)
    assert (r.quantity, r.reserved_quantity) == (10, 2)
    with session_factory() as s:
        st = s.get(WarehouseStatus, "WH-001")
        assert as_utc(st.last_event_at).isoformat() == "2026-09-28T15:00:00+00:00"


def test_duplicate_event_is_ignored(session_factory):
    p = Projection(session_factory)
    e = inv_event(10, "2026-09-28T15:00:00Z")
    assert p.handle(e) == "applied"
    dup = dict(e, payload=dict(e["payload"], quantity=999))
    assert p.handle(dup) == "duplicate"
    assert row(session_factory).quantity == 10


def test_out_of_order_event_is_discarded(session_factory):
    p = Projection(session_factory)
    assert p.handle(inv_event(7, "2026-09-28T15:00:10Z")) == "applied"
    assert p.handle(inv_event(3, "2026-09-28T15:00:05Z")) == "out_of_order_discarded"
    r = row(session_factory)
    assert r.quantity == 7
    assert p.handle(inv_event(8, "2026-09-28T15:00:20Z")) == "applied"
    assert row(session_factory).quantity == 8


def test_equal_timestamp_is_applied(session_factory):
    p = Projection(session_factory)
    p.handle(inv_event(1, "2026-09-28T15:00:00Z"))
    assert p.handle(inv_event(2, "2026-09-28T15:00:00Z")) == "applied"
    assert row(session_factory).quantity == 2


def test_product_created_does_not_overwrite_inventory(session_factory):
    p = Projection(session_factory)
    p.handle(inv_event(5, "2026-09-28T15:00:10Z"))
    pc = build_event("PRODUCT_CREATED", "wh-001", {"warehouse_id": "WH-001", "sku": "P-001", "name": "x",
                                                   "unit": "u", "category": "c"})
    pc["occurred_at"] = "2026-09-28T15:00:00Z"
    assert p.handle(pc) == "noop"
    assert row(session_factory).quantity == 5
    pc2 = build_event("PRODUCT_CREATED", "wh-002", {"warehouse_id": "WH-002", "sku": "P-002", "name": "x",
                                                    "unit": "u", "category": "c"})
    assert p.handle(pc2) == "applied"
    assert row(session_factory, "P-002", "WH-002").quantity == 0


def test_other_events_ignored_and_invalid_permanent(session_factory):
    p = Projection(session_factory)
    assert p.handle(build_event("SENSOR_READING", "x", {})) == "ignored"
    with pytest.raises(PermanentError):
        p.handle({"event_type": "INVENTORY_UPDATED"})
    with pytest.raises(PermanentError):
        p.handle(build_event("INVENTORY_UPDATED", "x", {"warehouse_id": "WH-001"}))
