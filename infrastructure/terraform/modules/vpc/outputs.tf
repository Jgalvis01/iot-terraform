output "vpc_id" {
  value = aws_vpc.this.id
}

output "cidr" {
  value = aws_vpc.this.cidr_block
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "public_subnet_cidrs" {
  value = aws_subnet.public[*].cidr_block
}

output "private_subnet_cidrs" {
  value = aws_subnet.private[*].cidr_block
}

# Mapa con claves estáticas para poder usar for_each en el módulo de peering.
output "route_table_ids" {
  value = {
    public  = aws_route_table.public.id
    private = aws_route_table.private.id
  }
}

output "nat_public_ip" {
  value = var.enable_nat ? aws_eip.nat[0].public_ip : null
}
