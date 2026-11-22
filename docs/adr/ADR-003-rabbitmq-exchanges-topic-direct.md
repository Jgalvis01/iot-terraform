# ADR-003 — RabbitMQ como broker con exchanges `topic` y `direct`

**Estado:** Aceptada

## Contexto

Hay procesos que no deben bloquear las APIs (reportes, transferencias, auditoría, proyección, invalidación de caché, IoT) y varios consumidores distintos interesados en el mismo hecho (p. ej. `INVENTORY_UPDATED` lo consumen caché, proyección y auditoría). Hace falta desacoplar productores de consumidores y que los mensajes sobrevivan a un worker caído (README §10, caso de fallo 3).

## Alternativas

1. Llamadas HTTP síncronas entre servicios.
2. Kafka.
3. SQS + SNS (nativo AWS).
4. **RabbitMQ** (AMQP, colas durables, exchanges con enrutamiento).

## Decisión

RabbitMQ 3.13 (`rabbitmq:3.13-management`, contenedor en local y en la EC2 `platform` en AWS), cliente `pika`, topología declarada de forma idempotente por `wcommon.rabbit.declare_topology`:

- `warehouse.events` — **topic**, durable. Eventos de hechos ya ocurridos. Routing key `<tipo>.<sub>.<wh>` (`inventory.updated.wh-001`, `iot.reading.wh-003`, ...). Permite bindings por bodega (`inventory.updated.wh-001`), por patrón (`inventory.updated.*`) o todo (`#`).
- `platform.commands` — **direct**, durable. Comandos con un único destinatario (`transfer.process`, `report.generate`).
- 9 colas de trabajo (`q.cache.wh-001..003`, `q.audit`, `q.projection`, `q.transfers`, `q.reports`, `q.iot.storage`, `q.iot.alerts`), cada una con su `.retry` y `.dlq` (ADR-005). Ver tabla completa en `docs/events.md`.
- Mensajes persistentes (`delivery_mode=2`), colas durables, publisher confirms (`confirm_delivery`), `prefetch` configurable por consumidor (1 para orquestador y reportes, 50 para audit/IoT).

Se separan eventos (topic, fan-out a N consumidores) de comandos (direct, 1 consumidor) para expresar la diferencia semántica "ocurrió X" vs "haz X".

## Consecuencias / Trade-offs

- Desacoplamiento, absorción de picos y reintentos/DLQ listos; añadir un consumidor nuevo no cambia al productor (solo un binding).
- Entrega **at-least-once**: pueden llegar duplicados (ADR-006) y, con reintentos, fuera de orden.
- Nueva pieza que operar y punto único de fallo (una sola instancia, sin clúster, en local y en AWS Learner Lab).
- Kafka daría log reproducible y orden por partición, pero es más pesado para el alcance; SQS/SNS no ofrece topic routing por patrón ni la misma UI de inspección de DLQ.

## Mitigación

- Outbox (ADR-004): si RabbitMQ cae, las escrituras de las bodegas siguen aceptándose y los eventos esperan en la tabla `outbox`.
- Consumidores idempotentes y tolerantes a desorden (ADR-006, ADR-008).
- Para comandos desde la API central: conexión compartida con timeout de conexión de 1 s y respuesta `503 BROKER_UNAVAILABLE` + `Retry-After` si no hay broker (`central-api/app/publisher.py`).
- Monitoreo de profundidad de colas vía plugin Prometheus de RabbitMQ (`:15692`) (ADR-013).
- En producción: clúster con colas quorum o Amazon MQ (ADR-012).
