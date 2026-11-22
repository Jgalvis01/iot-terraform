variable "repositories" {
  description = "Nombres de repositorios ECR (docs/design-contracts.md §10)."
  type        = list(string)
  default     = ["warehouse-api", "central-api", "workers", "batch", "iot", "frontend"]
}

variable "keep_last_images" {
  description = "Imágenes a conservar por repositorio (lifecycle policy)."
  type        = number
  default     = 10
}
