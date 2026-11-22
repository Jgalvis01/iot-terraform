variable "function_name" {
  type    = string
  default = "s3_processor"
}

variable "source_dir" {
  description = "Directorio con el código de la Lambda (handler.lambda_handler)."
  type        = string
}

variable "role_arn" {
  description = "ARN del rol de ejecución (LabRole en Learner Lab)."
  type        = string
}

variable "bucket_id" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "trigger_prefix" {
  type    = string
  default = "exports/"
}

variable "log_retention_days" {
  type    = number
  default = 7
}

variable "timeout" {
  type    = number
  default = 60
}

variable "memory_size" {
  type    = number
  default = 256
}
