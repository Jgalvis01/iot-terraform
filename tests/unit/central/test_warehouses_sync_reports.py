"""Sincronización de bodegas, reporte general y endpoints de la central que manejan las bodegas."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from wcommon.events import build_event
from wcommon.reports import warehouses_summary

from workers.db import as_utc
from workers.models import GlobalInventory, ReportJob, Transfer, WarehouseStatus, WarehouseSync
from workers.projection import Projection
from workers.report import ReportGenerator

from test_central_api import client, session  # noqa: F401  (fixture reutilizada)

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- reporte (función pura)
def test_summary_status_sync_lowstock_and_transfers():
    report = warehouses_summary(
        configured=["WH-001", "WH-002", "WH-003"],
        inventory=[
            {"sku": "A", "warehouse_id": "WH-001", "quantity": 10, "reserved_quantity": 2},
            {"sku": "B", "warehouse_id": "WH-001", "quantity": 3, "reserved_quantity": 0},
            {"sku": "A", "warehouse_id": "WH-002", "quantity": 1, "reserved_quantity": 1},
        ],
        last_event_at={"WH-001": NOW - timedelta(seconds=5), "WH-002": NOW - timedelta(minutes=5)},
        sync={"WH-001": {"outbox_pending": 0}, "WH-002": {"outbox_pending": 7, "oldest_pending_at": NOW}},
        transfers=[
            {"source_warehouse": "WH-001", "destination_warehouse": "WH-002", "status": "COMPLETED", "quantity": 4},
            {"source_warehouse": "WH-002", "destination_warehouse": "WH-001", "status": "FAILED", "quantity": 1},
        ],
        now=NOW, stale_after_seconds=60, low_stock_threshold=5, pending_central_commands=2)
    by = {w["warehouse_id"]: w for w in report["warehouses"]}
    assert [by[w]["status"] for w in ("WH-001", "WH-002", "WH-003")] == ["ONLINE", "OFFLINE", "NEVER_SEEN"]
    assert by["WH-001"]["sync"]["in_sync"] is True and by["WH-002"]["sync"]["in_sync"] is False
    assert by["WH-001"]["inventory"] == {"sku_count": 2, "total_units": 13, "reserved_units": 2,
                                         "available_units": 11, "low_stock_count": 1,
                                         "low_stock_skus": [{"sku": "B", "available": 3, "quantity": 3}]}
    assert by["WH-001"]["transfers"]["outgoing"] == {"COMPLETED": 1}
    assert by["WH-001"]["transfers"]["units_out_completed"] == 4
    assert by["WH-002"]["transfers"]["units_in_completed"] == 4
    t = report["totals"]
    assert (t["online"], t["offline"], t["never_seen"], t["in_sync"]) == (1, 1, 1, 1)
    assert t["outbox_pending"] == 7 and t["pending_central_commands"] == 2
    assert t["total_units"] == 14 and t["distinct_skus"] == 2
    assert t["transfers_by_status"] == {"COMPLETED": 1, "FAILED": 1}


# ---------------------------------------------------------------- projection: heartbeat + snapshot
def heartbeat(wh, at, pending, oldest=None):
    e = build_event("WAREHOUSE_HEARTBEAT", wh.lower(), {"warehouse_id": wh, "outbox_pending": pending,
                                                       "oldest_pending_at": oldest, "last_published_at": None})
    e["occurred_at"] = at
    return e


def snapshot(wh, at, items, snap="SNAP-1", chunk=1, chunks=1):
    e = build_event("INVENTORY_SNAPSHOT", wh.lower(), {
        "warehouse_id": wh, "snapshot_id": snap, "reason": "reconnect", "chunk": chunk, "chunks": chunks,
        "total_items": len(items), "items": items})
    e["occurred_at"] = at
    return e


def test_heartbeat_records_outbox_backlog(session_factory):
    p = Projection(session_factory)
    assert p.handle(heartbeat("WH-002", "2026-11-22T12:00:00Z", 42, "2026-11-22T11:55:00Z")) == "heartbeat"
    # un heartbeat viejo que llega tarde no pisa el estado más reciente
    assert p.handle(heartbeat("WH-002", "2026-11-22T11:00:00Z", 999)) == "heartbeat"
    with session_factory() as s:
        sync = s.get(WarehouseSync, "WH-002")
        assert sync.outbox_pending == 42
        assert as_utc(sync.oldest_pending_at).isoformat() == "2026-11-22T11:55:00+00:00"
        assert s.get(WarehouseStatus, "WH-002") is not None


def test_snapshot_resyncs_read_model_without_overwriting_newer(session_factory):
    with session_factory() as s, s.begin():
        # divergencia: la central quedó con 1 (se perdió un evento)
        s.add(GlobalInventory(sku="A", warehouse_id="WH-001", quantity=1, reserved_quantity=0,
                              last_event_at=datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)))
        # este ya tiene un evento más nuevo que el snapshot
        s.add(GlobalInventory(sku="B", warehouse_id="WH-001", quantity=50, reserved_quantity=0,
                              last_event_at=datetime(2026, 11, 22, 13, 0, tzinfo=timezone.utc)))
    p = Projection(session_factory)
    msg = snapshot("WH-001", "2026-11-22T12:00:00Z", [
        {"sku": "A", "quantity": 9, "reserved_quantity": 2, "updated_at": "2026-11-22T11:00:00Z"},
        {"sku": "B", "quantity": 7, "reserved_quantity": 0, "updated_at": "2026-11-22T11:30:00Z"},
        {"sku": "C", "quantity": 3, "reserved_quantity": 0, "updated_at": "2026-11-22T11:45:00Z"},
    ])
    assert p.handle(msg) == "snapshot"
    assert p.handle(msg) == "duplicate"
    with session_factory() as s:
        a, b, c = (s.get(GlobalInventory, (k, "WH-001")) for k in "ABC")
        assert (a.quantity, a.reserved_quantity) == (9, 2)
        assert b.quantity == 50
        assert c.quantity == 3
        sync = s.get(WarehouseSync, "WH-001")
        assert sync.last_snapshot_id == "SNAP-1" and sync.snapshot_items == 3


def test_invalid_snapshot_is_permanent(session_factory):
    from wcommon.errors import PermanentError
    with pytest.raises(PermanentError):
        Projection(session_factory).handle(snapshot("WH-001", "2026-11-22T12:00:00Z", [{"quantity": 1}]))


# ---------------------------------------------------------------- report worker -> S3
class FakeS3:
    def __init__(self):
        self.objects = {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[(Bucket, Key)] = json.loads(Body)


def test_report_worker_warehouses_summary(session_factory):
    now = datetime.now(timezone.utc)
    with session_factory() as s, s.begin():
        s.add(GlobalInventory(sku="A", warehouse_id="WH-001", quantity=4, reserved_quantity=0, last_event_at=now))
        s.add(WarehouseStatus(warehouse_id="WH-001", last_event_at=now))
        s.add(Transfer(transfer_id="TR-1", sku="A", source_warehouse="WH-001", destination_warehouse="WH-002",
                       quantity=1, status="COMPLETED", attempts=1, history=[]))
        s.add(ReportJob(job_id="JOB-W", report_type="warehouses_summary", status="PROCESSING"))
    s3 = FakeS3()
    key = ReportGenerator(session_factory, s3, "bucket").handle(
        build_event("GENERATE_REPORT", "central-api", {"job_id": "JOB-W", "report_type": "warehouses_summary"}))
    assert key.startswith("reports/warehouses/") and key.endswith("/JOB-W.json")
    body = s3.objects[("bucket", key)]
    assert body["report_type"] == "warehouses_summary" and body["job_id"] == "JOB-W"
    by = {w["warehouse_id"]: w for w in body["warehouses"]}
    assert by["WH-001"]["status"] == "ONLINE" and by["WH-001"]["inventory"]["low_stock_count"] == 1
    assert set(by) == {"WH-001", "WH-002", "WH-003"}


# ---------------------------------------------------------------- central-api endpoints
def test_reports_warehouses_endpoint_and_list(client):  # noqa: F811
    from app.models import WarehouseStatus as ApiStatus, WarehouseSync as ApiSync
    now = datetime.now(timezone.utc)
    with session() as s, s.begin():
        s.add(ApiStatus(warehouse_id="WH-001", last_event_at=now))
        s.add(ApiSync(warehouse_id="WH-001", outbox_pending=3, snapshot_items=0, last_heartbeat_at=now))
    r = client.get("/reports/warehouses?low_stock_threshold=0")
    assert r.status_code == 200
    d = r.json()
    assert d["totals"]["warehouses"] == 3 and d["totals"]["online"] == 1 and d["totals"]["outbox_pending"] == 3
    assert d["parameters"]["low_stock_threshold"] == 0
    items = {w["warehouse_id"]: w for w in client.get("/warehouses").json()["items"]}
    assert items["WH-001"]["status"] == "ONLINE" and items["WH-001"]["sync"]["outbox_pending"] == 3
    assert items["WH-002"]["status"] == "NEVER_SEEN"
    assert client.post("/reports", json={"report_type": "warehouses_summary"}).status_code == 202


def _mock_httpx(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_connectivity_from_central(client, monkeypatch):  # noqa: F811
    def handler(request: httpx.Request):
        if request.url.host == "wh2":
            raise httpx.ConnectTimeout("sin ruta")
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "checks": {"db": "ok", "redis": "ok"}})
        return httpx.Response(200, json={"outbox_pending": 0})

    _mock_httpx(monkeypatch, handler)
    d = client.get("/warehouses/connectivity").json()
    assert d["total"] == 3 and d["reachable"] == 2 and d["all_reachable"] is False
    by = {w["warehouse_id"]: w for w in d["warehouses"]}
    assert by["WH-001"]["healthy"] is True and by["WH-001"]["outbox"] == {"outbox_pending": 0}
    assert by["WH-002"]["reachable"] is False and by["WH-002"]["error"] == "ConnectTimeout"


def test_resync_proxies_to_warehouse(client, monkeypatch):  # noqa: F811
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.method, request.url.host, request.url.path, request.url.params.get("reason")))
        if request.url.host == "wh3":
            raise httpx.ConnectError("down")
        return httpx.Response(202, json={"snapshot_id": "SNAP-x", "items": 2})

    _mock_httpx(monkeypatch, handler)
    r = client.post("/warehouses/wh-001/resync")
    assert r.status_code == 202 and r.json()["snapshot_id"] == "SNAP-x"
    assert seen[0] == ("POST", "wh1", "/sync/snapshot", "central")
    assert client.post("/warehouses/WH-003/resync").status_code == 502
    assert client.post("/warehouses/WH-999/resync").status_code == 404
