#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
title "Caso 5: WH-002 caída"
step "Deteniendo WH-002 (lb + réplicas + db)"; dc stop wh-002-lb wh-002-api-1 wh-002-api-2 wh-002-api-3
step "WH-001 y WH-003 siguen funcionando"
curl -s -o /dev/null -w "WH-001 health -> %{http_code}\n" "$WH1/health"
curl -s -o /dev/null -w "WH-003 health -> %{http_code}\n" "$WH3/health"
curl -s -o /dev/null -w "WH-002 health -> %{http_code}\n" --max-time 3 "$WH2/health"
step "Consulta global por READ MODEL (responde rápido; WH-002 aparece stale tras STALE_AFTER_SECONDS sin eventos)"
curl -s "$CENTRAL/global/inventory/P-001?mode=readmodel" | j
step "Consulta global por FAN-OUT (WH-002 aparece en unavailable_warehouses)"
curl -s -o /tmp/fanout.json -w "(tiempo total %{time_total}s, HTTP %{http_code})\n" "$CENTRAL/global/inventory/P-001?mode=fanout"; j < /tmp/fanout.json
step "Recuperando WH-002"; dc --profile lb start wh-002-api-1 wh-002-api-2 wh-002-api-3 wh-002-lb; sleep 5
curl -s -o /dev/null -w "WH-002 health -> %{http_code}\n" "$WH2/health"
