#!/usr/bin/env bash
set -euo pipefail

TAG="${1:-latest}"
shift || true
REGION="${AWS_REGION:-us-east-1}"
PLATFORM="${DOCKER_PLATFORM:-linux/amd64}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

declare -A DOCKERFILES=(
  [warehouse-api]=services/warehouse-api/Dockerfile
  [central-api]=services/central-api/Dockerfile
  [workers]=services/workers/Dockerfile
  [batch]=services/batch/Dockerfile
  [iot]=services/iot/Dockerfile
  [frontend]=services/frontend/Dockerfile
)
ORDER=(warehouse-api central-api workers batch iot frontend)

if [[ $# -gt 0 ]]; then
  IMAGES=("$@")
else
  IMAGES=("${ORDER[@]}")
fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

echo "==> Login en ${REGISTRY}"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

FAILED=()
for name in "${IMAGES[@]}"; do
  dockerfile="${DOCKERFILES[$name]:-}"
  if [[ -z "$dockerfile" ]]; then
    echo "ERROR: imagen desconocida '$name' (válidas: ${ORDER[*]})" >&2
    exit 1
  fi
  if [[ ! -f "$dockerfile" ]]; then
    echo "AVISO: $dockerfile no existe; se omite $name" >&2
    FAILED+=("$name(sin Dockerfile)")
    continue
  fi

  local_ref="${name}:${TAG}"
  remote_ref="${REGISTRY}/${name}:${TAG}"
  echo "==> Build ${name} (${dockerfile}) -> ${remote_ref}"
  if docker build --platform "$PLATFORM" -f "$dockerfile" -t "$local_ref" "$ROOT"; then
    docker tag "$local_ref" "$remote_ref"
    docker push "$remote_ref"
  else
    echo "ERROR: build de ${name} falló" >&2
    FAILED+=("$name")
  fi
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "Imágenes con problemas: ${FAILED[*]}" >&2
  exit 1
fi
echo "Listo: imágenes publicadas con tag '${TAG}' en ${REGISTRY}"
