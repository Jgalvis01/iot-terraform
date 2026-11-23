variable "name" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "rds_sg_id" {
  type = string
}

variable "instance_class" {
  type    = string
  default = "db.t3.micro"
}

variable "engine_version" {
  type    = string
  default = "15"
}

variable "allocated_storage" {
  type    = number
  default = 20
}

variable "username" {
  type    = string
  default = "warehouse"
}

variable "password" {
  type      = string
  sensitive = true
}

variable "db_name" {
  description = "Base de datos inicial (read model de la central)."
  type        = string
  default     = "central_read"
}
