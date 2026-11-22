# ADR-011 — S3 + Batch vs Worker vs Lambda

**Estado:** Aceptada

## Contexto

Hay procesos pesados o periódicos que no deben bloquear las APIs: reportes de inventario, consolidado diario de movimientos y validación de archivos que llegan desde fuera (README §15-§17). Cada uno tiene un patrón de activación distinto.

## Alternativas

Para cada caso: worker continuo, proceso Batch programado o función Lambda. Comparación completa en `docs/comparisons.md`.

## Decisión

S3 como almacenamiento de objetos común (bucket `warehouse-platform-<sufijo>`: versionado, SSE AES256, acceso público bloqueado; S3Mock en local), y una herramienta por caso de uso:

| Caso | Mecanismo | Implementación |
|---|---|---|
| Reporte **bajo demanda** (`POST /reports` -> 202 + `job_id`) | **Worker** continuo | `report-worker` consume `q.reports`; escribe `reports/{yyyy}/{mm}/{job_id}.json`, actualiza `report_jobs`, publica `REPORT_COMPLETED` |
| Consolidado **diario** de movimientos | **Batch** programado | `python -m batch.daily_report --date`: consulta `GET /inventory/movements?date=` y `/products` de cada bodega; escribe `reports/daily/{date}/movements-{date}.json` y `warehouses/{WH}/{yyyy}/{mm}/inventory-{date}.json`; registra `batch_runs` (upsert por `run_date`, reejecutable). Una bodega caída => `COMPLETED_WITH_FAILURES` |
| Archivos **externos** `exports/*.json|csv` | **Lambda** por evento S3 | `services/lambda/s3_processor`: valida `sku, warehouse_id, quantity`, escribe `processed/<n>.json` + `.meta.json`, o copia a `rejected/`; logs JSON a CloudWatch |

Estado real de despliegue: el worker de reportes corre local y en AWS (EC2 `platform`); la Lambda se despliega con Terraform (módulo `lambda`, trigger prefijo `exports/`, `LabRole`); el Batch tiene imagen en ECR y servicio compose bajo el perfil `batch`, pero **no hay planificador desplegado en AWS** (la propuesta documentada en `services/batch/README.md` es una tarea ECS programada con EventBridge; en local, cron del host).

## Consecuencias / Trade-offs

- El trabajo pesado sale del camino de las APIs; cada herramienta se paga solo cuando se usa (Lambda/Batch) o es un proceso ya existente (worker).
- Tres mecanismos aumentan la superficie operativa; se justifica porque difieren en activación (evento de mensaje, calendario, evento S3).
- El batch y el reporte worker leen de fuentes distintas: batch consulta las bodegas (movimientos del día, fuente de verdad), el worker usa el read model (eventual); sus resultados pueden diferir por el retraso de la proyección.
- La Lambda tiene tiempo máximo de ejecución y no mantiene estado: apta para archivos pequeños/medianos.

## Mitigación

- Claves S3 deterministas y escritura que sobrescribe: reintentos y reejecuciones son seguros.
- El reporte incluye `warehouse_last_event_at` y la fuente "read model, eventual consistency".
- Los prefijos de salida de Lambda (`processed/`, `rejected/`) difieren de `exports/` para no re-disparar el trigger; el bucket expira `exports/` y `processed/` a los 30 días.
- Pruebas unitarias en `tests/unit/batch` y `tests/unit/lambda`.
