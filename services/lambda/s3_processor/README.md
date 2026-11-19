# Lambda s3_processor

Handler: `handler.lambda_handler`. Trigger `s3:ObjectCreated:*` con prefijo `exports/`. Solo stdlib + boto3 (incluido en el runtime Lambda).
Variable opcional: `S3_ENDPOINT_URL` (S3Mock local).

Por cada objeto `exports/<nombre>.json|csv` (columnas/campos `sku, warehouse_id, quantity`):
- valida cada fila (`sku` no vacio, `warehouse_id` = `WH-NNN`, `quantity` entero >= 0);
- escribe `processed/<nombre>.json` (filas validas normalizadas) y `processed/<nombre>.meta.json` (`source_key, rows, valid_rows, invalid_rows, processed_at` + `invalid_details`);
- si no hay filas validas o el archivo es ilegible: copia el original a `rejected/<nombre>` y registra el error.
Logs: una linea JSON por evento (CloudWatch). Nota: la salida usa prefijos distintos a `exports/`, por lo que no re-dispara el trigger.

Prueba local: subir `sample_exports/*` a `exports/` del bucket o invocar `lambda_handler` con un evento S3 (ver `tests/unit/lambda`).
Empaquetado: `zip handler.zip handler.py`.
