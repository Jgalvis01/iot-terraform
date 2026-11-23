# Una bodega = una EC2 en la subnet privada de SU propia VPC, con Docker y
# docker compose (API, PostgreSQL local, Redis, outbox relay y cache-worker).
#
# Aislamiento entre bodegas (defensa en profundidad):
#   1. Rutas: la VPC de la bodega solo tiene peering con la central.
#   2. Security group propio: solo acepta tráfico que venga de la CIDR de la central.
#   3. NACL: deniega explícitamente las CIDR de las otras bodegas (entrada y salida).

locals {
  slug        = lower(var.warehouse_id)
  service_set = toset(["api", "outbox", "cache-worker", "postgres", "redis"])
}

# ---------------------------------------------------------------- security group
resource "aws_security_group" "this" {
  name        = "${var.name}-${local.slug}-sg"
  description = "Bodega ${var.warehouse_id}: solo accesible desde la central"
  vpc_id      = var.vpc_id
  tags        = { Name = "${var.name}-${local.slug}-sg", Warehouse = var.warehouse_id }
}

resource "aws_vpc_security_group_ingress_rule" "api_from_central" {
  security_group_id = aws_security_group.this.id
  description       = "API de bodega desde la central (ALB, central-api, orquestador)"
  cidr_ipv4         = var.central_cidr
  ip_protocol       = "tcp"
  from_port         = var.api_port
  to_port           = var.api_port
}

resource "aws_vpc_security_group_ingress_rule" "icmp_from_central" {
  security_group_id = aws_security_group.this.id
  description       = "Ping de diagnostico desde la central"
  cidr_ipv4         = var.central_cidr
  ip_protocol       = "icmp"
  from_port         = -1
  to_port           = -1
}

resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.this.id
  description       = "HTTPS saliente via NAT (ECR, S3, CloudWatch, SSM, paquetes)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "http" {
  security_group_id = aws_security_group.this.id
  description       = "HTTP saliente via NAT (repositorios de paquetes)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_egress_rule" "broker" {
  security_group_id = aws_security_group.this.id
  description       = "AMQP hacia el broker de la central"
  cidr_ipv4         = var.central_cidr
  ip_protocol       = "tcp"
  from_port         = 5672
  to_port           = 5672
}

resource "aws_vpc_security_group_egress_rule" "icmp_to_central" {
  security_group_id = aws_security_group.this.id
  description       = "Ping de diagnostico hacia la central"
  cidr_ipv4         = var.central_cidr
  ip_protocol       = "icmp"
  from_port         = -1
  to_port           = -1
}

# ---------------------------------------------------------------- NACL
resource "aws_network_acl" "this" {
  vpc_id     = var.vpc_id
  subnet_ids = var.subnet_ids
  tags       = { Name = "${var.name}-${local.slug}-nacl", Warehouse = var.warehouse_id }
}

resource "aws_network_acl_rule" "deny_in" {
  for_each = { for idx, cidr in var.denied_cidrs : tostring(idx) => cidr }

  network_acl_id = aws_network_acl.this.id
  rule_number    = 100 + tonumber(each.key)
  egress         = false
  protocol       = "-1"
  rule_action    = "deny"
  cidr_block     = each.value
}

resource "aws_network_acl_rule" "deny_out" {
  for_each = { for idx, cidr in var.denied_cidrs : tostring(idx) => cidr }

  network_acl_id = aws_network_acl.this.id
  rule_number    = 100 + tonumber(each.key)
  egress         = true
  protocol       = "-1"
  rule_action    = "deny"
  cidr_block     = each.value
}

resource "aws_network_acl_rule" "allow_in" {
  network_acl_id = aws_network_acl.this.id
  rule_number    = 200
  egress         = false
  protocol       = "-1"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
}

resource "aws_network_acl_rule" "allow_out" {
  network_acl_id = aws_network_acl.this.id
  rule_number    = 200
  egress         = true
  protocol       = "-1"
  rule_action    = "allow"
  cidr_block     = "0.0.0.0/0"
}

# ---------------------------------------------------------------- logs
resource "aws_cloudwatch_log_group" "services" {
  for_each          = local.service_set
  name              = "${var.log_group_prefix}/${local.slug}-${each.value}"
  retention_in_days = var.log_retention_days
}

# ---------------------------------------------------------------- EC2
locals {
  compose = templatefile("${var.templates_dir}/warehouse-compose.yml.tftpl", {
    warehouse_id     = var.warehouse_id
    slug             = local.slug
    db_name          = replace(local.slug, "-", "_")
    db_password      = var.db_password
    region           = var.region
    log_group_prefix = var.log_group_prefix
    warehouse_image  = var.warehouse_image
    workers_image    = var.workers_image
    rabbitmq_url     = var.rabbitmq_url
    api_port         = var.api_port
    api_workers      = var.api_workers
    relay_replicas   = var.relay_replicas
    postgres_image   = var.postgres_image
    redis_image      = var.redis_image
  })
}

resource "aws_instance" "this" {
  ami                    = var.ami_id
  instance_type          = var.instance_type
  subnet_id              = var.private_subnet_id
  private_ip             = var.private_ip
  vpc_security_group_ids = [aws_security_group.this.id]
  iam_instance_profile   = var.instance_profile_name
  key_name               = var.key_name

  user_data_base64 = base64gzip(templatefile("${var.templates_dir}/user_data.sh.tftpl", {
    region          = var.region
    ecr_registry    = var.ecr_registry
    compose_version = var.compose_version
    compose         = local.compose
    warehouse_id    = var.warehouse_id
  }))
  user_data_replace_on_change = true

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_size = var.volume_size
    volume_type = "gp3"
    encrypted   = true
  }

  tags = {
    Name      = "${var.name}-${local.slug}"
    Role      = "warehouse"
    Warehouse = var.warehouse_id
  }

  depends_on = [aws_cloudwatch_log_group.services]
}
