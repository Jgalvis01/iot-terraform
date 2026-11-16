# Contratos de diseño (fuente de verdad para todos los servicios)

Este documento fija las decisiones transversales. Todo servicio DEBE respetarlo. Si algo no está aquí, decide lo más simple y documéntalo en el README del servicio.

## 0. Convenciones generales

- Python 3.11, FastAPI, SQLAlchemy 2, Pydantic 2, `pika` para RabbitMQ, `redis` (redis-py), `boto3`, `prometheus-client`.
- **Identidad global de producto = `sku`** (string, p.ej. `P-001`). Los `id` enteros son locales a cada bodega. Eventos, transferencias y read model usan `sku`.
- IDs de bodega: `WH-001`, `WH-002`, `WH-003` (mayúsculas en datos; en nombres de servicio/cola en minúsculas: `wh-001`).
- Todas las tablas se crean con `Base.metadata.create_all` al arrancar **y** se documentan en `db/schema.sql` (warehouse) y `db/central_schema.sql` (central).
- Logs: JSON en una línea por evento a stdout, campos mínimos: `timestamp, service, level, event, correlation_id` + contexto (`warehouse_id, sku, transfer_id, event_id`...).
- Configuración solo por variables de entorno (nunca secretos en código). Cada servicio tiene `.env.example`.

## 1. Librería común `libs/wcommon` (paquete Python instalable)

Ubicación: `libs/wcommon/` con `pyproject.toml` (nombre `wcommon`). Las imágenes se construyen con **contexto = raíz del repo** y hacen `pip install ./libs/wcommon`.

Módulos y API pública (no cambiar firmas):

```python
# wcommon/correlation.py
correlation_id_var: ContextVar[str]
def get_correlation_id() -> str            # genera "COR-<uuid4 hex[:12]>" si no hay
def set_correlation_id(value: str | None) -> str
class CorrelationIdMiddleware               # Starlette middleware: lee/crea header X-Correlation-ID, lo devuelve en la respuesta

# wcommon/logging.py
def configure_logging(service: str) -> None   # JSON a stdout, incluye correlation_id automáticamente
def log(event: str, level: str = "INFO", **fields) -> None

# wcommon/events.py
def build_event(event_type: str, source: str, payload: dict, event_version: int = 1,
                correlation_id: str | None = None, event_id: str | None = None) -> dict
# -> {"event_id": "EVT-<uuid4>", "event_type", "event_version", "occurred_at" (ISO8601 Z),
#     "correlation_id", "source", "payload"}
def validate_event(msg: dict) -> None          # lanza PermanentError si faltan campos obligatorios

# wcommon/errors.py
class TransientError(Exception)   # reintentable (timeout, conexión, 5xx)
class PermanentError(Exception)   # no reintentable (schema inválido, producto inexistente, JSON corrupto)

# wcommon/rabbit.py
EVENTS_EXCHANGE = "warehouse.events"      # topic, durable
COMMANDS_EXCHANGE = "platform.commands"   # direct, durable
MAX_RETRIES = 3
RETRY_DELAY_MS = 5000
def connect(url: str | None = None) -> pika.BlockingConnection   # usa RABBITMQ_URL, reintenta conexión con backoff
def declare_topology(channel) -> None   # declara exchanges + TODAS las colas de la sección 3 (idempotente)
def publish(channel, exchange: str, routing_key: str, message: dict) -> None
    # persistente (delivery_mode=2), content_type json, header correlation_id, publisher confirms
def consume(queue: str, handler: Callable[[dict], None], consumer_name: str, prefetch: int = 10) -> None
    # loop bloqueante. Para cada mensaje:
    #  - parsea JSON (si falla -> DLQ directo)
    #  - set_correlation_id(msg.get("correlation_id"))
    #  - handler(msg)
    #  - éxito -> ack
    #  - TransientError -> si x-retries < MAX_RETRIES: publica a "<queue>.retry" con header x-retries+1, ack
    #                      si no -> publica a "<queue>.dlq" con headers x-error, x-retries; ack
    #  - PermanentError o cualquier otra Exception -> "<queue>.dlq"; ack
    #  - métricas Prometheus: worker_messages_processed_total{consumer}, worker_messages_failed_total{consumer,kind},
    #    worker_messages_retried_total{consumer}, worker_messages_dlq_total{consumer}, worker_processing_seconds{consumer}

# wcommon/metrics.py
def start_metrics_server(port: int = 9100) -> None   # prometheus_client.start_http_server

# wcommon/idempotency.py  (SQLAlchemy)
def already_processed(session, event_id: str, consumer: str) -> bool
def mark_processed(session, event_id: str, consumer: str) -> None
# tabla processed_events(event_id TEXT, consumer TEXT, processed_at TIMESTAMPTZ, PK(event_id, consumer))
# Se inserta EN LA MISMA transacción que el efecto del consumidor.
ProcessedEvent  # modelo declarativo; cada servicio llama ensure_tables(engine)
def ensure_tables(engine) -> None
```

## 2. Contrato de eventos

```json
{
  "event_id": "EVT-…", "event_type": "INVENTORY_UPDATED", "event_version": 1,
  "occurred_at": "2026-09-28T15:00:00Z", "correlation_id": "COR-…", "source": "warehouse-001",
  "payload": { ... }
}
```

| event_type | routing key (exchange `warehouse.events`) | payload |
|---|---|---|
| `PRODUCT_CREATED` | `product.created.<wh>` | `warehouse_id, sku, name, unit, category` |
| `INVENTORY_UPDATED` | `inventory.updated.<wh>` | `warehouse_id, sku, product_id, quantity, reserved_quantity, movement_type, movement_quantity, reference` |
| `SENSOR_READING` | `iot.reading.<wh>` | `warehouse_id, sensor_id, sensor_type, value, unit, sequence_number, measured_at` |
| `SENSOR_ALERT` | `iot.alert.<wh>` | `warehouse_id, sensor_id, sensor_type, value, threshold, measured_at` |
| `REPORT_COMPLETED` | `report.completed` | `job_id, s3_key` |
| `WAREHOUSE_HEARTBEAT` | `warehouse.heartbeat.<wh>` | `warehouse_id` (cada 15 s desde el outbox relay; la proyección actualiza `warehouse_status` para que `stale` signifique "sin señales", no "sin movimientos") |
| `TRANSFER_STATUS_CHANGED` | `transfer.status.<status lower>` | `transfer_id, sku, source_warehouse, destination_warehouse, quantity, status, reason` |

`<wh>` = `wh-001` etc. (minúsculas).

Comandos (exchange `platform.commands`, direct):

| routing key | mensaje (mismo sobre que evento; event_type = nombre comando) |
|---|---|
| `transfer.process` | `PROCESS_TRANSFER` payload `{transfer_id}` |
| `report.generate` | `GENERATE_REPORT` payload `{job_id, report_type}` |

## 3. Topología RabbitMQ (declarada por `declare_topology`)

Para cada cola `Q` existen además `Q.retry` (args `x-message-ttl=RETRY_DELAY_MS`, `x-dead-letter-exchange=""`, `x-dead-letter-routing-key=Q`) y `Q.dlq` (sin consumidor; se inspecciona en la UI). Todas durables.

| Cola | Exchange | Binding | Consumidor |
|---|---|---|---|
| `q.cache.wh-001` / `wh-002` / `wh-003` | warehouse.events | `inventory.updated.wh-00X` | cache-worker de esa bodega (invalidación asíncrona, estrategia B) |
| `q.audit` | warehouse.events | `#` | audit-worker (guarda en tabla `audit_log` de central) |
| `q.projection` | warehouse.events | `inventory.updated.*`, `product.created.*`, `warehouse.heartbeat.*` | projection-worker (read model) |
| `q.transfers` | platform.commands | `transfer.process` | transfer-orchestrator |
| `q.reports` | platform.commands | `report.generate` | report-worker |
| `q.iot.storage` | warehouse.events | `iot.reading.*` | iot-storage-worker |
| `q.iot.alerts` | warehouse.events | `iot.reading.*` | iot-alert-worker (publica `SENSOR_ALERT`) |

## 4. Warehouse API (`services/warehouse-api`)

Puerto interno 8000. Env: `WAREHOUSE_ID`, `DATABASE_URL`, `REDIS_URL`, `CACHE_ENABLED` (true/false), `CACHE_TTL_SECONDS` (30), `RABBITMQ_URL`, `EVENTS_ENABLED` (true/false), `SERVICE_NAME`.

Endpoints:
```
GET    /health                         -> {"status":"ok","warehouse_id":..., "checks":{"db":"ok","redis":"ok|down|disabled"}}  (503 si DB caída)
GET    /metrics                        -> Prometheus (instrumentator + cache_hits_total, cache_misses_total, cache_errors_total)
POST   /products                       (crea product + inventory 0 + outbox PRODUCT_CREATED, 1 tx)
GET    /products ; GET /products/{id} ; PUT /products/{id} ; DELETE /products/{id} (409 si quantity>0 o reserved>0)
GET    /products/by-sku/{sku}
GET    /inventory/{product_id}         (Cache Aside)
GET    /inventory/by-sku/{sku}         (Cache Aside)
POST   /inventory/movements            (body: product_id | sku, type, quantity, reference) RECEIPT|SHIPMENT|ADJUSTMENT|RETURN|RESERVATION|RELEASE
GET    /inventory/{product_id}/movements
# Comandos de saga — idempotentes por (transfer_id, step) en tabla processed_commands
POST   /transfers/{transfer_id}/reserve     body {sku, quantity}             -> mueve quantity -> reserved (RESERVATION, reference=transfer_id)
POST   /transfers/{transfer_id}/release     body {sku, quantity}             -> reserved -> quantity (RELEASE)        (compensación)
POST   /transfers/{transfer_id}/commit-out  body {sku, quantity}             -> reserved -= quantity (TRANSFER_OUT)
POST   /transfers/{transfer_id}/receive     body {sku, quantity, name, unit, category} -> crea producto si no existe; quantity += (TRANSFER_IN)
```
Respuestas de comandos de saga: 200 `{"status":"applied"|"already_applied", ...inventory}`; 409 `{"detail": "...", "code":"INSUFFICIENT_STOCK"}` (error de negocio permanente); 404 producto inexistente (permanente).

Reglas:
- Toda mutación de inventario: `SELECT … FOR UPDATE`, valida, actualiza, inserta movimiento, inserta fila `outbox` (evento INVENTORY_UPDATED), inserta `processed_commands` si es comando de saga → **un solo COMMIT**. Rollback ante error.
- `ADJUSTMENT`: quantity puede ser +/- vía campo `quantity` siempre >0 y `direction` ("IN"/"OUT")? → Simplificar: ADJUSTMENT aplica `quantity` como nuevo valor absoluto (conteo físico). Nunca negativo.
- Después del commit: invalidación síncrona `DEL warehouse:{WH}:inventory:{product_id}` y `…:sku:{sku}` (estrategia A). Errores de Redis se registran y se ignoran.
- Cache Aside con fallback: si Redis lanza excepción → contar `cache_errors_total`, leer de PostgreSQL. Timeout de socket Redis 0.2 s.
- Clave: `warehouse:{WH}:inventory:{product_id}` y `warehouse:{WH}:inventory:sku:{sku}`.

Outbox:
- Tabla `outbox(id BIGSERIAL, event_id TEXT UNIQUE, routing_key TEXT, payload JSONB, created_at, published_at NULL, attempts INT)`.
- Proceso separado `python -m app.outbox_relay` (mismo imagen, otro command): cada 1 s toma hasta 100 filas `published_at IS NULL` con `FOR UPDATE SKIP LOCKED`, publica con confirms, marca `published_at`. Si RabbitMQ está caído: log + backoff; las filas esperan (no se pierden eventos, la API sigue aceptando escrituras).
- Si `EVENTS_ENABLED=false` no se escribe outbox (fase 1/2).

## 5. Central (`services/central-api` + workers)

DB `central_read` (Postgres propio). Env: `DATABASE_URL`, `RABBITMQ_URL`, `WAREHOUSES` = `WH-001=http://wh-001-api:8000,WH-002=http://wh-002-lb:80,WH-003=http://wh-003-api:8000`, `S3_BUCKET`, `S3_ENDPOINT_URL` (vacío en AWS, `http://s3:9090` local (S3Mock)), `AWS_REGION`, `STALE_AFTER_SECONDS` (60).

Tablas: `transfers`, `global_inventory(sku, warehouse_id, quantity, reserved_quantity, last_event_id, last_event_at, PK(sku,warehouse_id))`, `warehouse_status(warehouse_id PK, last_event_at)`, `processed_events`, `report_jobs(job_id, report_type, status, s3_key, error, created_at, updated_at)`, `audit_log(event_id PK, event_type, source, correlation_id, payload JSONB, received_at)`, `batch_runs(run_id, run_date, status, s3_key, started_at, finished_at, error)`.

Endpoints central-api (puerto 8000):
```
GET  /health ; GET /metrics
POST /transfers {sku, source_warehouse, destination_warehouse, quantity}  -> 202 {"transfer_id":"TR-<uuid8>","status":"PROCESSING"} ; header Idempotency-Key opcional (misma key => mismo transfer)
GET  /transfers/{id} ; GET /transfers
GET  /global/inventory/{sku}?mode=readmodel|fanout
     readmodel -> {"sku", "total", "warehouses":[{warehouse_id, quantity, reserved_quantity, last_event_at, stale: bool}], "stale_warehouses":[...], "mode":"readmodel"}
     fanout    -> consulta GET /inventory/by-sku/{sku} en cada bodega con timeout 2s; bodegas que fallan van en "unavailable_warehouses"
GET  /global/inventory  (lista por sku)
POST /reports {report_type:"inventory_snapshot"} -> 202 {"job_id":"JOB-<uuid8>","status":"PROCESSING"}
GET  /reports/jobs/{job_id}
GET  /audit?limit=50
```

### Saga de transferencia (orquestación)

Estados (columna `status`): `REQUESTED → RESERVING_SOURCE → SOURCE_RESERVED → IN_TRANSIT → DESTINATION_PENDING → DESTINATION_RECEIVED → COMPLETED`; fallo: `COMPENSATING → CANCELLED` (reserva liberada) o `FAILED` (no se llegó a reservar). Columnas extra: `reason`, `attempts`, `correlation_id`, `history JSONB` (lista de {status, at}).

Orquestador (`transfer-orchestrator`, consume `q.transfers`): al recibir `PROCESS_TRANSFER{transfer_id}` carga el transfer y **avanza desde su estado actual** (máquina de estados reanudable ⇒ redelivery seguro). Cada transición se persiste (commit) antes del siguiente paso y publica `TRANSFER_STATUS_CHANGED`.

1. REQUESTED → RESERVING_SOURCE → POST source `/transfers/{id}/reserve`. 409/404 ⇒ `FAILED` (sin compensación). Error de red/5xx ⇒ `TransientError` (retry por cola; tras MAX_RETRIES ⇒ DLQ y queda `FAILED` con reason "source unavailable" — el orquestador marca FAILED antes de lanzar el último error).
2. SOURCE_RESERVED → IN_TRANSIT → DESTINATION_PENDING → POST destination `/transfers/{id}/receive`. Error permanente ⇒ COMPENSATING. Transitorio ⇒ retry; al agotar reintentos ⇒ COMPENSATING.
3. COMPENSATING → POST source `/release` (reintentable indefinidamente vía retry; si agota ⇒ DLQ, queda COMPENSATING para intervención manual) → `CANCELLED`.
4. DESTINATION_RECEIVED → POST source `/commit-out` → `COMPLETED`.
- El número de intento viene del header `x-retries` (el handler puede leerlo vía `msg["_retries"]` que `consume` inyecta).
- Inyección de fallos para demos: env `FAIL_INJECTION` = `none|before_reserve|after_reserve|destination_down` y transferencias con `quantity` normal; además el header del POST /transfers `X-Fail-Step` sobreescribe para esa transferencia (se guarda en columna `fail_step`).

### Projection worker
Consume `q.projection`. Idempotente con `processed_events` (consumer=`projection`). Upsert en `global_inventory` sólo si `occurred_at` ≥ `last_event_at` existente (descarta eventos viejos fuera de orden). Actualiza `warehouse_status`.

### Audit worker
Consume `q.audit`, inserta en `audit_log` (`ON CONFLICT DO NOTHING` ⇒ idempotente).

### Cache worker (uno por bodega)
Env `WAREHOUSE_ID`, `REDIS_URL`. Consume `q.cache.wh-00X`, hace `DEL` de las dos claves. Demuestra estrategia B.

### Report worker
Consume `q.reports`, genera JSON/CSV desde `global_inventory`, sube a `s3://$S3_BUCKET/reports/{yyyy}/{mm}/{job_id}.json`, actualiza `report_jobs`, publica `REPORT_COMPLETED`.

## 6. Batch (`services/batch`)
`python -m batch.daily_report --date YYYY-MM-DD` : consulta `GET /inventory/...` NO; lee movimientos del día de cada bodega vía endpoint `GET /inventory/movements?date=YYYY-MM-DD` (añadir en warehouse-api) → genera reporte consolidado → `s3://bucket/reports/daily/{date}/movements-{date}.json` y por bodega `warehouses/{WH}/{yyyy}/{mm}/inventory-{date}.json` → registra en `batch_runs`. Reejecutable: misma clave S3 (sobrescribe) y upsert por `run_date`.

## 7. Lambda (`services/lambda/s3_processor`)
Trigger `s3:ObjectCreated:*` prefijo `exports/`. Valida JSON/CSV (campos `sku, warehouse_id, quantity`), transforma a JSON normalizado y escribe `processed/<nombre>.json` + `processed/<nombre>.meta.json` (`{source_key, rows, valid_rows, invalid_rows, processed_at}`); inválido ⇒ `rejected/<nombre>` + log de error. Logs JSON a CloudWatch. Sin dependencias fuera de boto3.

## 8. IoT (`services/iot`)
- `mosquitto` (eclipse-mosquitto:2, puerto 1883, anon permitido en dev).
- `simulator`: N sensores (env `SENSORS=100`, `WAREHOUSES=WH-001,…,WH-005`, `INTERVAL_SECONDS=2`, `DUPLICATE_RATE=0.05`, `OUT_OF_ORDER_RATE=0.05`); publica en `warehouses/{WH}/sensors/{SENSOR_ID}` QoS 1 el JSON del README §24 (+ `sensor_type`, `unit`, `timestamp`). Buffer local en memoria (deque) si desconectado; vacía al reconectar.
- `ingestion`: suscribe `warehouses/+/sensors/+`, valida, publica `SENSOR_READING` a RabbitMQ conservando `event_id` del sensor.
- `iot-storage-worker`: idempotente por `event_id`, guarda en `sensor_readings` (central DB); descarta lecturas con `sequence_number` ≤ último por sensor (registra `out_of_order_total`).
- `iot-alert-worker`: umbrales (temperatura > 8 °C, humedad > 70 %, puerta abierta > N) ⇒ publica `SENSOR_ALERT` y guarda en `sensor_alerts`.

## 9. Puertos locales (compose)

| Servicio | Host:puerto |
|---|---|
| wh-001-api | 8001 |
| wh-002-lb (nginx → 3 réplicas) | 8002 |
| wh-003-api | 8003 |
| central-api | 8000 |
| frontend (WH-001) | 8080 |
| RabbitMQ UI | 15672 (guest/guest solo local) |
| Prometheus | 9090 |
| Grafana | 3000 |
| S3 local (S3Mock) | 9000 |
| Mosquitto | 1883 |

Nombres de servicio en compose: `wh-001-db, wh-001-redis, wh-001-api, wh-001-outbox, wh-001-cache-worker` (idem 002/003; `wh-002-api-1/2/3` (réplicas explícitas; -2 y -3 en el profile `lb`) + `wh-002-lb`), `rabbitmq, central-db, central-api, transfer-orchestrator, projection-worker, audit-worker, report-worker, s3 (emulador S3Mock), prometheus, grafana, redis-exporter-*, postgres-exporter, mosquitto, iot-simulator, iot-ingestion, iot-storage-worker, iot-alert-worker`.

Profiles compose: (sin profile) = bodegas (db, redis, api); `events` = rabbitmq + outbox + cache-workers; `central` = central-db, central-api, orchestrator, projection, audit, report, s3 (S3Mock); `obs`; `iot`; `frontend`.

## 10. Imágenes (ECR / build)

Todas con contexto raíz del repo:
| Imagen | Dockerfile | Command(s) |
|---|---|---|
| `warehouse-api` | services/warehouse-api/Dockerfile | `uvicorn main:app` / `python -m app.outbox_relay` |
| `central-api` | services/central-api/Dockerfile | `uvicorn main:app` |
| `workers` | services/workers/Dockerfile | `python -m workers.<name>` (transfer_orchestrator, projection, audit, cache, report, iot_storage, iot_alert) |
| `batch` | services/batch/Dockerfile | `python -m batch.daily_report` |
| `iot` | services/iot/Dockerfile | `python -m iot.simulator` / `python -m iot.ingestion` |
| `frontend` | services/frontend/Dockerfile | nginx |
