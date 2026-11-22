# Eventos y mensajería (Entregable 5)

Fuente de verdad en código: `libs/wcommon/wcommon/rabbit.py` (topología, retry, DLQ) y `libs/wcommon/wcommon/events.py` (sobre del evento). Decisiones en ADR-003, ADR-004, ADR-005 y ADR-006.

## 1. Sobre común (evento y comando)

Todos los mensajes usan el mismo sobre (`build_event`):

```json
{
  "event_id": "EVT-6f1c0a7e-3b52-4e0e-9c63-2a1d8b7a9f10",
  "event_type": "INVENTORY_UPDATED",
  "event_version": 1,
  "occurred_at": "2026-09-28T15:00:00Z",
  "correlation_id": "COR-3f9a1c0b7d2e",
  "source": "warehouse-001",
  "payload": { }
}
```

| Campo | Obligatorio (`validate_event`) | Descripción |
|---|---|---|
| `event_id` | sí | `EVT-<uuid4>`. Único y estable: se conserva al republicar. Clave de idempotencia. En IoT lo asigna el sensor; en alertas es `ALR-<event_id de la lectura>` |
| `event_type` | sí | Nombre del hecho o comando |
| `event_version` | sí | Entero, hoy `1` en todos |
| `occurred_at` | sí | ISO 8601 UTC con `Z` (momento de construcción del evento) |
| `source` | sí | `warehouse-00X`, `central-api`, `central-orchestrator`, `central-report-worker`, `central-iot-alert-worker`, `iot-ingestion` |
| `payload` | sí (objeto) | Datos del evento |
| `correlation_id` | no obligatorio, siempre emitido | Se restaura en el contexto del consumidor |

Un mensaje al que le falte un campo obligatorio lanza `PermanentError` y va directo a la DLQ.

Propiedades AMQP: `delivery_mode=2` (persistente), `content_type=application/json`, `message_id=event_id`, `correlation_id`. Cabeceras añadidas por el consumidor: `x-retries`, `x-error`, `x-failed-queue`. Convención de ids de bodega: `WH-001` en datos, `wh-001` en routing keys y nombres de cola.

## 2. Exchanges

| Exchange | Tipo | Durable | Uso |
|---|---|---|---|
| `warehouse.events` | topic | sí | Hechos ocurridos (N consumidores) |
| `platform.commands` | direct | sí | Órdenes a un único consumidor |
| `""` (default) | direct | - | Interno: publicación en `Q.retry` y `Q.dlq` |

## 3. Colas, bindings y consumidores

| Cola | Exchange | Binding (routing key) | Consumidor | `prefetch` | Idempotencia |
|---|---|---|---|---|---|
| `q.cache.wh-001` | warehouse.events | `inventory.updated.wh-001` | `cache-worker` WH-001 (`workers.cache`) | 10 | `DEL` natural |
| `q.cache.wh-002` | warehouse.events | `inventory.updated.wh-002` | `cache-worker` WH-002 | 10 | `DEL` natural |
| `q.cache.wh-003` | warehouse.events | `inventory.updated.wh-003` | `cache-worker` WH-003 | 10 | `DEL` natural |
| `q.audit` | warehouse.events | `#` | `audit-worker` (`workers.audit`) | 50 | `audit_log.event_id` PK, `ON CONFLICT DO NOTHING` |
| `q.projection` | warehouse.events | `inventory.updated.*`, `product.created.*`, `warehouse.heartbeat.*` | `projection-worker` | 10 | `processed_events` (`projection`) + `occurred_at` |
| `q.transfers` | platform.commands | `transfer.process` | `transfer-orchestrator` | 1 | máquina de estados reanudable |
| `q.reports` | platform.commands | `report.generate` | `report-worker` | 1 | job `COMPLETED` no se repite |
| `q.iot.storage` | warehouse.events | `iot.reading.*` | `iot-storage-worker` | 50 | `processed_events` (`iot_storage`) + `sequence_number` |
| `q.iot.alerts` | warehouse.events | `iot.reading.*` | `iot-alert-worker` | 50 | `processed_events` (`iot_alert`) + `ALR-` determinista |

Eventos sin cola de negocio propia (solo los recibe `q.audit` por el binding `#`): `TRANSFER_STATUS_CHANGED`, `REPORT_COMPLETED`, `SENSOR_ALERT`, y también `SENSOR_READING`.

## 4. Reintentos y DLQ

Para cada cola `Q` existen:

| Cola | Argumentos | Función |
|---|---|---|
| `Q.retry` | `x-message-ttl=RETRY_DELAY_MS` (5000), `x-dead-letter-exchange=""`, `x-dead-letter-routing-key=Q` | espera y devuelve el mensaje a `Q` |
| `Q.dlq` | durable, sin consumidor | mensajes no procesables; se inspeccionan en `http://localhost:15672` |

```text
Q --(TransientError, x-retries < 3)--> Q.retry --(5 s TTL)--> Q
Q --(TransientError, x-retries = 3)--> Q.dlq
Q --(PermanentError / Exception / JSON inválido)--> Q.dlq
```

`MAX_RETRIES=3` y `RETRY_DELAY_MS=5000` se pueden sobrescribir por variable de entorno. Matriz detallada de qué error va a dónde en `docs/error-matrix.md`.

## 5. Contratos de eventos (exchange `warehouse.events`)

### 5.1 `PRODUCT_CREATED`

- Routing key: `product.created.<wh>` (p. ej. `product.created.wh-001`). Productor: `warehouse-api` vía outbox (también al crearse un producto por `receive` de una transferencia). Consumidores: `q.projection`, `q.audit`.

```json
{
  "event_id": "EVT-0b7d4c1e-58a9-4f2b-8d3e-1c5f9a2b7e44",
  "event_type": "PRODUCT_CREATED",
  "event_version": 1,
  "occurred_at": "2026-09-28T14:55:10Z",
  "correlation_id": "COR-a1b2c3d4e5f6",
  "source": "warehouse-001",
  "payload": {"warehouse_id": "WH-001", "sku": "P-001", "name": "Producto 1", "unit": "unit", "category": "general"}
}
```

Efecto en proyección: si no existe la fila `(sku, warehouse_id)` en `global_inventory`, la crea con cantidad 0.

### 5.2 `INVENTORY_UPDATED`

- Routing key: `inventory.updated.<wh>`. Productor: `warehouse-api` vía outbox en **cada** mutación de inventario (movimientos públicos y comandos de saga). Consumidores: `q.cache.<wh>`, `q.projection`, `q.audit`.
- `movement_type`: `RECEIPT | SHIPMENT | ADJUSTMENT | RETURN | RESERVATION | RELEASE | TRANSFER_OUT | TRANSFER_IN`. `quantity` y `reserved_quantity` son los valores **resultantes** (estado absoluto, no delta), lo que hace idempotente el upsert del read model.

```json
{
  "event_id": "EVT-9c2e7a10-4d6b-4b8e-a1f3-7e0d5c3b2a98",
  "event_type": "INVENTORY_UPDATED",
  "event_version": 1,
  "occurred_at": "2026-09-28T15:00:00Z",
  "correlation_id": "COR-3f9a1c0b7d2e",
  "source": "warehouse-001",
  "payload": {
    "warehouse_id": "WH-001", "sku": "P-001", "product_id": 1,
    "quantity": 120, "reserved_quantity": 0,
    "movement_type": "RECEIPT", "movement_quantity": 20, "reference": "PO-123"
  }
}
```

### 5.3 `SENSOR_READING`

- Routing key: `iot.reading.<wh>`. Productor: `iot-ingestion` (conserva el `event_id` del sensor). Consumidores: `q.iot.storage`, `q.iot.alerts`, `q.audit`.
- `payload.measured_at` es el instante de medición del sensor; `occurred_at` del sobre lo fija la ingestión.

```json
{
  "event_id": "EVT-S-WH001-T-014-000187",
  "event_type": "SENSOR_READING",
  "event_version": 1,
  "occurred_at": "2026-09-28T15:00:02Z",
  "correlation_id": "COR-77aa10bc22de",
  "source": "iot-ingestion",
  "payload": {
    "warehouse_id": "WH-001", "sensor_id": "T-014", "sensor_type": "temperature",
    "value": 9.4, "unit": "C", "sequence_number": 187, "measured_at": "2026-09-28T15:00:01Z"
  }
}
```

(El formato exacto del `event_id` lo decide el sensor; es una cadena no vacía.)

### 5.4 `SENSOR_ALERT`

- Routing key: `iot.alert.<wh>`. Productor: `iot-alert-worker` (publica antes del commit; `event_id` determinista `ALR-<event_id de la lectura>`). Consumidores: `q.audit`.
- Umbrales: temperatura > 8, humedad > 70, puerta abierta > 60 s (`door.value` = segundos abierta).

```json
{
  "event_id": "ALR-EVT-S-WH001-T-014-000187",
  "event_type": "SENSOR_ALERT",
  "event_version": 1,
  "occurred_at": "2026-09-28T15:00:03Z",
  "correlation_id": "COR-77aa10bc22de",
  "source": "central-iot-alert-worker",
  "payload": {
    "warehouse_id": "WH-001", "sensor_id": "T-014", "sensor_type": "temperature",
    "value": 9.4, "threshold": 8.0, "measured_at": "2026-09-28T15:00:01Z"
  }
}
```

### 5.5 `REPORT_COMPLETED`

- Routing key: `report.completed` (sin sufijo de bodega; **no** coincide con ningún binding de `q.projection`, solo `q.audit`). Productor: `report-worker`, best effort tras marcar el job `COMPLETED`.

```json
{
  "event_id": "EVT-5d0a3e9f-1b7c-4a62-9f8d-0c4e2b6a1d73",
  "event_type": "REPORT_COMPLETED",
  "event_version": 1,
  "occurred_at": "2026-09-28T15:02:11Z",
  "correlation_id": "COR-5e6f7a8b9c0d",
  "source": "central-report-worker",
  "payload": {"job_id": "JOB-1a2b3c4d", "s3_key": "reports/2026/09/JOB-1a2b3c4d.json"}
}
```

### 5.6 `TRANSFER_STATUS_CHANGED`

- Routing key: `transfer.status.<status en minúsculas>` (`transfer.status.requested` ... `transfer.status.compensating`, `transfer.status.cancelled`, `transfer.status.failed`, `transfer.status.completed`). Productor: `transfer-orchestrator`, best effort **después** de persistir cada transición. Consumidores: `q.audit`.

```json
{
  "event_id": "EVT-c4e1b2a0-7d3f-4e58-b6a9-3f2d1c0e9b87",
  "event_type": "TRANSFER_STATUS_CHANGED",
  "event_version": 1,
  "occurred_at": "2026-09-28T15:05:00Z",
  "correlation_id": "COR-9d8c7b6a5f4e",
  "source": "central-orchestrator",
  "payload": {
    "transfer_id": "TR-8f3a1c2d", "sku": "P-001",
    "source_warehouse": "WH-001", "destination_warehouse": "WH-002",
    "quantity": 30, "status": "CANCELLED",
    "reason": "destination unavailable after 3 retries: WH-002 no disponible (ConnectError)"
  }
}
```

### 5.7 `WAREHOUSE_HEARTBEAT`

- **Routing key:** `warehouse.heartbeat.<wh>` (p. ej. `warehouse.heartbeat.wh-001`).
- **Productor:** el outbox relay de cada bodega, cada 15 s, y solo si su base de datos respondió en ese ciclo. No pasa por la tabla outbox, porque es una señal efímera: perder uno no importa.
- **Consumidor:** `projection-worker` (`q.projection`). Actualiza `warehouse_status.last_event_at` sin registrar `processed_events`; es idempotente porque solo avanza la marca de tiempo. `audit-worker` lo ignora.
- **Para qué sirve:** que `stale` signifique "sin señales de la bodega" y no "sin movimientos de inventario".

```json
{
  "event_id": "EVT-…",
  "event_type": "WAREHOUSE_HEARTBEAT",
  "event_version": 1,
  "occurred_at": "2026-11-18T17:08:39Z",
  "correlation_id": "COR-…",
  "source": "warehouse-001",
  "payload": { "warehouse_id": "WH-001" }
}
```

## 6. Contratos de comandos (exchange `platform.commands`)

Mismo sobre; `event_type` = nombre del comando.

### 6.1 `PROCESS_TRANSFER`

- Routing key: `transfer.process`. Cola: `q.transfers`. Productor: `central-api` (tras el commit de `transfers`; re-publica en un replay idempotente si sigue `REQUESTED`). Consumidor: `transfer-orchestrator`. Contiene solo el id: el orquestador lee el resto de `central_read.transfers`.

```json
{
  "event_id": "EVT-e7f2a1b0-3c4d-4e5f-8a9b-0c1d2e3f4a5b",
  "event_type": "PROCESS_TRANSFER",
  "event_version": 1,
  "occurred_at": "2026-09-28T15:04:58Z",
  "correlation_id": "COR-9d8c7b6a5f4e",
  "source": "central-api",
  "payload": {"transfer_id": "TR-8f3a1c2d"}
}
```

### 6.2 `GENERATE_REPORT`

- Routing key: `report.generate`. Cola: `q.reports`. Productor: `central-api` (`POST /reports`). Consumidor: `report-worker`. `report_type` soportado: `inventory_snapshot`.

```json
{
  "event_id": "EVT-1f2e3d4c-5b6a-4978-8e0f-1a2b3c4d5e6f",
  "event_type": "GENERATE_REPORT",
  "event_version": 1,
  "occurred_at": "2026-09-28T15:02:00Z",
  "correlation_id": "COR-5e6f7a8b9c0d",
  "source": "central-api",
  "payload": {"job_id": "JOB-1a2b3c4d", "report_type": "inventory_snapshot"}
}
```

## 7. Garantías de entrega y orden

| Propiedad | Garantía |
|---|---|
| Entrega | At-least-once (mensajes persistentes, colas durables, publisher confirms, outbox). Pueden existir duplicados |
| Orden | **No garantizado** (reintentos reencolan al final; varios relays/consumidores). Los consumidores no dependen del orden: read model compara `occurred_at`; IoT compara `sequence_number`; los eventos `INVENTORY_UPDATED` llevan estado absoluto |
| Duplicados | Descartados por `event_id` (`processed_events` o PK) |
| Publicación desde bodegas | Transactional Outbox (ADR-004) |
| Publicación desde central/IoT | Directa, best effort; la fuente de verdad es la tabla propia |

## 8. Versionamiento (`event_version`) y compatibilidad

Estado actual: todos los eventos y comandos usan `event_version = 1`. **Ningún consumidor ramifica por versión todavía**; las reglas siguientes son la política acordada para evolucionar los contratos.

- **Cambios compatibles hacia atrás (no suben versión):** añadir campos opcionales al `payload`; los consumidores deben **ignorar campos desconocidos** (hoy leen solo las claves que necesitan, p. ej. `payload["sku"]`, `payload.get("reserved_quantity", 0)`).
- **Cambios incompatibles (suben `event_version`):** renombrar o eliminar campos, cambiar tipo o semántica (p. ej. de estado absoluto a delta). Se publica la versión nueva **sin cambiar el routing key**; el consumidor debe aceptar `N` y `N-1` durante la migración.
- **Orden de despliegue:** primero consumidores (que toleran N y N+1), después productores. Con mensajes ya en cola (`Q`, `Q.retry`, `Q.dlq`) pueden convivir versiones distintas, por eso los consumidores deben mantener compatibilidad hasta vaciar las colas.
- **Nuevos tipos de evento:** se añaden con un nuevo routing key; los bindings con comodín (`#`, `inventory.updated.*`) pueden recibirlos, por lo que los consumidores **ignoran tipos no soportados** (la proyección devuelve `ignored`; el orquestador y el reporte rechazan comandos inesperados como permanentes).
- **Campos obligatorios del sobre** (`event_id, event_type, event_version, occurred_at, source, payload`) no se pueden eliminar sin una versión mayor del sobre.
