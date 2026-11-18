#!/usr/bin/env bash
set -uo pipefail
URLS=("${1:-http://localhost:8001}" "${2:-http://localhost:8002}" "${3:-http://localhost:8003}")
STOCK=(100 20 80)
NAMES=("Tornillo M6" "Caja carton" "Pallet madera" "Cinta embalaje" "Guantes nitrilo")
for i in 0 1 2; do
  url=${URLS[$i]}
  echo "== Sembrando $url"
  for n in 1 2 3 4 5; do
    sku="P-00$n"
    code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$url/products" -H 'Content-Type: application/json' \
      -d "{\"sku\":\"$sku\",\"name\":\"${NAMES[$((n-1))]}\",\"unit\":\"unidad\",\"category\":\"insumos\"}")
    if [ "$code" = "201" ]; then
      curl -s -o /dev/null -X POST "$url/inventory/movements" -H 'Content-Type: application/json' \
        -d "{\"sku\":\"$sku\",\"type\":\"RECEIPT\",\"quantity\":${STOCK[$i]},\"reference\":\"seed\"}"
      echo "  $sku creado con stock ${STOCK[$i]}"
    else
      echo "  $sku no creado (HTTP $code: 409=ya existía, 000=API no disponible)"
    fi
  done
done
