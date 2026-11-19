import json
from datetime import datetime, timezone

import boto3
import pytest
from moto import mock_aws
from sqlalchemy import create_engine, text

from batch import daily_report as dr

MOV = {
    "http://wh1": [
        {"id": 1, "sku": "P-1", "warehouse_id": "WH-001", "type": "RECEIPT", "quantity": 10},
        {"id": 2, "sku": "P-1", "warehouse_id": "WH-001", "type": "SHIPMENT", "quantity": 3},
    ],
    "http://wh3": [{"id": 1, "sku": "P-2", "warehouse_id": "WH-003", "type": "RECEIPT", "quantity": 7}],
}


def fake_get(url, params=None, timeout=0):
    base = url.rsplit("/", 2)[0] if url.endswith("/inventory/movements") else url.rsplit("/", 1)[0]
    if base == "http://wh2":
        raise ConnectionError("down")
    if url.endswith("/inventory/movements"):
        assert params == {"date": "2026-09-28"}
        return MOV[base]
    return [{"id": 1, "sku": "P-1"}]


def test_parse_warehouses():
    assert dr.parse_warehouses("WH-001=http://a:8000/, WH-002=http://b") == {"WH-001": "http://a:8000",
                                                                         "WH-002": "http://b"}


def test_aggregate():
    agg = dr.aggregate({"WH-001": MOV["http://wh1"], "WH-003": MOV["http://wh3"]})
    assert agg["total_movements"] == 3
    assert agg["totals_by_type"]["RECEIPT"] == {"count": 2, "quantity": 17}
    assert agg["totals_by_sku"]["P-1"]["by_type"] == {"RECEIPT": 10, "SHIPMENT": 3}
    assert agg["totals_by_warehouse"]["WH-003"]["movements"] == 1


@mock_aws
def test_run_with_failed_warehouse_and_rerun():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket="test-bucket")
    engine = create_engine("sqlite://")
    whs = {"WH-001": "http://wh1", "WH-002": "http://wh2", "WH-003": "http://wh3"}
    for _ in range(2):
        res = dr.run("2026-09-28", whs, "test-bucket", s3, engine, get=fake_get)
    assert res["status"] == "COMPLETED_WITH_FAILURES"
    keys = {o["Key"] for o in s3.list_objects_v2(Bucket="test-bucket")["Contents"]}
    assert keys == {"warehouses/WH-001/2026/09/inventory-2026-09-28.json",
                    "warehouses/WH-003/2026/09/inventory-2026-09-28.json",
                    "reports/daily/2026-09-28/movements-2026-09-28.json"}
    rep = json.loads(s3.get_object(Bucket="test-bucket", Key="reports/daily/2026-09-28/movements-2026-09-28.json")["Body"].read())
    assert list(rep["failed_warehouses"]) == ["WH-002"]
    assert rep["total_movements"] == 3
    with engine.connect() as c:
        rows = c.execute(text("SELECT run_id, status, s3_key FROM batch_runs")).all()
    assert len(rows) == 1 and rows[0][1] == "COMPLETED_WITH_FAILURES"


@mock_aws
def test_all_failed_marks_failed_and_ensure_bucket():
    s3 = boto3.client("s3", region_name="us-east-1")
    engine = create_engine("sqlite://")
    res = dr.run("2026-09-28", {"WH-002": "http://wh2"}, "newbucket", s3, engine, get=fake_get, create_bucket=True)
    assert res["status"] == "FAILED"
    assert "newbucket" in [b["Name"] for b in s3.list_buckets()["Buckets"]]
