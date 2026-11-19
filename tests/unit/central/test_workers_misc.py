import json

import pytest
import redis

from wcommon.errors import TransientError
from wcommon.events import build_event

from workers.audit import Auditor
from workers.cache import CacheInvalidator
from workers.config import parse_warehouses
from workers.iot_alert import AlertEvaluator
from workers.iot_storage import ReadingStorage
from workers.models import AuditLog, GlobalInventory, ReportJob, SensorAlert, SensorReading, utcnow
from workers.report import ReportGenerator


def test_parse_warehouses():
    assert parse_warehouses("WH-001=http://a:8000, wh-002=http://b:80/") == {
        "WH-001": "http://a:8000", "WH-002": "http://b:80"}
    assert parse_warehouses("") == {}
    with pytest.raises(ValueError):
        parse_warehouses("WH-001")


def test_audit_is_idempotent(session_factory):
    a = Auditor(session_factory)
    e = build_event("INVENTORY_UPDATED", "wh-001", {"sku": "P-1"})
    a.handle(e)
    a.handle(e)
    with session_factory() as s:
        assert s.query(AuditLog).count() == 1


class FakeRedis:
    def __init__(self, fail=False):
        self.deleted = []
        self.fail = fail

    def delete(self, *keys):
        if self.fail:
            raise redis.ConnectionError("down")
        self.deleted.append(keys)
        return len(keys)


def test_cache_invalidates_both_keys():
    r = FakeRedis()
    c = CacheInvalidator("WH-001", r)
    e = build_event("INVENTORY_UPDATED", "wh-001", {"warehouse_id": "WH-001", "sku": "P-001", "product_id": 7})
    assert c.handle(e) == 2
    assert r.deleted == [("warehouse:WH-001:inventory:7", "warehouse:WH-001:inventory:sku:P-001")]
    other = build_event("INVENTORY_UPDATED", "wh-002", {"warehouse_id": "WH-002", "sku": "P", "product_id": 1})
    assert c.handle(other) == 0
    with pytest.raises(TransientError):
        CacheInvalidator("WH-001", FakeRedis(fail=True)).handle(e)


def reading(seq, value=5.0, sensor_type="temperature", event_id=None):
    return build_event("SENSOR_READING", "iot-ingestion", {
        "warehouse_id": "WH-001", "sensor_id": "TEMP-001", "sensor_type": sensor_type, "value": value,
        "unit": "C", "sequence_number": seq, "measured_at": "2026-09-28T15:00:00Z"}, event_id=event_id)


def test_iot_storage_duplicates_and_out_of_order(session_factory):
    st = ReadingStorage(session_factory)
    first = reading(10)
    assert st.handle(first) == "stored"
    assert st.handle(first) == "duplicate"
    assert st.handle(reading(9)) == "out_of_order"
    assert st.handle(reading(10)) == "out_of_order"
    assert st.handle(reading(11)) == "stored"
    with session_factory() as s:
        assert s.query(SensorReading).count() == 2


class FakePub:
    def __init__(self):
        self.msgs = []

    def publish(self, exchange, rk, msg):
        self.msgs.append((exchange, rk, msg))


def test_iot_alert_thresholds_and_idempotency(session_factory):
    pub = FakePub()
    al = AlertEvaluator(session_factory, pub, thresholds={"temperature": 8, "humidity": 70, "door": 60})
    assert al.handle(reading(1, 7.9)) == "ok"
    hot = reading(2, 8.4, event_id="EVT-hot")
    assert al.handle(hot) == "alert"
    assert al.handle(hot) == "duplicate"
    assert al.handle(reading(3, 120, sensor_type="door")) == "alert"
    assert al.handle(reading(4, 999, sensor_type="motion")) == "ok"
    assert len(pub.msgs) == 2
    ex, rk, msg = pub.msgs[0]
    assert (ex, rk, msg["event_type"], msg["event_id"]) == ("warehouse.events", "iot.alert.wh-001",
                                                           "SENSOR_ALERT", "ALR-EVT-hot")
    assert msg["payload"]["threshold"] == 8
    with session_factory() as s:
        assert s.query(SensorAlert).count() == 2


class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = json.loads(Body)


def test_report_generates_snapshot_and_is_idempotent(session_factory):
    now = utcnow()
    with session_factory() as s, s.begin():
        s.add(GlobalInventory(sku="P-001", warehouse_id="WH-001", quantity=5, reserved_quantity=1,
                              last_event_at=now))
        s.add(GlobalInventory(sku="P-001", warehouse_id="WH-002", quantity=3, reserved_quantity=0,
                              last_event_at=now))
        s.add(ReportJob(job_id="JOB-1", report_type="inventory_snapshot", status="PROCESSING"))
    s3, pub = FakeS3(), FakePub()
    gen = ReportGenerator(session_factory, s3, "bucket", pub)
    msg = build_event("GENERATE_REPORT", "central-api", {"job_id": "JOB-1", "report_type": "inventory_snapshot"})
    key = gen.handle(msg)
    assert key.startswith("reports/") and key.endswith("/JOB-1.json")
    body = s3.objects[("bucket", key)]
    assert body["items"][0]["total"] == 8
    with session_factory() as s:
        job = s.get(ReportJob, "JOB-1")
        assert (job.status, job.s3_key) == ("COMPLETED", key)
    assert pub.msgs[0][1] == "report.completed" and pub.msgs[0][2]["payload"] == {"job_id": "JOB-1", "s3_key": key}
    assert gen.handle(msg) == key
    assert len(pub.msgs) == 1
