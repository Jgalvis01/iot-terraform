#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
transfer() {
  local hdr=(); [ -n "${2:-}" ] && hdr=(-H "X-Fail-Step: $2")
  curl -s -X POST "$CENTRAL/transfers" -H 'Content-Type: application/json' "${hdr[@]}" \
    -d "{\"sku\":\"P-001\",\"source_warehouse\":\"WH-001\",\"destination_warehouse\":\"WH-002\",\"quantity\":$1}" \
    | python3 -c 'import sys,json;print(json.load(sys.stdin)["transfer_id"])'
}
show() { sleep "${2:-6}"; curl -s "$CENTRAL/transfers/$1" | python3 -c 'import sys,json;t=json.load(sys.stdin);print(t["transfer_id"],"=>",t["status"],"|",t.get("reason"));print("  historia:"," -> ".join(h["status"] for h in t.get("history",[])))'; }
stock() { for w in "$WH1" "$WH2"; do curl -s "$w/inventory/by-sku/P-001" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("  %s: quantity=%s reserved=%s" % (d["warehouse_id"], d["quantity"], d["reserved_quantity"]))'; done; }

title "Saga 1: transferencia exitosa de 30 unidades WH-001 -> WH-002"; stock
T=$(transfer 30); show "$T"; stock
title "Saga 2: stock insuficiente (FAILED, sin compensación)"
T=$(transfer 999999); show "$T"
title "Saga 3: fallo inyectado ANTES de reservar"
T=$(transfer 5 before_reserve); show "$T" 25
title "Saga 4: fallo inyectado DESPUÉS de reservar (COMPENSATING -> CANCELLED, reserva liberada)"
T=$(transfer 5 after_reserve); show "$T" 25; stock
title "Saga 5: destino caído (reintentos, luego compensación)"
T=$(transfer 5 destination_down); show "$T" 30; stock
title "Saga 6: redelivery del comando de una transferencia COMPLETED (idempotencia: no repite efectos)"
T=$(transfer 10); show "$T"; stock
dc exec -T transfer-orchestrator python - "$T" <<'PY'
import sys
from wcommon import rabbit
from wcommon.events import build_event
ch = rabbit.open_publisher_channel(rabbit.connect())
cmd = build_event("PROCESS_TRANSFER", "failure-test", {"transfer_id": sys.argv[1]})
rabbit.publish(ch, rabbit.COMMANDS_EXCHANGE, "transfer.process", cmd)
print("comando reenviado para", sys.argv[1])
PY
show "$T" 5; stock
echo "(El stock no debe cambiar tras el reenvío)"
