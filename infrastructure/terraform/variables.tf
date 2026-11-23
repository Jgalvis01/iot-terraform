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

variable "central_cidr" {
  description = "CIDR de la VPC de la bodega central (ECS)."
  type        = string
  default     = "10.0.0.0/16"
}

variable "warehouses" {
  description = <<-EOT
    Bodegas: cada una es una EC2 en su PROPIA VPC (subnet pública + privada).
    id => { cidr = CIDR /16 de su VPC, listener_port = puerto del ALB central que la expone }.
  EOT
  type = map(object({
    cidr          = string
    listener_port = number
  }))
  default = {
    "WH-001" = { cidr = "10.1.0.0/16", listener_port = 8001 }
    "WH-002" = { cidr = "10.2.0.0/16", listener_port = 8002 }
    "WH-003" = { cidr = "10.3.0.0/16", listener_port = 8003 }
  }

  validation {
    condition     = alltrue([for k, _ in var.warehouses : can(regex("^WH-[0-9]{3}$", k))])
    error_message = "Los ids de bodega deben tener el formato WH-001."
  }

  validation {
    condition     = length(distinct([for w in values(var.warehouses) : w.cidr])) == length(var.warehouses)
    error_message = "Cada bodega debe tener una CIDR distinta."
  }
}

variable "my_ip_cidr" {
  description = "Tu IP pública en CIDR (p.ej. 203.0.113.10/32) para la consola RabbitMQ. `curl -s https://checkip.amazonaws.com`."
  type        = string

  validation {
    condition     = can(cidrhost(var.my_ip_cidr, 0))
    error_message = "my_ip_cidr debe ser un CIDR válido, p.ej. 203.0.113.10/32."
  }
}

variable "public_ingress_cidrs" {
  description = "Quién puede usar el ALB público de la central (API central y bodegas a través de la central)."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "enable_nat" {
  description = "Un NAT Gateway por VPC: las EC2 y tareas ECS están en subnets privadas y necesitan salida a Internet."
  type        = bool
  default     = true
}

variable "key_name" {
  description = "Par de claves EC2 (opcional; el acceso a las bodegas es por SSM Session Manager)."
  type        = string
  default     = null
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
  description = "Password maestro del RDS central. Si es null se genera uno aleatorio."
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

variable "warehouse_instance_type" {
  type    = string
  default = "t3.small"
}

variable "warehouse_api_workers" {
  description = "Procesos uvicorn por bodega."
  type        = number
  default     = 2
}

variable "outbox_relay_replicas" {
  description = "Réplicas del outbox relay por bodega."
  type        = number
  default     = 2
}

variable "central_api_count" {
  type    = number
  default = 2
}

variable "projection_workers" {
  type    = number
  default = 2
}

variable "broker_cpu" {
  type    = number
  default = 1024
}

variable "broker_memory" {
  type    = number
  default = 2048
}

variable "enable_autoscaling" {
  description = "Autoescalado por CPU de central-api y projection-worker (requiere el rol vinculado de Application Auto Scaling)."
  type        = bool
  default     = false
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
