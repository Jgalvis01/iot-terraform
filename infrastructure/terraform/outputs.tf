locals {
  alb = module.load_balancer.alb_dns_name
}

output "alb_dns" {
  value = local.alb
}

output "urls" {
  description = "Endpoints públicos a través del ALB."
  value = {
    central = "http://${local.alb}"
    wh_001  = "http://${local.alb}:8001"
    wh_002  = "http://${local.alb}:8002"
    wh_003  = "http://${local.alb}:8003"
  }
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

output "s3_prefixes" {
  value = module.storage.prefixes
}

output "lambda_function" {
  value = module.lambda.function_name
}

output "rds_endpoint" {
  value = module.database.endpoint
}

output "public_ips" {
  description = "IPs públicas (CAMBIAN al parar/arrancar las EC2: `terraform refresh` o `terraform apply -refresh-only`)."
  value = {
    platform = module.compute.platform.public_ip
    wh_a     = module.compute.wh_a.public_ip
    wh_b     = [for i in module.compute.wh_b : i.public_ip]
  }
}

output "platform_private_ip" {
  value = module.compute.platform.private_ip
}

output "ssh_commands" {
  value = merge(
    {
      platform = "ssh -i labsuser.pem ec2-user@${module.compute.platform.public_ip}"
      wh_a     = "ssh -i labsuser.pem ec2-user@${module.compute.wh_a.public_ip}"
    },
    { for idx, i in module.compute.wh_b : "wh_b_${idx + 1}" => "ssh -i labsuser.pem ec2-user@${i.public_ip}" }
  )
}

output "rabbitmq_ui" {
  value = "http://${module.compute.platform.public_ip}:15672 (usuario warehouse; password: terraform output -raw rabbitmq_password)"
}

output "rabbitmq_password" {
  value     = local.rabbitmq_password
  sensitive = true
}

output "db_password" {
  value     = local.db_password
  sensitive = true
}

output "instance_ids" {
  description = "Para `aws ec2 start-instances --instance-ids ...` tras reiniciar la sesión."
  value       = module.compute.instance_ids
}

output "log_groups" {
  value = concat(module.compute.log_groups, [module.lambda.log_group])
}
