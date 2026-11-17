import json
import os

from sqlalchemy import select

from wcommon.errors import PermanentError, TransientError
from wcommon.events import build_event, validate_event
from wcommon.logging import log
from wcommon.rabbit import EVENTS_EXCHANGE, MAX_RETRIES

from workers.db import as_utc
from workers.models import GlobalInventory, ReportJob, WarehouseStatus, utcnow

CONSUMER = "report"
SUPPORTED = {"inventory_snapshot"}


def make_s3_client():
    import boto3
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
                        region_name=os.getenv("AWS_REGION", "us-east-1"),
                        config=Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 2}))


def build_snapshot(session, job_id: str, report_type: str) -> dict:
    rows = session.execute(select(GlobalInventory).order_by(GlobalInventory.sku,
                                                            GlobalInventory.warehouse_id)).scalars().all()
    statuses = {s.warehouse_id: s.last_event_at for s in session.execute(select(WarehouseStatus)).scalars()}
    by_sku: dict[str, dict] = {}
    for r in rows:
        item = by_sku.setdefault(r.sku, {"sku": r.sku, "total": 0, "reserved_total": 0, "warehouses": []})
        item["total"] += r.quantity
        item["reserved_total"] += r.reserved_quantity
        item["warehouses"].append({"warehouse_id": r.warehouse_id, "quantity": r.quantity,
                                   "reserved_quantity": r.reserved_quantity,
                                   "last_event_at": _iso(r.last_event_at)})
    return {"job_id": job_id, "report_type": report_type, "generated_at": _iso(utcnow()),
            "source": "central global_inventory (read model, eventual consistency)",
            "warehouse_last_event_at": {k: _iso(v) for k, v in statuses.items()},
            "sku_count": len(by_sku), "items": list(by_sku.values())}


def _iso(dt):
    dt = as_utc(dt)
    return dt.isoformat().replace("+00:00", "Z") if dt else None


class ReportGenerator:
    def __init__(self, session_factory, s3_client, bucket: str, publisher=None, max_retries: int = MAX_RETRIES):
        self.session_factory = session_factory
        self.s3 = s3_client
        self.bucket = bucket
        self.publisher = publisher
        self.max_retries = max_retries

    def handle(self, msg: dict) -> str:
        validate_event(msg)
        if msg["event_type"] != "GENERATE_REPORT":
            raise PermanentError(f"Comando inesperado en q.reports: {msg['event_type']}")
        job_id = msg["payload"].get("job_id")
        report_type = msg["payload"].get("report_type", "inventory_snapshot")
        if not job_id:
            raise PermanentError("GENERATE_REPORT sin job_id")
        with self.session_factory() as session:
            job = session.get(ReportJob, job_id)
            if job is None:
                raise PermanentError(f"report job {job_id} no existe")
            if job.status == "COMPLETED":
                log("report_already_completed", job_id=job_id)
                return job.s3_key
            if report_type not in SUPPORTED:
                self._finish(job_id, "FAILED", error=f"report_type no soportado: {report_type}")
                raise PermanentError(f"report_type no soportado: {report_type}")
            snapshot = build_snapshot(session, job_id, report_type)
        now = utcnow()
        key = f"reports/{now:%Y}/{now:%m}/{job_id}.json"
        try:
            self._upload(key, snapshot)
        except TransientError as exc:
            if int(msg.get("_retries", 0)) >= self.max_retries:
                self._finish(job_id, "FAILED", error=str(exc)[:500])
            raise
        self._finish(job_id, "COMPLETED", s3_key=key)
        if self.publisher is not None:
            event = build_event("REPORT_COMPLETED", "central-report-worker", {"job_id": job_id, "s3_key": key})
            try:
                self.publisher.publish(EVENTS_EXCHANGE, "report.completed", event)
            except Exception as exc:
                log("report_completed_publish_failed", level="WARNING", job_id=job_id, error=repr(exc))
        log("report_completed", job_id=job_id, s3_key=key, sku_count=snapshot["sku_count"])
        return key

    def _upload(self, key: str, snapshot: dict) -> None:
        from botocore.exceptions import BotoCoreError, ClientError
        body = json.dumps(snapshot, ensure_ascii=False, indent=2).encode()
        for attempt in (1, 2):
            try:
                self.s3.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType="application/json")
                return
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code == "NoSuchBucket" and attempt == 1 and os.getenv("S3_ENDPOINT_URL"):
                    self.s3.create_bucket(Bucket=self.bucket)
                    continue
                status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
                if status >= 500 or code in ("SlowDown", "RequestTimeout"):
                    raise TransientError(f"S3 {code}: {exc}") from exc
                raise PermanentError(f"S3 {code}: {exc}") from exc
            except BotoCoreError as exc:
                raise TransientError(f"S3 no disponible: {exc}") from exc

    def _finish(self, job_id: str, status: str, s3_key: str | None = None, error: str | None = None) -> None:
        with self.session_factory() as session, session.begin():
            job = session.get(ReportJob, job_id, with_for_update=True)
            job.status = status
            job.s3_key = s3_key or job.s3_key
            job.error = error
            job.updated_at = utcnow()


def main() -> None:
    from workers.publisher import RabbitPublisher
    from workers.runtime import bootstrap, run
    session_factory = bootstrap("report-worker")
    bucket = os.environ.get("S3_BUCKET", "warehouse-reports")
    run("q.reports", ReportGenerator(session_factory, make_s3_client(), bucket, RabbitPublisher()).handle,
        CONSUMER, prefetch=1)


if __name__ == "__main__":
    main()
