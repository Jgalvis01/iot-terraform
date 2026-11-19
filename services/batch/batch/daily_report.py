import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import requests


def log(event: str, level: str = "INFO", **fields) -> None:
    print(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                      "service": "batch", "level": level, "event": event, "correlation_id": None, **fields},
                     default=str), flush=True)


def parse_warehouses(raw: str) -> dict[str, str]:
    out = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        wh, _, url = item.partition("=")
        out[wh.strip()] = url.strip().rstrip("/")
    return out


def http_get_json(url: str, params: dict | None = None, timeout: float = 10):
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def aggregate(per_warehouse: dict[str, list[dict]]) -> dict:
    by_type: dict = defaultdict(lambda: {"count": 0, "quantity": 0})
    by_sku: dict = defaultdict(lambda: {"count": 0, "by_type": defaultdict(int)})
    by_wh: dict = {}
    for wh, movements in per_warehouse.items():
        wh_types: dict = defaultdict(lambda: {"count": 0, "quantity": 0})
        for m in movements:
            t, q, sku = m.get("type"), int(m.get("quantity") or 0), m.get("sku")
            for bucket in (by_type[t], wh_types[t]):
                bucket["count"] += 1
                bucket["quantity"] += q
            by_sku[sku]["count"] += 1
            by_sku[sku]["by_type"][t] += q
        by_wh[wh] = {"movements": len(movements), "by_type": dict(wh_types)}
    return {
        "total_movements": sum(len(v) for v in per_warehouse.values()),
        "totals_by_type": dict(by_type),
        "totals_by_sku": {k: {"count": v["count"], "by_type": dict(v["by_type"])} for k, v in by_sku.items()},
        "totals_by_warehouse": by_wh,
    }


def collect(warehouses: dict[str, str], day: str, get=http_get_json):
    movements: dict[str, list] = {}
    products: dict[str, list] = {}
    failed: dict[str, str] = {}
    for wh, url in warehouses.items():
        try:
            movements[wh] = get(f"{url}/inventory/movements", params={"date": day})
            products[wh] = get(f"{url}/products")
        except Exception as exc:
            failed[wh] = repr(exc)
            log("warehouse_failed", level="ERROR", warehouse_id=wh, error=repr(exc))
    return movements, products, failed


def build_reports(day: str, movements, products, failed, generated_at: str):
    y, m, _ = day.split("-")
    keys = {}
    for wh, mv in movements.items():
        keys[f"warehouses/{wh}/{y}/{m}/inventory-{day}.json"] = {
            "report_date": day, "warehouse_id": wh, "generated_at": generated_at,
            "products": products.get(wh, []), "movements": mv, **{k: v for k, v in aggregate({wh: mv}).items()
                                                                  if k != "totals_by_warehouse"},
        }
    consolidated_key = f"reports/daily/{day}/movements-{day}.json"
    consolidated = {
        "report_date": day, "generated_at": generated_at,
        "warehouses_ok": sorted(movements), "failed_warehouses": failed,
        **aggregate(movements),
    }
    return keys, consolidated_key, consolidated


def s3_client():
    import boto3
    endpoint = os.getenv("S3_ENDPOINT_URL") or None
    return boto3.client("s3", endpoint_url=endpoint, region_name=os.getenv("AWS_REGION", "us-east-1")), endpoint


def ensure_bucket(s3, bucket: str) -> None:
    try:
        s3.head_bucket(Bucket=bucket)
    except Exception:
        s3.create_bucket(Bucket=bucket)
        log("bucket_created", bucket=bucket)


def put_json(s3, bucket: str, key: str, body: dict) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(body, indent=2, default=str).encode(),
                  ContentType="application/json")


DDL = """CREATE TABLE IF NOT EXISTS batch_runs (
    run_id TEXT PRIMARY KEY, run_date DATE NOT NULL UNIQUE, status TEXT NOT NULL, s3_key TEXT,
    started_at TIMESTAMP, finished_at TIMESTAMP, error TEXT)"""


def record_run(engine, day: str, status: str, s3_key: str | None, started_at: datetime,
               finished_at: datetime | None, error: str | None) -> None:
    from sqlalchemy import text
    run_date = date.fromisoformat(day)
    with engine.begin() as conn:
        conn.execute(text(DDL))
        params = {"run_id": f"BATCH-{day}", "d": run_date, "status": status, "k": s3_key, "s": started_at,
                  "f": finished_at, "e": error}
        res = conn.execute(text("UPDATE batch_runs SET status=:status, s3_key=:k, started_at=:s, finished_at=:f, "
                                "error=:e WHERE run_date=:d"), params)
        if res.rowcount == 0:
            conn.execute(text("INSERT INTO batch_runs (run_id, run_date, status, s3_key, started_at, finished_at, "
                              "error) VALUES (:run_id, :d, :status, :k, :s, :f, :e)"), params)


def run(day: str, warehouses: dict[str, str], bucket: str, s3, engine=None, get=http_get_json,
        create_bucket: bool = False) -> dict:
    started = datetime.now(timezone.utc)
    key = f"reports/daily/{day}/movements-{day}.json"
    try:
        if engine is not None:
            record_run(engine, day, "RUNNING", None, started, None, None)
        if create_bucket:
            ensure_bucket(s3, bucket)
        movements, products, failed = collect(warehouses, day, get)
        generated_at = started.isoformat().replace("+00:00", "Z")
        wh_reports, key, consolidated = build_reports(day, movements, products, failed, generated_at)
        for k, body in wh_reports.items():
            put_json(s3, bucket, k, body)
        put_json(s3, bucket, key, consolidated)
        status = "COMPLETED_WITH_FAILURES" if failed else "COMPLETED"
        if warehouses and not movements:
            status = "FAILED"
        if engine is not None:
            record_run(engine, day, status, key, started, datetime.now(timezone.utc),
                       json.dumps(failed) if failed else None)
        log("batch_finished", report_date=day, status=status, s3_key=key, failed_warehouses=sorted(failed))
        return {"status": status, "s3_key": key, "failed_warehouses": failed}
    except Exception as exc:
        log("batch_error", level="ERROR", error=repr(exc))
        if engine is not None:
            try:
                record_run(engine, day, "FAILED", None, started, datetime.now(timezone.utc), repr(exc))
            except Exception:
                pass
        raise


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (default: ayer UTC)")
    args = ap.parse_args(argv)
    day = args.date or (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    date.fromisoformat(day)
    warehouses = parse_warehouses(os.getenv("WAREHOUSES", ""))
    bucket = os.environ["S3_BUCKET"]
    s3, endpoint = s3_client()
    engine = None
    if os.getenv("DATABASE_URL"):
        from sqlalchemy import create_engine
        engine = create_engine(os.environ["DATABASE_URL"])
    result = run(day, warehouses, bucket, s3, engine, create_bucket=bool(endpoint))
    return 1 if result["status"] == "FAILED" else 0


if __name__ == "__main__":
    sys.exit(main())
