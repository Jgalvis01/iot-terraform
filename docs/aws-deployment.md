# Despliegue en AWS Academy Learner Lab

Guía para desplegar la Warehouse Cloud Platform con Terraform en **AWS Academy Learner Lab**
(`us-east-1`) y verificarla con AWS CLI. Decisiones: [ADR-015](adr/ADR-015-vpc-por-bodega-y-central-ecs.md)
(topología) y [ADR-016](adr/ADR-016-colas-de-respaldo-y-resincronizacion.md) (colas de respaldo y resincronización).

Código: `infrastructure/terraform/` (módulos `vpc`, `peering`, `warehouse`, `central`, `database`, `ecr`,
`storage`, `lambda`) y `infrastructure/aws/` (user_data y docker-compose de cada EC2 de bodega).

## 1. Arquitectura desplegada

```text
                              Internet
                                 │
            ALB público  (VPC central, subnets públicas 10.0.0.0/24, 10.0.1.0/24)
     :80 central-api   :8001 WH-001   :8002 WH-002   :8003 WH-003   :15672 consola RabbitMQ (my_ip)
                                 │
 VPC central 10.0.0.0/16 ─ subnets privadas 10.0.10.0/24, 10.0.11.0/24 ─ NAT
 ┌──────────────────────── ECS Fargate (clúster warehouse-platform-central) ───────────────────────┐
 │ central-api ×2 · command-relay · transfer-orchestrator · projection-worker ×2 · audit · report  │
 │ rabbitmq (datos en EFS) ◀── NLB interno :5672                                                    │
 └──────────────────────────────────────────────────────────────────────────────────────────────────┘
   RDS PostgreSQL 15 (central_read)       S3 (reportes, exports) ─▶ Lambda s3_processor
          │ peering                 │ peering                 │ peering      ✗ sin peering entre bodegas
 VPC WH-001 10.1.0.0/16      VPC WH-002 10.2.0.0/16      VPC WH-003 10.3.0.0/16
  pública 10.1.0.0/24 (NAT)   pública 10.2.0.0/24 (NAT)   pública 10.3.0.0/24 (NAT)
  privada 10.1.10.0/24        privada 10.2.10.0/24        privada 10.3.10.0/24
   EC2 10.1.10.10 + Docker     EC2 10.2.10.10 + Docker     EC2 10.3.10.10 + Docker
   SG wh-001 + NACL            SG wh-002 + NACL            SG wh-003 + NACL
   api · postgres · redis · outbox relay ×2 · cache-worker   (en cada bodega)
```

| Requisito | Cómo se cumple |
|---|---|
| 3 EC2, una por bodega `WH-00X` | `module.warehouse["WH-00X"]`: EC2 AL2023 en la subnet privada de su VPC |
| Cada EC2 con su propia VPC pública y privada | `module.vpc_warehouse["WH-00X"]`: 1 subnet pública (NAT) + 1 privada |
| Central = ECS con su propia VPC pública y privada | `module.vpc_central` (2 AZ) + `module.central` (ECS Fargate) |
| Un security group por bodega | `warehouse-platform-wh-00X-sg`: entrada solo desde la CIDR central |
| Docker en cada EC2 | `user_data` instala Docker + compose y levanta los contenedores |
| La central accede a todas las bodegas | peering + rutas central→bodega; `GET /warehouses/connectivity` lo prueba en vivo |
| Las bodegas no se ven entre sí | sin peering entre ellas (el peering no es transitivo) + SG + NACL que deniega las otras CIDR |
| Broker para 1000-2000 req/s | RabbitMQ en ECS (1 vCPU/2 GB), relays y proyecciones con consumidores competidores |
| Si cae el broker, los mensajes siguen al volver | colas durables + mensajes persistentes + datos en EFS |
| Cola en caso de fallo del broker | `outbox` en cada bodega + `command_outbox` en la central (ADR-016) |
| Sincronizar la bodega al reanudar la conexión | el relay envía pendientes y luego un `INVENTORY_SNAPSHOT` |
| Reporte general de bodegas | `GET /reports/warehouses` y `POST /reports {"report_type":"warehouses_summary"}` → S3 |

Restricciones del Learner Lab (igual que ADR-012): no se pueden crear roles IAM (se usan `LabRole` y
`LabInstanceProfile`), credenciales de ~4 h, presupuesto de ~100 USD y estado de Terraform local.

## 2. Requisitos locales

- Terraform >= 1.7 (probado con 1.17), AWS CLI v2, Docker (para construir las imágenes), `curl` y Python 3.
- En **Windows** ejecuta los scripts `.sh` con **Git Bash** (desde PowerShell:
  `& "C:\Program Files\Git\bin\bash.exe" scripts/aws_verify.sh`).
- Opcional: [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
  para abrir una consola en las EC2 (`aws ssm start-session`). Las pruebas automáticas no lo necesitan.

## 3. Credenciales del lab

1. AWS Academy → *Learner Lab* → **Start Lab** (círculo verde).
2. **AWS Details** → *AWS CLI: Show* → copia el bloque `[default] ...`.
3. `scripts/lab_credentials.sh` → pega el bloque → Enter en una línea vacía. Muestra `aws sts get-caller-identity`.

> Las credenciales caducan al terminar la sesión (`ExpiredToken`). Repite el paso 3 en cada sesión.

## 4. Desplegar

Todo en un paso (crea `terraform.tfvars` con tu IP si no existe, ECR, imágenes y el resto):

```bash
scripts/aws_deploy.sh          # pide confirmación en cada apply
scripts/aws_deploy.sh -y       # sin confirmaciones
```

O a mano, en dos etapas (las EC2 y ECS descargan imágenes de ECR al arrancar):

```bash
cd infrastructure/terraform
cp terraform.tfvars.example terraform.tfvars      # edita my_ip_cidr
terraform init
terraform test                                    # pruebas de topología sin tocar AWS (proveedor simulado)
terraform apply -target=module.ecr                # etapa 1
cd ../.. && scripts/build_push_ecr.sh latest warehouse-api central-api workers
cd infrastructure/terraform && terraform apply    # etapa 2 (~10-15 min: NAT, RDS, ECS)
terraform output
```

El bootstrap de cada bodega tarda ~3-5 min después de crearse la EC2 (`/var/log/warehouse-bootstrap.log`).

## 5. Verificar con AWS CLI

```bash
scripts/aws_verify.sh            # red, aislamiento, Docker, ECS, ALB y pruebas funcionales
scripts/aws_verify.sh --chaos    # + caída y reinicio del broker (≈10 min, modifica servicios ECS y los restaura)
```

Qué comprueba (cada línea sale como ✔/✘ y el script termina con código ≠ 0 si algo falla):

| # | Comprobación | Comando AWS |
|---|---|---|
| 1 | 4 VPCs, cada una con subnet pública y privada; NAT disponible en cada una | `ec2 describe-vpcs`, `describe-subnets`, `describe-nat-gateways` |
| 2 | 3 peerings `active` central↔bodega y **ninguno** entre bodegas; las bodegas solo enrutan hacia la CIDR central | `ec2 describe-vpc-peering-connections`, `describe-route-tables` |
| 3 | SG de cada bodega con entrada solo desde la central; NACL que deniega las otras bodegas | `ec2 describe-security-group-rules`, `describe-network-acls` |
| 4 | EC2 `running`, sin IP pública, en subnet privada; Docker instalado y contenedores `running` | `ec2 describe-instances`, `ssm send-command` |
| 5 | Desde cada bodega: su API responde, llega al broker y **no** llega a las otras bodegas | `ssm send-command` (`curl`, `nc -z`) |
| 6 | Servicios ECS con todas sus tareas; target groups `healthy` | `ecs describe-services`, `elbv2 describe-target-health` |
| 7 | `/health` de la central y de cada bodega vía ALB; central→bodegas por red privada; producto → central (CQRS); reporte de bodegas; reporte en S3 | `curl`, `s3api head-object` |
| 8 | `--chaos`: broker a 0 tareas → bodega sigue aceptando movimientos (outbox crece), transferencia `queued`; broker a 1 → outbox y `command_outbox` vacíos, saga `COMPLETED`, central sincronizada, snapshot registrado | `ecs update-service`, `ecs wait services-stable` |
| 9 | `--chaos`: mensajes que estaban en cola sobreviven al reinicio del broker (EFS) | `ecs update-service --force-new-deployment` |

Comandos sueltos útiles:

```bash
cd infrastructure/terraform
ALB=$(terraform output -raw alb_dns)
curl -s http://$ALB/health                       # central (pending_commands = cola de respaldo)
curl -s http://$ALB/warehouses/connectivity      # la central prueba la red privada hacia cada bodega
curl -s http://$ALB/reports/warehouses           # reporte general de bodegas
curl -s http://$ALB:8002/sync/status             # cola local de WH-002 (outbox_pending)
curl -s -X POST http://$ALB/warehouses/WH-002/resync
terraform output ssm_commands                    # consola en cada bodega (Session Manager)
aws ecs describe-services --cluster $(terraform output -raw ecs_cluster) --services rabbitmq central-api \
  --query 'services[].[serviceName,runningCount,desiredCount]' --output table
aws logs tail /warehouse-platform/wh-002-outbox --since 10m
aws logs tail /warehouse-platform/central-projection-worker --follow
```

## 6. Logs

CloudWatch, retención 7 días: `/warehouse-platform/wh-00X-{api,outbox,cache-worker,postgres,redis}`,
`/warehouse-platform/central-{rabbitmq,central-api,command-relay,transfer-orchestrator,projection-worker,audit-worker,report-worker}`
y `/aws/lambda/s3_processor`.

## 7. Al terminar / reiniciar la sesión

- Las EC2 se detienen al terminar la sesión; al volver a *Start Lab* normalmente arrancan solas. Si no:
  `aws ec2 start-instances --instance-ids $(terraform output -json instance_ids | tr -d '[]" ' | tr ',' ' ')`.
- Las **IPs privadas de las bodegas son fijas** (`10.X.10.10`), así que la central las sigue encontrando.
  Docker y los contenedores (`restart: unless-stopped`) arrancan solos con los datos en disco.
- Al volver, cada outbox relay reconecta, envía lo pendiente y un snapshot: la central queda sincronizada sola.
- Los servicios ECS mantienen su número de tareas; el broker recupera sus colas desde EFS.

## 8. Costes estimados (us-east-1, on-demand)

| Recurso | USD/hora |
|---|---|
| 4 NAT Gateway (uno por VPC) | ~0.180 |
| 3 EC2 t3.small | 0.062 |
| Fargate: broker 1 vCPU/2 GB + 7 tareas de 0.25-0.5 vCPU | ~0.150 |
| RDS db.t3.micro | 0.017 |
| ALB + NLB | ~0.045 |
| IPv4 públicas (4 NAT + ALB) | ~0.030 |
| EFS, EBS, S3, Lambda, CloudWatch | céntimos |
| **Total** | **~0.50 USD/h ≈ 2 USD por sesión de 4 h** |

Los NAT Gateways cobran aunque las EC2 estén paradas: si no vas a usar el entorno en días, `scripts/aws_deploy.sh --destroy`
(o `terraform destroy`). Para la prueba de carga de 1000-2000 req/s sube `warehouse_instance_type` (p. ej. `t3.medium`),
`broker_cpu`/`broker_memory`, `projection_workers` y `central_api_count`, y bájalos al terminar.

## 9. Problemas frecuentes

| Síntoma | Causa / solución |
|---|---|
| `ExpiredToken` | Sesión caducada: `scripts/lab_credentials.sh` |
| Targets de bodega `unhealthy` justo tras el apply | Bootstrap en curso (3-5 min) o imágenes ausentes en ECR → `build_push_ecr.sh` y en la EC2 `sudo /opt/warehouse/redeploy.sh` (por SSM) |
| Tareas ECS en `PROVISIONING`/`STOPPED` con `CannotPullContainerError` | Imágenes no publicadas en ECR o NAT aún no disponible; ECS reintenta solo |
| `aws_verify.sh` falla en las secciones 4-5 (SSM) | El agente SSM tarda unos minutos en registrarse; si el lab no permite SSM, prueba el aislamiento con `GET /warehouses/connectivity` y revisa SG/NACL/rutas (secciones 1-3) |
| La consola RabbitMQ no abre | Tu IP cambió: actualiza `my_ip_cidr` y `terraform apply` |
