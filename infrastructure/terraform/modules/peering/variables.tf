variable "name" {
  type = string
}

variable "spoke_name" {
  description = "Identificador de la bodega (wh-001...)."
  type        = string
}

variable "hub_vpc_id" {
  type = string
}

variable "hub_cidr" {
  type = string
}

variable "hub_route_table_ids" {
  description = "Mapa con claves estáticas (public/private) => id de tabla de rutas de la central."
  type        = map(string)
}

variable "spoke_vpc_id" {
  type = string
}

variable "spoke_cidr" {
  type = string
}

variable "spoke_route_table_ids" {
  description = "Mapa con claves estáticas (public/private) => id de tabla de rutas de la bodega."
  type        = map(string)
}
