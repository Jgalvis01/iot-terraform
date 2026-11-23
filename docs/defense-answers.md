# Respuestas de defensa (README §33 y guía de entrega)

Respuestas concisas referidas a nuestra implementación. Detalle en `docs/adr/` y `docs/architecture.md`.

## Arquitectura

**1. ¿Por qué cada bodega tiene su propia base?**
Para autonomía, aislamiento de fallos y escalamiento independiente: cada `warehouse-api` solo conoce su `DATABASE_URL` (`wh-001-db`, `wh-002-db`, `wh-003-db`; en AWS, bases lógicas `wh_001..wh_003` en un RDS). Las escrituras (`FOR UPDATE` + movimiento + outbox) son ACID locales y baratas. El costo es que lo global requiere eventos y Saga. ADR-001.

**2. ¿Por qué no utilizar una única base central?**
Sería un punto único de fallo y de contención: la carga de la bodega más activa (WH-004 con 2.000 req/s) bloquearía a las demás con locks y conexiones, y ninguna bodega operaría si la base cae. Preferimos bases por bodega y un read model separado (`central_read`) para la visión consolidada.

**3. ¿Qué sucede si una bodega deja de responder?**
Las demás siguen operando. La central sigue respondiendo con el read model y marca `stale` a esa bodega tras `STALE_AFTER_SECONDS` (60 s) sin eventos; en `mode=fanout` aparece en `unavailable_warehouses`. Las transferencias que la involucren reintentan (3 x 5 s) y se compensan o fallan según el paso. Demo: `tests/failure/05_warehouse_down.sh`.

**4. ¿Cómo escalaría una sola bodega?**
Horizontalmente la API (stateless) detrás de un balanceador: WH-002 con `wh-002-api-1/2/3` y Nginx localmente, o, en AWS, más procesos (`warehouse_api_workers`) o más EC2 en el target group `:8002` del ALB central. Las lecturas se absorben con Redis (Cache Aside); para escrituras se escala la BD (mayor instancia, réplicas de lectura). Se prueba con `tests/load/run_matrix.sh` (1 vs 3 réplicas). ADR-010.

**5. ¿Cómo evitaría que una bodega afecte a las demás?**
Base, Redis, API y cache-worker/outbox propios por bodega, balanceo independiente y tráfico entre bodegas solo mediante eventos asíncronos o comandos de saga con timeout (3 s) y reintentos acotados. Una cola por bodega para la caché (`q.cache.wh-00X`). Límite real: en AWS Learner Lab el RDS y la EC2 `platform` se comparten por presupuesto (ADR-012).

## Redis

**6. ¿Por qué utilizar Redis?**
Las consultas de inventario son muy frecuentes y repetitivas. Cache Aside en `GET /inventory/...` baja la latencia y quita carga a PostgreSQL (TTL 30 s, `allkeys-lru`, 128 MB). Se mide con la prueba Redis OFF/ON (`CACHE_ENABLED`). ADR-002.

**7. ¿Qué sucede si Redis está caído?**
La API sigue: cada excepción de Redis se cuenta en `cache_errors_total` y la lectura cae a PostgreSQL (timeout de socket 0,2 s). La invalidación fallida se ignora tras el commit. `/health` muestra `redis: down` pero responde 200. Prueba: `tests/failure/01_redis_down.sh`.

**8. ¿Cómo se invalida la caché?**
Tres capas: (A) `DEL` síncrono tras el commit en la API, (B) evento `INVENTORY_UPDATED` -> `q.cache.wh-00X` -> `cache-worker` que hace `DEL`, y (TTL) expiración a 30 s como red de seguridad. Claves: `warehouse:{WH}:inventory:{product_id}` y `...:inventory:sku:{sku}`.

**9. ¿Qué es un cache miss?**
Una lectura cuya clave no está en Redis (nunca cacheada, expirada o invalidada): la API consulta PostgreSQL y rellena la caché (Cache Aside). Se cuenta en `cache_misses_total`; el hit ratio es `hits / (hits + misses)`.

**10. ¿Qué problema aparece con datos obsoletos?**
Una lectura puede devolver un inventario anterior a la última escritura (p. ej. una carrera lector/commit/DEL, o Redis caído durante la invalidación). Se acota con la invalidación A+B y el TTL de 30 s; las decisiones críticas (reservas y transferencias) no leen de caché sino de PostgreSQL con `FOR UPDATE`.

## RabbitMQ

**11. ¿Por qué usar un broker?**
Para desacoplar y no bloquear las APIs: un hecho (`INVENTORY_UPDATED`) lo consumen caché, proyección y auditoría sin que el productor los conozca, y los mensajes persisten si un consumidor está caído. Usamos `warehouse.events` (topic) y `platform.commands` (direct) con colas durables. ADR-003.

**12. ¿Qué diferencia existe entre procesamiento síncrono y asíncrono?**
Síncrono: el cliente espera el resultado (p. ej. un movimiento de inventario, que responde tras el commit). Asíncrono: se acepta y se procesa después (`POST /transfers` y `POST /reports` responden 202 con un id; el resultado se consulta luego). Lo asíncrono evita bloquear la API y absorbe picos, a cambio de consistencia eventual.

**13. ¿Qué sucede si un worker falla?**
Los mensajes siguen en la cola (durable + persistente) y se procesan cuando vuelve. Si el handler falla a mitad, no hubo `ack` o se reencola por la política retry/DLQ; los consumidores son idempotentes, así que reprocesar no duplica efectos. Prueba: `tests/failure/03_worker_stopped.sh`.

**14. ¿Qué es una DLQ?**
La Dead Letter Queue: cola donde terminan los mensajes que no se pueden procesar (error permanente, JSON inválido o reintentos agotados). Cada cola `Q` tiene `Q.retry` (TTL 5 s, vuelve a `Q`) y `Q.dlq` (sin consumidor, se inspecciona en la UI `:15672` con `x-error`). Prueba: `tests/failure/07_dlq.sh`. ADR-005.

**15. ¿Cómo evitar mensajes duplicados?**
No se evitan en el transporte (at-least-once); se **neutralizan** con idempotencia: `processed_events(event_id, consumer)` en la misma transacción que el efecto, `audit_log.event_id` PK, `DEL` natural y `processed_commands(<transfer_id>:<step>)` en las bodegas. ADR-006. Prueba: `tests/failure/04_duplicate_message.sh`.

## Distribución

**16. ¿Por qué una transferencia entre bodegas es una operación distribuida?**
Modifica dos bases independientes (reservar/descontar en origen y acreditar en destino) sin transacción ACID común; cualquier paso puede fallar a mitad. Por eso necesitamos coordinar pasos, persistir estado y compensar (ADR-007).

**17. ¿Qué es Saga?**
Una secuencia de transacciones locales con compensaciones en vez de una transacción global. La nuestra es orquestada por `transfer-orchestrator` (`workers/saga.py`): `reserve` (origen) -> `receive` (destino) -> `commit-out` (origen), con estados persistidos en `transfers` y reanudable ante redelivery.

**18. ¿Qué es una compensación?**
La acción que deshace el efecto de un paso ya confirmado. En nuestra saga es `POST /transfers/{id}/release` en el origen, que devuelve el stock reservado a `quantity` si el destino falla (`COMPENSATING` -> `CANCELLED`). Si falla la propia compensación queda para intervención manual (DLQ).

**19. ¿Qué significa consistencia eventual?**
Que tras una escritura los demás componentes (read model, caché asíncrona, auditoría) convergen al mismo estado pasado un tiempo, no instantáneamente. Dentro de una bodega la consistencia es fuerte (ACID); entre bodegas y en el read model es eventual (outbox ~1 s + cola + proyección).

**20. ¿Qué sucede si la bodega destino no responde?**
`receive` lanza `TransientError`: se reintenta 3 veces (cada 5 s) y, al agotarse, la saga pasa a `COMPENSATING`, ejecuta `release` en origen y termina `CANCELLED` con la razón registrada. Demo: `X-Fail-Step: destination_down` en `tests/failure/06_saga_failures.sh`.

## CQRS

**21. ¿Por qué crear un Read Model?**
Para responder consultas globales (`GET /global/inventory/{sku}`) sin consultar cada bodega: `projection-worker` mantiene `global_inventory` desde eventos, separando el modelo de lectura del de escritura. ADR-008.

**22. ¿Por qué no consultar todas las bodegas?**
Latencia = la de la más lenta, disponibilidad = la de la más frágil, y cada consulta global golpea a las bodegas de alto tráfico. Lo mantenemos como `mode=fanout` solo para contraste (timeout 2 s, respuesta parcial). Ver `docs/comparisons.md`.

**23. ¿Qué significa que el Read Model pueda estar desactualizado?**
Que refleja el estado hasta el último evento procesado, no el instante actual. Lo hacemos explícito: cada bodega trae `last_event_at` y `stale` (sin eventos en 60 s) y la respuesta lista `stale_warehouses`. Limitación: una bodega inactiva también aparece `stale`.

**24. ¿Qué ventaja ofrece una proyección?**
Lectura rápida y local (un `SELECT` por PK `(sku, warehouse_id)`), independiente de las bodegas, con forma optimizada para la consulta. Es idempotente (`processed_events`) y descarta eventos viejos (`occurred_at < last_event_at`).

## Cloud

**25. ¿Por qué utilizar EC2?**
Así lo pide el enunciado: cada bodega es una EC2 con Docker en su propia VPC (aislamiento físico de red y de base de datos) y la central es un ECS Fargate, que escala por servicio sin administrar servidores. Learner Lab no deja crear roles, así que las EC2 usan `LabInstanceProfile` y ECS usa `LabRole` como rol de ejecución y de tarea (ADR-015).

**26. ¿Qué problema resuelve ECR?**
Registro privado de imágenes: construimos una vez (`scripts/build_push_ecr.sh`, 6 repositorios) y cada EC2 hace `docker pull` al arrancar con su instance profile, sin construir en las máquinas ni usar un registro público. Por eso el despliegue es en dos etapas (`-target=module.ecr`, publicar, resto).

**27. ¿Qué diferencia existe entre RDS y PostgreSQL local?**
RDS es administrado (parches, almacenamiento cifrado, backups/Multi-AZ configurables, subnets privadas con SG) y no se opera el servidor; en local es un contenedor con un volumen. Nuestro RDS es un `db.t3.micro` single-AZ sin backups, con 4 bases lógicas (trade-off de coste, ADR-012).

**28. ¿Qué problema resuelve S3?**
Almacenamiento de objetos duradero y barato para reportes, exports y salidas de batch (`reports/`, `warehouses/`, `exports/`, `processed/`), desacoplado de las instancias y capaz de disparar Lambda por evento. Bucket versionado, cifrado, sin acceso público (S3Mock como sustituto local).

**29. ¿Cuándo utilizar Lambda?**
Para trabajo corto, sin estado y disparado por eventos: `s3_processor` valida y normaliza archivos que llegan a `exports/` sin mantener un servidor. No para procesos continuos con colas (worker) ni procesos masivos periódicos (batch). Ver tabla en `docs/comparisons.md`.

## Observabilidad

**30. ¿Cómo seguir una operación de extremo a extremo?**
Con el `X-Correlation-ID` (`COR-...`): lo crea la API, viaja en el sobre de cada evento, en la cabecera AMQP, en las llamadas HTTP del orquestador y en los logs JSON; se persiste en `transfers` y `audit_log`. Basta filtrar por ese id (logs locales, CloudWatch Logs o `GET /audit`).

**31. ¿Qué información debe contener un log?**
JSON de una línea con `timestamp, service, level, event, correlation_id` más contexto de negocio (`warehouse_id, sku, transfer_id, event_id`...). Sin secretos. Ejemplos: `transfer_transition`, `outbox_published`, `message_sent_to_dlq`.

**32. ¿Cómo detectar una cola saturada?**
Profundidad de cola creciente (`queue_coarse_metrics` del plugin Prometheus de RabbitMQ, panel Grafana "RabbitMQ profundidad de colas" y UI `:15672`) junto a `worker_processing_seconds` alto, y/o `worker_messages_dlq_total` > 0. Limitación: no hay reglas de alerta definidas; la detección es visual.

**33. ¿Cómo identificar una consulta lenta?**
Latencia P95/P99 por endpoint (instrumentator, panel Grafana), el panel de PostgreSQL (conexiones y locks por `postgres_exporter`), y el `correlation_id` de la petición lenta para ver en logs dónde se fue el tiempo. En Redis, un hit ratio bajo indica que se está yendo a la base.

## Pregunta final

**Si la plataforma tuviera que atender 100 bodegas y cada bodega tuviera una carga diferente, ¿qué componentes escalarían independientemente y por qué?**

Escalan **por bodega**, según su carga: (1) las réplicas de `warehouse-api` tras su balanceador (stateless; lo demostramos con WH-002 de 1 a 3 réplicas); (2) su Redis y su base PostgreSQL (la bodega caliente en una instancia mayor o con réplicas de lectura), y su `cache-worker`/`outbox-relay`, todos aislados por diseño (ADR-001, ADR-010). Escalan **por rol, no por bodega**: `projection-worker`, `audit-worker`, `report-worker` y `central-api`, que se multiplican según la profundidad de sus colas (`q.projection`, `q.audit`, `q.reports`) y no según el número de bodegas; el orquestador escala con las transferencias concurrentes (hoy `prefetch=1`).

Lo que **no** escala independientemente hoy: el RabbitMQ único y la base `central_read`; a 100 bodegas habría que pasar a un clúster de RabbitMQ (colas quorum) y particionar o replicar el read model (por `sku` o bodega). Además, los 100 `q.cache.*` y los 100 outbox serían plantilla generada (hoy el compose se genera con `generate_compose.py`), idealmente con ECS/EKS + autoscaling.

Soporte: arquitectura (`docs/architecture.md`), métricas por bodega y por consumidor (RPS, P95, hit ratio, profundidad de colas, `worker_messages_*`), pruebas (`tests/load/run_matrix.sh` 1 vs 3 réplicas, Redis OFF/ON; `tests/failure/05_warehouse_down.sh` aislamiento) y decisiones (ADR-001, 003, 008, 010, 012).
