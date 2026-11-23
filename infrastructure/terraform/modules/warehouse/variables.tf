variable "name" {
  type = string
}

variable "warehouse_id" {
  description = "WH-001, WH-002, WH-003..."
  type        = string
}

variable "region" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = "Todas las subnets (públicas y privadas) de la VPC de la bodega, para asociar la NACL."
  type        = list(string)
}

variable "private_subnet_id" {
  type = string
}

variable "private_ip" {
  description = "IP privada fija de la EC2 (la central la usa para llegar a la bodega; no cambia al parar/arrancar)."
  type        = string
}

variable "central_cidr" {
  description = "CIDR de la VPC central: única red autorizada a entrar a la bodega."
  type        = string
}

variable "denied_cidrs" {
  description = "CIDR de las otras bodegas: se deniegan en la NACL."
  type        = list(string)
}

variable "ami_id" {
  type = string
}

variable "instance_type" {
  type    = string
  default = "t3.small"
}

variable "volume_size" {
  type    = number
  default = 20
}

variable "instance_profile_name" {
  type = string
}

variable "key_name" {
  description = "Par de claves (opcional: el acceso es por SSM Session Manager, la EC2 no tiene IP pública)."
  type        = string
  default     = null
}

variable "templates_dir" {
  type = string
}

variable "ecr_registry" {
  type = string
}

variable "warehouse_image" {
  type = string
}

variable "workers_image" {
  type = string
}

variable "postgres_image" {
  type    = string
  default = "public.ecr.aws/docker/library/postgres:15-alpine"
}

variable "redis_image" {
  type    = string
  default = "public.ecr.aws/docker/library/redis:7-alpine"
}

variable "rabbitmq_url" {
  type      = string
  sensitive = true
}

variable "db_password" {
  description = "Password del PostgreSQL local de la bodega."
  type        = string
  sensitive   = true
}

variable "api_port" {
  type    = number
  default = 8000
}

variable "api_workers" {
  description = "Procesos uvicorn de la API de bodega."
  type        = number
  default     = 2
}

variable "relay_replicas" {
  description = "Réplicas del outbox relay (compiten con SKIP LOCKED; suben el throughput hacia el broker)."
  type        = number
  default     = 2
}

variable "compose_version" {
  type    = string
  default = "v2.29.7"
}

variable "log_group_prefix" {
  type = string
}

variable "log_retention_days" {
  type    = number
  default = 7
}
