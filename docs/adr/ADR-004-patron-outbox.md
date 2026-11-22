# ADR-004 — Patrón Transactional Outbox

**Estado:** Aceptada

## Contexto

Una mutación de inventario debe (1) cambiar la base de la bodega y (2) publicar `INVENTORY_UPDATED`. Son dos sistemas (PostgreSQL y RabbitMQ) sin transacción común: si se publica antes del commit y la transacción falla, se emite un evento falso; si se publica después y RabbitMQ está caído o el proceso muere, se pierde el evento y el read model queda desactualizado para siempre ("dual write").

## Alternativas

1. Publicar directamente desde la API tras el commit (best effort).
2. Publicar antes del commit.
3. Two-phase commit / XA entre PostgreSQL y RabbitMQ (no soportado por RabbitMQ).
4. **Transactional Outbox:** insertar el evento en una tabla `outbox` en la misma transacción y publicarlo con un proceso aparte.
5. CDC (Debezium) sobre el WAL de PostgreSQL.

## Decisión

Outbox por bodega:

- Tabla `outbox(id BIGSERIAL, event_id UNIQUE, routing_key, payload JSONB, created_at, published_at NULL, attempts)` en la base de cada bodega (`db/schema.sql`, índice parcial `WHERE published_at IS NULL`).
- `enqueue_event` (`app/outbox.py`) añade la fila en la sesión; el commit único incluye stock, movimiento, `processed_commands` (si es comando de saga) y outbox. Con `EVENTS_ENABLED=false` no se escribe outbox (fases 1-2; el valor por defecto en compose es `false`).
- Proceso separado `python -m app.outbox_relay` (`wh-00X-outbox`, misma imagen): cada 1 s toma hasta 100 filas sin publicar con `FOR UPDATE SKIP LOCKED`, las publica en `warehouse.events` con publisher confirms y marca `published_at`. Si RabbitMQ cae, registra el intento (`attempts`), cierra la conexión y reintenta con backoff exponencial (1 s -> máx. 30 s); las filas esperan.
- Alcance: aplica a `PRODUCT_CREATED` e `INVENTORY_UPDATED`. Los eventos generados fuera de las bodegas (`TRANSFER_STATUS_CHANGED`, `REPORT_COMPLETED`, `SENSOR_ALERT`) **no** usan outbox: se publican directamente y de forma best effort tras persistir su estado (ver Consecuencias).

## Consecuencias / Trade-offs

- Garantía **at-least-once**: ningún evento confirmado se pierde, pero el relay puede publicar y morir antes de marcar `published_at`, por lo que se republica (duplicado). Los consumidores deben ser idempotentes (ADR-006).
- Latencia añadida (hasta ~1 s de polling) entre commit y evento: la caché asíncrona y el read model son eventuales por diseño.
- El orden global no está garantizado si hay varios relays con `SKIP LOCKED` o reintentos; el read model compara `occurred_at` (ADR-008).
- La tabla crece: no hay purga implementada de filas publicadas (limitación conocida).
- Un proceso más por bodega (`wh-00X-outbox`; en AWS corren en la EC2 `platform`).

## Mitigación

- `event_id` UNIQUE + idempotencia en consumidores.
- `tests/failure/02_rabbitmq_down.sh` demuestra el caso: con RabbitMQ detenido se aceptan 3 movimientos (HTTP 200), `outbox` tiene >= 3 filas pendientes y se vacía al volver el broker.
- Los eventos fuera de outbox son informativos: su fuente de verdad es una tabla (`transfers`, `report_jobs`, `sensor_alerts`) y un fallo de publicación se registra en logs (`transfer_status_publish_failed`, `report_completed_publish_failed`).
- Mejora propuesta (no implementada): purga periódica de `outbox` y alerta si `count(*) WHERE published_at IS NULL` crece.
