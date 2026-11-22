# Warehouse Cloud Platform

Plataforma distribuida para varias bodegas independientes. Cada bodega tiene su propia API, PostgreSQL y Redis. Una
central consolida la información con CQRS, coordina transferencias entre bodegas con una Saga orquestada y genera
reportes en S3. La comunicación es asíncrona por RabbitMQ, con patrón outbox, idempotencia, reintentos y DLQ.
También hay una extensión IoT por MQTT y una Lambda que procesa archivos exportados a S3.

Proyecto integrador de *Computación en la Nube e IoT*. El enunciado está en `docs/enunciado/`.

## Arquitectura

```text
               Clientes / k6 / Frontend
                         │
   ┌─────────────┬───────┴───────┬──────────────────┐
WH-001 API    WH-002 (LB, 3 réplicas)   WH-003 API     Central API (CQRS, transferencias, reportes)
 │  PG Redis    │  PG Redis          │  PG Redis       │  central_read (read model)
 └──── outbox relay ─────────────────┴─────────────────┤
                         ▼                             │
                     RabbitMQ  ◀───────────────────────┘
   transfer-orchestrator · projection · audit · report · cache-workers · iot-storage · iot-alert
                         │
                 S3 (reportes, exports) ──▶ Lambda s3_processor
   IoT: sensores ─MQTT─▶ Mosquitto ─▶ ingesta ─▶ RabbitMQ
```

Los diagramas detallados, los flujos y los modelos de consistencia están en `docs/architecture.md`.

## Estructura

```text
libs/wcommon/            librería común: logs JSON, correlation id, eventos, RabbitMQ con retry/DLQ, idempotencia
services/
  warehouse-api/         API de bodega (FastAPI) + outbox relay
  central-api/           transferencias, inventario global (CQRS), reportes, auditoría
  workers/               transfer_orchestrator (saga), projection, audit, cache, report, iot_storage, iot_alert
  batch/                 reporte diario consolidado a S3
  lambda/s3_processor/   Lambda disparada por S3 (exports/ → processed/ | rejected/)
  iot/                   simulador de sensores MQTT + servicio de ingesta
  frontend/              UI web de la bodega WH-001
infrastructure/
  compose/               docker-compose.yml por profiles, Nginx LB, Prometheus, Grafana
  terraform/             IaC para AWS (network, ecr, database, compute, storage, lambda, load_balancer)
  aws/                   plantillas de user_data y docker-compose para las EC2
db/                      schema.sql, seed.sql, central_schema.sql
tests/                   unit/, integration/, load/ (k6), failure/ (escenarios de fallo), http/ (colecciones .http)
scripts/                 seed_local.sh, run_tests.sh, build_push_ecr.sh, lab_credentials.sh
docs/                    ADRs, arquitectura, contratos, eventos, matriz de errores, comparaciones, despliegue AWS
```

## Requisitos

Docker con Compose v2, Python 3.12 (solo para los tests) y bash.

## Ejecución local

Todos los servicios están en `infrastructure/compose/docker-compose.yml` y se activan por profiles:

| Profile | Servicios |
|---|---|
| *(ninguno)* | 3 bodegas: `wh-00X-db`, `wh-00X-redis`, `wh-001-api`, `wh-002-api-1`, `wh-002-lb`, `wh-003-api` |
| `lb` | réplicas `wh-002-api-2` y `wh-002-api-3` |
| `events` | RabbitMQ, outbox relays y cache-workers |
| `central` | `central-db`, `central-api`, `transfer-orchestrator`, `projection-worker`, `audit-worker`, `report-worker`, S3 local (S3Mock) |
| `batch` | proceso batch (se ejecuta con `run`) |
| `obs` | Prometheus, Grafana y exporters |
| `iot` | Mosquitto, simulador, ingesta y workers IoT |
| `frontend` | UI web de WH-001 |

```bash
cp infrastructure/compose/.env.example infrastructure/compose/.env
alias dc='docker compose --project-directory infrastructure/compose -f infrastructure/compose/docker-compose.yml'

dc --profile lb --profile events --profile central --profile obs --profile iot --profile frontend up -d --build
dc ps
./scripts/seed_local.sh            # productos e inventario inicial en las 3 bodegas
```

Variables de `infrastructure/compose/.env`:

| Variable | Valores | Uso |
|---|---|---|
| `EVENTS_ENABLED` | `true` / `false` | Fases 1-2 sin eventos; desde la Fase 3, `true` |
| `CACHE_ENABLED` | `true` / `false` | Redis activado o desactivado (comparaciones de carga) |
| `FAIL_INJECTION` | `none`, `before_reserve`, `after_reserve`, `destination_down` | Inyección de fallos en la saga |
| `IOT_SENSORS` | número | Sensores simulados |

| Servicio | URL local |
|---|---|
| Central API (Swagger en `/docs`) | http://localhost:8000 |
| WH-001 / WH-002 (LB) / WH-003 | http://localhost:8001 · :8002 · :8003 |
| Frontend WH-001 | http://localhost:8080 |
| RabbitMQ UI | http://localhost:15672 |
| Prometheus / Grafana | http://localhost:9090 · http://localhost:3000 |
| S3 local (S3Mock) | http://localhost:9000 |
| MQTT | localhost:1883 |

Las colecciones de peticiones de ejemplo están en `tests/http/warehouse.http` y `tests/http/central.http`.

## Pruebas

```bash
./scripts/run_tests.sh          # unitarios (132)
./scripts/run_tests.sh --all    # + integración contra PostgreSQL y RabbitMQ reales en Docker (15)
```

- **Fallos:** `tests/failure/01_redis_down.sh` … `07_dlq.sh` (Redis caído, RabbitMQ caído, worker detenido, mensaje duplicado, bodega caída, fallos de saga, DLQ).
- **Carga (k6 en Docker):** `tests/load/run_k6.sh <script> -e BASE_URL=... -e VUS=... -e DURATION=...`, matriz completa con `tests/load/run_matrix.sh` y tabla de resultados con `python3 tests/load/summarize.py tests/load/results`.

## Despliegue en AWS

La infraestructura en AWS Academy Learner Lab está definida en `infrastructure/terraform/`: VPC, RDS PostgreSQL,
ALB, EC2 (platform, wh-a, wh-b × N), ECR, S3, Lambda y CloudWatch Logs. El procedimiento, los costos y la operación
están en `docs/aws-deployment.md`. Las decisiones condicionadas por Learner Lab se explican en `docs/adr/ADR-012-despliegue-aws-learner-lab.md`.

## Entregables

| Entregable | Ubicación |
|---|---|
| Arquitectura | `docs/architecture.md` |
| Decisiones de arquitectura (ADRs) | `docs/adr/ADR-001` … `ADR-014` |
| Backend | `services/warehouse-api`, `services/central-api`, `services/workers`, `libs/wcommon` |
| Docker | `services/*/Dockerfile`, `infrastructure/compose/` |
| Persistencia | `db/schema.sql`, `db/seed.sql`, `db/central_schema.sql` (las tablas también se crean al arrancar) |
| Eventos (exchanges, colas, routing keys, DLQ) | `docs/events.md` |
| Contratos técnicos | `docs/design-contracts.md` |
| Matriz de errores | `docs/error-matrix.md` |
| Comparaciones (caché, fan-out vs read model, worker/batch/Lambda) | `docs/comparisons.md` |
| AWS + Terraform | `infrastructure/terraform/`, `infrastructure/aws/`, `docs/aws-deployment.md` |
| Pruebas | `tests/unit`, `tests/integration`, `tests/load`, `tests/failure` |
| Preguntas de defensa | `docs/defense-answers.md` |

## Limitaciones conocidas

- `TRANSFER_STATUS_CHANGED`, `REPORT_COMPLETED` y `SENSOR_ALERT` se publican directamente, sin outbox: son notificaciones y la verdad está en la base de datos.
- Si `release` o `commit-out` agotan sus reintentos, la transferencia queda en DLQ para intervención manual.
- No hay purga periódica de `outbox` ni de `processed_events`.
- En AWS, la instancia `platform` (RabbitMQ y Redis) es un punto único de fallo. En producción se usarían Amazon MQ y ElastiCache multi-AZ (ADR-012).
