# IoT (simulador + ingestion + mosquitto)

Imagen `iot` (contexto raiz): `docker build -f services/iot/Dockerfile .`

- `python -m iot.simulator` (default CMD): N sensores publican en `warehouses/{WH}/sensors/{SENSOR_ID}` (QoS 1) cada `INTERVAL_SECONDS`.
  Mensaje: `{event_id, sequence_number, warehouse_id, sensor_id, sensor_type, value, unit, timestamp}`.
  Sensores: temperature (C), humidity (%), door (`value` = segundos que lleva abierta, 0 = cerrada), motion (0/1), energy (kW).
  ~3 % de lecturas fuera de umbral. Duplicados (`DUPLICATE_RATE`) = mismo mensaje reenviado; desorden (`OUT_OF_ORDER_RATE`) = un mensaje se retiene y sale despues del siguiente.
  Si el broker cae, los mensajes van a un buffer (`BUFFER_SIZE`) y se vacian al reconectar.
- `python -m iot.ingestion`: suscribe `warehouses/+/sensors/+`, valida, publica `SENSOR_READING` (conserva `event_id` del sensor, `source=iot-ingestion`) en `warehouse.events` con routing key `iot.reading.<wh-minusculas>`. Si RabbitMQ cae, bufferiza en memoria (`MAX_PENDING`) y reconecta.
- Metricas Prometheus en `:9100` (`iot_sim_*`, `iot_ingestion_*`).
- `mosquitto/mosquitto.conf`: listener 1883, anonimo, persistencia (usar con `eclipse-mosquitto:2`).

Variables: ver `.env.example`. Tests: `PYTHONPATH=services/iot pytest tests/unit/iot`.
