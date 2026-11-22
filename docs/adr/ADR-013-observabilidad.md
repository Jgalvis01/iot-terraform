# ADR-013 — Observabilidad: correlation id, logs JSON, Prometheus/Grafana, CloudWatch

**Estado:** Aceptada

## Contexto

Cada request y cada evento debe poder seguirse de extremo a extremo (HTTP -> API -> RabbitMQ -> worker -> BD, README §20) y el sistema debe medirse, no suponerse (README §21-§22, preguntas 30-33).

## Alternativas

1. Logs de texto sin estructura y sin identificador común.
2. Tracing distribuido completo (OpenTelemetry + Jaeger/X-Ray).
3. **Correlation id propio + logs JSON + métricas Prometheus** (elegida, la más ligera para el alcance).

## Decisión

**Correlation id** (`libs/wcommon/wcommon/correlation.py`): `CorrelationIdMiddleware` lee/crea `X-Correlation-ID` (`COR-<uuid hex[:12]>`) y lo devuelve en la respuesta. Se propaga: (a) en el campo `correlation_id` del sobre de cada evento/comando; (b) en la cabecera AMQP `correlation_id` y `consume` lo restaura en el contexto del worker; (c) en las llamadas HTTP del orquestador y del fan-out a las bodegas; (d) se persiste en `transfers.correlation_id` y `audit_log.correlation_id` (indexado). Nginx local registra `cid=$http_x_correlation_id`.

**Logs JSON** (`wcommon/logging.py`): una línea por evento en stdout con `timestamp, service, level, event, correlation_id` + contexto (`warehouse_id, sku, transfer_id, event_id...`). Eventos de interés: `inventory_mutation`, `outbox_published`, `transfer_transition`, `message_retry_scheduled`, `message_sent_to_dlq`, `cache_error`.

**Métricas Prometheus** (scrape cada 5 s, `infrastructure/compose/prometheus/prometheus.yml`):
- API: instrumentator HTTP (RPS, P50/P95/P99, 5xx), `cache_hits_total`, `cache_misses_total`, `cache_errors_total`.
- Workers (`:9100`): `worker_messages_processed_total`, `_failed_total{kind}`, `_retried_total`, `_dlq_total`, `_duplicated_total`, `worker_processing_seconds`; `transfer_transitions_total`, `projection_out_of_order_total`, `iot_out_of_order_total`, `iot_alerts_total`, `iot_readings_stored_total`.
- RabbitMQ: plugin Prometheus `:15692` (profundidad de colas: `queue_coarse_metrics`).
- Redis: `redis_exporter` por bodega. PostgreSQL: `postgres_exporter` por base (conexiones, locks).
- **Grafana** (`:3000`) con el dashboard provisionado `warehouse-platform.json` (10 paneles: RPS por bodega, latencia P50/P95/P99, error 5xx, distribución por réplica WH-002, cache hit ratio, Redis, profundidad de colas, workers procesados/reintentos/DLQ, tiempo de procesamiento, conexiones/locks de PostgreSQL).

**CloudWatch (AWS):** driver `awslogs` por contenedor, grupo `/warehouse-platform/<servicio>`, stream = instancia, retención 7 días; la Lambda escribe a `/aws/lambda/s3_processor`.

## Consecuencias / Trade-offs

- Barato y suficiente para seguir una operación con `grep correlation_id` (o `GET /audit`); no hay spans ni árbol de tiempos por salto como en tracing real.
- Prometheus y Grafana solo existen en el stack local (perfil `obs`); en AWS solo hay logs (CloudWatch Logs Insights puede filtrar por `correlation_id` en el JSON). No hay métricas de CloudWatch personalizadas ni alarmas.
- No hay reglas de alerta definidas (`prometheus.yml` no tiene `rule_files`): la detección de cola saturada es visual en Grafana/UI de RabbitMQ.
- Los workers fijan el correlation id por mensaje, pero el del fan-out y el del outbox dependen de que quien origina lo cree: un evento publicado por el outbox lleva el `correlation_id` con el que se construyó en la API (el relay no lo regenera).

## Mitigación

- Respuesta `X-Correlation-ID` para que el cliente pueda reportar una operación.
- Para detectar cola saturada: `rabbitmq_queue_messages_ready` creciente + `worker_processing_seconds` alto; para consultas lentas: P95/P99 por endpoint y panel de PostgreSQL (locks/conexiones).
- Propuesta futura: reglas de alerta Prometheus (DLQ > 0, outbox pendiente), exportador de métricas a CloudWatch/Managed Prometheus y OpenTelemetry.
