# ADR-001 — Una base de datos PostgreSQL por bodega

**Estado:** Aceptada

## Contexto

Cada bodega debe operar de forma autónoma (README §1): escrituras consistentes dentro de la bodega, escalamiento independiente y aislamiento de fallos. Las bodegas tienen cargas muy distintas (50 a 2.000 req/s en el enunciado). Una transferencia entre bodegas toca dos dominios de datos.

Esquema por bodega (`db/schema.sql`): `products`, `inventory` (con `CHECK quantity >= 0` y `reserved_quantity >= 0`), `inventory_movements`, `outbox`, `processed_commands`. La plataforma central tiene su propia base `central_read` (`db/central_schema.sql`).

## Alternativas

1. **Una única base central compartida** con columna `warehouse_id` en cada tabla.
2. **Un esquema por bodega** dentro de una misma instancia PostgreSQL.
3. **Una base (y un contenedor PostgreSQL) por bodega**, más una base propia para la central.

## Decisión

Alternativa 3 en local: `wh-001-db`, `wh-002-db`, `wh-003-db` (puertos 5401-5403) y `central-db` (5410), cada una con su volumen en `infrastructure/compose/docker-compose.yml`. Cada `warehouse-api` solo conoce su `DATABASE_URL`; nadie más lee las tablas de una bodega (la central consume eventos y llama a la API de la bodega, nunca a su base).

En AWS (ADR-012) se mantiene la separación **lógica**: un único RDS `db.t3.micro` con cuatro bases `wh_001`, `wh_002`, `wh_003`, `central_read`.

## Consecuencias / Trade-offs

Beneficios:
- Aislamiento de fallos y de carga: un `SELECT ... FOR UPDATE` pesado o un lock en WH-004 no bloquea a WH-001.
- Escalamiento independiente (más réplicas de API, Redis o una instancia de BD mayor solo para la bodega caliente).
- Las reglas de consistencia fuerte (`FOR UPDATE` + movimiento + outbox en un COMMIT) son locales y baratas.

Costos:
- No hay transacciones ACID entre bodegas: una transferencia requiere una Saga (ADR-007) y consistencia eventual.
- No existe un `JOIN` global: hace falta un read model (ADR-008).
- Más bases que operar, migrar y monitorear (un `postgres-exporter` por base en el perfil `obs`).

## Mitigación

- Saga con compensaciones y comandos idempotentes para las operaciones entre bodegas.
- Read model central alimentado por eventos para las consultas globales.
- DDL único `db/schema.sql` + `Base.metadata.create_all` al arrancar para que todas las bodegas tengan el mismo esquema.
- En AWS Learner Lab se comparte la instancia RDS por presupuesto; el aislamiento es lógico (ver ADR-012 para el trade-off y la alternativa de producción).
