import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator
from sqlalchemy import text

import app.models
from app.config import settings
from app.database import Base, engine
from app.errors import register_error_handlers
from app.modules.health.health_controller import router as health_controller
from app.modules.inventory.inventory_controller import router as inventory_controller
from app.modules.products.products_controller import router as products_controller
from app.modules.transfers.transfers_controller import router as transfers_controller
from app.modules.sync.sync_controller import router as sync_controller
from wcommon.correlation import CorrelationIdMiddleware
from wcommon.logging import configure_logging, log

configure_logging(settings.SERVICE_NAME)


def init_db(retries: int = 30, delay: float = 1.0) -> None:
    for attempt in range(1, retries + 1):
        try:
            Base.metadata.create_all(bind=engine)
            return
        except Exception as exc:
            if attempt == retries:
                raise
            log("db_not_ready", level="WARNING", attempt=attempt, error=repr(exc))
            time.sleep(delay)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    log("service_started", warehouse_id=settings.WAREHOUSE_ID, cache_enabled=settings.CACHE_ENABLED,
        events_enabled=settings.EVENTS_ENABLED)
    yield


app = FastAPI(
    title=f"Warehouse API ({settings.WAREHOUSE_ID})",
    description="Servicio de bodega: productos, inventario, caché Cache Aside, outbox y comandos de saga.",
    version="2.0.0",
    lifespan=lifespan,
)
app.add_middleware(CorrelationIdMiddleware)
register_error_handlers(app)
Instrumentator().instrument(app).expose(app, include_in_schema=False)

app.include_router(health_controller)
app.include_router(products_controller)
app.include_router(inventory_controller)
app.include_router(transfers_controller)
app.include_router(sync_controller)
