#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
title "Caso 2: RabbitMQ temporalmente caído (patrón Outbox)"
step "Deteniendo rabbitmq"; dc stop rabbitmq
for i in 1 2 3; do
  curl -s -o /dev/null -w "movimiento $i -> HTTP %{http_code}\n" -X POST "$WH1/inventory/movements" \
    -H 'Content-Type: application/json' -d "{\"sku\":\"P-002\",\"type\":\"RECEIPT\",\"quantity\":1,\"reference\":\"rmq-down-$i\"}"
done
step "Eventos pendientes en outbox de WH-001 (deben ser >= 3)"
psql_wh wh-001 "SELECT count(*) FROM outbox WHERE published_at IS NULL;"
step "Levantando rabbitmq y esperando al relay"; dc start rabbitmq; sleep 25
pending=$(psql_wh wh-001 "SELECT count(*) FROM outbox WHERE published_at IS NULL;" | tr -d '[:space:]')
[ "$pending" = "0" ] && ok "outbox vacío: eventos publicados tras la recuperación" || fail "quedan $pending eventos pendientes (espera unos segundos más y repite la consulta)"
