# central-api

API central (puerto 8000) de la Warehouse Cloud Platform: write side de las transferencias (saga
orquestada por `workers.transfer_orchestrator`) y read side CQRS (`global_inventory`, alimentado por
`workers.projection`). Contrato: `docs/design-contracts.md` §5. Esquema: `db/central_schema.sql`.

```bash
docker build -f services/central-api/Dockerfile -t central-api .   # desde la RAÍZ del repo
docker run --env-file services/central-api/.env.example -p 8000:8000 central-api
```

## Endpoints
| Método | Ruta | Notas |
|---|---|---|
| GET | `/health`, `/metrics` | 503 si la BD está caída; métricas del instrumentator + `central_transfers_created_total`, `central_command_publish_failures_total` |
| POST | `/transfers` | 202 `{"transfer_id","status":"PROCESSING","saga_status"}`. Headers `Idempotency-Key` (misma key ⇒ mismo transfer; mismo key con otro cuerpo ⇒ 409) y `X-Fail-Step` (`none|before_reserve|after_reserve|destination_down`, se guarda en `fail_step`) |
| GET | `/transfers/{id}`, `/transfers?status=&limit=` | incluye `history` |
| GET | `/global/inventory/{sku}?mode=readmodel\|fanout` | readmodel: `stale` por bodega + `stale_warehouses`; fanout: `GET /inventory/by-sku/{sku}` a cada bodega (timeout 2 s), `unavailable_warehouses`, `not_found_warehouses`; 503 si todas fallan |
| GET | `/global/inventory` | agrupado por sku |
| POST | `/reports` | 202 `{"job_id","status":"PROCESSING"}` y comando `GENERATE_REPORT` |
| GET | `/reports/jobs/{job_id}` | estado + `s3_key` |
| GET | `/audit?limit=50&event_type=&correlation_id=` | |

## Decisiones
- **Publicación con RabbitMQ caído (sin outbox).** POST /transfers hace `COMMIT` de la fila
  `REQUESTED` y *después* publica `PROCESS_TRANSFER` (así el orquestador nunca recibe un id que aún no
  existe). Si la publicación falla, se **borra** la fila y se responde **503** `BROKER_UNAVAILABLE`
  (+ `Retry-After`): no quedan transferencias huérfanas y el cliente puede reintentar con la misma
  `Idempotency-Key`. Un replay de una key cuyo transfer sigue en `REQUESTED` re-publica el comando
  (seguro: el orquestador es idempotente), lo que recupera el caso límite "commit ok + crash antes de
  publicar". Se descartó el outbox en la central porque añade otro proceso relay y aquí el
  cliente está presente para recibir el 503. Igual para POST /reports.
- **Publisher:** una conexión pika compartida por proceso, con lock y reconexión perezosa (reintenta
  una vez con conexión nueva; espera de conexión 1 s ⇒ el 503 llega rápido). Evita el handshake
  TCP+AMQP por request; el canal usa publisher confirms y declara la topología.
- **`stale`:** una bodega es stale si `warehouse_status.last_event_at` (último evento proyectado de
  esa bodega) es más viejo que `STALE_AFTER_SECONDS` o no existe. `stale_warehouses` incluye bodegas
  configuradas en `WAREHOUSES` que nunca enviaron eventos.
- La respuesta 202 usa `status: "PROCESSING"` (contrato) y añade `saga_status` con el estado real.
- Los modelos (`app/models.py`) son copia de `services/workers/workers/models.py`; ambos deben
  coincidir con `db/central_schema.sql` (lo verifica `tests/unit/central/test_models_consistency.py`).

## Variables
Ver `.env.example`: `DATABASE_URL`, `RABBITMQ_URL`, `WAREHOUSES` (`WH-001=http://...,WH-002=...`),
`STALE_AFTER_SECONDS` (60), `FANOUT_TIMEOUT_SECONDS` (2), `SERVICE_NAME`.
