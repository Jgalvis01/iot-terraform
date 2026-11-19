# Batch: reporte diario de movimientos

`python -m batch.daily_report [--date YYYY-MM-DD]` (default: ayer UTC).

Por cada bodega de `WAREHOUSES` consulta `GET {url}/inventory/movements?date=...` y `GET {url}/products`.
Una bodega que falla se anota en `failed_warehouses` del consolidado (estado `COMPLETED_WITH_FAILURES`) sin abortar.

Salidas (se sobrescriben al reejecutar):
- `warehouses/{WH}/{yyyy}/{mm}/inventory-{date}.json`
- `reports/daily/{date}/movements-{date}.json` (totales por tipo, sku y bodega)

Registro en `batch_runs(run_id, run_date, status, s3_key, started_at, finished_at, error)` (upsert por `run_date`, `run_id = BATCH-<date>`; la tabla se crea si no existe). Si no hay `DATABASE_URL` se omite.
El bucket se crea automaticamente solo si hay `S3_ENDPOINT_URL` (S3Mock).

Variables: ver `.env.example`.

## Ejecucion
```
docker build -f services/batch/Dockerfile -t batch .
docker compose run --rm batch python -m batch.daily_report --date 2026-09-28
```
Cron (host, diario 23:00 UTC; se pasa la fecha de hoy porque el default es ayer):
```
0 23 * * * cd /ruta/ProyectoIOT && docker compose run --rm batch python -m batch.daily_report --date $(date -u +\%F) >> /var/log/batch.log 2>&1
```
En AWS: tarea ECS programada con EventBridge usando la misma imagen.
