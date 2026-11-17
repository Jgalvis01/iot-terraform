import json

import redis

from app import cache as cache_mod
from wh_unit_helpers import move


def counter(c):
    return c._value.get()


def test_miss_then_hit_and_both_keys_populated(client, product, fake_redis):
    pid = product["id"]
    h0, m0 = counter(cache_mod.CACHE_HITS), counter(cache_mod.CACHE_MISSES)
    a = client.get(f"/inventory/{pid}").json()
    b = client.get(f"/inventory/{pid}").json()
    assert a == b
    assert counter(cache_mod.CACHE_MISSES) == m0 + 1 and counter(cache_mod.CACHE_HITS) == h0 + 1
    assert fake_redis.get(f"warehouse:WH-001:inventory:{pid}") is not None
    assert fake_redis.get("warehouse:WH-001:inventory:sku:P-001") is not None
    assert 0 < fake_redis.ttl(f"warehouse:WH-001:inventory:{pid}") <= 30
    h1 = counter(cache_mod.CACHE_HITS)
    client.get("/inventory/by-sku/P-001")
    assert counter(cache_mod.CACHE_HITS) == h1 + 1


def test_mutation_invalidates_cache(client, product, fake_redis):
    pid = product["id"]
    client.get(f"/inventory/{pid}")
    move(client, pid, "RECEIPT", 9)
    assert fake_redis.get(f"warehouse:WH-001:inventory:{pid}") is None
    assert fake_redis.get("warehouse:WH-001:inventory:sku:P-001") is None
    assert client.get(f"/inventory/{pid}").json()["quantity"] == 9


def test_saga_command_invalidates_cache(client, product, fake_redis):
    move(client, product["id"], "RECEIPT", 9)
    client.get("/inventory/by-sku/P-001")
    client.post("/transfers/TR-9/reserve", json={"sku": "P-001", "quantity": 2})
    assert client.get("/inventory/by-sku/P-001").json()["reserved_quantity"] == 2


class BrokenRedis:
    def __getattr__(self, name):
        def boom(*a, **k):
            raise redis.exceptions.ConnectionError("down")
        return boom


def test_redis_failure_falls_back_to_postgres(client, product, monkeypatch):
    move(client, product["id"], "RECEIPT", 4)
    monkeypatch.setattr(cache_mod.cache, "_client", BrokenRedis())
    e0 = counter(cache_mod.CACHE_ERRORS)
    r = client.get(f"/inventory/{product['id']}")
    assert r.status_code == 200 and r.json()["quantity"] == 4
    assert counter(cache_mod.CACHE_ERRORS) > e0
    assert move(client, product["id"], "RECEIPT", 1).status_code == 201
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["checks"]["redis"] == "down"


def test_cache_disabled_never_touches_redis(client, product, fake_redis, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "CACHE_ENABLED", False)
    client.get(f"/inventory/{product['id']}")
    assert fake_redis.keys("*") == []
