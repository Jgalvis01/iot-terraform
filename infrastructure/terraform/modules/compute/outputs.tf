output "platform" {
  value = {
    id         = aws_instance.platform.id
    public_ip  = aws_instance.platform.public_ip
    private_ip = aws_instance.platform.private_ip
  }
}

output "wh_a" {
  value = {
    id         = aws_instance.wh_a.id
    public_ip  = aws_instance.wh_a.public_ip
    private_ip = aws_instance.wh_a.private_ip
  }
}

output "wh_b" {
  value = [for i in aws_instance.wh_b : {
    id         = i.id
    public_ip  = i.public_ip
    private_ip = i.private_ip
  }]
}

output "instance_ids" {
  value = concat([aws_instance.platform.id, aws_instance.wh_a.id], aws_instance.wh_b[*].id)
}

output "log_groups" {
  value = [for g in aws_cloudwatch_log_group.services : g.name]
}
