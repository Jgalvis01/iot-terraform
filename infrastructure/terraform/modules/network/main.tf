data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.name}-vpc" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-igw" }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.this.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index + 1)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${var.name}-public-${count.index + 1}", Tier = "public" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 101)
  availability_zone = local.azs[count.index]

  tags = { Name = "${var.name}-private-${count.index + 1}", Tier = "private" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "${var.name}-public-rt" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${var.name}-private-rt" }
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

resource "aws_security_group" "alb" {
  name        = "${var.name}-alb-sg"
  description = "ALB publico: 80 (central) y 8001-8003 (bodegas)"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${var.name}-alb-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP central-api"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_ingress_rule" "alb_warehouses" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP bodegas WH-001..WH-003"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 8001
  to_port           = 8003
}

resource "aws_vpc_security_group_egress_rule" "alb_all" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "apps" {
  name        = "${var.name}-apps-sg"
  description = "EC2 de aplicacion (platform, wh-a, wh-b)"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${var.name}-apps-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "apps_from_alb" {
  security_group_id            = aws_security_group.apps.id
  description                  = "Trafico del ALB a las APIs"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8003
}

resource "aws_vpc_security_group_ingress_rule" "apps_ssh" {
  security_group_id = aws_security_group.apps.id
  description       = "SSH del operador"
  cidr_ipv4         = var.my_ip_cidr
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
}

resource "aws_vpc_security_group_ingress_rule" "apps_rabbit_ui" {
  security_group_id = aws_security_group.apps.id
  description       = "Consola RabbitMQ solo para el operador"
  cidr_ipv4         = var.my_ip_cidr
  ip_protocol       = "tcp"
  from_port         = 15672
  to_port           = 15672
}

locals {
  internal_ports = {
    amqp    = { from = 5672, to = 5672 }
    redis   = { from = 6379, to = 6381 }
    apis    = { from = 8000, to = 8003 }
    metrics = { from = 9100, to = 9100 }
    rmq_ui  = { from = 15672, to = 15672 }
  }
}

resource "aws_vpc_security_group_ingress_rule" "apps_internal" {
  for_each = local.internal_ports

  security_group_id            = aws_security_group.apps.id
  description                  = "Interno apps: ${each.key}"
  referenced_security_group_id = aws_security_group.apps.id
  ip_protocol                  = "tcp"
  from_port                    = each.value.from
  to_port                      = each.value.to
}

resource "aws_vpc_security_group_egress_rule" "apps_all" {
  security_group_id = aws_security_group.apps.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "rds" {
  name        = "${var.name}-rds-sg"
  description = "PostgreSQL solo desde las EC2 de aplicacion"
  vpc_id      = aws_vpc.this.id
  tags        = { Name = "${var.name}-rds-sg" }
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_apps" {
  security_group_id            = aws_security_group.rds.id
  description                  = "PostgreSQL desde apps"
  referenced_security_group_id = aws_security_group.apps.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
