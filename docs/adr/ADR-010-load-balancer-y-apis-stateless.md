# ADR-010 — Load balancer: Nginx local / ALB en AWS, y APIs stateless

**Estado:** Aceptada

## Contexto

La bodega de más tráfico (WH-002 en el enunciado, 800 req/s de lectura) debe poder escalar horizontalmente sin afectar al resto (README §12). Para repartir peticiones entre réplicas, estas deben ser intercambiables.

## Alternativas

1. Escalar verticalmente (instancia más grande).
2. Balanceo en el cliente / DNS round-robin.
3. **Reverse proxy / load balancer** delante de N réplicas: Nginx (local), ALB (AWS).

## Decisión

**APIs stateless** (`warehouse-api`): no guardan sesión ni estado en memoria de proceso; el estado vive en PostgreSQL (verdad) y Redis (caché compartida por bodega). La concurrencia entre réplicas se resuelve en la BD con `SELECT ... FOR UPDATE`; la caché compartida significa que una invalidación vale para todas las réplicas. El outbox relay corre como proceso separado (`SKIP LOCKED` permitiría varios). El correlation id viaja por cabecera, no por estado.

**Local (Docker Compose):** `wh-002-lb` (nginx 1.25) en el puerto 8002 balancea round robin sobre `wh-002-api-1/2/3` (`infrastructure/compose/nginx/wh-002-lb.conf`): health check pasivo (`max_fails=2 fail_timeout=10s`), `proxy_next_upstream` ante `error timeout 502 503 504`, `proxy_connect_timeout 2s`, reenvía `X-Correlation-ID` y añade `X-Upstream` para ver qué réplica respondió. Solo `wh-002-api-1` está en el perfil por defecto; `wh-002-api-2` y `-3` pertenecen al perfil `lb`. WH-001 y WH-003 no tienen balanceador. El servicio `frontend` (nginx, perfil `frontend`) sirve la UI estática y hace proxy de `/api/` a una API (`API_TARGET`); no es un balanceador de réplicas.

**AWS:** un único ALB público (`warehouse-platform-alb`) con un listener por servicio: `:80 -> central-api`, `:8001 -> WH-001`, `:8002 -> WH-002`, `:8003 -> WH-003`; target groups tipo `instance`, health check `GET /health` cada 15 s (2 OK / 2 fallos). El escalamiento de WH-002 es `wh002_instances` (default 2) EC2 `wh-b` registradas en el target group de `:8002` (`terraform apply -var wh002_instances=3`). La central llega a las bodegas por el **DNS del ALB** (estable al reiniciar el lab).

## Consecuencias / Trade-offs

- Escalamiento horizontal por bodega, tolerancia a caída de una réplica y despliegues sin corte.
- Todas las réplicas comparten la misma BD: escalar APIs no escala escrituras; el cuello de botella pasa a PostgreSQL (y a los locks por fila).
- El health check de Nginx es pasivo (detecta fallos con tráfico real); el ALB es activo.
- Un listener por puerto en un ALB es una decisión por presupuesto/simplicidad del Learner Lab: producción usaría host/path routing con HTTPS (ver ADR-012).
- La ruta ALB -> `wh-001` y `wh-003` apunta a una sola instancia (`wh-a`): no escalan horizontalmente por defecto.

## Mitigación

- Pruebas 1 vs 3 réplicas con `tests/load/run_matrix.sh` (experimento B) y panel Grafana "Distribución por réplica (WH-002)".
- Caché Redis por bodega para absorber lecturas antes de que lleguen a PostgreSQL (ADR-002).
- Para escrituras calientes: particionar por SKU, réplicas de lectura o BD más grande solo para esa bodega (ADR-001).
