"""La central maneja las bodegas: estado, conectividad (central -> bodega) y resincronización.

Las bodegas no se conocen entre ellas; solo la central tiene la lista (WAREHOUSES)
y la red solo permite tráfico central <-> bodega.
"""
import asyncio
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from wcommon.correlation import HEADER as CORRELATION_HEADER
from wcommon.correlation import get_correlation_id
from wcommon.logging import log

from app.config import get_settings
from app.database import get_db
from app.warehouse_report import build_summary

router = APIRouter(tags=["warehouses"])


@router.get("/warehouses")
def list_warehouses(db: Session = Depends(get_db)):
    """Estado de cada bodega según la central (heartbeats/eventos recibidos por el broker)."""
    summary = build_summary(db)
    return {"count": len(summary["warehouses"]), "items": [
        {"warehouse_id": w["warehouse_id"], "configured": w["configured"], "status": w["status"],
         "last_event_at": w["last_event_at"], "sync": w["sync"]} for w in summary["warehouses"]]}


async def _probe(client: httpx.AsyncClient, wh: str, base: str) -> dict:
    headers = {CORRELATION_HEADER: get_correlation_id()}
    started = time.perf_counter()
    try:
        health = await client.get(f"{base}/health", headers=headers)
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        sync = None
        try:
            resp = await client.get(f"{base}/sync/status", headers=headers)
            if resp.status_code == 200:
                sync = resp.json()
        except (httpx.HTTPError, ValueError):
            sync = None
        body = health.json() if health.headers.get("content-type", "").startswith("application/json") else {}
        return {"warehouse_id": wh, "url": base, "reachable": True, "http_status": health.status_code,
                "healthy": health.status_code == 200, "latency_ms": latency_ms,
                "checks": body.get("checks"), "outbox": sync}
    except httpx.HTTPError as exc:
        return {"warehouse_id": wh, "url": base, "reachable": False, "healthy": False,
                "error": type(exc).__name__}


@router.get("/warehouses/connectivity")
async def connectivity(timeout: float = Query(3.0, gt=0, le=10)):
    """Prueba en vivo que la central llega a cada bodega por la red privada (peering)."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=timeout) as client:
        results = await asyncio.gather(*(_probe(client, wh, base) for wh, base in settings.warehouses.items()))
    results = sorted(results, key=lambda r: r["warehouse_id"])
    reachable = sum(1 for r in results if r["reachable"])
    log("warehouses_connectivity", reachable=reachable, total=len(results))
    return {"from": "central", "total": len(results), "reachable": reachable,
            "all_reachable": reachable == len(results), "warehouses": results}


@router.post("/warehouses/{warehouse_id}/resync", status_code=202)
async def resync(warehouse_id: str):
    """Pide a la bodega que reenvíe su inventario completo (INVENTORY_SNAPSHOT) por el broker."""
    wh = warehouse_id.upper()
    base = get_settings().warehouses.get(wh)
    if base is None:
        raise HTTPException(404, f"Bodega desconocida: {wh}")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{base}/sync/snapshot", params={"reason": "central"},
                                     headers={CORRELATION_HEADER: get_correlation_id()})
    except httpx.HTTPError as exc:
        log("warehouse_resync_unreachable", level="WARNING", warehouse_id=wh, error=repr(exc))
        return JSONResponse(status_code=502, content={"detail": f"{wh} no responde", "code": "WAREHOUSE_UNREACHABLE"})
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:200]}
    log("warehouse_resync_requested", warehouse_id=wh, status_code=resp.status_code)
    return JSONResponse(status_code=202 if resp.status_code < 300 else resp.status_code, content=body)
