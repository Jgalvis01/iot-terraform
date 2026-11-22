# Matriz de errores y casos de fallo (Fase 10 / Fase 11)

Clasificación implementada en `libs/wcommon/wcommon/rabbit.py` y `wcommon/errors.py`: `TransientError` (reintentable) y `PermanentError` (no reintentable). Un error inesperado (cualquier otra `Exception`) se trata como permanente. Parámetros: `MAX_RETRIES=3`, `RETRY_DELAY_MS=5000`. Ver ADR-005 y ADR-007.

## 1. Matriz de errores

| # | Error | ¿Retry? | ¿DLQ? | ¿Compensación? | Justificación |
|---|---|---|---|---|---|
| 1 | JSON corrupto / no parseable | No | Sí, inmediata (`x-error: JSON inválido`) | No | Reintentar nunca lo arregla; se conserva el cuerpo en la DLQ para análisis (`tests/failure/07_dlq.sh`) |
| 2 | Evento sin campos obligatorios (`validate_event`) o `payload` inválido | No | Sí, inmediata | No | Error de contrato del productor: es permanente y sólo se corrige republicando |
| 3 | Producto inexistente (404) en un comando de saga | No (`WarehouseRejected` es `PermanentError`) | No en `reserve`/`receive`: la saga lo absorbe (`FAILED`/`COMPENSATING`). Sí en `commit-out`/`release` (rechazo => DLQ) | Según el paso (ver 4-9) | Es un error de negocio determinista |
| 4 | Stock insuficiente en `reserve` (409 `INSUFFICIENT_STOCK`) | No | No (la saga lo maneja) | No: `FAILED` (no llegó a reservar) | No hay nada que deshacer; la transferencia termina con `reason` |
| 5 | Timeout / 5xx / conexión rechazada de la bodega origen en `reserve` | Sí (hasta 3, cada 5 s) | Sí, al agotar (queda `FAILED`, "source unavailable") | No (no se reservó nada) | Error transitorio; al agotar se cierra la transferencia antes de lanzar el último error |
| 6 | Timeout / 5xx de la bodega destino en `receive` | Sí (hasta 3) | No: al agotar la saga pasa a `COMPENSATING` y no se lanza error | Sí: `release` en origen -> `CANCELLED` | El stock ya está reservado y no debe quedar bloqueado indefinidamente |
| 7 | `receive` rechazado por el destino (4xx permanente) | No | No | Sí: `COMPENSATING` -> `release` -> `CANCELLED` | Error de negocio del destino; se libera la reserva |
| 8 | Fallo en `commit-out` (5xx/timeout) tras recibir en destino | Sí (hasta 3) | Sí, al agotar (queda `DESTINATION_RECEIVED`) | No automática | El destino ya tiene el stock: cancelar duplicaría; requiere intervención manual / reproceso |
| 9 | `release` (compensación) falla: transitorio / rechazo | Transitorio: sí; rechazo: no | Sí (tras agotar o de inmediato). Queda `COMPENSATING` | No hay compensación de la compensación: intervención manual | La reserva sigue retenida; se deja visible para operador |
| 10 | BD central caída (`OperationalError`) en projection / audit / IoT / report | Sí | Sí, tras 3 reintentos | No | Típicamente transitoria; el mensaje espera (retry) y no se pierde |
| 11 | Conflicto de concurrencia (`IntegrityError` en proyección/IoT por dos réplicas) | Sí | Sólo si persiste | No | El reintento ve el estado ya confirmado (idempotencia) |
| 12 | Redis caído en la API (lectura/invalidación) | No (no hay mensaje) | No | No | Se cuenta `cache_errors_total` y se lee de PostgreSQL |
| 13 | Redis caído en `cache-worker` (`RedisError`) | Sí | Sí, tras 3 reintentos | No | Se reintenta; el TTL de 30 s acota la obsolescencia aunque llegue a la DLQ |
| 14 | S3 5xx / `SlowDown` / timeout en `report-worker` | Sí | Sí, tras agotar (job `FAILED`) | No | Transitorio; clave S3 determinista permite reintentar sin duplicar |
| 15 | Error S3 4xx (permisos, bucket inexistente en AWS) | No | Sí | No | Permanente: no mejora con el tiempo |
| 16 | `report_type` no soportado | No | Sí (job `FAILED`) | No | Datos inválidos |
| 17 | Mensaje duplicado (mismo `event_id`) | No | No | No | Se descarta por `processed_events` / PK; `worker_messages_duplicated_total` (`tests/failure/04_duplicate_message.sh`) |
| 18 | Evento fuera de orden (proyección: `occurred_at` anterior; IoT: `sequence_number` <= último) | No | No | No | No es un error: se descarta y marca como procesado; métricas `*_out_of_order_total` |
| 19 | RabbitMQ caído al escribir en una bodega | No aplica a la API (acepta la escritura) | No | No | Outbox: el evento queda en `outbox` y el relay reintenta con backoff |
| 20 | RabbitMQ caído en `POST /transfers` o `POST /reports` | El cliente reintenta (`Retry-After: 5`, `Idempotency-Key`) | No | No (la fila se borra) | Sin broker no hay saga; se rechaza con 503 `BROKER_UNAVAILABLE` en vez de dejar una transferencia huérfana |
| 21 | Stock insuficiente en movimiento público (`POST /inventory/movements`) | No | No | No | Respuesta síncrona 400 `INSUFFICIENT_STOCK`, rollback de la transacción |
| 22 | Lectura IoT inválida (campo faltante / `value` no numérico) | No | Sí (`PermanentError`) en los workers; la ingestion rechaza antes de publicar | No | Dato corrupto no recuperable |

Notas:
- `TransientError` con `x-retries == 3` va a `Q.dlq`; los reintentos esperan 5 s en `Q.retry` y pierden el orden original.
- No existe reproceso automático desde la DLQ: se inspecciona en `http://localhost:15672` (cabeceras `x-error`, `x-failed-queue`) y se republica manualmente.

## 2. Casos de fallo del README §18 (Fase 11)

Todos los scripts se ejecutan con el stack local levantado (`infrastructure/compose`, perfiles `events`, `central`, y datos `db/seed.sql`) y están en `tests/failure/`. Utilidades comunes en `tests/failure/common.sh`.

### Caso 1 — Redis caído (`tests/failure/01_redis_down.sh`)

- **Comportamiento esperado:** la API responde 200 desde PostgreSQL (fallback con timeout de socket 0,2 s); `/health` muestra `redis: down` pero sigue 200; las escrituras se confirman y la invalidación fallida se ignora; `cache_errors_total` aumenta. Al volver Redis, la caché se repuebla sola (Cache Aside).
- **Qué hace el script:** lectura con Redis arriba -> `docker compose stop wh-001-redis` -> lectura (debe ser 200) -> health -> un movimiento -> métricas `cache_*` -> reinicia Redis.

### Caso 2 — RabbitMQ temporalmente caído (`tests/failure/02_rabbitmq_down.sh`)

- **Decisión justificada (README exige elegir):** *almacenar localmente y publicar después* (Outbox): las operaciones críticas de bodega **no se rechazan**; el evento se guarda en la misma transacción. Para comandos de la central (`POST /transfers`, `POST /reports`) la decisión es **rechazar** con 503 + `Retry-After`, porque sin broker la saga no puede avanzar.
- **Comportamiento esperado:** 3 movimientos con HTTP 200, >= 3 filas con `published_at IS NULL` en `outbox`; al arrancar RabbitMQ, el relay (backoff exponencial hasta 30 s) las publica y el conteo vuelve a 0.
- **Qué hace el script:** detiene `rabbitmq`, crea 3 movimientos, cuenta pendientes, arranca `rabbitmq`, espera 25 s y verifica 0 pendientes.

### Caso 3 — Worker detenido (`tests/failure/03_worker_stopped.sh`)

- **Comportamiento esperado:** los mensajes quedan en `q.projection` (durable/persistente); el read model se queda desactualizado; al volver el worker se drena la cola y `global_inventory` coincide con el inventario real de WH-001.
- **Qué hace el script:** detiene `projection-worker`, hace 5 movimientos, muestra la profundidad de `q.projection` y el read model viejo, arranca el worker, muestra la cola drenada y compara con `GET /inventory/by-sku`.

### Caso 4 — Mensaje duplicado (`tests/failure/04_duplicate_message.sh`)

- **Comportamiento esperado:** publicar el mismo `event_id` dos veces no duplica efectos: 1 fila por consumidor en `processed_events`, 1 fila en `audit_log`; contador `worker_messages_duplicated_total` incrementado.
- **Qué hace el script:** publica dos veces `EVT-DUP-<ts>` (`INVENTORY_UPDATED`) en `warehouse.events` y consulta `processed_events`, `audit_log` y `/metrics` del worker.

### Caso 5 — Bodega inaccesible (`tests/failure/05_warehouse_down.sh`)

- **Comportamiento esperado:** WH-001 y WH-003 siguen operando; la central responde. Con `mode=readmodel` responde rápido con el último dato conocido y marca `stale: true` / `stale_warehouses` cuando pasan `STALE_AFTER_SECONDS` (60 s) sin eventos de WH-002 (el script no espera ese tiempo salvo que se repita la consulta). Con `mode=fanout` WH-002 aparece en `unavailable_warehouses` (`partial: true`) tras el timeout de 2 s.
- **Qué hace el script:** detiene `wh-002-lb` y las réplicas, comprueba health de las demás, consulta ambos modos, y recupera WH-002 (`--profile lb`).

### Fallos de la saga (`tests/failure/06_saga_failures.sh`)

Seis escenarios sobre transferencias `P-001` WH-001 -> WH-002:

| Saga | Qué provoca | Comportamiento esperado |
|---|---|---|
| 1 | Transferencia de 30 unidades | `COMPLETED`; stock origen -30, destino +30, `reserved` vuelve a 0 |
| 2 | Cantidad 999999 | `FAILED` ("source rejected reserve"), sin compensación, stock intacto |
| 3 | `X-Fail-Step: before_reserve` | reintentos (3 x 5 s) y `FAILED` ("source unavailable"), sin reserva |
| 4 | `X-Fail-Step: after_reserve` | `COMPENSATING` -> `CANCELLED`, reserva liberada (stock restaurado) |
| 5 | `X-Fail-Step: destination_down` | reintentos y luego `COMPENSATING` -> `CANCELLED` |
| 6 | Redelivery de `PROCESS_TRANSFER` ya `COMPLETED` | No repite efectos (stock sin cambios) |

### DLQ (`tests/failure/07_dlq.sh`)

- **Comportamiento esperado:** un JSON corrupto y un evento sin campos obligatorios publicados en `q.projection` terminan en `q.projection.dlq` sin reintentos (>= 2 mensajes), con cabecera `x-error`.
- **Qué hace el script:** publica ambos mensajes desde el contenedor `projection-worker`, lista las colas `dlq` y recuerda inspeccionarlas en la UI de RabbitMQ (Queues -> `q.projection.dlq` -> Get messages).

Ejecutar, por ejemplo: `bash tests/failure/06_saga_failures.sh`.
