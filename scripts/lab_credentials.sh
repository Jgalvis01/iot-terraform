#!/usr/bin/env bash
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
AWS_DIR="${HOME}/.aws"
CRED_FILE="${AWS_DIR}/credentials"
CONFIG_FILE="${AWS_DIR}/config"

if [[ $# -ge 1 ]]; then
  INPUT="$(cat -- "$1")"
else
  if [[ -t 0 ]]; then
    echo "Pega el bloque 'AWS CLI' de Learner Lab (AWS Details -> Show)."
    echo "Termina con una línea vacía o Ctrl-D:"
    INPUT=""
    while IFS= read -r line; do
      [[ -z "${line//[[:space:]]/}" && -n "$INPUT" ]] && break
      INPUT+="${line}"$'\n'
    done
  else
    INPUT="$(cat)"
  fi
fi

get_value() {
  printf '%s\n' "$INPUT" | tr -d '\r' | grep -E "^[[:space:]]*$1[[:space:]]*=" | head -n1 \
    | sed -E "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//; s/[[:space:]]*$//"
}

KEY_ID="$(get_value aws_access_key_id || true)"
SECRET="$(get_value aws_secret_access_key || true)"
TOKEN="$(get_value aws_session_token || true)"

if [[ -z "$KEY_ID" || -z "$SECRET" || -z "$TOKEN" ]]; then
  echo "ERROR: no se encontraron aws_access_key_id, aws_secret_access_key y aws_session_token en el texto pegado." >&2
  exit 1
fi

mkdir -p "$AWS_DIR"
chmod 700 "$AWS_DIR"

if [[ -f "$CRED_FILE" ]]; then
  BACKUP="${CRED_FILE}.bak.$(date +%Y%m%d%H%M%S)"
  cp "$CRED_FILE" "$BACKUP"
  echo "Copia de seguridad: $BACKUP"
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
if [[ -f "$CRED_FILE" ]]; then
  awk 'BEGIN{skip=0} /^\[.*\]/{skip=($0=="[default]")} !skip' "$CRED_FILE" > "$TMP"
fi
{
  echo "[default]"
  echo "aws_access_key_id=${KEY_ID}"
  echo "aws_secret_access_key=${SECRET}"
  echo "aws_session_token=${TOKEN}"
  echo
  cat "$TMP"
} > "$CRED_FILE"
chmod 600 "$CRED_FILE"

if ! grep -qs '^\[default\]' "$CONFIG_FILE"; then
  printf '[default]\nregion = %s\noutput = json\n' "$REGION" >> "$CONFIG_FILE"
  chmod 600 "$CONFIG_FILE"
fi

echo "Credenciales escritas en $CRED_FILE (perfil default). Verificando..."
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN AWS_PROFILE
aws sts get-caller-identity --region "$REGION" --output table
echo "OK. Recuerda: caducan al terminar la sesión del Learner Lab."
