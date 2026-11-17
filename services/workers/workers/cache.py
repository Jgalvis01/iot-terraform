import os

import redis

from wcommon.errors import TransientError
from wcommon.events import validate_event
from wcommon.logging import log


def cache_keys(warehouse_id: str, payload: dict) -> list[str]:
    keys = []
    if payload.get("product_id") is not None:
        keys.append(f"warehouse:{warehouse_id}:inventory:{payload['product_id']}")
    if payload.get("sku"):
        keys.append(f"warehouse:{warehouse_id}:inventory:sku:{payload['sku']}")
    return keys


class CacheInvalidator:
    def __init__(self, warehouse_id: str, redis_client):
        self.warehouse_id = warehouse_id.upper()
        self.redis = redis_client

    def handle(self, msg: dict) -> int:
        validate_event(msg)
        if msg["event_type"] != "INVENTORY_UPDATED":
            return 0
        payload = msg["payload"]
        if str(payload.get("warehouse_id", "")).upper() != self.warehouse_id:
            log("cache_event_other_warehouse_ignored", level="WARNING", event_id=msg["event_id"],
                warehouse_id=payload.get("warehouse_id"))
            return 0
        keys = cache_keys(self.warehouse_id, payload)
        if not keys:
            return 0
        try:
            deleted = self.redis.delete(*keys)
        except redis.RedisError as exc:
            raise TransientError(f"Redis no disponible: {exc!r}") from exc
        log("cache_invalidated", event_id=msg["event_id"], warehouse_id=self.warehouse_id,
            sku=payload.get("sku"), keys=keys, deleted=deleted)
        return deleted


def main() -> None:
    from workers.runtime import bootstrap, run
    warehouse_id = os.environ["WAREHOUSE_ID"].upper()
    bootstrap(f"cache-worker-{warehouse_id.lower()}", with_db=False)
    client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                                  socket_timeout=1.0, socket_connect_timeout=1.0)
    run(f"q.cache.{warehouse_id.lower()}", CacheInvalidator(warehouse_id, client).handle,
        f"cache-{warehouse_id.lower()}")


if __name__ == "__main__":
    main()
