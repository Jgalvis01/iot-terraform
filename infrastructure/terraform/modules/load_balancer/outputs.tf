output "alb_dns_name" {
  value = aws_lb.this.dns_name
}

output "alb_arn" {
  value = aws_lb.this.arn
}

output "target_group_arns" {
  description = "Mapa central|wh001|wh002|wh003 => ARN del target group."
  value       = { for k, tg in aws_lb_target_group.this : k => tg.arn }
}
