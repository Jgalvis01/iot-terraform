import asyncio
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from wcommon.correlation import HEADER as CORRELATION_HEADER
from wcommon.correlation import get_correlation_id

from app.config import get_settings
from app.database import as_utc, get_db, iso
from app.models import GlobalInventory, WarehouseStatus, utcnow

router = APIRouter(tags=["global-inventory"])


def _stale_map(db: Session, warehouse_ids) -> dict[str, bool]:
    limit = utcnow() - timedelta(seconds=get_settings().stale_after_seconds)
    last = {s.warehouse_id: as_utc(s.last_event_at) for s in db.execute(select(WarehouseStatus)).scalars()}
    return {wh: (last.get(wh) is None or last[wh] < limit) for wh in warehouse_ids}


def _readmodel(db: Session, sku: str) -> dict | None:
    rows = db.execute(select(GlobalInventory).where(GlobalInventory.sku == sku)
                      .order_by(GlobalInventory.warehouse_id)).scalars().all()
    if not rows:
        return None
    configured = list(get_settings().warehouses)
    stale = _stale_map(db, set(configured) | {r.warehouse_id for r in rows})
    return {
        "sku": sku, "mode": "readmodel",
        "total": sum(r.quantity for r in rows),
        "reserved_total": sum(r.reserved_quantity for r in rows),
        "warehouses": [{"warehouse_id": r.warehouse_id, "quantity": r.quantity,
                        "reserved_quantity": r.reserved_quantity, "last_event_at": iso(r.last_event_at),
                        "stale": stale[r.warehouse_id]} for r in rows],
        "stale_warehouses": sorted(wh for wh, s in stale.items() if s),
    }


async def _fanout(sku: str) -> tuple[int, dict]:
    settings = get_settings()
    headers = {CORRELATION_HEADER: get_correlation_id()}
    async with httpx.AsyncClient(timeout=settings.fanout_timeout_seconds) as client:
        async def one(wh: str, base: str):
            try:
                resp = await client.get(f"{base}/inventory/by-sku/{sku}", headers=headers)
            except httpx.HTTPError as exc:
                return wh, "unavailable", f"{type(exc).__name__}"
            if resp.status_code == 404:
                return wh, "not_found", None
            if resp.status_code >= 400:
                return wh, "unavailable", f"HTTP {resp.status_code}"
            try:
                return wh, "ok", resp.json()
            except ValueError:
                return wh, "unavailable", "invalid JSON"

        results = await asyncio.gather(*(one(wh, base) for wh, base in settings.warehouses.items()))
    warehouses, unavailable, not_found = [], [], []
    for wh, kind, data in results:
        if kind == "ok":
            warehouses.append({"warehouse_id": wh, "quantity": int(data.get("quantity", 0)),
                               "reserved_quantity": int(data.get("reserved_quantity", 0)),
                               "updated_at": data.get("updated_at")})
        elif kind == "not_found":
            not_found.append(wh)
        else:
            unavailable.append({"warehouse_id": wh, "error": data})
    body = {"sku": sku, "mode": "fanout",
            "total": sum(w["quantity"] for w in warehouses),
            "reserved_total": sum(w["reserved_quantity"] for w in warehouses),
            "warehouses": warehouses,
            "unavailable_warehouses": sorted(u["warehouse_id"] for u in unavailable),
            "unavailable_details": unavailable,
            "not_found_warehouses": sorted(not_found),
            "partial": bool(unavailable)}
    if settings.warehouses and len(unavailable) == len(settings.warehouses):
        return 503, body
    if not warehouses and not unavailable:
        return 404, {"detail": f"SKU {sku} no existe en ninguna bodega", **body}
    return 200, body


@router.get("/global/inventory/{sku}")
async def global_inventory_by_sku(sku: str, mode: str = Query("readmodel", pattern="^(readmodel|fanout)$"),
                                  db: Session = Depends(get_db)):
    if mode == "fanout":
        status, body = await _fanout(sku)
        return JSONResponse(status_code=status, content=body)
    result = await run_in_threadpool(_readmodel, db, sku)
    if result is None:
        raise HTTPException(404, f"SKU {sku} no está en el read model")
    return result


@router.get("/global/inventory")
def global_inventory(limit: int = Query(200, ge=1, le=2000), offset: int = Query(0, ge=0),
                     db: Session = Depends(get_db)):
    rows = db.execute(select(GlobalInventory).order_by(GlobalInventory.sku, GlobalInventory.warehouse_id)
                      ).scalars().all()
    configured = list(get_settings().warehouses)
    stale = _stale_map(db, set(configured) | {r.warehouse_id for r in rows})
    by_sku: dict[str, dict] = {}
    for r in rows:
        item = by_sku.setdefault(r.sku, {"sku": r.sku, "total": 0, "reserved_total": 0, "warehouses": []})
        item["total"] += r.quantity
        item["reserved_total"] += r.reserved_quantity
        item["warehouses"].append({"warehouse_id": r.warehouse_id, "quantity": r.quantity,
                                   "reserved_quantity": r.reserved_quantity,
                                   "last_event_at": iso(r.last_event_at), "stale": stale[r.warehouse_id]})
    items = list(by_sku.values())
    return {"mode": "readmodel", "count": len(items), "items": items[offset:offset + limit],
            "stale_warehouses": sorted(wh for wh, s in stale.items() if s)}
