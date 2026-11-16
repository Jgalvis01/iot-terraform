from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import cache as cache_mod
from app.config import settings
from app.database import get_db

router = APIRouter(tags=["System"])


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    checks = {"db": "ok", "redis": cache_mod.cache.ping()}
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        checks["db"] = "down"
    body = {"status": "ok" if checks["db"] == "ok" else "error",
            "warehouse_id": settings.WAREHOUSE_ID, "checks": checks}
    return JSONResponse(body, status_code=200 if checks["db"] == "ok" else 503)
