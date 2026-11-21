#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE="docker-compose --project-directory $ROOT/infrastructure/compose -f $ROOT/infrastructure/compose/docker-compose.yml"
command -v docker-compose >/dev/null || COMPOSE="docker compose --project-directory $ROOT/infrastructure/compose -f $ROOT/infrastructure/compose/docker-compose.yml"
VUS_LIST="${1:-100 500 1000 5000}"
K6="$ROOT/tests/load/run_k6.sh"

run() { "$K6" inventory_read.js -e BASE_URL="$1" -e VUS="$2" -e DURATION=60s -e RESULT_NAME="$3" || true; }

echo "### Experimento A: Redis OFF vs ON (WH-001, 1 instancia)"
for mode in false true; do
  CACHE_ENABLED=$mode $COMPOSE up -d --no-deps wh-001-api
  sleep 8
  for v in $VUS_LIST; do run http://host.docker.internal:8001 "$v" "wh001_cache_${mode}_${v}vus"; done
done

echo "### Experimento B: WH-002 con 1 réplica vs 3 réplicas (detrás de Nginx)"
$COMPOSE stop wh-002-api-2 wh-002-api-3 || true
sleep 12
for v in $VUS_LIST; do run http://host.docker.internal:8002 "$v" "wh002_1replica_${v}vus"; done
$COMPOSE --profile lb up -d wh-002-api-2 wh-002-api-3
sleep 10
for v in $VUS_LIST; do run http://host.docker.internal:8002 "$v" "wh002_3replicas_${v}vus"; done

echo "Resultados JSON en tests/load/results/. Resumen:"
python3 "$ROOT/tests/load/summarize.py" "$ROOT/tests/load/results"
