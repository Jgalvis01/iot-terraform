# ADR-014 — IoT: MQTT + ingestion + dedupe/orden por `sequence_number`

**Estado:** Aceptada (extensión, solo local)

## Contexto

Las bodegas tendrían sensores (temperatura, humedad, puerta, movimiento, energía) que emiten lecturas frecuentes en redes poco fiables: pueden llegar **duplicadas, fuera de orden** o tras una desconexión (README §23-§24). Las lecturas no deben tocar el inventario ni las APIs de bodega.

## Alternativas

1. Sensores publican por HTTP directo a la API.
2. Sensores publican directo a RabbitMQ (AMQP) — pesado para dispositivos.
3. **MQTT al broker Mosquitto + un servicio `ingestion` que traduce a eventos de RabbitMQ** (elegida).
4. AWS IoT Core (no disponible/costoso en Learner Lab).

## Decisión

- **Sensores (simulador):** `iot.simulator` publica en `warehouses/{WH}/sensors/{SENSOR_ID}` con **QoS 1** (at-least-once). Mensaje: `{event_id, sequence_number, warehouse_id, sensor_id, sensor_type, value, unit, timestamp}`. Inyecta duplicados (`DUPLICATE_RATE`), desorden (`OUT_OF_ORDER_RATE`) y ~3 % de valores fuera de umbral; si el broker cae, usa un buffer en memoria (`BUFFER_SIZE`) y lo vacía al reconectar.
- **Ingestion** (`iot.ingestion`): se suscribe a `warehouses/+/sensors/+`, **valida** (campos obligatorios, `warehouse_id` con formato, `sequence_number` entero >= 0, `value` numérico, `timestamp` ISO8601) y publica `SENSOR_READING` en `warehouse.events` con routing key `iot.reading.<wh>`, **conservando el `event_id` del sensor**. Si RabbitMQ cae, bufferiza en memoria (`MAX_PENDING`) y reconecta.
- **`iot-storage-worker`** (`q.iot.storage`): idempotente por `event_id` (`processed_events`, consumer `iot_storage`); mantiene `sensor_state(warehouse_id, sensor_id, last_sequence_number)` y **descarta** lecturas con `sequence_number <=` último aceptado (`iot_out_of_order_total`), guardando el resto en `sensor_readings`.
- **`iot-alert-worker`** (`q.iot.alerts`): umbrales por env (`TEMP_MAX_C=8`, `HUMIDITY_MAX_PCT=70`, `DOOR_OPEN_MAX_SECONDS=60`); publica `SENSOR_ALERT` (`iot.alert.<wh>`, `event_id` determinista `ALR-<event_id>`) y guarda `sensor_alerts`.
- Ambas colas están enlazadas a `iot.reading.*`; `q.audit` (`#`) también recibe lecturas y alertas.
- Mosquitto 2 (puerto 1883, anónimo, persistencia) con perfil compose `iot`; métricas `iot_sim_*` / `iot_ingestion_*` en `:9100`.

## Consecuencias / Trade-offs

- MQTT QoS 1 + deduplicación por `event_id` da efecto exactly-once en almacenamiento; el `sequence_number` por sensor da un orden lógico independiente del reloj.
- **Descartar** lecturas con `sequence_number` menor evita retroceder el estado, pero **pierde** lecturas válidas que llegaron tarde (no se reordenan ni se insertan históricas).
- Un `sequence_number` que se reinicia (reinicio del sensor) haría que se descarten lecturas hasta superar el último valor (no se maneja "epoch").
- Los buffers en memoria (simulador, ingestion) se pierden si el proceso muere.
- Volumen: 100 sensores cada 2 s generan ~50 mensajes/s con un registro por lectura; `sensor_readings` crece sin política de retención ni downsampling.
- El `occurred_at` del evento lo fija ingestion; el instante real de medición va en `payload.measured_at`.
- No se despliega en AWS (ADR-012).

## Mitigación

- Alertas idempotentes (id determinista) y deduplicadas por `processed_events`.
- Métricas de duplicados (`worker_messages_duplicated_total`) y desorden (`iot_out_of_order_total`) para cuantificar la pérdida.
- Propuesta futura: ventana de reordenamiento, retención/rollup de lecturas, MQTT con autenticación y TLS, y AWS IoT Core en producción.
