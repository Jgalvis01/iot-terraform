# Pruebas de la topología sin tocar AWS (proveedor simulado).
#   cd infrastructure/terraform && terraform init -backend=false && terraform test

mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = { names = ["us-east-1a", "us-east-1b", "us-east-1c"] }
  }
  mock_data "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/LabRole" }
  }
  mock_data "aws_iam_instance_profile" {
    defaults = { name = "LabInstanceProfile", arn = "arn:aws:iam::123456789012:instance-profile/LabInstanceProfile" }
  }
  mock_data "aws_ami" {
    defaults = { id = "ami-0123456789abcdef0" }
  }
  mock_resource "aws_ecr_repository" {
    defaults = { repository_url = "123456789012.dkr.ecr.us-east-1.amazonaws.com/repo" }
  }
  mock_resource "aws_lb" {
    defaults = {
      arn      = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/x/1"
      dns_name = "lb.us-east-1.elb.amazonaws.com"
    }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/x/1" }
  }
  mock_resource "aws_db_instance" {
    defaults = { address = "central.rds.amazonaws.com", port = 5432, endpoint = "central.rds.amazonaws.com:5432" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::warehouse-platform-test" }
  }
  mock_resource "aws_lambda_function" {
    defaults = { arn = "arn:aws:lambda:us-east-1:123456789012:function:s3_processor" }
  }
  mock_resource "aws_ecs_task_definition" {
    defaults = { arn = "arn:aws:ecs:us-east-1:123456789012:task-definition/x:1" }
  }
  mock_resource "aws_ecs_cluster" {
    defaults = { arn = "arn:aws:ecs:us-east-1:123456789012:cluster/x" }
  }
}

variables {
  my_ip_cidr         = "203.0.113.10/32"
  s3_create_with_cli = false # en las pruebas no se ejecuta AWS CLI
}

run "topologia_completa" {
  command = apply

  assert {
    condition     = length(output.vpcs) == 4
    error_message = "Deben existir 4 VPCs (central + 3 bodegas)."
  }

  assert {
    condition = alltrue([for k, v in output.vpcs :
    length(v.public_subnets) >= 1 && length(v.private_subnets) >= 1])
    error_message = "Cada VPC debe tener al menos una subnet pública y una privada."
  }

  assert {
    condition     = length(output.vpcs["central"].public_subnets) == 2 && length(output.vpcs["central"].private_subnets) == 2
    error_message = "La central necesita 2 zonas (ALB, RDS, EFS)."
  }

  assert {
    condition     = length(distinct([for v in values(output.vpcs) : v.cidr])) == 4
    error_message = "Las 4 VPCs deben tener CIDR distintas."
  }

  assert {
    condition     = length(output.peering_connections) == 3
    error_message = "Debe haber exactamente 3 peerings (central<->cada bodega), ninguno entre bodegas."
  }

  assert {
    condition     = length(output.warehouses) == 3 && length(output.instance_ids) == 3
    error_message = "Debe haber una EC2 por bodega."
  }

  assert {
    condition     = output.warehouses["WH-001"].private_ip == "10.1.10.10" && output.warehouses["WH-003"].private_ip == "10.3.10.10"
    error_message = "IP privada fija esperada .10 en la subnet privada de cada bodega."
  }

  assert {
    condition     = length(distinct([for w in values(output.warehouses) : w.security_group_id])) == 3
    error_message = "Cada bodega debe tener su propio security group."
  }

  assert {
    condition = toset(output.ecs_services) == toset([
      "rabbitmq", "central-api", "command-relay", "transfer-orchestrator",
    "projection-worker", "audit-worker", "report-worker"])
    error_message = "Servicios ECS de la central incompletos."
  }

  assert {
    condition     = output.urls["wh-002"] == "http://lb.us-east-1.elb.amazonaws.com:8002"
    error_message = "Las bodegas se exponen a través del ALB de la central."
  }
}

# --- Peering: rutas solo hacia la central desde la bodega
run "peering_solo_hub" {
  command = apply

  module {
    source = "./modules/peering"
  }

  variables {
    name                  = "t"
    spoke_name            = "wh-001"
    hub_vpc_id            = "vpc-hub"
    hub_cidr              = "10.0.0.0/16"
    hub_route_table_ids   = { public = "rtb-hub-pub", private = "rtb-hub-priv" }
    spoke_vpc_id          = "vpc-wh1"
    spoke_cidr            = "10.1.0.0/16"
    spoke_route_table_ids = { public = "rtb-wh1-pub", private = "rtb-wh1-priv" }
  }

  assert {
    condition     = alltrue([for r in aws_route.spoke_to_hub : r.destination_cidr_block == "10.0.0.0/16"])
    error_message = "La bodega solo debe tener rutas de peering hacia la CIDR de la central."
  }

  assert {
    condition     = alltrue([for r in aws_route.hub_to_spoke : r.destination_cidr_block == "10.1.0.0/16"])
    error_message = "La central debe enrutar hacia la bodega."
  }

  assert {
    condition     = length(aws_route.hub_to_spoke) == 2 && length(aws_route.spoke_to_hub) == 2
    error_message = "Rutas en tablas públicas y privadas de ambos lados."
  }
}

# --- Bodega: SG propio solo desde la central y NACL que bloquea a las otras bodegas
run "bodega_aislada" {
  command = apply

  module {
    source = "./modules/warehouse"
  }

  variables {
    name                  = "t"
    warehouse_id          = "WH-002"
    region                = "us-east-1"
    vpc_id                = "vpc-wh2"
    subnet_ids            = ["subnet-pub", "subnet-priv"]
    private_subnet_id     = "subnet-priv"
    private_ip            = "10.2.10.10"
    central_cidr          = "10.0.0.0/16"
    denied_cidrs          = ["10.1.0.0/16", "10.3.0.0/16"]
    ami_id                = "ami-0123456789abcdef0"
    instance_profile_name = "LabInstanceProfile"
    templates_dir         = "../aws"
    ecr_registry          = "123456789012.dkr.ecr.us-east-1.amazonaws.com"
    warehouse_image       = "123456789012.dkr.ecr.us-east-1.amazonaws.com/warehouse-api:latest"
    workers_image         = "123456789012.dkr.ecr.us-east-1.amazonaws.com/workers:latest"
    rabbitmq_url          = "amqp://u:p@mq:5672/%2F"
    db_password           = "abcdefghijkl1234"
    log_group_prefix      = "/t"
  }

  assert {
    condition = (aws_vpc_security_group_ingress_rule.api_from_central.cidr_ipv4 == "10.0.0.0/16"
    && aws_vpc_security_group_ingress_rule.icmp_from_central.cidr_ipv4 == "10.0.0.0/16")
    error_message = "La bodega solo acepta tráfico desde la VPC central."
  }

  assert {
    condition     = toset([for r in aws_network_acl_rule.deny_in : r.cidr_block]) == toset(["10.1.0.0/16", "10.3.0.0/16"])
    error_message = "La NACL debe denegar la entrada desde las otras bodegas."
  }

  assert {
    condition     = alltrue([for r in aws_network_acl_rule.deny_out : r.rule_action == "deny" && r.egress])
    error_message = "La NACL debe denegar la salida hacia las otras bodegas."
  }

  assert {
    condition     = alltrue([for r in aws_network_acl_rule.deny_in : r.rule_number < aws_network_acl_rule.allow_in.rule_number])
    error_message = "Las reglas deny deben evaluarse antes que el allow general."
  }

  assert {
    condition     = aws_instance.this.private_ip == "10.2.10.10" && aws_instance.this.subnet_id == "subnet-priv"
    error_message = "La EC2 vive en la subnet privada con IP fija."
  }

  assert {
    condition     = aws_vpc_security_group_egress_rule.broker.cidr_ipv4 == "10.0.0.0/16" && aws_vpc_security_group_egress_rule.broker.from_port == 5672
    error_message = "La bodega solo habla AMQP con la central."
  }
}
