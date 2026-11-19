import csv
import io
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3

WH_RE = re.compile(r"^WH-\d{3}$")
_s3 = None


def _client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", endpoint_url=os.getenv("S3_ENDPOINT_URL") or None)
    return _s3


def _log(event: str, level: str = "INFO", **fields) -> None:
    print(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                      "service": "s3-processor", "level": level, "event": event, **fields}, default=str))


class UnreadableFile(Exception):
    pass


def parse_rows(name: str, body: bytes) -> list:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise UnreadableFile(f"no es UTF-8: {exc}")
    lower = name.lower()
    if lower.endswith(".json"):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise UnreadableFile(f"JSON invalido: {exc}")
        if not isinstance(data, list):
            raise UnreadableFile("el JSON debe ser una lista de objetos")
        return data
    if lower.endswith(".csv"):
        try:
            reader = csv.DictReader(io.StringIO(text))
            if not reader.fieldnames or "sku" not in [f.strip() for f in reader.fieldnames]:
                raise UnreadableFile("CSV sin cabecera con columna 'sku'")
            return [{(k or "").strip(): v for k, v in row.items()} for row in reader]
        except csv.Error as exc:
            raise UnreadableFile(f"CSV invalido: {exc}")
    raise UnreadableFile("extension no soportada (solo .json o .csv)")


# valida cada fila del archivo
def validate_row(row) -> tuple[dict | None, str | None]:
    if not isinstance(row, dict):
        return None, "la fila no es un objeto"
    sku = row.get("sku")
    if not isinstance(sku, str) or not sku.strip():
        return None, "sku vacio o invalido"
    wh = row.get("warehouse_id")
    if not isinstance(wh, str) or not WH_RE.match(wh.strip()):
        return None, "warehouse_id con formato invalido (WH-NNN)"
    q = row.get("quantity")
    if isinstance(q, bool):
        return None, "quantity invalida"
    if isinstance(q, str):
        qs = q.strip()
        if not re.fullmatch(r"\d+", qs):
            return None, "quantity debe ser entero >= 0"
        q = int(qs)
    elif isinstance(q, float) and q.is_integer():
        q = int(q)
    if not isinstance(q, int) or q < 0:
        return None, "quantity debe ser entero >= 0"
    return {"sku": sku.strip(), "warehouse_id": wh.strip(), "quantity": q}, None


def process_object(bucket: str, key: str) -> dict:
    s3 = _client()
    base = key.rsplit("/", 1)[-1]
    stem = base.rsplit(".", 1)[0]
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    try:
        rows = parse_rows(base, body)
    except UnreadableFile as exc:
        s3.put_object(Bucket=bucket, Key=f"rejected/{base}", Body=body)
        _log("file_rejected", level="ERROR", bucket=bucket, key=key, reason=str(exc))
        return {"key": key, "result": "rejected", "reason": str(exc)}
    valid, invalid = [], []
    for i, row in enumerate(rows):
        norm, err = validate_row(row)
        if norm:
            valid.append(norm)
        else:
            invalid.append({"row": i + 1, "error": err})
    if not valid:
        s3.put_object(Bucket=bucket, Key=f"rejected/{base}", Body=body)
        _log("file_rejected", level="ERROR", bucket=bucket, key=key, reason="0 filas validas",
             rows=len(rows), invalid_rows=len(invalid))
        return {"key": key, "result": "rejected", "reason": "0 filas validas"}
    meta = {"source_key": key, "rows": len(rows), "valid_rows": len(valid), "invalid_rows": len(invalid),
            "processed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}
    s3.put_object(Bucket=bucket, Key=f"processed/{stem}.json", Body=json.dumps(valid).encode(),
                  ContentType="application/json")
    s3.put_object(Bucket=bucket, Key=f"processed/{stem}.meta.json",
                  Body=json.dumps({**meta, "invalid_details": invalid[:50]}).encode(), ContentType="application/json")
    _log("file_processed", bucket=bucket, key=key, **{k: meta[k] for k in ("rows", "valid_rows", "invalid_rows")})
    return {"key": key, "result": "processed", **meta}


# se ejecuta cuando se sube un archivo a exports/
def lambda_handler(event, context):
    results = []
    for record in (event or {}).get("Records", []):
        try:
            bucket = record["s3"]["bucket"]["name"]
            key = unquote_plus(record["s3"]["object"]["key"])
        except (KeyError, TypeError):
            _log("bad_record", level="ERROR")
            continue
        if not key.startswith("exports/") and key.startswith(("processed/", "rejected/")):
            continue
        try:
            results.append(process_object(bucket, key))
        except Exception as exc:
            _log("processing_error", level="ERROR", bucket=bucket, key=key, error=repr(exc))
            results.append({"key": key, "result": "error", "reason": repr(exc)})
    return {"results": results}
