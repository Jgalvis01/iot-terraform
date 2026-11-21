#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
SCRIPT="$1"; shift
mkdir -p results
# en windows (git bash) la ruta tiene que ir como C:/...
HOST_DIR="$(pwd -W 2>/dev/null || pwd)"
MSYS_NO_PATHCONV=1 docker run --rm -i --user "$(id -u):$(id -g)" --add-host=host.docker.internal:host-gateway \
  -v "$HOST_DIR:/scripts:ro,z" -v "$HOST_DIR/results:/results:z" \
  grafana/k6:0.53.0 run "$@" "/scripts/$SCRIPT"
