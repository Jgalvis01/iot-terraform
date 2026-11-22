# ADR-002 — Redis con Cache Aside + invalidación síncrona (A) y asíncrona (B)

**Estado:** Aceptada

## Contexto

Las consultas de inventario son muy frecuentes (WH-002 ~800 req/s de lectura, README §1). Leer siempre de PostgreSQL añade latencia y carga. Introducir una caché genera el problema de datos obsoletos y debe tolerar que Redis caiga.

## Alternativas

Para el patrón de lectura:
1. Consultar siempre PostgreSQL.
2. Cache Aside (la aplicación lee de Redis, y en miss carga de BD y rellena).
3. Write-through / read-through (la caché es la ruta de acceso principal).

Para la invalidación (README §9.3, comparación detallada en `docs/comparisons.md`):
- **A. Síncrona:** la API hace `DEL` de las claves justo después del COMMIT.
- **B. Asíncrona:** la API no toca Redis al escribir; se emite `INVENTORY_UPDATED` (outbox) y un `cache-worker` por bodega hace el `DEL`.
- Solo TTL (sin invalidación).

## Decisión

Cache Aside con Redis por bodega (`wh-00X-redis`, `maxmemory 128mb`, política `allkeys-lru`):

- Claves: `warehouse:{WH}:inventory:{product_id}` y `warehouse:{WH}:inventory:sku:{sku}` (`app/cache.py`). TTL `CACHE_TTL_SECONDS=30` como red de seguridad.
- **Estrategia A (siempre activa si `CACHE_ENABLED=true`):** `InventoryService.after_commit` ejecuta `cache.invalidate(...)` tras el commit. Un error de Redis se cuenta en `cache_errors_total` y se ignora.
- **Estrategia B (activa con el perfil `events`, `EVENTS_ENABLED=true`):** `q.cache.wh-00X` (binding `inventory.updated.wh-00X`) alimenta a `workers.cache` (`CacheInvalidator`), que hace `DEL` de las mismas dos claves. `DEL` es idempotente, por eso este consumidor no usa `processed_events`.
- Fallback: timeout de socket de 0,2 s; ante cualquier excepción la API lee de PostgreSQL. `/health` reporta `redis: ok|down|disabled`.
- Se mantienen **ambas** invalidaciones a la vez: A da consistencia inmediata en el camino feliz; B aporta una segunda invalidación desacoplada y sirve para demostrar y comparar la estrategia.

## Consecuencias / Trade-offs

- Menor latencia y menor carga en PostgreSQL, a cambio de posible dato obsoleto.
- Con A existe una ventana en la que una lectura concurrente puede repoblar la caché con el valor viejo (leyó de BD antes del commit y escribe en Redis después del `DEL`); queda obsoleto hasta el TTL o hasta el siguiente `DEL`.
- A acopla la ruta de escritura a Redis (mitigado: errores ignorados y timeout 0,2 s). B no acopla, pero es eventual (retardo de la cola) y depende de RabbitMQ y del worker.
- Si Redis cae durante una escritura, A no puede invalidar; la clave puede sobrevivir (Redis conserva datos si no se reinició) hasta el TTL. B reintenta (`TransientError` -> cola retry) cuando Redis vuelve.

## Mitigación

- TTL de 30 s acota la obsolescencia máxima en cualquier escenario.
- B corrige parte de la carrera de A: su `DEL` llega después y borra un posible valor viejo repoblado.
- Métricas `cache_hits_total`, `cache_misses_total`, `cache_errors_total` y `worker_messages_*{consumer="cache-wh-00X"}` permiten medir el hit ratio y el retraso de B.
- `tests/failure/01_redis_down.sh` demuestra el fallback.
- Pruebas de carga Redis OFF/ON con `CACHE_ENABLED=false|true` (`tests/load/run_matrix.sh`).
