variable "region" {
  description = "Región AWS (Learner Lab solo permite us-east-1/us-west-2)."
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefijo de nombres de recursos (máx. 18 caracteres por límite de nombres de ALB/TG)."
  type        = string
  default     = "warehouse-platform"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,17}$", var.project_name))
    error_message = "project_name: minúsculas, dígitos y guiones, 2-18 caracteres."
  }
}

variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}

variable "my_ip_cidr" {
  description = "Tu IP pública en CIDR (p.ej. 203.0.113.10/32) para SSH y consola RabbitMQ. `curl -s https://checkip.amazonaws.com`."
  type        = string

  validation {
    condition     = can(cidrhost(var.my_ip_cidr, 0))
    error_message = "my_ip_cidr debe ser un CIDR válido, p.ej. 203.0.113.10/32."
  }
}

variable "key_name" {
  description = "Par de claves EC2. En Learner Lab existe `vockey` (descarga labsuser.pem desde AWS Details)."
  type        = string
  default     = "vockey"
}

variable "image_tag" {
  description = "Tag de las imágenes en ECR (el mismo que se pasa a scripts/build_push_ecr.sh)."
  type        = string
  default     = "latest"
}

variable "db_username" {
  type    = string
  default = "warehouse"
}

variable "db_password" {
  description = "Password maestro de RDS. Si es null se genera uno aleatorio (ver output/terraform state)."
  type        = string
  sensitive   = true
  default     = null

  validation {
    condition     = var.db_password == null || can(regex("^[A-Za-z0-9]{12,64}$", var.db_password))
    error_message = "db_password: 12-64 caracteres alfanuméricos (se embebe en DATABASE_URL)."
  }
}

variable "rabbitmq_password" {
  description = "Password del usuario RabbitMQ `warehouse`. Si es null se genera."
  type        = string
  sensitive   = true
  default     = null

  validation {
    condition     = var.rabbitmq_password == null || can(regex("^[A-Za-z0-9]{8,64}$", var.rabbitmq_password))
    error_message = "rabbitmq_password: 8-64 caracteres alfanuméricos."
  }
}

variable "wh002_instances" {
  description = "Número de EC2 wh-b que sirven WH-002 detrás del ALB (escalamiento horizontal)."
  type        = number
  default     = 2

  validation {
    condition     = var.wh002_instances >= 1 && var.wh002_instances <= 4
    error_message = "wh002_instances entre 1 y 4 (límite de instancias del Learner Lab)."
  }
}

variable "platform_instance_type" {
  type    = string
  default = "t3.medium"
}

variable "warehouse_instance_type" {
  type    = string
  default = "t3.small"
}

variable "db_instance_class" {
  type    = string
  default = "db.t3.micro"
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "lab_role_name" {
  description = "Rol IAM preexistente del Learner Lab (no se pueden crear roles)."
  type        = string
  default     = "LabRole"
}

variable "lab_instance_profile_name" {
  type    = string
  default = "LabInstanceProfile"
}
