# ADR-015 — Una VPC por bodega (EC2) y central en ECS con peering hub-and-spoke

**Estado:** Aceptada (reemplaza la topología de ADR-012)

## Contexto

Requisitos del despliegue en AWS:

- Cada bodega (`WH-001`, `WH-002`, `WH-003`) es **una EC2** con Docker, en **su propia VPC** con subnet pública y privada, y con **su propio security group**.
- La bodega central es un **ECS** con su propia VPC pública y privada.
- **Solo hay comunicación central <-> bodega.** Las bodegas no se ven entre ellas: todo pasa por la central.
- Debe haber un **broker** que soporte picos de 1000-2000 peticiones/s generadas por las 3 bodegas.

ADR-012 desplegaba todo en una sola VPC compartida, con RabbitMQ, Redis y los workers en una única EC2 `platform`.

## Alternativas

1. **Transit Gateway** con tablas de rutas separadas: aislamiento correcto, pero cuesta ~0,05 USD/h por adjunto (4 adjuntos) y es más complejo de razonar.
2. **Una sola VPC con subnets y SG por bodega:** barato, pero no cumple "una VPC por bodega" y el aislamiento depende solo de los SG.
3. **4 VPCs + VPC peering hub-and-spoke** (elegida).

## Decisión

```text
                    Internet
                       │
         ALB público (VPC central, subnets públicas)
   :80 central-api   :8001/:8002/:8003 bodegas   :15672 consola broker (solo my_ip)
                       │
VPC central 10.0.0.0/16 — subnets privadas (2 AZ), NAT
  ECS Fargate: central-api ×2, command-relay, transfer-orchestrator,
               projection-worker ×2, audit-worker, report-worker
  RabbitMQ (ECS + EFS) detrás de un NLB interno :5672      RDS PostgreSQL central_read
        │ peering            │ peering            │ peering        (no hay peering entre bodegas)
VPC WH-001 10.1/16    VPC WH-002 10.2/16    VPC WH-003 10.3/16
 pública: NAT          pública: NAT          pública: NAT
 privada: EC2 + Docker privada: EC2 + Docker privada: EC2 + Docker
 (api, postgres, redis, outbox relay ×2, cache-worker) — SG propio + NACL
```

| Elemento | Detalle |
|---|---|
| VPCs | `modules/vpc`: N subnets públicas (IGW) + N privadas (NAT). Central con 2 AZ (ALB, RDS y EFS lo exigen); cada bodega con 1 AZ |
| Peering | `modules/peering`: un peering central <-> bodega con rutas en ambas direcciones. **El peering no es transitivo**: aunque las 3 bodegas estén unidas a la central, una bodega no puede llegar a otra a través de ella |
| Bodegas | `modules/warehouse`: EC2 AL2023 en la subnet **privada**, sin IP pública, IP privada fija (`.10`), Docker + compose instalados por `user_data`, PostgreSQL propio (ADR-001 se cumple físicamente), acceso de operador por **SSM Session Manager** (sin SSH abierto) |
| Security group por bodega | Entrada: solo `8000/tcp` e ICMP desde la CIDR de la central. Salida: `443`/`80` (NAT: ECR, S3, CloudWatch, SSM, paquetes), `5672` y ICMP solo hacia la central |
| NACL por bodega | Deniega explícitamente entrada y salida hacia las CIDR de las otras bodegas (defensa en profundidad si alguien añade una ruta por error) |
| Central | `modules/central`: clúster ECS Fargate, ALB público, NLB interno para el broker, EFS para los datos de RabbitMQ y RDS en las subnets privadas. Llega a cada bodega por su IP privada (`WAREHOUSES=WH-001=http://10.1.10.10:8000,...`) |
| Acceso de clientes a las bodegas | Por el ALB de la central (`:8001..8003`, target groups de tipo `ip` con `availability_zone = "all"` porque las IPs están en VPCs peered). El cliente nunca entra directamente a la VPC de una bodega |
| Broker | RabbitMQ 3.13 en ECS (1 vCPU / 2 GB por defecto), colas durables y mensajes persistentes en EFS, nodo fijo `rabbit@localhost` para reutilizar los datos tras un reinicio, despliegue `min 0% / max 100%` para que nunca haya dos brokers sobre el mismo directorio |
| Throughput | Bodega: uvicorn con `--workers 2` y 2 réplicas del outbox relay que compiten con `SKIP LOCKED`. Central: `projection-worker` ×2 como consumidores competidores (el orden se protege con `occurred_at`, ADR-008). Autoescalado por CPU opcional (`enable_autoscaling`) |
| Pruebas | `terraform test` con proveedor simulado (`tests/topology.tftest.hcl`): 4 VPCs, 3 peerings, SG/NACL por bodega, servicios ECS. Verificación real con `scripts/aws_verify.sh` |

## Consecuencias / Trade-offs

- Aislamiento por tres capas independientes (rutas, SG, NACL), verificable con AWS CLI y con pruebas de red reales (`nc`) desde cada bodega vía SSM.
- **Coste:** 4 NAT Gateways (~0,18 USD/h) son el mayor gasto. Se puede desactivar con `enable_nat=false` solo si las imágenes ya están en las EC2 (sin NAT no hay ECR ni CloudWatch). Total estimado ~0,50 USD/h con todo encendido.
- El broker sigue siendo una sola instancia (no hay clúster de RabbitMQ). Su caída **no detiene el servicio** (ADR-016) y sus mensajes sobreviven al reinicio (EFS), pero durante la caída no hay procesamiento en la central.
- RabbitMQ sobre EFS tiene más latencia de disco que sobre EBS. Para 1000-2000 msg/s se recomienda subir `broker_cpu`/`broker_memory` durante la prueba de carga.
- Una sola EC2 por bodega: si la EC2 se cae, esa bodega no atiende, pero las demás sí y la central la marca `OFFLINE` en el reporte.

## Mitigación y cómo sería en producción

- Amazon MQ for RabbitMQ en clúster multi-AZ (o Amazon MSK) en lugar del broker en ECS.
- Transit Gateway con una tabla de rutas por bodega cuando haya muchas bodegas (el número de peerings crece linealmente, pero cada uno se administra aparte).
- VPC endpoints (ECR, S3, CloudWatch, SSM) en lugar de NAT para tráfico de AWS; Auto Scaling Group por bodega detrás del ALB.
