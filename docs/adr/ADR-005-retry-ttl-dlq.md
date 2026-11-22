# ADR-005 — Retry con colas TTL + DLQ; errores transitorios vs permanentes

**Estado:** Aceptada

## Contexto

No todo error debe reintentarse indefinidamente (README §19). Un timeout o una BD momentáneamente caída se resuelven solos; un JSON corrupto o un producto inexistente jamás se arreglarán reintentando y, si se reencolan, "envenenan" la cola (poison message).

## Alternativas

1. Reencolar con `basic_nack(requeue=True)` (bucle inmediato, sin espera ni límite).
2. Plugin de mensajes retrasados (`rabbitmq-delayed-message-exchange`).
3. **Colas de retry con TTL** que devuelven el mensaje a la cola original (dead-letter) + DLQ final.
4. Reintento en memoria dentro del consumidor.

## Decisión

Implementado en `wcommon.rabbit.consume` (opción 3). Para cada cola `Q`:

- `Q.retry`: durable, `x-message-ttl = RETRY_DELAY_MS` (5000 por defecto), `x-dead-letter-exchange=""`, `x-dead-letter-routing-key=Q`. Al expirar el TTL el mensaje vuelve solo a `Q`.
- `Q.dlq`: durable, sin consumidor; se inspecciona en la UI (`:15672`).
- `MAX_RETRIES=3` (variables `MAX_RETRIES` y `RETRY_DELAY_MS`).

Clasificación de errores (`wcommon/errors.py`):

| Excepción del handler | Acción |
|---|---|
| éxito | `ack` |
| `TransientError` y `x-retries < MAX_RETRIES` | publica en `Q.retry` con `x-retries+1` y `x-error`; `ack` |
| `TransientError` y reintentos agotados | publica en `Q.dlq` ("Reintentos agotados"); `ack` |
| `PermanentError` | directo a `Q.dlq`, sin reintentos |
| cualquier otra `Exception` | directo a `Q.dlq` (se trata como permanente) |
| cuerpo no es JSON | directo a `Q.dlq` ("JSON inválido") |

Cada decisión incrementa métricas `worker_messages_failed_total{consumer,kind}`, `worker_messages_retried_total`, `worker_messages_dlq_total` y deja un log JSON (`message_retry_scheduled`, `message_sent_to_dlq`). Cabeceras añadidas: `x-retries`, `x-error`, `x-failed-queue`. Los workers clasifican: `OperationalError`/`IntegrityError` de BD, Redis, S3 5xx y HTTP 5xx/timeouts => transitorio; esquema inválido, entidad inexistente, 404/409 de negocio => permanente.

## Consecuencias / Trade-offs

- Retraso fijo de 5 s (no hay backoff exponencial) y sin jitter; es simple, predecible y suficiente para el alcance.
- Un mensaje reintentado se reencola al final: **se pierde el orden**; los consumidores deben tolerar desorden (read model por `occurred_at`, IoT por `sequence_number`).
- La DLQ no se reprocesa automáticamente: requiere intervención (republicar a mano). No hay herramienta de replay en el repositorio.
- Tratar toda excepción desconocida como permanente evita bucles pero puede enviar a la DLQ errores que eran transitorios.
- El reintento de saga mezcla retry con estado persistido (ADR-007): el orquestador lee `msg["_retries"]` para decidir `FAILED`/`COMPENSATING` antes de agotar.

## Mitigación

- Panel Grafana "Workers: procesados / reintentos / DLQ" y profundidad de colas en RabbitMQ (ADR-013).
- `tests/failure/07_dlq.sh` publica JSON corrupto y un evento sin campos obligatorios en `q.projection` y verifica que van a `q.projection.dlq`; `tests/integration/central/test_rabbit_retry_dlq.py` cubre el ciclo retry -> DLQ.
- Mensajes en DLQ conservan cuerpo original + cabecera `x-error` para diagnóstico y republicación manual.
