#!/usr/bin/env bash
# Verificación con AWS CLI de la plataforma desplegada con Terraform.
#
#   scripts/aws_verify.sh            # red, cómputo, aislamiento y pruebas funcionales
#   scripts/aws_verify.sh --chaos    # + caída del broker: cola local, cola de respaldo y persistencia
#
# Requisitos: AWS CLI v2 con credenciales del lab, terraform, curl y python.
# En Windows: ejecútalo desde Git Bash.
set -uo pipefail
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL="*" # Git Bash: no convertir /rutas en argumentos
export AWS_PAGER=""

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$ROOT/infrastructure/terraform"
CHAOS=false
[[ "${1:-}" == "--chaos" ]] && CHAOS=true

PY="$(command -v python3 2>/dev/null || true)"
"${PY:-false}" -c 1 2>/dev/null || PY="$(command -v python)"

PASS=0; FAIL=0; FAILED_CHECKS=()
ok()   { PASS=$((PASS + 1)); printf '  \033[32m✔\033[0m %s\n' "$*"; }
bad()  { FAIL=$((FAIL + 1)); FAILED_CHECKS+=("$*"); printf '  \033[31m✘\033[0m %s\n' "$*"; }
check() { local msg="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$msg"; else bad "$msg"; fi; }
section() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
aws_t() { aws "$@" --output text 2>/dev/null | tr -d '\r'; }
tf_raw() { (cd "$TF_DIR" && terraform output -raw "$1") 2>/dev/null | tr -d '\r'; }
# jget '<expresión python sobre d>' < json
jget() { "$PY" -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: d=None
try: r=eval(sys.argv[1])
except Exception: r=""
print(json.dumps(r) if isinstance(r,(dict,list)) else ("" if r is None else r))' "$1"; }
http_code() { curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$@"; }
wait_until() { # wait_until <segundos> <comando...>
  local timeout="$1"; shift; local start=$SECONDS
  until "$@" >/dev/null 2>&1; do
    (( SECONDS - start >= timeout )) && return 1
    sleep 5
  done
}

# ------------------------------------------------------------------ contexto
section "Contexto"
if ! IDENTITY="$(aws_t sts get-caller-identity --query Arn)" || [[ -z "$IDENTITY" ]]; then
  echo "Credenciales AWS no válidas/caducadas: ejecuta scripts/lab_credentials.sh"; exit 2
fi
echo "  identidad: $IDENTITY"
TF_JSON="$(cd "$TF_DIR" && terraform output -json 2>/dev/null)"
[[ -z "$TF_JSON" || "$TF_JSON" == "{}" ]] && { echo "No hay outputs de Terraform (¿terraform apply?)"; exit 2; }
REGION="$(tf_raw region)"; export AWS_DEFAULT_REGION="$REGION"
ALB="$(tf_raw alb_dns)"
CLUSTER="$(tf_raw ecs_cluster)"
BROKER="$(tf_raw broker_dns)"
BUCKET="$(tf_raw s3_bucket)"
CENTRAL_VPC="$(jget 'd["vpcs"]["value"]["central"]["vpc_id"]' <<<"$TF_JSON")"
CENTRAL_CIDR="$(jget 'd["vpcs"]["value"]["central"]["cidr"]' <<<"$TF_JSON")"
mapfile -t WHS < <(jget '"\n".join(sorted(d["warehouses"]["value"]))' <<<"$TF_JSON" | tr -d '\r')
declare -A WH_VPC WH_CIDR WH_IID WH_IP WH_SG WH_URL
for wh in "${WHS[@]}"; do
  WH_VPC[$wh]="$(jget "d['vpcs']['value']['$wh']['vpc_id']" <<<"$TF_JSON")"
  WH_CIDR[$wh]="$(jget "d['vpcs']['value']['$wh']['cidr']" <<<"$TF_JSON")"
  WH_IID[$wh]="$(jget "d['warehouses']['value']['$wh']['instance_id']" <<<"$TF_JSON")"
  WH_IP[$wh]="$(jget "d['warehouses']['value']['$wh']['private_ip']" <<<"$TF_JSON")"
  WH_SG[$wh]="$(jget "d['warehouses']['value']['$wh']['security_group_id']" <<<"$TF_JSON")"
  WH_URL[$wh]="$(jget "d['warehouses']['value']['$wh']['url_via_central']" <<<"$TF_JSON")"
done
echo "  región: $REGION | ALB: $ALB | cluster: $CLUSTER"
echo "  bodegas: ${WHS[*]}"

# ------------------------------------------------------------------ red
section "1. Red: 4 VPCs con subnet pública y privada"
ALL_VPCS=("$CENTRAL_VPC"); for wh in "${WHS[@]}"; do ALL_VPCS+=("${WH_VPC[$wh]}"); done
[[ ${#ALL_VPCS[@]} -eq $(( ${#WHS[@]} + 1 )) ]] && ok "VPCs en Terraform: ${#ALL_VPCS[@]} (central + ${#WHS[@]} bodegas)" || bad "número de VPCs"
for v in "${ALL_VPCS[@]}"; do
  read -r cidr name <<<"$(aws_t ec2 describe-vpcs --vpc-ids "$v" --query 'Vpcs[0].[CidrBlock,Tags[?Key==`Name`]|[0].Value]')"
  pub="$(aws_t ec2 describe-subnets --filters Name=vpc-id,Values="$v" Name=tag:Tier,Values=public --query 'length(Subnets)')"
  priv="$(aws_t ec2 describe-subnets --filters Name=vpc-id,Values="$v" Name=tag:Tier,Values=private --query 'length(Subnets)')"
  if [[ -n "$cidr" && "${pub:-0}" -ge 1 && "${priv:-0}" -ge 1 ]]; then
    ok "$name ($v) $cidr: $pub pública(s), $priv privada(s)"
  else
    bad "VPC $v: cidr=$cidr públicas=$pub privadas=$priv"
  fi
done
nat="$(aws_t ec2 describe-nat-gateways --filter Name=vpc-id,Values="$(IFS=,; echo "${ALL_VPCS[*]}")" Name=state,Values=available --query 'length(NatGateways)')"
[[ "${nat:-0}" -eq ${#ALL_VPCS[@]} ]] && ok "NAT Gateway disponible en cada VPC ($nat)" || bad "NAT Gateways disponibles: $nat de ${#ALL_VPCS[@]}"

section "2. Peering hub-and-spoke (central <-> bodega, nunca bodega <-> bodega)"
for wh in "${WHS[@]}"; do
  st="$(aws_t ec2 describe-vpc-peering-connections --filters \
    Name=requester-vpc-info.vpc-id,Values="$CENTRAL_VPC" Name=accepter-vpc-info.vpc-id,Values="${WH_VPC[$wh]}" \
    --query 'VpcPeeringConnections[0].Status.Code')"
  [[ "$st" == "active" ]] && ok "peering central <-> $wh: active" || bad "peering central <-> $wh: $st"
done
for a in "${WHS[@]}"; do for b in "${WHS[@]}"; do
  [[ "$a" < "$b" ]] || continue
  n="$(aws_t ec2 describe-vpc-peering-connections --filters \
    Name=requester-vpc-info.vpc-id,Values="${WH_VPC[$a]}","${WH_VPC[$b]}" \
    Name=accepter-vpc-info.vpc-id,Values="${WH_VPC[$a]}","${WH_VPC[$b]}" \
    Name=status-code,Values=active,pending-acceptance --query 'length(VpcPeeringConnections)')"
  [[ "${n:-0}" -eq 0 ]] && ok "sin peering $a <-> $b" || bad "existe peering $a <-> $b"
done; done
for wh in "${WHS[@]}"; do
  dests="$(aws_t ec2 describe-route-tables --filters Name=vpc-id,Values="${WH_VPC[$wh]}" \
    --query 'RouteTables[].Routes[] | [?VpcPeeringConnectionId!=`null`].DestinationCidrBlock' | tr '\t' '\n' | sort -u | xargs)"
  [[ "$dests" == "$CENTRAL_CIDR" ]] && ok "$wh solo enruta por peering hacia la central ($dests)" \
    || bad "$wh rutas de peering inesperadas: '$dests'"
done

section "3. Security groups y NACL por bodega"
for wh in "${WHS[@]}"; do
  srcs="$(aws_t ec2 describe-security-group-rules --filters Name=group-id,Values="${WH_SG[$wh]}" \
    --query 'SecurityGroupRules[?IsEgress==`false`].[CidrIpv4,ReferencedGroupInfo.GroupId]' | tr '\t' '\n' | grep -v None | sort -u | xargs)"
  [[ "$srcs" == "$CENTRAL_CIDR" ]] && ok "SG $wh (${WH_SG[$wh]}): entrada solo desde la central ($srcs)" \
    || bad "SG $wh acepta orígenes: '$srcs'"
  others=(); for o in "${WHS[@]}"; do [[ "$o" != "$wh" ]] && others+=("${WH_CIDR[$o]}"); done
  denied="$(aws_t ec2 describe-network-acls --filters Name=vpc-id,Values="${WH_VPC[$wh]}" Name=default,Values=false \
    --query 'NetworkAcls[].Entries[] | [?RuleAction==`deny` && Egress==`false` && RuleNumber<`32767`].CidrBlock' | tr '\t' '\n' | sort -u | xargs)"
  expected="$(printf '%s\n' "${others[@]}" | sort -u | xargs)"
  [[ "$denied" == "$expected" ]] && ok "NACL $wh deniega a las otras bodegas ($denied)" || bad "NACL $wh deny='$denied' esperado='$expected'"
done

# ------------------------------------------------------------------ cómputo
section "4. EC2 de bodegas (subnet privada, Docker)"
for wh in "${WHS[@]}"; do
  read -r state pubip subnet <<<"$(aws_t ec2 describe-instances --instance-ids "${WH_IID[$wh]}" \
    --query 'Reservations[0].Instances[0].[State.Name,PublicIpAddress,SubnetId]')"
  tier="$(aws_t ec2 describe-subnets --subnet-ids "$subnet" --query 'Subnets[0].Tags[?Key==`Tier`]|[0].Value')"
  [[ "$state" == "running" && "$pubip" == "None" && "$tier" == "private" ]] \
    && ok "$wh ${WH_IID[$wh]} running, sin IP pública, subnet $tier" \
    || bad "$wh estado=$state ip_pública=$pubip subnet=$tier"
done

ssm_run() { # ssm_run <instance-id> <script> -> stdout del comando
  local iid="$1" script="$2" cid status
  cid="$(aws_t ssm send-command --instance-ids "$iid" --document-name AWS-RunShellScript \
    --parameters "$("$PY" -c 'import json,sys; print(json.dumps({"commands":[sys.argv[1]]}))' "$script")" \
    --query 'Command.CommandId')" || return 1
  [[ -z "$cid" ]] && return 1
  for _ in $(seq 1 30); do
    sleep 3
    status="$(aws_t ssm get-command-invocation --command-id "$cid" --instance-id "$iid" --query Status)"
    [[ "$status" == "Success" || "$status" == "Failed" || "$status" == "TimedOut" ]] && break
  done
  aws_t ssm get-command-invocation --command-id "$cid" --instance-id "$iid" --query StandardOutputContent
}

for wh in "${WHS[@]}"; do
  online="$(aws_t ssm describe-instance-information --filters Key=InstanceIds,Values="${WH_IID[$wh]}" \
    --query 'InstanceInformationList[0].PingStatus')"
  if [[ "$online" != "Online" ]]; then bad "$wh: agente SSM no está Online ($online)"; continue; fi
  out="$(ssm_run "${WH_IID[$wh]}" 'docker --version; docker compose version --short; cd /opt/warehouse && docker compose ps --format "{{.Service}}={{.State}}" | sort -u')"
  if grep -q "Docker version" <<<"$out"; then ok "$wh: $(head -1 <<<"$out")"; else bad "$wh: Docker no instalado"; fi
  for svc in api postgres redis outbox cache-worker; do
    grep -q "^$svc=running" <<<"$out" && ok "$wh: contenedor $svc running" || bad "$wh: contenedor $svc no está running"
  done
done

section "5. Aislamiento real (pruebas de red desde cada bodega por SSM)"
for wh in "${WHS[@]}"; do
  script="curl -s -o /dev/null -w 'self=%{http_code}\n' --max-time 5 http://localhost:8000/health;"
  script+=" nc -z -w 5 $BROKER 5672 && echo broker=open || echo broker=closed;"
  for o in "${WHS[@]}"; do
    [[ "$o" == "$wh" ]] && continue
    script+=" nc -z -w 5 ${WH_IP[$o]} 8000 && echo $o=open || echo $o=closed;"
  done
  out="$(ssm_run "${WH_IID[$wh]}" "$script")"
  grep -q "self=200" <<<"$out" && ok "$wh: su propia API responde (200)" || bad "$wh: API local no responde"
  grep -q "broker=open" <<<"$out" && ok "$wh -> broker central :5672 abierto" || bad "$wh no llega al broker"
  for o in "${WHS[@]}"; do
    [[ "$o" == "$wh" ]] && continue
    grep -q "$o=closed" <<<"$out" && ok "$wh -> $o (${WH_IP[$o]}:8000) BLOQUEADO" || bad "$wh puede ver a $o"
  done
done

section "6. Bodega central (ECS Fargate)"
mapfile -t SERVICES < <(jget '"\n".join(d["ecs_services"]["value"])' <<<"$TF_JSON" | tr -d '\r')
while read -r name running desired; do
  [[ -z "$name" ]] && continue
  [[ "$running" == "$desired" && "$desired" -gt 0 ]] && ok "servicio $name: $running/$desired tareas" || bad "servicio $name: $running/$desired"
done < <(aws_t ecs describe-services --cluster "$CLUSTER" --services "${SERVICES[@]}" \
  --query 'services[].[serviceName,runningCount,desiredCount]')
while read -r arn; do
  [[ -z "$arn" ]] && continue
  tg="${arn##*targetgroup/}"; tg="${tg%%/*}"
  states="$(aws_t elbv2 describe-target-health --target-group-arn "$arn" --query 'TargetHealthDescriptions[].TargetHealth.State' | xargs)"
  [[ -n "$states" && "$states" != *unhealthy* && "$states" != *initial* && "$states" != *unused* ]] \
    && ok "target group $tg: $states" || bad "target group $tg: '${states:-sin targets}'"
done < <(jget '"\n".join(d["target_group_arns"]["value"].values())' <<<"$TF_JSON" | tr -d '\r')

# ------------------------------------------------------------------ funcional
section "7. Pruebas funcionales a través del ALB de la central"
check "central /health = 200" test "$(http_code "http://$ALB/health")" = 200
for wh in "${WHS[@]}"; do
  check "$wh vía central (${WH_URL[$wh]}/health) = 200" test "$(http_code "${WH_URL[$wh]}/health")" = 200
done
conn="$(curl -s --max-time 20 "http://$ALB/warehouses/connectivity")"
[[ "$(jget 'd["all_reachable"]' <<<"$conn")" == "True" ]] \
  && ok "central -> todas las bodegas por red privada: $(jget 'd["reachable"]' <<<"$conn")/$(jget 'd["total"]' <<<"$conn")" \
  || bad "central no alcanza todas las bodegas: $conn"

FIRST="${WHS[0]}"; SKU="VERIFY-$(date +%s)"
code="$(http_code -X POST "${WH_URL[$FIRST]}/products" -H 'Content-Type: application/json' \
  -d "{\"sku\":\"$SKU\",\"name\":\"Verificacion\",\"unit\":\"unit\",\"category\":\"test\"}")"
check "$FIRST: crear producto $SKU (201)" test "$code" = 201
code="$(http_code -X POST "${WH_URL[$FIRST]}/inventory/movements" -H 'Content-Type: application/json' \
  -d "{\"sku\":\"$SKU\",\"type\":\"RECEIPT\",\"quantity\":10,\"reference\":\"aws-verify\"}")"
check "$FIRST: entrada de 10 unidades (201)" test "$code" = 201
global_total() { curl -s --max-time 5 "http://$ALB/global/inventory/$1" | jget 'd["total"]'; }
wh_qty() { # wh_qty <sku> <WH> -> cantidad de esa bodega en el read model de la central
  curl -s --max-time 5 "http://$ALB/global/inventory/$1" | jget "[w['quantity'] for w in d['warehouses'] if w['warehouse_id']=='$2'][0]"; }
if wait_until 90 test "$(global_total "$SKU")" = 10; then
  ok "sincronización bodega -> broker -> central: total global de $SKU = 10"
else
  bad "la central no recibió el inventario de $SKU (total=$(global_total "$SKU"))"
fi
rep="$(curl -s --max-time 10 "http://$ALB/reports/warehouses")"
online="$(jget 'd["totals"]["online"]' <<<"$rep")"
[[ "$online" == "${#WHS[@]}" ]] && ok "reporte de bodegas: ${online}/${#WHS[@]} ONLINE, unidades=$(jget 'd["totals"]["total_units"]' <<<"$rep")" \
  || bad "reporte de bodegas: online=$online"
job="$(curl -s --max-time 10 -X POST "http://$ALB/reports" -H 'Content-Type: application/json' -d '{"report_type":"warehouses_summary"}' | jget 'd["job_id"]')"
job_status() { curl -s --max-time 5 "http://$ALB/reports/jobs/$job" | jget 'd["status"]'; }
if [[ -n "$job" ]] && wait_until 90 test "$(job_status)" = COMPLETED; then
  key="$(curl -s "http://$ALB/reports/jobs/$job" | jget 'd["s3_key"]')"
  check "reporte $job en S3 (s3://$BUCKET/$key)" aws s3api head-object --bucket "$BUCKET" --key "$key"
else
  bad "el reporte asíncrono no terminó (job=$job status=$(job_status))"
fi

# ------------------------------------------------------------------ caos: broker caído
if $CHAOS; then
  section "8. Caída del broker: la operación continúa y nada se pierde"
  WH2="${WHS[1]:-${WHS[0]}}"; WH3="${WHS[2]:-${WHS[0]}}"; SKU2="CHAOS-$(date +%s)"
  for w in "$WH2" "$WH3"; do
    curl -s -o /dev/null -X POST "${WH_URL[$w]}/products" -H 'Content-Type: application/json' \
      -d "{\"sku\":\"$SKU2\",\"name\":\"Caos\",\"unit\":\"unit\"}"
  done
  curl -s -o /dev/null -X POST "${WH_URL[$WH2]}/inventory/movements" -H 'Content-Type: application/json' \
    -d "{\"sku\":\"$SKU2\",\"type\":\"RECEIPT\",\"quantity\":20,\"reference\":\"chaos-seed\"}"
  wait_until 90 test "$(global_total "$SKU2")" = 20 && ok "estado inicial sincronizado ($SKU2 = 20)" || bad "estado inicial no sincronizado"

  echo "  -> deteniendo el broker (rabbitmq desired=0)"
  aws ecs update-service --cluster "$CLUSTER" --service rabbitmq --desired-count 0 >/dev/null
  aws ecs wait services-stable --cluster "$CLUSTER" --services rabbitmq
  sleep 10
  ok_moves=0
  for i in 1 2 3 4 5; do
    [[ "$(http_code -X POST "${WH_URL[$WH2]}/inventory/movements" -H 'Content-Type: application/json' \
      -d "{\"sku\":\"$SKU2\",\"type\":\"RECEIPT\",\"quantity\":2,\"reference\":\"chaos-$i\"}")" == 201 ]] && ok_moves=$((ok_moves + 1))
  done
  [[ $ok_moves -eq 5 ]] && ok "$WH2 sigue operando sin broker (5/5 movimientos aceptados)" || bad "$WH2 movimientos aceptados: $ok_moves/5"
  pending="$(curl -s "${WH_URL[$WH2]}/sync/status" | jget 'd["outbox_pending"]')"
  [[ "${pending:-0}" -ge 5 ]] && ok "cola local de $WH2: $pending eventos pendientes" || bad "outbox pendiente de $WH2: $pending"
  tr_resp="$(curl -s -X POST "http://$ALB/transfers" -H 'Content-Type: application/json' \
    -d "{\"sku\":\"$SKU2\",\"source_warehouse\":\"$WH2\",\"destination_warehouse\":\"$WH3\",\"quantity\":3}")"
  TR_ID="$(jget 'd["transfer_id"]' <<<"$tr_resp")"
  [[ "$(jget 'd["delivery"]' <<<"$tr_resp")" == "queued" ]] && ok "central acepta la transferencia $TR_ID en la cola de respaldo" \
    || bad "transferencia sin broker: $tr_resp"
  pc="$(curl -s "http://$ALB/health" | jget 'd["pending_commands"]')"
  [[ "${pc:-0}" -ge 1 ]] && ok "command_outbox de la central: $pc comando(s) pendiente(s)" || bad "pending_commands=$pc"

  echo "  -> arrancando el broker (rabbitmq desired=1)"
  aws ecs update-service --cluster "$CLUSTER" --service rabbitmq --desired-count 1 >/dev/null
  aws ecs wait services-stable --cluster "$CLUSTER" --services rabbitmq
  wait_until 240 test "$(curl -s "${WH_URL[$WH2]}/sync/status" | jget 'd["outbox_pending"]')" = 0 \
    && ok "$WH2 vació su cola local al reconectar" || bad "$WH2 no vació el outbox"
  wait_until 180 test "$(curl -s "http://$ALB/health" | jget 'd["pending_commands"]')" = 0 \
    && ok "command-relay publicó los comandos encolados" || bad "quedan comandos en command_outbox"
  tstatus() { curl -s "http://$ALB/transfers/$TR_ID" | jget 'd["status"]'; }
  wait_until 180 test "$(tstatus)" = COMPLETED && ok "transferencia $TR_ID COMPLETED tras reanudarse el broker" \
    || bad "transferencia $TR_ID estado=$(tstatus)"
  wait_until 180 test "$(wh_qty "$SKU2" "$WH2")" = 27 \
    && ok "central sincronizada tras la caída: $WH2 = 20 + 10 - 3 = 27" || bad "$WH2 = $(wh_qty "$SKU2" "$WH2") (esperado 27)"
  wait_until 120 test "$(wh_qty "$SKU2" "$WH3")" = 3 \
    && ok "central sincronizada tras la caída: $WH3 = 3 (recibido por transferencia)" || bad "$WH3 = $(wh_qty "$SKU2" "$WH3") (esperado 3)"
  snap="$(curl -s "http://$ALB/reports/warehouses" | jget "[w for w in d['warehouses'] if w['warehouse_id']=='$WH2'][0]['sync']['last_snapshot_at']")"
  [[ -n "$snap" ]] && ok "$WH2 envió snapshot de resincronización ($snap)" || bad "no se registró snapshot de $WH2"

  section "9. Persistencia del broker (mensajes en cola sobreviven a su reinicio)"
  PROJ_COUNT="$(aws_t ecs describe-services --cluster "$CLUSTER" --services projection-worker --query 'services[0].desiredCount')"
  aws ecs update-service --cluster "$CLUSTER" --service projection-worker --desired-count 0 >/dev/null
  aws ecs wait services-stable --cluster "$CLUSTER" --services projection-worker
  for i in 1 2 3; do
    curl -s -o /dev/null -X POST "${WH_URL[$WH3]}/inventory/movements" -H 'Content-Type: application/json' \
      -d "{\"sku\":\"$SKU2\",\"type\":\"RECEIPT\",\"quantity\":1,\"reference\":\"persist-$i\"}"
  done
  sleep 15
  echo "  -> reiniciando la tarea del broker (datos en EFS)"
  aws ecs update-service --cluster "$CLUSTER" --service rabbitmq --force-new-deployment >/dev/null
  aws ecs wait services-stable --cluster "$CLUSTER" --services rabbitmq
  aws ecs update-service --cluster "$CLUSTER" --service projection-worker --desired-count "${PROJ_COUNT:-1}" >/dev/null
  aws ecs wait services-stable --cluster "$CLUSTER" --services projection-worker
  wait_until 180 test "$(wh_qty "$SKU2" "$WH3")" = 6 \
    && ok "eventos que estaban en cola antes del reinicio del broker se procesaron ($WH3 = 3 + 3 = 6)" \
    || bad "eventos perdidos tras reiniciar el broker ($WH3 = $(wh_qty "$SKU2" "$WH3"), esperado 6)"
fi

# ------------------------------------------------------------------ resumen
section "Resumen"
echo "  correctas: $PASS   fallidas: $FAIL"
if [[ $FAIL -gt 0 ]]; then
  printf '  - %s\n' "${FAILED_CHECKS[@]}"
  exit 1
fi
echo "  TODO CORRECTO"
