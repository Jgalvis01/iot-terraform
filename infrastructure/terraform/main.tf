# Topología (hub-and-spoke):
#
#                    Internet
#                       |
#          +------------v-------------+         VPC central 10.0.0.0/16
#          | ALB :80 :8001-8003 :15672|  subnets públicas (2 AZ) + NAT
#          +------------+-------------+
#                       |                       subnets privadas (2 AZ)
#     ECS Fargate: central-api, command-relay, orquestador, projection,
#     audit, report  +  RabbitMQ (EFS)  <- NLB interno :5672   RDS central_read
#            |                 |                 |
#        peering           peering           peering        (NO hay peering entre bodegas)
#            |                 |                 |
#   VPC WH-001 10.1/16   VPC WH-002 10.2/16   VPC WH-003 10.3/16
#   pública (NAT)        pública (NAT)        pública (NAT)
#   privada: EC2+Docker  privada: EC2+Docker  privada: EC2+Docker
#   SG propio + NACL     SG propio + NACL     SG propio + NACL

data "aws_iam_role" "lab" {
  name = var.lab_role_name
}

data "aws_iam_instance_profile" "lab" {
  name = var.lab_instance_profile_name
}

data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "random_password" "db" {
  length  = 24
  special = false
}

resource "random_password" "rabbitmq" {
  length  = 20
  special = false
}

resource "random_password" "warehouse_db" {
  for_each = var.warehouses
  length   = 24
  special  = false
}

locals {
  db_password       = coalesce(var.db_password, random_password.db.result)
  rabbitmq_password = coalesce(var.rabbitmq_password, random_password.rabbitmq.result)
  log_group_prefix  = "/${var.project_name}"
  api_port          = 8000

  # IP privada fija de cada EC2: .10 de su subnet privada (la misma que crea el módulo vpc).
  warehouse_ips = { for id, w in var.warehouses : id => cidrhost(cidrsubnet(w.cidr, 8, 10), 10) }

  images = {
    warehouse_api = "${module.ecr.repository_urls["warehouse-api"]}:${var.image_tag}"
    central_api   = "${module.ecr.repository_urls["central-api"]}:${var.image_tag}"
    workers       = "${module.ecr.repository_urls["workers"]}:${var.image_tag}"
  }
}

# ------------------------------------------------------------------ redes
module "vpc_central" {
  source     = "./modules/vpc"
  name       = "${var.project_name}-central"
  role       = "central"
  cidr       = var.central_cidr
  az_count   = 2 # ALB, RDS y EFS necesitan al menos 2 zonas
  enable_nat = var.enable_nat
}

module "vpc_warehouse" {
  source   = "./modules/vpc"
  for_each = var.warehouses

  name       = "${var.project_name}-${lower(each.key)}"
  role       = "warehouse"
  cidr       = each.value.cidr
  az_count   = 1
  enable_nat = var.enable_nat
}

module "peering" {
  source   = "./modules/peering"
  for_each = var.warehouses

  name                  = "${var.project_name}-${lower(each.key)}"
  spoke_name            = lower(each.key)
  hub_vpc_id            = module.vpc_central.vpc_id
  hub_cidr              = module.vpc_central.cidr
  hub_route_table_ids   = module.vpc_central.route_table_ids
  spoke_vpc_id          = module.vpc_warehouse[each.key].vpc_id
  spoke_cidr            = module.vpc_warehouse[each.key].cidr
  spoke_route_table_ids = module.vpc_warehouse[each.key].route_table_ids
}

# ------------------------------------------------------------------ servicios base
module "ecr" {
  source = "./modules/ecr"
}

module "storage" {
  source          = "./modules/storage"
  bucket_prefix   = var.project_name
  region          = var.region
  create_with_cli = var.s3_create_with_cli
}

module "database" {
  source             = "./modules/database"
  name               = var.project_name
  private_subnet_ids = module.vpc_central.private_subnet_ids
  rds_sg_id          = module.central.rds_security_group_id
  instance_class     = var.db_instance_class
  username           = var.db_username
  password           = local.db_password
  db_name            = "central_read"
}

# ------------------------------------------------------------------ bodega central (ECS)
module "central" {
  source = "./modules/central"

  name               = var.project_name
  region             = var.region
  vpc_id             = module.vpc_central.vpc_id
  vpc_cidr           = module.vpc_central.cidr
  public_subnet_ids  = module.vpc_central.public_subnet_ids
  private_subnet_ids = module.vpc_central.private_subnet_ids

  warehouses = { for id, w in var.warehouses : id => {
    private_ip    = local.warehouse_ips[id]
    cidr          = w.cidr
    api_port      = local.api_port
    listener_port = w.listener_port
  } }

  my_ip_cidr           = var.my_ip_cidr
  public_ingress_cidrs = var.public_ingress_cidrs
  lab_role_arn         = data.aws_iam_role.lab.arn

  images = {
    central_api = local.images.central_api
    workers     = local.images.workers
  }

  rabbitmq_password = local.rabbitmq_password
  database_url      = "postgresql://${var.db_username}:${local.db_password}@${module.database.address}:${module.database.port}/${module.database.db_name}"
  s3_bucket         = module.storage.bucket_name

  central_api_count  = var.central_api_count
  projection_workers = var.projection_workers
  broker_cpu         = var.broker_cpu
  broker_memory      = var.broker_memory
  enable_autoscaling = var.enable_autoscaling

  log_group_prefix   = local.log_group_prefix
  log_retention_days = var.log_retention_days

  depends_on = [module.peering]
}

# ------------------------------------------------------------------ bodegas (EC2)
module "warehouse" {
  source   = "./modules/warehouse"
  for_each = var.warehouses

  name         = var.project_name
  warehouse_id = each.key
  region       = var.region

  vpc_id            = module.vpc_warehouse[each.key].vpc_id
  subnet_ids        = concat(module.vpc_warehouse[each.key].public_subnet_ids, module.vpc_warehouse[each.key].private_subnet_ids)
  private_subnet_id = module.vpc_warehouse[each.key].private_subnet_ids[0]
  private_ip        = local.warehouse_ips[each.key]
  central_cidr      = module.vpc_central.cidr
  denied_cidrs      = [for other, w in var.warehouses : w.cidr if other != each.key]

  ami_id                = data.aws_ami.al2023.id
  instance_type         = var.warehouse_instance_type
  instance_profile_name = data.aws_iam_instance_profile.lab.name
  key_name              = var.key_name
  templates_dir         = "${path.root}/../aws"

  ecr_registry    = module.ecr.registry
  warehouse_image = local.images.warehouse_api
  workers_image   = local.images.workers
  rabbitmq_url    = module.central.rabbitmq_url
  db_password     = random_password.warehouse_db[each.key].result

  api_port       = local.api_port
  api_workers    = var.warehouse_api_workers
  relay_replicas = var.outbox_relay_replicas

  log_group_prefix   = local.log_group_prefix
  log_retention_days = var.log_retention_days

  depends_on = [module.peering]
}

# ------------------------------------------------------------------ Lambda S3
module "lambda" {
  source             = "./modules/lambda"
  source_dir         = "${path.root}/../../services/lambda/s3_processor"
  role_arn           = data.aws_iam_role.lab.arn
  bucket_id          = module.storage.bucket_name
  bucket_arn         = module.storage.bucket_arn
  log_retention_days = var.log_retention_days
}
