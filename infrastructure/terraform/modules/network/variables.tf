variable "name" {
  description = "Prefijo de nombres de recursos."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR de la VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "my_ip_cidr" {
  description = "CIDR del operador (SSH y consola RabbitMQ)."
  type        = string
}
