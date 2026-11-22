data "aws_iam_role" "lab" {
  name = var.lab_role_name
}

data "aws_iam_instance_profile" "lab" {
  name = var.lab_instance_profile_name
}

resource "random_password" "db" {
  length  = 24
  special = false
}

resource "random_password" "rabbitmq" {
  length  = 20
  special = false
}

locals {
  db_password       = coalesce(var.db_password, random_password.db.result)
  rabbitmq_password = coalesce(var.rabbitmq_password, random_password.rabbitmq.result)
}

module "network" {
  source     = "./modules/network"
  name       = var.project_name
  vpc_cidr   = var.vpc_cidr
  my_ip_cidr = var.my_ip_cidr
}

module "ecr" {
  source = "./modules/ecr"
}

module "storage" {
  source        = "./modules/storage"
  bucket_prefix = var.project_name
}

module "database" {
  source             = "./modules/database"
  name               = var.project_name
  private_subnet_ids = module.network.private_subnet_ids
  rds_sg_id          = module.network.rds_sg_id
  instance_class     = var.db_instance_class
  username           = var.db_username
  password           = local.db_password
}

module "load_balancer" {
  source            = "./modules/load_balancer"
  name              = var.project_name
  vpc_id            = module.network.vpc_id
  public_subnet_ids = module.network.public_subnet_ids
  alb_sg_id         = module.network.alb_sg_id
}

module "compute" {
  source        = "./modules/compute"
  name          = var.project_name
  region        = var.region
  templates_dir = "${path.root}/../aws"

  public_subnet_ids     = module.network.public_subnet_ids
  apps_sg_id            = module.network.apps_sg_id
  instance_profile_name = data.aws_iam_instance_profile.lab.name
  key_name              = var.key_name

  platform_instance_type  = var.platform_instance_type
  warehouse_instance_type = var.warehouse_instance_type
  wh002_instances         = var.wh002_instances

  ecr_registry        = module.ecr.registry
  ecr_repository_urls = module.ecr.repository_urls
  image_tag           = var.image_tag

  db_address  = module.database.address
  db_port     = module.database.port
  db_username = module.database.username
  db_password = local.db_password

  rabbitmq_password = local.rabbitmq_password
  s3_bucket         = module.storage.bucket_name

  alb_dns_name      = module.load_balancer.alb_dns_name
  target_group_arns = module.load_balancer.target_group_arns

  log_retention_days = var.log_retention_days
}

module "lambda" {
  source             = "./modules/lambda"
  source_dir         = "${path.root}/../../services/lambda/s3_processor"
  role_arn           = data.aws_iam_role.lab.arn
  bucket_id          = module.storage.bucket_name
  bucket_arn         = module.storage.bucket_arn
  log_retention_days = var.log_retention_days
}
