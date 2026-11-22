# Despliegue en AWS Academy Learner Lab

Guía paso a paso para desplegar la Warehouse Cloud Platform con Terraform en
**AWS Academy Learner Lab** (región `us-east-1`).

Código: `infrastructure/terraform/` (módulos `network, ecr, database, compute,
storage, lambda, load_balancer`) y `infrastructure/aws/` (user_data y plantillas
de docker-compose que se renderizan para cada EC2).

## 1. Arquitectura desplegada

```text
                          Internet
                             |
          ALB público (warehouse-platform-alb)
   :80 -> central   :8001 -> WH-001   :8002 -> WH-002   :8003 -> WH-003
      |                  |                  |                  |
 +----v-----------+  +---v---------------------v--+   +-------v-------+
 | EC2 platform   |  | EC2 wh-a (t3.small)        |   | EC2 wh-b x N  |
 | (t3.medium)    |  |  wh-001-api :8001          |   | (t3.small)    |
 | rabbitmq       |  |  wh-003-api :8003          |   | wh-002-api    |
 | redis-wh-001..3|  +----------------------------+   |   :8002       |
 | central-api    |              subnets públicas      +---------------+
 | orchestrator, projection, audit, report, cache-workers x3, outbox x3 |
 +-------+--------+
         |                 subnets privadas
   RDS PostgreSQL 15 (db.t3.micro): wh_001, wh_002, wh_003, central_read

 S3 warehouse-platform-<sufijo>  -- exports/ --> Lambda s3_processor --> processed/ | rejected/
 ECR: warehouse-api, central-api, workers, batch, iot, frontend
 CloudWatch Logs: /warehouse-platform/<servicio>, /aws/lambda/s3_processor
```

Decisiones condicionadas por el Learner Lab:

| Restricción | Decisión |
|---|---|
| No se pueden crear roles/políticas IAM | `data "aws_iam_role" "lab"` (LabRole) para la Lambda y `LabInstanceProfile` para las EC2 |
| Presupuesto ~100 USD | Sin NAT Gateway: EC2 en subnets públicas con SG restrictivos; RDS en subnets privadas. Un solo RDS `db.t3.micro` single-AZ con 4 bases lógicas. RabbitMQ y Redis en contenedores (no Amazon MQ/ElastiCache) |
| Credenciales temporales (~4 h) | Estado Terraform local; script `scripts/lab_credentials.sh` para renovarlas |
| Las EC2 se detienen al terminar la sesión | Docker habilitado en systemd + `restart: unless-stopped`; central llega a las bodegas por el **DNS del ALB** (estable), no por IPs |
| Par de claves | `vockey` (descarga `labsuser.pem` desde *AWS Details*) |

Escalamiento horizontal: `wh002_instances` (default 2) EC2 `wh-b` registradas en
el target group de `:8002`. Para probar más réplicas: `terraform apply -var wh002_instances=3`.

## 2. Requisitos locales

- Terraform >= 1.6 (probado con 1.9.8), AWS CLI v2, Docker (con buildx si usas Mac ARM).
- Tu IP pública: `curl -s https://checkip.amazonaws.com` → `x.x.x.x/32`.

## 3. Iniciar el lab y cargar credenciales

1. En AWS Academy → *Learner Lab* → **Start Lab**; espera el círculo verde.
2. **AWS Details** → *AWS CLI: Show* → copia el bloque `[default] ...`.
3. Descarga **Download PEM** → `labsuser.pem` (`chmod 400 labsuser.pem`).
4. Carga las credenciales:

```bash
scripts/lab_credentials.sh          # pega el bloque y pulsa Enter en una línea vacía (o Ctrl-D)
# muestra el resultado de: aws sts get-caller-identity
```

> Las credenciales caducan cuando termina la sesión. Repite el paso 4 en cada sesión.

## 4. Configurar variables

```bash
cd infrastructure/terraform
cp terraform.tfvars.example terraform.tfvars
# edita my_ip_cidr (obligatorio). db_password/rabbitmq_password: si no se ponen se generan.
```

## 5. Despliegue en dos etapas

Las EC2 descargan imágenes de ECR al arrancar, así que primero se crean los
repositorios y se publican las imágenes.

```bash
cd infrastructure/terraform
terraform init

# Etapa 1: solo ECR
terraform apply -target=module.ecr

# Publicar imágenes (desde la raíz del repo). El tag debe coincidir con image_tag.
cd ../..
scripts/build_push_ecr.sh latest
# (o solo algunas: scripts/build_push_ecr.sh latest warehouse-api workers)

# Etapa 2: todo lo demás (VPC, RDS ~5-10 min, EC2, ALB, S3, Lambda)
cd infrastructure/terraform
terraform plan -out tfplan
terraform apply tfplan
terraform output
```

El bootstrap de cada EC2 (user_data) tarda ~3-5 min: instala Docker y el plugin
compose, hace login en ECR con el instance profile, (en `platform`) crea las
bases `wh_001, wh_002, wh_003, central_read` en RDS de forma idempotente,
escribe `/opt/warehouse/docker-compose.yml` y ejecuta `docker compose up -d`.
Si las imágenes no estaban en ECR, reintenta ~10 min; después basta con
`sudo /opt/warehouse/redeploy.sh` en la instancia.

## 6. Verificación

```bash
ALB=$(terraform output -raw alb_dns)
curl -s http://$ALB/health            # central-api
curl -s http://$ALB:8001/health       # WH-001
curl -s http://$ALB:8002/health       # WH-002 (repetir: alterna entre instancias wh-b)
curl -s http://$ALB:8003/health       # WH-003

curl -s -X POST http://$ALB:8001/products -H 'Content-Type: application/json' \
  -d '{"sku":"P-001","name":"Producto 1","unit":"unit","category":"general"}'
curl -s -X POST http://$ALB:8001/inventory/movements -H 'Content-Type: application/json' \
  -d '{"sku":"P-001","type":"RECEIPT","quantity":10,"reference":"aws-demo"}'
curl -s "http://$ALB/global/inventory/P-001?mode=readmodel"

# Lambda: subir un export y ver el resultado procesado
BUCKET=$(terraform output -raw s3_bucket)
printf 'sku,warehouse_id,quantity\nP-001,WH-001,10\n' > /tmp/demo.csv
aws s3 cp /tmp/demo.csv s3://$BUCKET/exports/demo.csv
aws s3 ls s3://$BUCKET/processed/
```

Estado de los targets: consola EC2 → *Target Groups* (deben estar `healthy`;
el health check es `GET /health`, 2 OK / 2 fallos, cada 15 s).

## 7. Logs y acceso

- **CloudWatch Logs**: un grupo por servicio `/warehouse-platform/<servicio>`
  (p.ej. `/warehouse-platform/wh-002-api`, stream = instancia `wh-b-1`, `wh-b-2`)
  y `/aws/lambda/s3_processor`. Retención 7 días.

  ```bash
  aws logs tail /warehouse-platform/central-api --follow
  aws logs tail /warehouse-platform/wh-002-api --since 10m
  ```

- **SSH** (solo desde `my_ip_cidr`): `terraform output ssh_commands`

  ```bash
  ssh -i labsuser.pem ec2-user@<ip>
  sudo docker compose -f /opt/warehouse/docker-compose.yml ps
  sudo tail -f /var/log/warehouse-bootstrap.log
  ```

- **RabbitMQ UI**: `terraform output rabbitmq_ui` (usuario `warehouse`,
  password `terraform output -raw rabbitmq_password`). Solo desde tu IP.
- **Redesplegar** tras publicar nuevas imágenes con el mismo tag: SSH a la
  instancia y `sudo /opt/warehouse/redeploy.sh`. Con un tag nuevo:
  `terraform apply -var image_tag=v2` (recrea las EC2, porque cambia el user_data).

## 8. Al terminar / reiniciar la sesión

Cuando la sesión del lab termina (o pulsas *End Lab*):

- Las **EC2 se detienen** automáticamente. Al pulsar *Start Lab* de nuevo, el
  Learner Lab normalmente vuelve a arrancar las instancias que estaban corriendo
  (puede tardar unos minutos); compruébalo y, si siguen en `stopped`, arráncalas:

  ```bash
  aws ec2 start-instances --instance-ids $(terraform output -json instance_ids | jq -r '.[]')
  ```

- Al arrancar, Docker inicia solo y los contenedores (`restart: unless-stopped`)
  se levantan con las imágenes ya descargadas: no hace falta re-ejecutar user_data
  ni volver a hacer login en ECR.
- La **IP pública de cada EC2 cambia** (no se usan Elastic IPs para ahorrar).
  Actualiza los outputs: `terraform apply -refresh-only`. Las **IPs privadas no
  cambian**, por lo que las conexiones internas (Redis/RabbitMQ en `platform`)
  siguen funcionando.
- El **DNS del ALB se mantiene**: las URLs públicas y la variable `WAREHOUSES`
  de central siguen siendo válidas.
- Las credenciales son nuevas: repite `scripts/lab_credentials.sh`.
- Si tu IP pública cambió, actualiza `my_ip_cidr` y `terraform apply`.
- Orden de recuperación natural: `platform` (RabbitMQ/Redis) puede arrancar
  después que las bodegas; las APIs degradan (caché caída → PostgreSQL, outbox
  acumula eventos) y se recuperan solas.

## 9. Destruir

```bash
cd infrastructure/terraform
terraform destroy
```

Los repositorios ECR (`force_delete`) y el bucket S3 (`force_destroy`, incluso
con versiones) se eliminan aunque tengan contenido. RDS no deja snapshot final.

## 10. Costes estimados y control del presupuesto

Precios on-demand aproximados en `us-east-1`:

| Recurso | USD/hora |
|---|---|
| EC2 platform t3.medium | 0.042 |
| EC2 wh-a + 2× wh-b t3.small | 0.062 |
| RDS db.t3.micro single-AZ (+20 GB gp2) | 0.017 (+0.003) |
| ALB (base + LCU bajo) | ~0.03 |
| IPv4 públicas (4 EC2 + ALB) | ~0.03 |
| EBS gp3 68 GB | ~0.008 |
| **Total con todo en marcha** | **~0.19 USD/h ≈ 0.75 USD por sesión de 4 h** |

S3, Lambda, ECR (<1 GB) y CloudWatch Logs con 7 días de retención son céntimos.

Con la sesión terminada las EC2 paran, pero **siguen cobrando** ALB, EBS,
IPv4 del ALB, ECR y almacenamiento de RDS (y la instancia RDS si el lab no la
detiene): ≈ 1-1.5 USD/día. Recomendaciones:

- Si no vas a usar el entorno en varios días: `terraform destroy` (re-crearlo
  tarda ~15 min) o, como mínimo, `aws rds stop-db-instance --db-instance-identifier warehouse-platform-pg`
  (AWS lo vuelve a arrancar a los 7 días).
- Vigila el gasto en la cabecera del Learner Lab (*Budget: $x of $100*); se
  actualiza con varias horas de retraso.
- No subas `wh002_instances` ni los tipos de instancia salvo para la prueba de
  carga, y vuelve a bajarlos después.
- Si el presupuesto se agota, el lab se bloquea y **se borran los recursos**.

## 11. Problemas frecuentes

| Síntoma | Causa / solución |
|---|---|
| `ExpiredToken` en Terraform/CLI | Sesión caducada: `scripts/lab_credentials.sh` |
| Targets `unhealthy` justo tras el apply | Bootstrap en curso (3-5 min) o imágenes ausentes en ECR → `build_push_ecr.sh` + `redeploy.sh` |
| `AccessDenied` al crear algo | Servicio/acción no permitido en Learner Lab; usa solo los tipos de instancia `nano..large` |
| API de bodega reinicia en bucle | La base aún no existía (platform no terminó); se recupera sola al crearse |
| No llega por SSH | Tu IP cambió: actualiza `my_ip_cidr` |
