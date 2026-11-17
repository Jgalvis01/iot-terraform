import json
from typing import Optional

import redis
from prometheus_client import Counter

from app.config import settings
from wcommon.logging import log

CACHE_HITS = Counter("cache_hits", "Lecturas de inventario servidas desde Redis")
CACHE_MISSES = Counter("cache_misses", "Lecturas de inventario que fueron a PostgreSQL")
CACHE_ERRORS = Counter("cache_errors", "Errores de Redis (se hizo fallback a PostgreSQL)")


def key_by_id(product_id: int) -> str:
    return f"warehouse:{settings.WAREHOUSE_ID}:inventory:{product_id}"


def key_by_sku(sku: str) -> str:
    return f"warehouse:{settings.WAREHOUSE_ID}:inventory:sku:{sku}"


class InventoryCache:
    def __init__(self, client=None):
        self._client = client

    @property
    def enabled(self) -> bool:
        return settings.CACHE_ENABLED

    @property
    def client(self):
        if self._client is None:
            self._client = redis.Redis.from_url(
                settings.REDIS_URL, socket_timeout=0.2, socket_connect_timeout=0.2)
        return self._client

    def get(self, key: str) -> Optional[dict]:
        if not self.enabled:
            return None
        try:
            raw = self.client.get(key)
        except Exception as exc:
            CACHE_ERRORS.inc()
            log("cache_error", level="WARNING", op="get", key=key, error=repr(exc))
            return None
        if raw is None:
            CACHE_MISSES.inc()
            return None
        CACHE_HITS.inc()
        return json.loads(raw)

    def set_inventory(self, product_id: int, sku: str, data: dict) -> None:
        if not self.enabled:
            return
        raw = json.dumps(data, default=str)
        try:
            pipe = self.client.pipeline()
            pipe.setex(key_by_id(product_id), settings.CACHE_TTL_SECONDS, raw)
            pipe.setex(key_by_sku(sku), settings.CACHE_TTL_SECONDS, raw)
            pipe.execute()
        except Exception as exc:
            CACHE_ERRORS.inc()
            log("cache_error", level="WARNING", op="set", error=repr(exc))

    # borra la cache cuando cambia el inventario
    def invalidate(self, product_id: int, sku: str) -> None:
        if not self.enabled:
            return
        try:
            self.client.delete(key_by_id(product_id), key_by_sku(sku))
        except Exception as exc:
            CACHE_ERRORS.inc()
            log("cache_error", level="WARNING", op="invalidate", error=repr(exc))

    def ping(self) -> str:
        if not self.enabled:
            return "disabled"
        try:
            self.client.ping()
            return "ok"
        except Exception:
            return "down"


cache = InventoryCache()
