# ADR-016 — Colas de respaldo ante la caída del broker y resincronización de bodegas

**Estado:** Aceptada

## Contexto

Requisitos:

- "Si en algún momento se cae la conexión, por cualquier motivo, cuando se reanude hay que enviar los mensajes pendientes y sincronizar la bodega."
- "Si se cae el broker, cuando se reanude deben seguir los mensajes."
- "Debe implementarse una cola en caso de que falle el broker para no parar el servicio."

Antes de este cambio las bodegas ya tenían outbox (ADR-004), pero la central rechazaba con `503` las transferencias y los reportes si el broker no respondía, y no había forma de corregir el read model de la central si un evento se perdía (p. ej. en DLQ).

## Alternativas

1. **Cola secundaria en SQS** cuando falla RabbitMQ: añade otro sistema y otra ruta de entrega que también hay que vigilar, y las bodegas tendrían que hablar con SQS.
2. **Reintentar en memoria** en la API: se pierde todo si el proceso se reinicia.
3. **Colas durables en PostgreSQL a ambos lados (outbox) + snapshot de resincronización** (elegida).

## Decisión

| Pieza | Dónde | Qué hace |
|---|---|---|
| Cola local de la bodega | tabla `outbox` (PostgreSQL de la bodega) | Cada cambio guarda su evento en la misma transacción. Si el broker o la red caen, la bodega **sigue aceptando operaciones** y los eventos se acumulan. `GET /sync/status` muestra `outbox_pending`, `oldest_pending_at` y `last_published_at` |
| Envío de pendientes | `outbox relay` (2 réplicas, `SKIP LOCKED`) | Reintenta con backoff exponencial (hasta 30 s). Al reconectar publica los pendientes **en orden** y registra `outbox_rabbit_connected` con `pending_events` y `downtime_seconds` |
| Resincronización | `INVENTORY_SNAPSHOT` (`app/sync.py`) | Tras arrancar o reconectar, el relay encola el inventario completo **detrás** de los pendientes (no se duplica si ya hay uno pendiente). La central aplica last-write-wins por SKU: corrige divergencias y nunca pisa un dato más nuevo. También bajo demanda: `POST /sync/snapshot` o, desde la central, `POST /warehouses/{id}/resync` |
| Versión exacta | `inventory.updated_at == occurred_at` | El evento y la fila comparten el mismo instante; si no, la central descartaría el snapshot como "viejo" (bug encontrado en la prueba end-to-end y cubierto con un test) |
| Cola de respaldo de la central | tabla `command_outbox` (RDS central) | Si `PROCESS_TRANSFER` o `GENERATE_REPORT` no se pueden publicar, el comando se guarda y la API responde `202` con `"delivery": "queued"` en lugar de `503`. Solo responde `503` si también falla la base de datos |
| Envío de comandos | servicio ECS `command-relay` (`python -m app.command_relay`) | Publica en orden los comandos encolados cuando vuelve el broker. `GET /health` muestra `pending_commands` |
| Persistencia del broker | RabbitMQ en ECS + EFS | Colas durables + `delivery_mode=2` + datos en EFS con nombre de nodo fijo: lo que estaba en cola sigue allí después de reiniciar la tarea |
| Visibilidad | heartbeat del relay | Cada 15 s envía `outbox_pending`/`oldest_pending_at`. La central lo guarda en `warehouse_sync` y lo muestra en `GET /reports/warehouses` (`in_sync`, `OFFLINE`, backlog) |

Flujo de una caída del broker:

```text
t0  broker cae ─┬─ bodega: movimientos 201, outbox_pending crece
                └─ central: POST /transfers 202 {"delivery":"queued"}, pending_commands crece
t1  broker vuelve (mismos datos en EFS)
    ├─ relay de cada bodega: publica pendientes en orden -> INVENTORY_SNAPSHOT
    ├─ command-relay: publica PROCESS_TRANSFER -> la saga continúa
    └─ projection: aplica eventos + snapshot -> read model == bodegas
```

## Consecuencias / Trade-offs

- Ninguna operación se rechaza por la caída del broker; la latencia de sincronización crece mientras dura la caída (consistencia eventual, ya asumida en ADR-008).
- Entrega **al menos una vez**: un comando puede publicarse dos veces (relay + replay de idempotencia). El orquestador y los consumidores ya son idempotentes (ADR-006).
- Los snapshots aumentan el tráfico tras cada reconexión (500 SKU por mensaje). Se puede desactivar con `RESYNC_ON_RECONNECT=false`.
- Si se cae la EC2 de una bodega, sus eventos pendientes quedan en su disco (EBS) hasta que vuelva; no se pierden, pero tampoco llegan antes.
- No hay purga periódica de `outbox` ni de `command_outbox` (ya era una limitación conocida).

## Verificación

- Unitarias: `tests/unit/warehouse_api/test_wh_sync.py`, `tests/unit/central/test_warehouses_sync_reports.py`, `test_central_api.py::test_broker_down_queues_command_and_relay_flushes`.
- Local (Docker): `tests/failure/08_broker_down_sync.sh` (broker caído, 5 movimientos aceptados, transferencia encolada, divergencia corregida por el snapshot).
- AWS: `scripts/aws_verify.sh --chaos` (detiene el servicio ECS del broker, verifica las dos colas, lo reinicia y comprueba que los mensajes en cola sobreviven al reinicio).
