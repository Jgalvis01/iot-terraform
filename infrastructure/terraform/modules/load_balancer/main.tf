locals {
  targets = {
    central = { listener_port = 80, target_port = 8000 }
    wh001   = { listener_port = 8001, target_port = 8001 }
    wh002   = { listener_port = 8002, target_port = 8002 }
    wh003   = { listener_port = 8003, target_port = 8003 }
  }
}

resource "aws_lb" "this" {
  name               = "${var.name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [var.alb_sg_id]
  subnets            = var.public_subnet_ids
  idle_timeout       = 60
}

resource "aws_lb_target_group" "this" {
  for_each = local.targets

  name                 = "${var.name}-${each.key}"
  port                 = each.value.target_port
  protocol             = "HTTP"
  target_type          = "instance"
  vpc_id               = var.vpc_id
  deregistration_delay = 15

  health_check {
    path                = "/health"
    protocol            = "HTTP"
    matcher             = "200"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 15
    timeout             = 5
  }
}

resource "aws_lb_listener" "this" {
  for_each = local.targets

  load_balancer_arn = aws_lb.this.arn
  port              = each.value.listener_port
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this[each.key].arn
  }
}
