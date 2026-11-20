#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
title "Caso 1: Redis de WH-001 caído"
step "Lectura con Redis arriba (miss y luego hit)"
curl -s "$WH1/inventory/by-sku/P-001" | j; curl -s -o /dev/null "$WH1/inventory/by-sku/P-001"
step "Deteniendo wh-001-redis"; dc stop wh-001-redis
step "Lectura con Redis caído (debe responder 200 desde PostgreSQL)"
code=$(curl -s -o /tmp/r.json -w '%{http_code}' "$WH1/inventory/by-sku/P-001"); cat /tmp/r.json; echo
[ "$code" = "200" ] && ok "API respondió $code sin Redis" || fail "API respondió $code"
step "Health muestra redis=down"; curl -s "$WH1/health" | j
step "Escritura con Redis caído (la invalidación falla pero el commit se mantiene)"
curl -s -X POST "$WH1/inventory/movements" -H 'Content-Type: application/json' \
  -d '{"sku":"P-001","type":"RECEIPT","quantity":1,"reference":"redis-down-test"}' | j
step "Métricas de caché"; curl -s "$WH1/metrics" | grep -E '^cache_(hits|misses|errors)_total'
step "Recuperando Redis"; dc start wh-001-redis; sleep 2; curl -s "$WH1/health" | j
