# Comparaciones de alternativas (Fases 2, 7 y 9)

Los análisis cualitativos se derivan del código. Las cifras de latencia/throughput **no se inventan aquí**: se obtienen con `tests/load/` y se registran en el informe de pruebas de carga.

## 1. Fase 2 — Invalidación de caché: A (síncrona) vs B (asíncrona)

Ambas están implementadas y operan a la vez (ADR-002). A: `InventoryService.after_commit` -> `cache.invalidate` (`DEL` tras el commit). B: `INVENTORY_UPDATED` -> `q.cache.wh-00X` -> `workers.cache`.

| Criterio | A. Invalidación síncrona (en la API) | B. Invalidación asíncrona (evento + cache-worker) |
|---|---|---|
| Momento del `DEL` | Inmediatamente tras el COMMIT, en el mismo request | Tras outbox (~1 s) + cola + worker |
| Ventana de obsolescencia | Mínima (solo la carrera lectura/commit) | Mayor: retardo del pipeline de eventos; acotada además por el TTL de 30 s |
| Latencia de la escritura | Suma una llamada a Redis (timeout 0,2 s) | No añade nada a la ruta de escritura |
| Acoplamiento escritura-Redis | Sí; errores se ignoran y se cuentan en `cache_errors_total` | No: la API no toca Redis al escribir |
| Si Redis está caído al escribir | La invalidación se pierde; la clave vieja puede durar hasta el TTL | El worker reintenta (retry TTL, 3 veces); si agota va a DLQ y manda el TTL |
| Si RabbitMQ / worker están caídos | Sin efecto | La invalidación se retrasa (mensajes esperan en cola/outbox); manda el TTL |
| Carrera "lector repuebla con valor viejo" | Puede dejar el valor viejo hasta TTL | El `DEL` tardío de B puede limpiarlo |
| Complejidad operativa | Baja | Mayor: outbox-relay + cola + worker por bodega |
| Escalabilidad | La API hace una operación extra por escritura | Desacopla y absorbe picos; el worker escala independientemente |
| Observabilidad | `cache_errors_total` | `worker_messages_*{consumer="cache-wh-00X"}`, profundidad de `q.cache.*` |

**Conclusión del proyecto:** usar **A como camino principal** (frescura inmediata, simple) y **B como segunda línea** y demostración del patrón basado en eventos; el **TTL de 30 s** es la red de seguridad común. Para medir ambas: `CACHE_ENABLED` y `EVENTS_ENABLED` controlan cada estrategia; observar `cache_hits_total / (hits+misses)` y el retraso de la cola.

## 2. Fase 7 — Fan-out vs Read Model

Implementados ambos: `GET /global/inventory/{sku}?mode=fanout|readmodel` (`central-api/app/routers/global_inventory.py`). Ver ADR-008.

| Criterio | Fan-out (consultar cada bodega) | Read Model central (CQRS) |
|---|---|---|
| **Latencia** | La de la bodega más lenta (llamadas HTTP en paralelo, timeout 2 s por bodega) + red; crece con el nº de bodegas | Un `SELECT` a `central_read` (índice por PK `(sku, warehouse_id)`); independiente del nº de bodegas |
| **Disponibilidad** | Depende de todas las bodegas: una caída da respuesta parcial (`unavailable_warehouses`); 503 solo si caen todas | Independiente de las bodegas; si una cae sigue respondiendo con su último dato (marcado `stale` tras 60 s sin eventos) |
| **Consistencia** | Fresca (lee la fuente de verdad, vía su Redis: puede traer hasta 30 s de caché) | Eventual: retraso outbox + RabbitMQ + proyección; puede estar desactualizada |
| **Carga sobre las bodegas** | Cada consulta global golpea todas las APIs/BD de bodega (puede afectar a las de alto tráfico) | Cero carga sobre las bodegas en lectura |
| **Complejidad** | Baja (sin infraestructura extra) | Mayor: proyección idempotente, `global_inventory`, `warehouse_status`, manejo de orden/duplicados |
| **Dependencia** | Acoplamiento síncrono central -> bodegas | Acoplamiento asíncrono por eventos (RabbitMQ + projection-worker) |
| **Escalamiento de las consultas** | Cada consulta multiplica tráfico por N bodegas | Lectura escala con réplicas de `central-api`/`central_read` |
| **Reconstrucción** | No aplica | Sin replay implementado: solo se rehace con eventos nuevos |

**Matiz importante:** `stale` se basa en la última señal recibida de la bodega: eventos de inventario **o** el `WAREHOUSE_HEARTBEAT` que el outbox relay publica cada 15 s. Así una bodega sana sin movimientos no aparece obsoleta; una caída se detecta pasados `STALE_AFTER_SECONDS` (60 s) sin latidos. Fan-out distingue explícitamente "no disponible".

**Decisión:** read model como modo predeterminado y fan-out como verificación/contraste. Evidencia: `tests/failure/05_warehouse_down.sh` ejecuta ambos modos con WH-002 caída. Latencias concretas: medir con k6 contra ambos modos (`tests/load/`) y anotarlas en el informe.

## 3. Fase 9 — Worker vs Batch vs Lambda

Tabla del enunciado completada con nuestra implementación (se usa como herramienta de decisión, no como regla absoluta). Ver ADR-011.

| Característica | Worker | Batch | Lambda |
|---|---|---|---|
| Ejecución continua | Sí (consumidor de cola siempre activo) | No (ejecución puntual/programada) | No (se ejecuta por invocación) |
| Activación por evento | Sí (mensaje en cola) | No necesariamente (calendario/cron/manual) | Sí (evento S3 `ObjectCreated` en `exports/`) |
| Escalamiento | Controlado por nosotros (más réplicas / `prefetch`) | Controlado (nº de ejecuciones, tamaño del job) | Administrado por AWS (concurrencia automática) |
| Estado | Externo (PostgreSQL: `report_jobs`, `processed_events`) | Externo (`batch_runs`, S3) | Externo (S3; sin estado local) |
| Uso ideal | Procesamiento continuo y bajo demanda con reintentos/DLQ | Procesamiento masivo/periódico y reejecutable | Funciones cortas disparadas por eventos |
| **Nuestro caso** | `report-worker` (reportes `POST /reports`), proyección, auditoría, saga | `batch.daily_report` (consolidado diario de movimientos) | `s3_processor` (validar/normalizar `exports/*.json|csv` -> `processed/` o `rejected/`) |
| Reintentos/DLQ | Retry TTL + DLQ de RabbitMQ | Reejecutar el job (claves S3 deterministas, upsert por `run_date`) | Política de reintentos de Lambda sobre eventos asíncronos de S3 (no configurada en el repo) |
| Coste | Proceso siempre encendido | Solo mientras corre (en local: cron del host) | Pago por invocación |
| Límite | Capacidad de las instancias | Tiempo de la ventana batch | Tiempo máximo y memoria de Lambda |

**Cuándo usar cada uno (según nuestro diseño):** worker cuando hay flujo continuo de mensajes y se requiere control de reintentos/orden (reportes bajo demanda); Batch cuando el trabajo es masivo, periódico y no urgente (cierre diario consultando todas las bodegas); Lambda cuando el disparador es un objeto nuevo en S3 y el procesamiento es corto y sin estado.
