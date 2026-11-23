#!/usr/bin/env bash
# Caso 8: broker caído -> las bodegas y la central siguen atendiendo; al volver, todo se sincroniza.
#   - cola local de la bodega (outbox) acumula eventos y los envía al reconectar
#   - cola de respaldo de la central (command_outbox) guarda la transferencia y command-relay la publica
#   - el relay publica un INVENTORY_SNAPSHOT tras reconectar y corrige divergencias del read model
# Requiere: profiles events + central levantados (ver README).
source "$(dirname "$0")/common.sh"
PY="$(command -v python3 2>/dev/null || true)"; "${PY:-false}" -c 1 2>/dev/null || PY="$(command -v python)"
jget() { "$PY" -c 'import json,sys
try: d=json.load(sys.stdin); r=eval(sys.argv[1])
except Exception: r=""
print("" if r is None else r)' "$1"; }
FAILS=0
check() { if [ "$1" = "$2" ]; then ok "$3 ($1)"; else fail "$3: obtenido '$1', esperado '$2'"; FAILS=$((FAILS + 1)); fi; }
wait_eq() { # wait_eq <segundos> <esperado> <comando...>
  local t="$1" want="$2"; shift 2; local start=$SECONDS v
  while :; do v="$("$@")"; [ "$v" = "$want" ] && { echo "$v"; return 0; }
    (( SECONDS - start >= t )) && { echo "$v"; return 1; }; sleep 3; done; }
wh_qty() { curl -s "$CENTRAL/global/inventory/$1" | jget "[w['quantity'] for w in d['warehouses'] if w['warehouse_id']=='$2'][0]"; }
post() { curl -s -o /dev/null -w '%{http_code}' -X POST "$1" -H 'Content-Type: application/json' -d "$2"; }

title "Caso 8: broker caído, colas de respaldo y resincronización"
A="SYNC-A-$RANDOM"; B="SYNC-B-$RANDOM"
step "Preparando $A (WH-002 y WH-003) y $B (WH-002)"
for w in "$WH2" "$WH3"; do post "$w/products" "{\"sku\":\"$A\",\"name\":\"A\",\"unit\":\"u\"}" >/dev/null; done
post "$WH2/products" "{\"sku\":\"$B\",\"name\":\"B\",\"unit\":\"u\"}" >/dev/null
post "$WH2/inventory/movements" "{\"sku\":\"$A\",\"type\":\"RECEIPT\",\"quantity\":20}" >/dev/null
post "$WH2/inventory/movements" "{\"sku\":\"$B\",\"type\":\"RECEIPT\",\"quantity\":7}" >/dev/null
check "$(wait_eq 60 20 wh_qty "$A" WH-002)" 20 "central sincronizada: $A en WH-002"
check "$(wait_eq 60 7 wh_qty "$B" WH-002)" 7 "central sincronizada: $B en WH-002"

step "Deteniendo el broker"; dc stop rabbitmq >/dev/null 2>&1
codes=""; for i in 1 2 3 4 5; do codes+="$(post "$WH2/inventory/movements" "{\"sku\":\"$A\",\"type\":\"RECEIPT\",\"quantity\":2,\"reference\":\"down-$i\"}") "; done
check "$(echo $codes)" "201 201 201 201 201" "WH-002 sigue aceptando movimientos sin broker"
pending="$(curl -s "$WH2/sync/status" | jget 'd["outbox_pending"]')"
[ "${pending:-0}" -ge 5 ] && ok "cola local WH-002: $pending eventos pendientes" || { fail "outbox_pending=$pending"; FAILS=$((FAILS + 1)); }
tr="$(curl -s -X POST "$CENTRAL/transfers" -H 'Content-Type: application/json' \
  -d "{\"sku\":\"$A\",\"source_warehouse\":\"WH-002\",\"destination_warehouse\":\"WH-003\",\"quantity\":3}")"
TR_ID="$(echo "$tr" | jget 'd["transfer_id"]')"
check "$(echo "$tr" | jget 'd["delivery"]')" queued "central acepta la transferencia $TR_ID en la cola de respaldo"
check "$(curl -s "$CENTRAL/health" | jget 'd["pending_commands"] >= 1')" True "command_outbox con comandos pendientes"
step "Simulando divergencia en la central: $B/WH-002 = 999 (como si se hubiera perdido un evento)"
psql_central "UPDATE global_inventory SET quantity = 999 WHERE sku = '$B' AND warehouse_id = 'WH-002';" >/dev/null

step "Levantando el broker"; dc start rabbitmq >/dev/null 2>&1
check "$(wait_eq 120 0 sh -c "curl -s $WH2/sync/status | \"$PY\" -c 'import json,sys; print(json.load(sys.stdin)[\"outbox_pending\"])'")" 0 "WH-002 vació su cola local"
check "$(wait_eq 120 0 sh -c "curl -s $CENTRAL/health | \"$PY\" -c 'import json,sys; print(json.load(sys.stdin)[\"pending_commands\"])'")" 0 "command-relay publicó los comandos"
tstatus() { curl -s "$CENTRAL/transfers/$TR_ID" | jget 'd["status"]'; }
check "$(wait_eq 120 COMPLETED tstatus)" COMPLETED "transferencia $TR_ID"
check "$(wait_eq 60 27 wh_qty "$A" WH-002)" 27 "WH-002 = 20 + 10 - 3"
check "$(wait_eq 60 3 wh_qty "$A" WH-003)" 3 "WH-003 recibió la transferencia"
check "$(wait_eq 60 7 wh_qty "$B" WH-002)" 7 "snapshot de reconexión corrigió la divergencia de $B"
snap="$(curl -s "$CENTRAL/reports/warehouses" | jget "[w for w in d['warehouses'] if w['warehouse_id']=='WH-002'][0]['sync']['last_snapshot_at']")"
[ -n "$snap" ] && ok "WH-002 last_snapshot_at = $snap" || { fail "sin snapshot registrado"; FAILS=$((FAILS + 1)); }
online="$(curl -s "$CENTRAL/reports/warehouses" | jget 'd["totals"]["online"]')"
check "$online" 3 "reporte: bodegas ONLINE"
[ $FAILS -eq 0 ] && ok "Caso 8 completo" || { fail "Caso 8: $FAILS comprobaciones fallidas"; exit 1; }
