variable "bucket_prefix" {
  description = "Prefijo del nombre del bucket; se añade un sufijo aleatorio."
  type        = string
  default     = "warehouse-platform"
}

variable "expire_days" {
  description = "Días antes de expirar objetos en exports/ y processed/."
  type        = number
  default     = 30
}
