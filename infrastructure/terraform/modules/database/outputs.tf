output "address" {
  description = "Hostname del RDS."
  value       = aws_db_instance.this.address
}

output "port" {
  value = aws_db_instance.this.port
}

output "endpoint" {
  value = aws_db_instance.this.endpoint
}

output "username" {
  value = aws_db_instance.this.username
}

output "databases" {
  description = "Bases lógicas que crea el bootstrap de la instancia platform."
  value       = ["wh_001", "wh_002", "wh_003", "central_read"]
}
