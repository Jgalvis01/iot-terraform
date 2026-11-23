import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import text
from sqlalchemy.orm import Session

from wcommon.correlation import CorrelationIdMiddleware
from wcommon.logging import configure_logging, log

from app.config import get_settings
from app.database import get_db, init_db
from app.command_outbox import pending_count
from app.routers import audit, global_inventory, reports, transfers, warehouses


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    init_db()
    log("central_api_started", warehouses=list(settings.warehouses),
        stale_after_seconds=settings.stale_after_seconds)
    yield


def create_app() -> FastAPI:
    configure_logging(get_settings().service_name)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    app =FastAPI(title="Warehouse Cloud Platform - Central API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(transfers.router)
    app.include_router(global_inventory.router)
    app.include_router(reports.router)
    app.include_router(audit.router)
    app.include_router(warehouses.router)

    @app.get("/health")
    def health(db: Session = Depends(get_db)):
        settings = get_settings()
        pending = None
        try:
            db.execute(text("SELECT 1"))
            db_status = "ok"
            pending = pending_count(db)
        except Exception as exc:
            log("health_db_down", level="ERROR", error=repr(exc))
            db_status = "down"
        body = {"status": "ok" if db_status == "ok" else "degraded", "service": settings.service_name,
                "checks": {"db": db_status}, "warehouses": sorted(settings.warehouses),
                "pending_commands": pending}
        return JSONResponse(status_code=200 if db_status == "ok" else 503, content=body)

    Instrumentator(excluded_handlers=["/metrics", "/health"]).instrument(app).expose(
        app, include_in_schema=False)
    return app
