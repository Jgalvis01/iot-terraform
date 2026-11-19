import json
import pathlib

import boto3
import pytest
from moto import mock_aws

import handler

SAMPLES = pathlib.Path(handler.__file__).parent / "sample_exports"


def event(key, bucket="test-bucket"):
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


@pytest.fixture
def s3():
    with mock_aws():
        handler._s3 = None
        c = boto3.client("s3", region_name="us-east-1")
        c.create_bucket(Bucket="test-bucket")
        yield c
        handler._s3 = None


def put(s3, key, body):
    s3.put_object(Bucket="test-bucket", Key=key, Body=body)


def get(s3, key):
    return s3.get_object(Bucket="test-bucket", Key=key)["Body"].read()


def test_valid_csv(s3):
    put(s3, "exports/inv sample.csv", (SAMPLES / "inventory-sample.csv").read_bytes())
    out = handler.lambda_handler(event("exports/inv+sample.csv"), None)
    assert out["results"][0]["result"] == "processed"
    rows = json.loads(get(s3, "processed/inv sample.json"))
    assert rows[0] == {"sku": "P-001", "warehouse_id": "WH-001", "quantity": 120} and len(rows) == 4
    meta = json.loads(get(s3, "processed/inv sample.meta.json"))
    assert (meta["rows"], meta["valid_rows"], meta["invalid_rows"]) == (4, 4, 0)
    assert meta["source_key"] == "exports/inv sample.csv"


def test_json_with_invalid_rows(s3):
    put(s3, "exports/inv.json", (SAMPLES / "inventory-sample.json").read_bytes())
    handler.lambda_handler(event("exports/inv.json"), None)
    assert len(json.loads(get(s3, "processed/inv.json"))) == 2
    meta = json.loads(get(s3, "processed/inv.meta.json"))
    assert (meta["rows"], meta["valid_rows"], meta["invalid_rows"]) == (5, 2, 3)


def test_corrupt_json_rejected(s3, capsys):
    put(s3, "exports/bad.json", b"{not json")
    out = handler.lambda_handler(event("exports/bad.json"), None)
    assert out["results"][0]["result"] == "rejected"
    assert get(s3, "rejected/bad.json") == b"{not json"
    assert '"file_rejected"' in capsys.readouterr().out
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="test-bucket")["Contents"]]
    assert not any(k.startswith("processed/") for k in keys)


def test_zero_valid_rows_rejected(s3):
    put(s3, "exports/none.csv", b"sku,warehouse_id,quantity\n,WH-001,1\nP-1,WH-1,2\nP-2,WH-001,x\n")
    out = handler.lambda_handler(event("exports/none.csv"), None)
    assert out["results"][0]["result"] == "rejected"
    assert s3.head_object(Bucket="test-bucket", Key="rejected/none.csv")


def test_binary_garbage_rejected(s3):
    put(s3, "exports/x.csv", b"\xff\xfe\x00\x80")
    assert handler.lambda_handler(event("exports/x.csv"), None)["results"][0]["result"] == "rejected"
