output "id" {
  value = aws_vpc_peering_connection.this.id
}

output "status" {
  value = aws_vpc_peering_connection.this.accept_status
}
