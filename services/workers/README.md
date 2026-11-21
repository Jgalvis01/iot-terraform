# workers (central)

Una imagen, varios procesos: `python -m workers.<name>`. Todos usan `wcommon` (logs JSON,
`consume()` con retry/DLQ, idempotencia) y exponen métricas Prometheus en `:9100` (`METRICS_PORT`).

```bash
docker build -f services/workers/Dockerfile -t workers .     # desde la RAÍZ del repo
docker run --env-file services/workers/.env.example workers python -m workers.projection
```

| Módulo | Cola | Qué hace |
|---|---|---|
| `transfer_orchestrator` | `q.transfers` | Saga de transferencia (abajo). prefetch=1 |
| `projection` | `q.projection` | Upsert `global_inventory` + `warehouse_status`; idempotente (`processed_events`, consumer `projection`); descarta eventos con `occurred_at` < `last_event_at` (`projection_out_of_order_total`) |
| `audit` | `q.audit` | `INSERT ... ON CONFLICT DO NOTHING` en `audit_log` |
| `cache` | `q.cache.<wh>` | Env `WAREHOUSE_ID`, `REDIS_URL`; `DEL warehouse:{WH}:inventory:{product_id}` y `...:sku:{sku}` (estrategia B) |
| `report` | `q.reports` | Snapshot JSON de `global_inventory` → `s3://$S3_BUCKET/reports/{yyyy}/{mm}/{job_id}.json` (`S3_ENDPOINT_URL` = S3Mock local; crea el bucket sólo en local), actualiza `report_jobs`, publica `REPORT_COMPLETED` |
| `iot_storage` | `q.iot.storage` | Idempotente por `event_id`; descarta `sequence_number` ≤ último por (bodega, sensor) (`sensor_state`, métrica `iot_out_of_order_total`); guarda `sensor_readings` |
| `iot_alert` | `q.iot.alerts` | Umbrales `TEMP_MAX_C`=8, `HUMIDITY_MAX_PCT`=70, `DOOR_OPEN_MAX_SECONDS`=60 (door: value = segundos abierta); guarda `sensor_alerts` y publica `SENSOR_ALERT` (`iot.alert.<wh>`, event_id determinista `ALR-<event_id>`) |

## Saga de transferencia

```
 REQUESTED ─► RESERVING_SOURCE ──reserve ok──► SOURCE_RESERVED ─► IN_TRANSIT ─► DESTINATION_PENDING
                  │                                  │                              │
                  │ 409/404, o transitorio           │ fallo (after_reserve)        │ receive ok
                  │ con reintentos agotados          ▼                              ▼
                  ▼                             COMPENSATING ◄── 409/404, o ── DESTINATION_RECEIVED
               FAILED                                │      reintentos agotados      │ commit-out ok
          (nada que compensar)                       │ release ok                    ▼
                                                     ▼                           COMPLETED
                                                 CANCELLED
```

Cada paso: `SELECT ... FOR UPDATE` del transfer → llamada HTTP idempotente a la bodega (httpx, timeout
3 s) → transición + `history` → **COMMIT** → `TRANSFER_STATUS_CHANGED` (`transfer.status.<status>`).
Un `PROCESS_TRANSFER` duplicado o redelivered continúa desde el estado persistido: nunca repite pasos
ya confirmados (y la bodega además deduplica por `(transfer_id, step)`). Estados terminales ⇒ no-op.

Errores: timeout/conexión/5xx ⇒ `TransientError` (retry por cola `q.transfers.retry`); 404/409 ⇒
permanente para ese paso. El intento actual es `msg["_retries"]` (header `x-retries`) vs `MAX_RETRIES`.
Cada intento fallido incrementa `attempts` y añade `{"event":"attempt_failed"}` al `history`.

| Paso fallido | Error permanente (404/409) | Transitorio, reintentos agotados | Compensación |
|---|---|---|---|
| reserve (origen) | `FAILED` | `FAILED` ("source unavailable") y el mensaje va a DLQ | ninguna (no se reservó) |
| fallo inyectado `after_reserve` | `COMPENSATING` | — | `release` en origen → `CANCELLED` |
| get product (origen) / receive (destino) | `COMPENSATING` | `COMPENSATING` (en la misma ejecución) | `release` en origen → `CANCELLED` |
| release (compensación) | queda `COMPENSATING`, DLQ (manual) | queda `COMPENSATING`, DLQ (manual) | — |
| commit-out (origen) | queda `DESTINATION_RECEIVED`, DLQ (manual) | ídem | no se compensa: el destino ya recibió |

Inyección de fallos: env `FAIL_INJECTION` y, por transferencia, header `X-Fail-Step` (columna
`fail_step`, tiene prioridad; `none` explícito anula el env). `before_reserve` y `destination_down`
simulan un error transitorio **sin** llamar a la bodega (demuestran retry → FAILED / COMPENSATING);
`after_reserve` fuerza compensación inmediata.

Limitaciones conocidas (documentadas):
- El presupuesto de reintentos (`x-retries`) es por mensaje, compartido entre pasos de la misma saga.
- Si un `reserve` se aplicó pero su respuesta se perdió (timeout) y se agotan los reintentos, queda
  `FAILED` según contrato; la reserva quedaría huérfana en origen (el contrato no prevé release aquí).
- Igual para `receive` aplicado sin respuesta y luego compensado: el destino conservaría el stock.
- `TRANSFER_STATUS_CHANGED` se publica best effort tras el commit (sin outbox en la central); si
  RabbitMQ falla se registra en logs, el estado en BD sigue siendo la fuente de verdad.

## Modelos
`workers/models.py` es copia de `services/central-api/app/models.py` (+ tablas IoT). Ambas deben
coincidir con `db/central_schema.sql` — lo verifica `tests/unit/central/test_models_consistency.py`.

## Tests
```bash
pytest tests/unit/central                      # SQLite + fakes
docker run -d --name central-test-pg -e POSTGRES_PASSWORD=postgres -p 55433:5432 postgres:15-alpine
docker run -d --name central-test-rmq -p 55672:5672 rabbitmq:3.13-management
pytest tests/integration/central               # PG y RabbitMQ reales (se omiten si no están)
```
