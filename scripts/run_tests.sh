#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"

SUITES=(
  "tests/unit/warehouse_api|warehouse|services/warehouse-api/requirements-dev.txt|"
  "tests/unit/central|central|services/central-api/requirements.txt,services/workers/requirements.txt|pytest==8.3.4"
  "tests/unit/iot|iot|services/iot/requirements.txt,services/batch/requirements.txt|pytest==8.3.4 moto[s3]==5.0.28"
  "tests/unit/batch|iot||"
  "tests/unit/lambda|iot||"
)
if [ "${1:-}" = "--all" ]; then
  SUITES+=("tests/integration/warehouse_api|warehouse||" "tests/integration/central|central||")
  cleanup() { docker rm -f wh-test-pg central-test-pg central-test-rmq >/dev/null 2>&1 || true; }
  trap cleanup EXIT; cleanup
  docker run -d --name wh-test-pg -e POSTGRES_PASSWORD=postgres -p 55432:5432 postgres:15-alpine >/dev/null
  docker run -d --name central-test-pg -e POSTGRES_PASSWORD=postgres -p 55433:5432 postgres:15-alpine >/dev/null
  docker run -d --name central-test-rmq -p 55672:5672 rabbitmq:3.13-management >/dev/null
  echo "Esperando a Postgres y RabbitMQ (20 s)..."; sleep 20
fi

FAILED=0
for suite in "${SUITES[@]}"; do
  IFS='|' read -r group venv reqs extra <<<"$suite"
  V="$ROOT/.venvs/$venv"
  if [ ! -x "$V/bin/python" ] || [ -n "$reqs" ] && [ ! -f "$V/.ok" ]; then
    echo "Creando venv $venv..."
    python3 -m venv "$V"
    args=(./libs/wcommon); IFS=',' read -ra rs <<<"$reqs"; for r in "${rs[@]}"; do args+=(-r "$r"); done
    "$V/bin/pip" install -q "${args[@]}" $extra && touch "$V/.ok"
  fi
  printf '\n=== %s ===\n' "$group"
  "$V/bin/python" -m pytest -q "$group" 2>&1 | tail -2
  [ "${PIPESTATUS[0]}" -eq 0 ] || FAILED=1
done
if [ $FAILED -eq 0 ]; then echo -e "\nTODOS LOS TESTS PASARON"; else echo -e "\nHAY TESTS FALLIDOS"; exit 1; fi
