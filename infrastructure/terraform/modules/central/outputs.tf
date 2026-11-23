output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "broker_dns_name" {
  value = aws_lb.broker.dns_name
}

output "rabbitmq_url" {
  value     = local.rabbitmq_url
  sensitive = true
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "service_names" {
  value = concat([aws_ecs_service.broker.name], [for s in aws_ecs_service.app : s.name])
}

output "alb_security_group_id" {
  value = aws_security_group.alb.id
}

output "tasks_security_group_id" {
  value = aws_security_group.tasks.id
}

output "rds_security_group_id" {
  value = aws_security_group.rds.id
}

output "efs_id" {
  value = aws_efs_file_system.broker.id
}

output "target_group_arns" {
  value = merge(
    { central_api = aws_lb_target_group.api.arn, rabbit_ui = aws_lb_target_group.rabbit_ui.arn, amqp = aws_lb_target_group.amqp.arn },
    { for k, tg in aws_lb_target_group.warehouse : k => tg.arn },
  )
}

output "log_groups" {
  value = [for g in aws_cloudwatch_log_group.services : g.name]
}
