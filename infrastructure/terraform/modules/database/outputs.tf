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

output "db_name" {
  value = aws_db_instance.this.db_name
}
