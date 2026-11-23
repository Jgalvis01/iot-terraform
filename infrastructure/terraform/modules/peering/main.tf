# Peering hub (central) <-> spoke (una bodega).
# El peering de VPC NO es transitivo: aunque las 3 bodegas estén conectadas a la
# central, una bodega no puede llegar a otra a través de ella. Es la base del
# aislamiento entre bodegas (además de los SG y las NACL del módulo warehouse).

resource "aws_vpc_peering_connection" "this" {
  vpc_id      = var.hub_vpc_id
  peer_vpc_id = var.spoke_vpc_id
  auto_accept = true

  tags = { Name = "${var.name}-peering", Side = "central<->${var.spoke_name}" }
}

# Central -> bodega (todas las tablas de rutas de la central: el ALB vive en las
# públicas y las tareas ECS en las privadas).
resource "aws_route" "hub_to_spoke" {
  for_each = var.hub_route_table_ids

  route_table_id            = each.value
  destination_cidr_block    = var.spoke_cidr
  vpc_peering_connection_id = aws_vpc_peering_connection.this.id
}

# Bodega -> central (solo la CIDR de la central; nunca la de otra bodega).
resource "aws_route" "spoke_to_hub" {
  for_each = var.spoke_route_table_ids

  route_table_id            = each.value
  destination_cidr_block    = var.hub_cidr
  vpc_peering_connection_id = aws_vpc_peering_connection.this.id
}
