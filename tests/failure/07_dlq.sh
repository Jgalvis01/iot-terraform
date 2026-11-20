#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
title "Fase 10: DLQ"
step "Publicando JSON corrupto y un evento sin campos obligatorios en q.projection"
dc exec -T projection-worker python - <<'PY'
import pika
from wcommon import rabbit
ch = rabbit.open_publisher_channel(rabbit.connect())
ch.basic_publish("", "q.projection", b"{esto no es json", pika.BasicProperties(delivery_mode=2))
ch.basic_publish("", "q.projection", b'{"event_type":"INVENTORY_UPDATED"}', pika.BasicProperties(delivery_mode=2))
print("publicados 2 mensajes inválidos")
PY
sleep 4
step "Colas DLQ (q.projection.dlq debe tener >= 2)"; queues 'dlq'
step "Ver detalle: http://localhost:15672 (guest/guest) -> Queues -> q.projection.dlq -> Get messages (header x-error)"
