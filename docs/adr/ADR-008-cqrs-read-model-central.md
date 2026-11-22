# ADR-008 — CQRS: read model central (vs fan-out a las bodegas)

**Estado:** Aceptada

## Contexto

La dirección necesita una vista consolidada del inventario por SKU en todas las bodegas (README §14). Las bodegas tienen bases separadas (ADR-001). Consultarlas todas por cada request acopla la disponibilidad de la central a la de cada bodega.

## Alternativas

1. **Fan-out:** la central llama `GET /inventory/by-sku/{sku}` a cada bodega en cada consulta.
2. **Base única compartida** (descartada, ver ADR-001).
3. **Read model central (CQRS):** las bodegas son el lado de escritura; la central mantiene una proyección alimentada por eventos.

## Decisión

Se implementan **ambos** caminos, seleccionables con `GET /global/inventory/{sku}?mode=readmodel|fanout`, siendo `readmodel` el predeterminado (comparación medida en `docs/comparisons.md`):

- **Escritura (command side):** cada `warehouse-api` con su base y outbox.
- **Proyección:** `projection-worker` consume `q.projection` (`inventory.updated.*`, `product.created.*`) y hace upsert en `global_inventory(sku, warehouse_id, quantity, reserved_quantity, last_event_id, last_event_at)` y actualiza `warehouse_status.last_event_at`. Idempotente con `processed_events` y descarta eventos con `occurred_at` anterior a `last_event_at` (contador `projection_out_of_order_total`).
- **Lectura (query side):** `central-api` lee `global_inventory`. Cada bodega incluye `stale: true` si no llegó ningún evento suyo en `STALE_AFTER_SECONDS` (60), y la respuesta lista `stale_warehouses`.
- **Fan-out (referencia):** `httpx` asíncrono en paralelo con timeout 2 s por bodega; las que fallan van en `unavailable_warehouses` (respuesta parcial `partial: true`; 503 solo si todas fallan).

## Consecuencias / Trade-offs

- El read model responde rápido y no depende de que las bodegas estén vivas, pero puede estar **desactualizado** (consistencia eventual): retraso del outbox (~1 s) + cola + proyección.
- `stale` se calcula por la última señal de la bodega en `warehouse_status`: eventos de inventario o `WAREHOUSE_HEARTBEAT` (cada 15 s desde el outbox relay). Así se distingue "sin movimientos" (sigue habiendo latidos) de "caída o aislada" (sin latidos durante `STALE_AFTER_SECONDS`). El latido prueba que la BD y el relay de la bodega están vivos, no la API; para la API está el health check del balanceador.
- Fan-out da dato fresco pero su latencia es la de la bodega más lenta (hasta 2 s) y falla parcialmente si una cae.
- Más piezas (worker, tablas, base central) y duplicación de datos.
- La proyección no se recalcula desde cero (no hay replay de eventos ni snapshot inicial implementado): si se pierde `central_read`, se reconstruye solo con eventos nuevos.

## Mitigación

- Campo `stale` + `last_event_at` por bodega en la respuesta: la UI/cliente sabe qué dato es dudoso (README §18 caso 5).
- Idempotencia y descarte por `occurred_at` ante duplicados y desorden.
- `mode=fanout` disponible como verificación contra la fuente de verdad.
- `tests/failure/03_worker_stopped.sh` (cola acumula, read model se pone al día) y `05_warehouse_down.sh` (central sigue funcionando).
- Propuesta (no implementada): endpoint de reconciliación que compare `global_inventory` contra el fan-out y reconstruya la proyección.
