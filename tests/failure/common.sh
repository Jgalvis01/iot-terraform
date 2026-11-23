#!/usr/bin/env bash
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CDIR="$ROOT/infrastructure/compose"
if docker compose version >/dev/null 2>&1; then DC=(docker compose); else DC=(docker-compose); fi
# COMPOSE_OVERRIDE=/ruta/override.yml para añadir un fichero extra (p.ej. remapear puertos)
dc() { "${DC[@]}" --project-directory "$CDIR" -f "$CDIR/docker-compose.yml" ${COMPOSE_OVERRIDE:+-f "$COMPOSE_OVERRIDE"} "$@"; }
WH1=${WH1:-http://localhost:8001}; WH2=${WH2:-http://localhost:8002}; WH3=${WH3:-http://localhost:8003}
CENTRAL=${CENTRAL:-http://localhost:8000}
title() { printf '\n\033[1;34m### %s\033[0m\n' "$*"; }
step()  { printf '\033[1;33m>> %s\033[0m\n' "$*"; }
ok()    { printf '\033[1;32mOK: %s\033[0m\n' "$*"; }
fail()  { printf '\033[1;31mFALLO: %s\033[0m\n' "$*"; }
j()     { python3 -m json.tool 2>/dev/null || cat; }
queues() { dc exec -T rabbitmq rabbitmqctl -q list_queues name messages_ready messages_unacknowledged | grep -E "${1:-q\.}" ; }
psql_wh() { dc exec -T "$1-db" psql -U postgres -d warehouse_db -tAc "$2"; }
psql_central() { dc exec -T central-db psql -U postgres -d central_read -tAc "$1"; }
