# Registro de decisiones de arquitectura (ADR)

Cada ADR sigue el formato pedido en `docs/enunciado/13-guia-de-entrega-y-evaluacion.md` y el criterio del README §34:
**Contexto -> Alternativas -> Decisión -> Consecuencias / Trade-offs -> Mitigación**.
Todas las decisiones describen lo que **realmente existe** en el repositorio; lo que no está implementado se marca como "propuesta" o "no implementado".

| ADR | Decisión | Fase / tema | Código principal |
|---|---|---|---|
| [ADR-001](ADR-001-base-de-datos-por-bodega.md) | Una base de datos PostgreSQL por bodega | F1, F4 | `infrastructure/compose/docker-compose.yml`, `db/schema.sql` |
| [ADR-002](ADR-002-redis-cache-aside-e-invalidacion.md) | Redis Cache Aside + invalidación síncrona (A) y asíncrona (B) | F2 | `services/warehouse-api/app/cache.py`, `workers/cache.py` |
| [ADR-003](ADR-003-rabbitmq-exchanges-topic-direct.md) | RabbitMQ como broker (exchanges topic y direct) | F3 | `libs/wcommon/wcommon/rabbit.py` |
| [ADR-004](ADR-004-patron-outbox.md) | Patrón Transactional Outbox | F3, F10 | `warehouse-api/app/outbox.py`, `outbox_relay.py` |
| [ADR-005](ADR-005-retry-ttl-dlq.md) | Retry con colas TTL + DLQ; errores transitorios vs permanentes | F10 | `wcommon/rabbit.py`, `wcommon/errors.py` |
| [ADR-006](ADR-006-idempotencia.md) | Idempotencia (`processed_events` / `processed_commands`) | F6, F10 | `wcommon/idempotency.py`, `transfers_service.py` |
| [ADR-007](ADR-007-saga-orquestada.md) | Saga orquestada con compensaciones (vs coreografía) | F6 | `workers/saga.py` |
| [ADR-008](ADR-008-cqrs-read-model-central.md) | CQRS: read model central (vs fan-out) | F7 | `workers/projection.py`, `central-api/.../global_inventory.py` |
| [ADR-009](ADR-009-sku-identidad-global.md) | `sku` como identidad global de producto | F4, F6, F7 | `docs/design-contracts.md` §0 |
| [ADR-010](ADR-010-load-balancer-y-apis-stateless.md) | Nginx local / ALB en AWS y APIs stateless | F5 | `infrastructure/compose/nginx/wh-002-lb.conf`, `modules/load_balancer` |
| [ADR-011](ADR-011-s3-batch-worker-lambda.md) | S3 + Batch vs Worker vs Lambda | F8, F9 | `services/batch`, `workers/report.py`, `services/lambda` |
| [ADR-012](ADR-012-despliegue-aws-learner-lab.md) | Despliegue AWS Learner Lab (EC2 + ECR + RDS + ALB) | F11 / AWS | `infrastructure/terraform/`, `docs/aws-deployment.md` |
| [ADR-013](ADR-013-observabilidad.md) | Observabilidad: correlation id, logs JSON, Prometheus/Grafana, CloudWatch | F11 | `wcommon/correlation.py`, `logging.py`, `metrics.py` |
| [ADR-014](ADR-014-iot-mqtt-ingestion.md) | IoT: MQTT + ingestion + dedupe/orden por `sequence_number` | F12 | `services/iot`, `workers/iot_storage.py` |

## Documentos relacionados

- `docs/architecture.md`: diagrama final, componentes, consistencia y flujos.
- `docs/events.md`: Entregable 5, contratos de eventos y topología RabbitMQ.
- `docs/error-matrix.md`: matriz de errores (Fase 10) y casos de fallo (README §18).
- `docs/comparisons.md`: comparaciones A vs B (F2), fan-out vs read model (F7), Worker/Batch/Lambda (F9).
- `docs/defense-answers.md`: respuestas a las preguntas de defensa (README §33).
- `docs/design-contracts.md`: contratos de diseño (fuente de verdad técnica).
