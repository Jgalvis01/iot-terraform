#!/usr/bin/env bash
# Despliegue completo en AWS (Learner Lab): ECR -> imágenes -> resto de la infraestructura.
#
#   scripts/aws_deploy.sh            # pide confirmación en cada terraform apply
#   scripts/aws_deploy.sh -y         # sin confirmaciones
#   scripts/aws_deploy.sh --destroy  # elimina todo
#
# En Windows: ejecútalo desde Git Bash. Después: scripts/aws_verify.sh
set -euo pipefail
export AWS_PAGER=""

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$ROOT/infrastructure/terraform"
TAG="${IMAGE_TAG:-latest}"
APPROVE=()
DESTROY=false
for a in "$@"; do
  case "$a" in
    -y|--yes) APPROVE=(-auto-approve) ;;
    --destroy) DESTROY=true ;;
    *) echo "Opción desconocida: $a"; exit 2 ;;
  esac
done
tf() { (cd "$TF_DIR" && terraform "$@"); }

echo "==> Credenciales"
aws sts get-caller-identity --query Arn --output text || { echo "Credenciales caducadas: scripts/lab_credentials.sh"; exit 2; }

if $DESTROY; then
  tf destroy "${APPROVE[@]}"
  exit 0
fi

if [[ ! -f "$TF_DIR/terraform.tfvars" ]]; then
  MYIP="$(curl -s --max-time 10 https://checkip.amazonaws.com | tr -d '[:space:]')"
  [[ -z "$MYIP" ]] && { echo "No pude obtener tu IP pública; crea terraform.tfvars a mano"; exit 2; }
  sed "s#^my_ip_cidr .*#my_ip_cidr = \"$MYIP/32\"#; s#^image_tag .*#image_tag = \"$TAG\"#" \
    "$TF_DIR/terraform.tfvars.example" > "$TF_DIR/terraform.tfvars"
  echo "==> Creado terraform.tfvars (my_ip_cidr = $MYIP/32)"
fi

echo "==> terraform init"
tf init -input=false

echo "==> Etapa 1: repositorios ECR"
tf apply -input=false -target=module.ecr "${APPROVE[@]}"

echo "==> Etapa 2: build + push de imágenes (tag $TAG)"
"$ROOT/scripts/build_push_ecr.sh" "$TAG" warehouse-api central-api workers

echo "==> Etapa 3: VPCs, peering, EC2 de bodegas, ECS central, broker, RDS, S3, Lambda"
tf apply -input=false -var "image_tag=$TAG" "${APPROVE[@]}"

CLUSTER="$(tf output -raw ecs_cluster)"
REGION="$(tf output -raw region)"
echo "==> Esperando a que los servicios ECS estén estables (puede tardar ~5-10 min)"
mapfile -t SERVICES < <(tf output -json ecs_services | tr -d '[]" \r' | tr ',' '\n' | sed '/^$/d')
aws ecs wait services-stable --region "$REGION" --cluster "$CLUSTER" --services "${SERVICES[@]}" \
  || echo "AVISO: algún servicio aún no está estable; revisa: aws ecs describe-services --cluster $CLUSTER"

echo "==> Esperando a que las EC2 de bodega pasen los status checks"
mapfile -t IIDS < <(tf output -json instance_ids | tr -d '[]" \r' | tr ',' '\n' | sed '/^$/d')
aws ec2 wait instance-status-ok --region "$REGION" --instance-ids "${IIDS[@]}" || true

echo
tf output urls
echo
echo "Listo. Verifica con: scripts/aws_verify.sh   (o scripts/aws_verify.sh --chaos)"
echo "El bootstrap de cada bodega (Docker + contenedores) tarda ~3-5 min tras arrancar la EC2."
