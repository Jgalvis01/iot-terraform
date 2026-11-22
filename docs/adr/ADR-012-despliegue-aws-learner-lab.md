# ADR-012 — Despliegue en AWS Learner Lab (EC2 + ECR + RDS único + ALB por puertos)

**Estado:** Aceptada (condicionada por las restricciones del Learner Lab)

## Contexto

El despliegue debe ser reproducible con Terraform (README §29-§30) dentro de **AWS Academy Learner Lab** (`us-east-1`): presupuesto ~100 USD, credenciales temporales de ~4 h, imposibilidad de crear roles/políticas IAM propios y servicios restringidos. Procedimiento operativo en `docs/aws-deployment.md`.

## Alternativas

1. ECS Fargate / EKS para contenedores; ElastiCache, Amazon MQ y un RDS por bodega (arquitectura "de producción").
2. Una sola EC2 con todo (sin escalar bodegas por separado).
3. **EC2 con Docker Compose por rol + ECR + un RDS compartido + ALB** (elegida).

## Decisión

Terraform (`infrastructure/terraform/`, módulos `network, ecr, database, compute, storage, lambda, load_balancer`, estado local) crea:

| Recurso | Detalle |
|---|---|
| VPC `10.20.0.0/16` | 2 subnets públicas (EC2, ALB) y 2 privadas (RDS), **sin NAT Gateway** (ahorro) |
| ECR | 6 repositorios: `warehouse-api, central-api, workers, batch, iot, frontend` |
| EC2 `platform` (t3.medium) | RabbitMQ, un Redis por bodega, `central-api`, orquestador, projection, audit y report workers, 3 `cache-worker` y 3 `outbox relay` |
| EC2 `wh-a` (t3.small) | `wh-001-api` (:8001) y `wh-003-api` (:8003) |
| EC2 `wh-b` x `wh002_instances` (t3.small, default 2) | réplicas de `wh-002-api` (:8002) |
| RDS PostgreSQL 15 `db.t3.micro` | single-AZ, cifrado, subnets privadas; bases lógicas `wh_001`, `wh_002`, `wh_003`, `central_read` (creadas idempotentemente por user_data de `platform`) |
| ALB público | `:80` central, `:8001/:8002/:8003` bodegas; health `/health` (ADR-010) |
| S3 | bucket versionado, SSE, sin acceso público, lifecycle de `exports/` y `processed/` |
| Lambda `s3_processor` | trigger `ObjectCreated` en `exports/` |
| CloudWatch Logs | driver `awslogs`, grupo `/warehouse-platform/<servicio>`, retención 7 días |
| IAM | `data "aws_iam_role" "lab"` (**LabRole**) para la Lambda y **LabInstanceProfile** para las EC2 (pull de ECR, logs, S3); no se crean roles |
| Seguridad | SG del ALB abierto a Internet en 80/8001-8003; SG `apps` solo acepta del ALB, SSH y UI de RabbitMQ únicamente desde `my_ip_cidr`; RDS solo desde `apps` |

Operación: despliegue en dos etapas (ECR -> publicar imágenes con `scripts/build_push_ecr.sh` -> resto), `restart: unless-stopped` para sobrevivir a la parada de EC2 al terminar la sesión, y la central usa el **DNS del ALB** (estable) para llegar a las bodegas, no IPs. Coste estimado ~0,19 USD/h.

Se despliega en AWS: APIs, workers de central, RabbitMQ/Redis, RDS, S3, Lambda y ALB. **No se despliega en AWS:** IoT (Mosquitto, simulador, ingestion, workers IoT), Prometheus/Grafana, ni el planificador del Batch (solo local).

## Consecuencias / Trade-offs

- Cumple las restricciones y es barato y reconstruible con `terraform apply`.
- **Aislamiento reducido:** un único RDS comparte CPU/IO/conexiones entre bodegas (el aislamiento es lógico, no físico), contradiciendo parcialmente ADR-001 en el eje "carga".
- **Puntos únicos de fallo:** la EC2 `platform` concentra RabbitMQ, Redis y los workers/central; RDS es single-AZ sin backups (`backup_retention_period=0`); `wh-a` aloja dos bodegas.
- RabbitMQ y Redis en contenedores (sin gestión, sin HA); sin NAT, las EC2 están en subnets públicas (IP pública) protegidas por SG; la IP pública cambia al reiniciar.
- ALB con puertos distintos y HTTP sin TLS; credenciales en variables/user_data (estado Terraform local con secretos).
- Observabilidad en AWS se reduce a CloudWatch Logs (ADR-013).

## Mitigación y cómo sería en producción

- Documentado en `docs/aws-deployment.md` (restricciones, costes, recuperación tras reinicio del lab); `terraform destroy` limpia todo.
- Producción: ECS/EKS (Fargate) con autoscaling por servicio y despliegue continuo; **RDS/Aurora multi-AZ** (una instancia o clúster por bodega crítica, réplicas de lectura para las calientes) con backups y PITR; **ElastiCache** (Redis) por bodega; **Amazon MQ (RabbitMQ en clúster)** o SQS+SNS; ALB con host/path routing, **HTTPS (ACM)** y WAF; subnets privadas para cómputo con NAT/VPC endpoints; **Secrets Manager** + roles IAM de mínimo privilegio por tarea; estado Terraform remoto en S3 con bloqueo (la alternativa ya está comentada en `backend.tf`); Prometheus/Grafana gestionados o CloudWatch + X-Ray; EventBridge + ECS/AWS Batch para el batch diario.
