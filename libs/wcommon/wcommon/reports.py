"""Reporte general de bodegas (estado operativo, sincronización, inventario y transferencias).

Función pura: la usan la central (GET /reports/warehouses) y el report-worker
(POST /reports {"report_type": "warehouses_summary"} -> JSON en S3).
"""
from collections import Counter
from datetime import datetime, timezone

ONLINE, OFFLINE, NEVER_SEEN = "ONLINE", "OFFLINE", "NEVER_SEEN"


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    dt = _utc(dt)
    return dt.isoformat().replace("+00:00", "Z") if dt else None


def warehouses_summary(*, configured: list[str], inventory: list[dict], last_event_at: dict[str, datetime | None],
                       sync: dict[str, dict], transfers: list[dict], now: datetime, stale_after_seconds: int,
                       low_stock_threshold: int, pending_central_commands: int = 0, top_low_stock: int = 20) -> dict:
    """
    inventory: [{sku, warehouse_id, quantity, reserved_quantity}]
    last_event_at: {warehouse_id: datetime} (último evento o heartbeat recibido)
    sync: {warehouse_id: {last_heartbeat_at, outbox_pending, oldest_pending_at, last_snapshot_at, last_snapshot_id}}
    transfers: [{source_warehouse, destination_warehouse, status, quantity}]
    """
    now = _utc(now)
    ids = sorted(set(configured) | {r["warehouse_id"] for r in inventory} | set(last_event_at) | set(sync))
    per: dict[str, dict] = {}
    for wh in ids:
        last = _utc(last_event_at.get(wh))
        age = (now - last).total_seconds() if last else None
        status = NEVER_SEEN if last is None else (ONLINE if age <= stale_after_seconds else OFFLINE)
        s = sync.get(wh) or {}
        pending = int(s.get("outbox_pending") or 0)
        per[wh] = {
            "warehouse_id": wh,
            "configured": wh in configured,
            "status": status,
            "last_event_at": _iso(last),
            "seconds_since_last_event": round(age, 1) if age is not None else None,
            "sync": {
                "in_sync": status == ONLINE and pending == 0,
                "outbox_pending": pending,
                "oldest_pending_at": _iso(s.get("oldest_pending_at")),
                "last_heartbeat_at": _iso(s.get("last_heartbeat_at")),
                "last_snapshot_at": _iso(s.get("last_snapshot_at")),
                "last_snapshot_id": s.get("last_snapshot_id"),
            },
            "inventory": {"sku_count": 0, "total_units": 0, "reserved_units": 0, "available_units": 0,
                          "low_stock_count": 0, "low_stock_skus": []},
            "transfers": {"outgoing": {}, "incoming": {}, "units_out_completed": 0, "units_in_completed": 0},
        }

    low: dict[str, list] = {wh: [] for wh in ids}
    for r in inventory:
        inv = per[r["warehouse_id"]]["inventory"]
        qty, reserved = int(r.get("quantity") or 0), int(r.get("reserved_quantity") or 0)
        available = qty - reserved
        inv["sku_count"] += 1
        inv["total_units"] += qty
        inv["reserved_units"] += reserved
        inv["available_units"] += available
        if available <= low_stock_threshold:
            low[r["warehouse_id"]].append({"sku": r["sku"], "available": available, "quantity": qty})
    for wh, items in low.items():
        items.sort(key=lambda i: (i["available"], i["sku"]))
        per[wh]["inventory"]["low_stock_count"] = len(items)
        per[wh]["inventory"]["low_stock_skus"] = items[:top_low_stock]

    out_c: dict[str, Counter] = {wh: Counter() for wh in ids}
    in_c: dict[str, Counter] = {wh: Counter() for wh in ids}
    by_status: Counter = Counter()
    for t in transfers:
        src, dst, st = t["source_warehouse"], t["destination_warehouse"], t["status"]
        by_status[st] += 1
        if src in out_c:
            out_c[src][st] += 1
            if st == "COMPLETED":
                per[src]["transfers"]["units_out_completed"] += int(t.get("quantity") or 0)
        if dst in in_c:
            in_c[dst][st] += 1
            if st == "COMPLETED":
                per[dst]["transfers"]["units_in_completed"] += int(t.get("quantity") or 0)
    for wh in ids:
        per[wh]["transfers"]["outgoing"] = dict(sorted(out_c[wh].items()))
        per[wh]["transfers"]["incoming"] = dict(sorted(in_c[wh].items()))

    statuses = Counter(p["status"] for p in per.values())
    return {
        "report_type": "warehouses_summary",
        "generated_at": _iso(now),
        "source": "central read model (consistencia eventual)",
        "parameters": {"stale_after_seconds": stale_after_seconds, "low_stock_threshold": low_stock_threshold},
        "totals": {
            "warehouses": len(ids),
            "online": statuses.get(ONLINE, 0),
            "offline": statuses.get(OFFLINE, 0),
            "never_seen": statuses.get(NEVER_SEEN, 0),
            "in_sync": sum(1 for p in per.values() if p["sync"]["in_sync"]),
            "outbox_pending": sum(p["sync"]["outbox_pending"] for p in per.values()),
            "pending_central_commands": pending_central_commands,
            "distinct_skus": len({r["sku"] for r in inventory}),
            "total_units": sum(p["inventory"]["total_units"] for p in per.values()),
            "reserved_units": sum(p["inventory"]["reserved_units"] for p in per.values()),
            "available_units": sum(p["inventory"]["available_units"] for p in per.values()),
            "low_stock_items": sum(p["inventory"]["low_stock_count"] for p in per.values()),
            "transfers_by_status": dict(sorted(by_status.items())),
        },
        "warehouses": [per[wh] for wh in ids],
    }
