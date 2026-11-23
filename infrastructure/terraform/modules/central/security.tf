# Security groups de la central (VPC hub).

resource "aws_security_group" "alb" {
  name        = "${var.name}-central-alb-sg"
  description = "ALB publico de la central: API central y acceso a bodegas a traves de la central"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-central-alb-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  for_each = toset(var.public_ingress_cidrs)

  security_group_id = aws_security_group.alb.id
  description       = "HTTP central-api"
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_ingress_rule" "alb_warehouses" {
  for_each = toset(var.public_ingress_cidrs)

  security_group_id = aws_security_group.alb.id
  description       = "HTTP hacia las bodegas (pasan por la central)"
  cidr_ipv4         = each.value
  ip_protocol       = "tcp"
  from_port         = min([for w in values(var.warehouses) : w.listener_port]...)
  to_port           = max([for w in values(var.warehouses) : w.listener_port]...)
}

resource "aws_vpc_security_group_ingress_rule" "alb_rabbit_ui" {
  security_group_id = aws_security_group.alb.id
  description       = "Consola RabbitMQ solo para el operador"
  cidr_ipv4         = var.my_ip_cidr
  ip_protocol       = "tcp"
  from_port         = 15672
  to_port           = 15672
}

resource "aws_vpc_security_group_egress_rule" "alb_to_central" {
  security_group_id = aws_security_group.alb.id
  description       = "ALB -> tareas ECS de la central"
  cidr_ipv4         = var.vpc_cidr
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_warehouses" {
  for_each = var.warehouses

  security_group_id = aws_security_group.alb.id
  description       = "ALB -> API de ${each.key} por peering"
  cidr_ipv4         = each.value.cidr
  ip_protocol       = "tcp"
  from_port         = each.value.api_port
  to_port           = each.value.api_port
}

# Tareas ECS (central-api, workers, broker).
resource "aws_security_group" "tasks" {
  name        = "${var.name}-central-tasks-sg"
  description = "Tareas ECS de la central"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-central-tasks-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "tasks_api_from_alb" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "ALB -> central-api"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_ingress_rule" "tasks_rabbit_ui_from_alb" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "ALB -> consola RabbitMQ"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 15672
  to_port                      = 15672
}

# El NLB interno no tiene SG y no preserva la IP del cliente: el tráfico AMQP
# (de la central y de las bodegas) llega a la tarea desde IPs del NLB en esta VPC.
resource "aws_vpc_security_group_ingress_rule" "tasks_amqp_from_vpc" {
  security_group_id = aws_security_group.tasks.id
  description       = "AMQP via NLB interno"
  cidr_ipv4         = var.vpc_cidr
  ip_protocol       = "tcp"
  from_port         = 5672
  to_port           = 5672
}

resource "aws_vpc_security_group_egress_rule" "tasks_all" {
  security_group_id = aws_security_group.tasks.id
  description       = "Salida: NAT (ECR, S3, logs), RDS, broker y bodegas por peering"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "efs" {
  name        = "${var.name}-central-efs-sg"
  description = "EFS de datos del broker"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-central-efs-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "efs_from_tasks" {
  security_group_id            = aws_security_group.efs.id
  description                  = "NFS desde las tareas ECS"
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "tcp"
  from_port                    = 2049
  to_port                      = 2049
}

resource "aws_security_group" "rds" {
  name        = "${var.name}-central-rds-sg"
  description = "PostgreSQL central solo desde las tareas ECS"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-central-rds-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_tasks" {
  security_group_id            = aws_security_group.rds.id
  description                  = "PostgreSQL desde ECS"
  referenced_security_group_id = aws_security_group.tasks.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
