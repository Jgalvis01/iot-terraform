variable "name" {
  description = "Prefijo de nombres (p.ej. warehouse-platform-wh-001)."
  type        = string
}

variable "role" {
  description = "Rol de la VPC (central | warehouse), se usa como tag."
  type        = string
}

variable "cidr" {
  description = "CIDR /16 de la VPC."
  type        = string

  validation {
    condition     = can(cidrhost(var.cidr, 0)) && tonumber(split("/", var.cidr)[1]) <= 16
    error_message = "cidr debe ser un CIDR válido de tamaño /16 o mayor."
  }
}

variable "az_count" {
  description = "Número de zonas: 1 subnet pública + 1 privada por zona."
  type        = number
  default     = 1

  validation {
    condition     = var.az_count >= 1 && var.az_count <= 3
    error_message = "az_count entre 1 y 3."
  }
}

variable "enable_nat" {
  description = "Crea un NAT Gateway para que la subnet privada salga a Internet."
  type        = bool
  default     = true
}
