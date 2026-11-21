# warehouse-api

API de una bodega (FastAPI + PostgreSQL + Redis + RabbitMQ). Contrato: `docs/design-contracts.md` §4.

Capas por módulo: controller -> service -> repository. Módulos: `products`, `inventory`, `transfers` (comandos de saga), `health`.

## Ejecutar
```bash
docker build -f services/warehouse-api/Dockerfile -t warehouse-api:dev .   # desde la raíz del repo
docker run --env-file services/warehouse-api/.env.example -p 8000:8000 warehouse-api:dev
docker run --env-file ... warehouse-api:dev python -m app.outbox_relay      # relay del outbox
```
Las tablas se crean al arrancar (`create_all`); DDL equivalente en `db/schema.sql`, datos de ejemplo en `db/seed.sql`.

## Variables de entorno
`SERVICE_NAME`, `WAREHOUSE_ID`, `DATABASE_URL`, `REDIS_URL`, `CACHE_ENABLED`, `CACHE_TTL_SECONDS`, `RABBITMQ_URL`, `EVENTS_ENABLED` (ver `.env.example`).

## Decisiones
- Mutaciones: `SELECT ... FOR UPDATE` + movimiento + outbox (si `EVENTS_ENABLED`) + `processed_commands` en un único COMMIT.
- `ADJUSTMENT` = nuevo valor absoluto de `quantity` (>= 0). `TRANSFER_OUT` consume stock reservado.
- Movimiento con stock insuficiente: `400` (`POST /inventory/movements`) ; comandos de saga: `409` con `code=INSUFFICIENT_STOCK`.
- Cache Aside: Redis (timeout 0.2 s) con fallback a PostgreSQL; invalidación síncrona tras commit.
- `DELETE /products/{id}` devuelve 204; 409 si hay stock o reservas. `sku` es inmutable.
- Métricas en `/metrics` (`cache_hits_total`, `cache_misses_total`, `cache_errors_total` + HTTP).

## Tests
```bash
pip install ./libs/wcommon -r services/warehouse-api/requirements-dev.txt
pytest tests/unit/warehouse_api tests/integration/warehouse_api
# integración: docker run -d --name wh-test-pg -e POSTGRES_PASSWORD=postgres -p 55432:5432 postgres:15-alpine
```
