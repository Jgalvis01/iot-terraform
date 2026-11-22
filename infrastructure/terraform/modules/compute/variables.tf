variable "name" {
  type = string
}

variable "region" {
  type = string
}

variable "templates_dir" {
  description = "Directorio con user_data.sh.tftpl y *-compose.yml.tftpl (infrastructure/aws)."
  type        = string
}

variable "public_subnet_ids" {
  type = list(string)
}

variable "apps_sg_id" {
  type = string
}

variable "instance_profile_name" {
  description = "Instance profile existente (LabInstanceProfile en Learner Lab)."
  type        = string
}

variable "key_name" {
  type = string
}

variable "platform_instance_type" {
  type    = string
  default = "t3.medium"
}

variable "warehouse_instance_type" {
  type    = string
  default = "t3.small"
}

variable "wh002_instances" {
  type    = number
  default = 2
}

variable "ecr_registry" {
  type = string
}

variable "ecr_repository_urls" {
  description = "Mapa nombre de repo => URL (warehouse-api, central-api, workers...)."
  type        = map(string)
}

variable "image_tag" {
  type = string
}

variable "db_address" {
  type = string
}

variable "db_port" {
  type = number
}

variable "db_username" {
  type = string
}

variable "db_password" {
  type      = string
  sensitive = true
}

variable "rabbitmq_user" {
  type    = string
  default = "warehouse"
}

variable "rabbitmq_password" {
  type      = string
  sensitive = true
}

variable "s3_bucket" {
  type = string
}

variable "alb_dns_name" {
  type = string
}

variable "target_group_arns" {
  description = "Mapa central|wh001|wh002|wh003 => ARN."
  type        = map(string)
}

variable "log_group_prefix" {
  type    = string
  default = "/warehouse-platform"
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "compose_version" {
  description = "Versión del plugin docker compose a instalar."
  type        = string
  default     = "v2.29.7"
}
