from datetime import datetime, timezone

import pytest

from wcommon.errors import PermanentError, TransientError
from wcommon.events import build_event

from workers.models import Transfer
from workers.saga import TransferSaga
from workers.warehouse_client import WarehouseRejected

MAX = 3


class FakeWarehouses:

    def __init__(self, **fail):
        self.calls: list[tuple] = []
        self.fail = fail

    def _do(self, op, wh, *args):
        self.calls.append((op, wh) + args)
        f = self.fail.get(op)
        if isinstance(f, list):
            f = f.pop(0) if f else None
        if f is not None:
            raise f
        return {"status": "applied"}

    def reserve(self, wh, tid, sku, qty):
        return self._do("reserve", wh, tid, sku, qty)

    def release(self, wh, tid, sku, qty):
        return self._do("release", wh, tid, sku, qty)

    def commit_out(self, wh, tid, sku, qty):
        return self._do("commit_out", wh, tid, sku, qty)

    def receive(self, wh, tid, sku, qty, name, unit, category):
        return self._do("receive", wh, tid, sku, qty, name, unit, category)

    def get_product(self, wh, sku):
        self._do("get_product", wh, sku)
        return {"sku": sku, "name": "Leche", "unit": "lt", "category": "lacteos"}

    def ops(self):
        return [c[0] for c in self.calls]


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, exchange, routing_key, message):
        self.messages.append((exchange, routing_key, message))

    def statuses(self):
        return [m[2]["payload"]["status"] for m in self.messages]


def make_transfer(sf, status="REQUESTED", fail_step=None, tid="TR-test0001"):
    now = datetime.now(timezone.utc)
    with sf() as s, s.begin():
        s.add(Transfer(transfer_id=tid, sku="P-001", source_warehouse="WH-001",
                       destination_warehouse="WH-002", quantity=5, status=status, attempts=0,
                       correlation_id="COR-test", fail_step=fail_step,
                       history=[{"status": status, "at": now.isoformat()}], created_at=now, updated_at=now))
    return tid


def cmd(tid, retries=0):
    msg = build_event("PROCESS_TRANSFER", "test", {"transfer_id": tid})
    msg["_retries"] = retries
    return msg


def load(sf, tid) -> Transfer:
    with sf() as s:
        return s.get(Transfer, tid)


def make_saga(sf, wh, fail_injection="none"):
    pub = FakePublisher()
    return TransferSaga(sf, wh, publisher=pub, fail_injection=fail_injection, max_retries=MAX), pub


HAPPY = ["REQUESTED", "RESERVING_SOURCE", "SOURCE_RESERVED", "IN_TRANSIT", "DESTINATION_PENDING",
         "DESTINATION_RECEIVED", "COMPLETED"]


def test_happy_path_completes(session_factory):
    tid = make_transfer(session_factory)
    wh = FakeWarehouses()
    saga, pub = make_saga(session_factory, wh)
    saga.handle(cmd(tid))
    t = load(session_factory, tid)
    assert t.status == "COMPLETED"
    assert wh.ops() == ["reserve", "get_product", "receive", "commit_out"]
    assert wh.calls[2] == ("receive", "WH-002", tid, "P-001", 5, "Leche", "lt", "lacteos")
    assert [h["status"] for h in t.history] == HAPPY
    assert pub.statuses() == HAPPY[1:]
    assert pub.messages[-1][1] == "transfer.status.completed"
    assert pub.messages[-1][2]["correlation_id"] == "COR-test"


def test_insufficient_stock_fails_without_compensation(session_factory):
    tid = make_transfer(session_factory)
    wh = FakeWarehouses(reserve=WarehouseRejected("WH-001", 409, "INSUFFICIENT_STOCK", "no stock"))
    saga, pub = make_saga(session_factory, wh)
    saga.handle(cmd(tid))
    t = load(session_factory, tid)
    assert t.status == "FAILED"
    assert "INSUFFICIENT_STOCK" in t.reason
    assert wh.ops() == ["reserve"]
    assert pub.statuses() == ["RESERVING_SOURCE", "FAILED"]


def test_destination_down_retries_then_compensates_and_cancels(session_factory):
    tid = make_transfer(session_factory)
    wh = FakeWarehouses(receive=TransientError("connection refused"))
    saga, _ = make_saga(session_factory, wh)
    for retries in range(MAX):
        with pytest.raises(TransientError):
            saga.handle(cmd(tid, retries))
        assert load(session_factory, tid).status == "DESTINATION_PENDING"
    saga.handle(cmd(tid, MAX))
    t = load(session_factory, tid)
    assert t.status == "CANCELLED"
    assert "destination unavailable" in t.reason
    assert wh.ops().count("reserve") == 1
    assert wh.ops().count("receive") == MAX + 1
    assert wh.ops().count("release") == 1
    assert "commit_out" not in wh.ops()
    assert t.attempts == MAX
    assert [h["status"] for h in t.history if "event" not in h][-2:] == ["COMPENSATING", "CANCELLED"]


def test_destination_down_by_env_fail_injection(session_factory):
    tid = make_transfer(session_factory)
    wh = FakeWarehouses()
    saga, _ = make_saga(session_factory, wh, fail_injection="destination_down")
    with pytest.raises(TransientError):
        saga.handle(cmd(tid, 0))
    saga.handle(cmd(tid, MAX))
    assert load(session_factory, tid).status == "CANCELLED"
    assert "receive" not in wh.ops()
    assert wh.ops() == ["reserve", "release"]


def test_source_down_marks_failed_before_last_error(session_factory):
    tid = make_transfer(session_factory)
    wh = FakeWarehouses(reserve=TransientError("timeout"))
    saga, pub = make_saga(session_factory, wh)
    for retries in range(MAX):
        with pytest.raises(TransientError):
            saga.handle(cmd(tid, retries))
        assert load(session_factory, tid).status == "RESERVING_SOURCE"
    with pytest.raises(TransientError):
        saga.handle(cmd(tid, MAX))
    t = load(session_factory, tid)
    assert t.status == "FAILED"
    assert t.reason.startswith("source unavailable")
    assert "release" not in wh.ops()
    assert pub.statuses()[-1] == "FAILED"


def test_before_reserve_header_injection(session_factory):
    tid = make_transfer(session_factory, fail_step="before_reserve")
    wh = FakeWarehouses()
    saga, _ = make_saga(session_factory, wh)
    with pytest.raises(TransientError):
        saga.handle(cmd(tid, MAX))
    assert load(session_factory, tid).status == "FAILED"
    assert wh.ops() == []


def test_failure_after_reserve_compensates(session_factory):
    tid = make_transfer(session_factory, fail_step="after_reserve")
    wh = FakeWarehouses()
    saga, pub = make_saga(session_factory, wh)
    saga.handle(cmd(tid))
    t = load(session_factory, tid)
    assert t.status == "CANCELLED"
    assert t.reason == "injected failure: after_reserve"
    assert wh.ops() == ["reserve", "release"]
    assert pub.statuses() == ["RESERVING_SOURCE", "SOURCE_RESERVED", "COMPENSATING", "CANCELLED"]


def test_header_none_overrides_env_injection(session_factory):
    tid = make_transfer(session_factory, fail_step="none")
    wh = FakeWarehouses()
    saga, _ = make_saga(session_factory, wh, fail_injection="after_reserve")
    saga.handle(cmd(tid))
    assert load(session_factory, tid).status == "COMPLETED"


def test_destination_rejects_receive_compensates(session_factory):
    tid = make_transfer(session_factory)
    wh = FakeWarehouses(receive=WarehouseRejected("WH-002", 404, None, "not found"))
    saga, _ = make_saga(session_factory, wh)
    saga.handle(cmd(tid))
    assert load(session_factory, tid).status == "CANCELLED"
    assert wh.ops() == ["reserve", "get_product", "receive", "release"]


def test_release_transient_failure_stays_compensating_then_recovers(session_factory):
    tid = make_transfer(session_factory, fail_step="after_reserve")
    wh = FakeWarehouses(release=[TransientError("down"), None])
    saga, _ = make_saga(session_factory, wh)
    with pytest.raises(TransientError):
        saga.handle(cmd(tid, 0))
    assert load(session_factory, tid).status == "COMPENSATING"
    saga.handle(cmd(tid, 1))
    assert load(session_factory, tid).status == "CANCELLED"
    assert wh.ops() == ["reserve", "release", "release"]


def test_release_exhausted_goes_to_dlq_and_stays_compensating(session_factory):
    tid = make_transfer(session_factory, status="COMPENSATING")
    wh = FakeWarehouses(release=TransientError("down"))
    saga, _ = make_saga(session_factory, wh)
    with pytest.raises(TransientError):
        saga.handle(cmd(tid, MAX))
    assert load(session_factory, tid).status == "COMPENSATING"


def test_commit_out_transient_then_redelivery_does_not_repeat_receive(session_factory):
    tid = make_transfer(session_factory)
    wh = FakeWarehouses(commit_out=[TransientError("5xx"), None])
    saga, _ = make_saga(session_factory, wh)
    with pytest.raises(TransientError):
        saga.handle(cmd(tid, 0))
    assert load(session_factory, tid).status == "DESTINATION_RECEIVED"
    saga.handle(cmd(tid, 1))
    assert load(session_factory, tid).status == "COMPLETED"
    assert wh.ops() == ["reserve", "get_product", "receive", "commit_out", "commit_out"]


@pytest.mark.parametrize("status,expected_ops", [
    ("RESERVING_SOURCE", ["reserve", "get_product", "receive", "commit_out"]),
    ("SOURCE_RESERVED", ["get_product", "receive", "commit_out"]),
    ("IN_TRANSIT", ["get_product", "receive", "commit_out"]),
    ("DESTINATION_PENDING", ["get_product", "receive", "commit_out"]),
    ("DESTINATION_RECEIVED", ["commit_out"]),
    ("COMPENSATING", ["release"]),
    ("COMPLETED", []),
    ("CANCELLED", []),
    ("FAILED", []),
])
def test_redelivery_resumes_from_current_state(session_factory, status, expected_ops):
    tid = make_transfer(session_factory, status=status)
    wh = FakeWarehouses()
    saga, pub = make_saga(session_factory, wh)
    saga.handle(cmd(tid))
    assert wh.ops() == expected_ops
    if status in ("COMPLETED", "CANCELLED", "FAILED"):
        assert pub.messages == []
        assert load(session_factory, tid).status == status


def test_duplicate_message_after_completion_has_no_effects(session_factory):
    tid = make_transfer(session_factory)
    wh = FakeWarehouses()
    saga, pub = make_saga(session_factory, wh)
    msg = cmd(tid)
    saga.handle(msg)
    calls, events = len(wh.calls), len(pub.messages)
    saga.handle(msg)
    saga.handle(cmd(tid, 2))
    assert len(wh.calls) == calls and len(pub.messages) == events
    assert load(session_factory, tid).status == "COMPLETED"


def test_unknown_transfer_and_bad_command_are_permanent(session_factory):
    saga, _ = make_saga(session_factory, FakeWarehouses())
    with pytest.raises(PermanentError):
        saga.handle(cmd("TR-missing"))
    with pytest.raises(PermanentError):
        saga.handle(build_event("SOMETHING_ELSE", "t", {"transfer_id": "x"}))
    with pytest.raises(PermanentError):
        saga.handle({"event_type": "PROCESS_TRANSFER"})


def test_status_event_publish_failure_does_not_break_saga(session_factory):
    class BrokenPublisher:
        def publish(self, *a):
            raise TransientError("rabbit down")

    tid = make_transfer(session_factory)
    saga = TransferSaga(session_factory, FakeWarehouses(), publisher=BrokenPublisher(), max_retries=MAX)
    saga.handle(cmd(tid))
    assert load(session_factory, tid).status == "COMPLETED"
