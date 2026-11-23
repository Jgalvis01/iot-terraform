variable "name" {
  type = string
}

variable "region" {
  type = string
}

variable "vpc_id" {
  type = string
}

variable "vpc_cidr" {
  type = string
}

variable "public_subnet_ids" {
  description = "Subnets públicas (>= 2 zonas) para el ALB."
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "Subnets privadas (>= 2 zonas) para ECS, NLB del broker, EFS y RDS."
  type        = list(string)
}

variable "warehouses" {
  description = "Bodegas que maneja la central: id => {private_ip, cidr, api_port, listener_port}."
  type = map(object({
    private_ip    = string
    cidr          = string
    api_port      = number
    listener_port = number
  }))
}

variable "my_ip_cidr" {
  description = "CIDR del operador (consola RabbitMQ)."
  type        = string
}

variable "public_ingress_cidrs" {
  description = "CIDR que pueden usar el ALB público (API central y bodegas)."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "lab_role_arn" {
  description = "LabRole: rol de ejecución y de tarea de ECS (Learner Lab no permite crear roles)."
  type        = string
}

variable "images" {
  description = "Imágenes ECR: central_api, workers."
  type = object({
    central_api = string
    workers     = string
  })
}

variable "broker_image" {
  type    = string
  default = "public.ecr.aws/docker/library/rabbitmq:3.13-management"
}

variable "broker_cpu" {
  type    = number
  default = 1024
}

variable "broker_memory" {
  type    = number
  default = 2048
}

variable "rabbitmq_user" {
  type    = string
  default = "warehouse"
}

variable "rabbitmq_password" {
  type      = string
  sensitive = true
}

variable "database_url" {
  type      = string
  sensitive = true
}

variable "s3_bucket" {
  type = string
}

variable "central_api_count" {
  type    = number
  default = 2
}

variable "projection_workers" {
  description = "Consumidores competidores de q.projection (throughput de sincronización)."
  type        = number
  default     = 2
}

variable "audit_workers" {
  type    = number
  default = 1
}

variable "low_stock_threshold" {
  description = "Unidades disponibles por debajo de las cuales un SKU aparece como bajo stock en los reportes."
  type        = number
  default     = 5
}

variable "enable_autoscaling" {
  type    = bool
  default = false
}

variable "autoscaling_max" {
  type    = number
  default = 4
}

variable "container_insights" {
  type    = bool
  default = false
}

variable "log_group_prefix" {
  type = string
}

variable "log_retention_days" {
  type    = number
  default = 7
}
