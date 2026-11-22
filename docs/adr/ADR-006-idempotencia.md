# ADR-006 — Idempotencia (`processed_events` y `processed_commands`)

**Estado:** Aceptada

## Contexto

Con entrega at-least-once (outbox, reintentos, redelivery tras caída de consumidor) el mismo mensaje puede llegar varias veces. Procesarlo dos veces duplicaría una operación (README §18 caso 4, §13.4). También el cliente puede reintentar un `POST /transfers`.

## Alternativas

1. Confiar en "exactly once" del broker (no existe de extremo a extremo).
2. Operaciones naturalmente idempotentes (p. ej. `DEL`, `INSERT ... ON CONFLICT DO NOTHING`).
3. Registro explícito de ids procesados en la misma transacción que el efecto.
4. Deduplicar en memoria / Redis (se pierde al reiniciar; no atómico con el efecto).

## Decisión

Se combinan tres mecanismos según el consumidor:

1. **`processed_events(event_id, consumer, processed_at, PK(event_id, consumer))`** (`wcommon/idempotency.py`, en `central_read`): `already_processed` + `mark_processed` **en la misma transacción** que el efecto. Lo usan `projection`, `iot_storage` e `iot_alert`. Un duplicado incrementa `worker_messages_duplicated_total{consumer}` y se descarta. La PK compuesta permite que cada consumidor procese el mismo evento una vez.
2. **Idempotencia natural** donde no hace falta tabla: `audit` (`audit_log.event_id` PK, `ON CONFLICT DO NOTHING`) y `cache` (`DEL` repetido es inocuo).
3. **`processed_commands(command_id = '<transfer_id>:<step>')`** en cada bodega (`transfers_service.py`) para los comandos de saga `reserve`, `release`, `commit-out`, `receive`. La fila se inserta en el mismo COMMIT que el movimiento; la fila de inventario se bloquea con `FOR UPDATE` **antes** de comprobar, así dos entregas simultáneas se serializan y la segunda responde `200 {"status":"already_applied"}` sin cambiar stock.
4. Adicionales: `POST /transfers` acepta cabecera `Idempotency-Key` (`transfers.idempotency_key` UNIQUE; misma key + mismo cuerpo => mismo `transfer_id`, distinto cuerpo => 409); el report-worker no repite un job ya `COMPLETED` y usa clave S3 determinista; el orquestador es una máquina de estados reanudable (un `PROCESS_TRANSFER` duplicado sobre una transferencia terminal no hace nada); la alerta IoT usa `event_id` determinista `ALR-<event_id lectura>`.

## Consecuencias / Trade-offs

- Garantiza efecto "exactly once" **sobre el estado**, aunque el transporte sea at-least-once.
- Cada mensaje cuesta una lectura+escritura extra; la tabla `processed_events` crece sin límite (no hay purga/TTL implementada).
- Requiere un `event_id` estable: se asigna al crear el evento (`EVT-<uuid4>`) y se conserva al republicar desde outbox; en IoT lo asigna el sensor.
- Dos réplicas procesando el mismo evento a la vez pueden chocar en la PK: se convierte en `TransientError` y el reintento ve el estado ya confirmado.

## Mitigación

- `tests/failure/04_duplicate_message.sh` publica dos veces el mismo `event_id` y comprueba 1 fila por consumidor en `processed_events` y 1 en `audit_log`.
- `tests/failure/06_saga_failures.sh` (Saga 6) reenvía `PROCESS_TRANSFER` de una transferencia `COMPLETED` y verifica que el stock no cambia.
- Mejora propuesta: purga de `processed_events` más antiguas que la ventana máxima de redelivery.
