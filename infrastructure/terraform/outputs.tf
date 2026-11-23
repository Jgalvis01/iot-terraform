locals {
  alb = module.central.alb_dns_name
}

output "alb_dns" {
  value = local.alb
}

output "urls" {
  description = "Endpoints públicos. Las bodegas solo se alcanzan a través del ALB de la central."
  value = merge(
    { central = "http://${local.alb}", rabbitmq_ui = "http://${local.alb}:15672" },
    { for id, w in var.warehouses : lower(id) => "http://${local.alb}:${w.listener_port}" },
  )
}

output "vpcs" {
  description = "4 VPCs: central + una por bodega, cada una con subnet pública y privada."
  value = merge(
    {
      central = {
        vpc_id          = module.vpc_central.vpc_id
        cidr            = module.vpc_central.cidr
        public_subnets  = module.vpc_central.public_subnet_ids
        private_subnets = module.vpc_central.private_subnet_ids
      }
    },
    { for id, v in module.vpc_warehouse : id => {
      vpc_id          = v.vpc_id
      cidr            = v.cidr
      public_subnets  = v.public_subnet_ids
      private_subnets = v.private_subnet_ids
    } },
  )
}

output "peering_connections" {
  description = "Solo central <-> bodega (no existe peering entre bodegas)."
  value       = { for id, p in module.peering : id => p.id }
}

output "warehouses" {
  value = { for id, w in module.warehouse : id => {
    instance_id       = w.instance_id
    private_ip        = w.private_ip
    security_group_id = w.security_group_id
    network_acl_id    = w.network_acl_id
    url_via_central   = "http://${local.alb}:${var.warehouses[id].listener_port}"
  } }
}

output "instance_ids" {
  description = "Para `aws ec2 start-instances --instance-ids ...` tras reiniciar la sesión del lab."
  value       = [for w in module.warehouse : w.instance_id]
}

output "ssm_commands" {
  description = "Shell en cada bodega (no tienen IP pública ni SSH abierto)."
  value       = { for id, w in module.warehouse : id => "aws ssm start-session --target ${w.instance_id} --region ${var.region}" }
}

output "ecs_cluster" {
  value = module.central.cluster_name
}

output "ecs_services" {
  value = module.central.service_names
}

output "broker_dns" {
  description = "NLB interno del broker (solo alcanzable desde la VPC central y las bodegas)."
  value       = module.central.broker_dns_name
}

output "rabbitmq_password" {
  value     = local.rabbitmq_password
  sensitive = true
}

output "db_password" {
  value     = local.db_password
  sensitive = true
}

output "rds_endpoint" {
  value = module.database.endpoint
}

output "target_group_arns" {
  value = module.central.target_group_arns
}

output "ecr_repository_urls" {
  value = module.ecr.repository_urls
}

output "ecr_registry" {
  value = module.ecr.registry
}

output "s3_bucket" {
  value = module.storage.bucket_name
}

output "lambda_function" {
  value = module.lambda.function_name
}

output "log_groups" {
  value = concat(module.central.log_groups, flatten([for w in module.warehouse : w.log_groups]), [module.lambda.log_group])
}

output "region" {
  value = var.region
}
