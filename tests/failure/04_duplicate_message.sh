#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
title "Caso 4: mensaje duplicado"
EVT="EVT-DUP-$(date +%s)"
step "Publicando dos veces el evento $EVT (INVENTORY_UPDATED) en warehouse.events"
dc exec -T projection-worker python - "$EVT" <<'PY'
import sys
from wcommon import rabbit
from wcommon.events import build_event
evt = build_event("INVENTORY_UPDATED", "warehouse-001", {"warehouse_id": "WH-001", "sku": "P-DUP", "product_id": 999,
      "quantity": 7, "reserved_quantity": 0, "movement_type": "RECEIPT", "movement_quantity": 7, "reference": "dup"},
      event_id=sys.argv[1])
ch = rabbit.open_publisher_channel(rabbit.connect())
for _ in range(2):
    rabbit.publish(ch, rabbit.EVENTS_EXCHANGE, "inventory.updated.wh-001", evt)
print("publicado 2 veces", evt["event_id"])
PY
sleep 5
step "processed_events para $EVT (debe haber 1 fila por consumidor, no 2)"
psql_central "SELECT consumer, count(*) FROM processed_events WHERE event_id='$EVT' GROUP BY consumer;"
step "audit_log para $EVT (1 fila)"; psql_central "SELECT count(*) FROM audit_log WHERE event_id='$EVT';"
step "Métrica de duplicados ignorados"
dc exec -T projection-worker python -c "import urllib.request;print('\n'.join(l for l in urllib.request.urlopen('http://localhost:9100/metrics').read().decode().splitlines() if 'duplicated' in l and not l.startswith('#')))"
