output "instance_id" {
  value = aws_instance.this.id
}

output "private_ip" {
  value = aws_instance.this.private_ip
}

output "security_group_id" {
  value = aws_security_group.this.id
}

output "network_acl_id" {
  value = aws_network_acl.this.id
}

output "log_groups" {
  value = [for g in aws_cloudwatch_log_group.services : g.name]
}
