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
  terraform/             IaC para AWS (vpc, peering, warehouse, central, database, ecr, storage, lambda) + tests/
  aws/                   plantillas de user_data y docker-compose para las EC2 de bodega
db/                      schema.sql, seed.sql, central_schema.sql
tests/                   unit/, integration/, load/ (k6), failure/ (escenarios de fallo), http/ (colecciones .http)
scripts/                 seed_local.sh, run_tests.sh, build_push_ecr.sh, lab_credentials.sh, aws_deploy.sh, aws_verify.sh
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
| `central` | `central-db`, `central-api`, `command-relay`, `transfer-orchestrator`, `projection-worker`, `audit-worker`, `report-worker`, S3 local (S3Mock) |
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

- **Fallos:** `tests/failure/01_redis_down.sh` … `08_broker_down_sync.sh` (Redis caído, RabbitMQ caído, worker detenido, mensaje duplicado, bodega caída, fallos de saga, DLQ, broker caído con colas de respaldo y resincronización). Si algún puerto ya está ocupado, `COMPOSE_OVERRIDE=/ruta/override.yml` añade un fichero de compose extra.
- **Infraestructura:** `cd infrastructure/terraform && terraform test` (topología con proveedor AWS simulado).
- **Carga (k6 en Docker):** `tests/load/run_k6.sh <script> -e BASE_URL=... -e VUS=... -e DURATION=...`, matriz completa con `tests/load/run_matrix.sh` y tabla de resultados con `python3 tests/load/summarize.py tests/load/results`.

## Despliegue en AWS

Topología (ADR-015): **4 VPCs**, cada una con subnet pública y privada. Cada bodega `WH-00X` es una **EC2 con Docker**
en la subnet privada de su VPC, con su propio security group; la **bodega central es un clúster ECS Fargate** en su VPC
(central-api, command-relay, orquestador, proyección, auditoría, reportes y el broker RabbitMQ con datos en EFS).
Peering **hub-and-spoke**: la central llega a todas las bodegas; las bodegas no se ven entre sí (sin peering entre
ellas, SG y NACL). Los clientes entran por el ALB de la central (`:80` central, `:8001-8003` bodegas).

Si se cae el broker nada se detiene (ADR-016): las bodegas acumulan eventos en su `outbox`, la central guarda los
comandos en `command_outbox`, y al volver se envía todo y cada bodega manda un snapshot para resincronizar.

```bash
scripts/lab_credentials.sh       # credenciales del Learner Lab
scripts/aws_deploy.sh            # ECR -> imágenes -> VPCs, peering, EC2, ECS, RDS, S3, Lambda
scripts/aws_verify.sh            # verificación con AWS CLI (red, aislamiento, Docker, ECS, funcional)
scripts/aws_verify.sh --chaos    # + caída/reinicio del broker
```

Endpoints añadidos: `GET /warehouses`, `GET /warehouses/connectivity`, `POST /warehouses/{id}/resync`,
`GET /reports/warehouses` (central) y `GET /sync/status`, `POST /sync/snapshot` (bodega).
Procedimiento, costes y operación en `docs/aws-deployment.md`.

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
- En AWS el broker es una sola tarea ECS: su caída no detiene el servicio (colas de respaldo) y sus colas sobreviven al reinicio (EFS), pero no hay clúster. En producción se usaría Amazon MQ multi-AZ (ADR-015).
- No hay purga periódica de `command_outbox`.
