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

variable "create_with_cli" {
  description = "Crear el bucket con AWS CLI (necesario en Learner Lab por su SCP sobre S3 Object Lock)."
  type        = bool
  default     = true
}

variable "region" {
  type    = string
  default = "us-east-1"
}
