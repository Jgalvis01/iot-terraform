#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
title "Caso 3: projection-worker detenido"
step "Deteniendo projection-worker"; dc stop projection-worker
for i in $(seq 1 5); do
  curl -s -o /dev/null -X POST "$WH1/inventory/movements" -H 'Content-Type: application/json' \
    -d "{\"sku\":\"P-003\",\"type\":\"RECEIPT\",\"quantity\":2,\"reference\":\"worker-down-$i\"}"
done
sleep 3
step "Profundidad de q.projection (mensajes esperando)"; queues '^q\.projection\s'
step "Read model (desactualizado mientras el worker está caído)"; curl -s "$CENTRAL/global/inventory/P-003" | j
step "Arrancando projection-worker"; dc start projection-worker; sleep 8
step "Cola drenada"; queues '^q\.projection\s'
step "Read model actualizado"; curl -s "$CENTRAL/global/inventory/P-003" | j
step "Inventario real en WH-001 (debe coincidir con el read model)"; curl -s "$WH1/inventory/by-sku/P-003" | j
