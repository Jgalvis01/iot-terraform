# ALB público de la central.
#   :80     -> central-api (tareas ECS)
#   :8001.. -> API de cada bodega (IP privada de la EC2, alcanzada por peering).
#              Los clientes llegan a las bodegas SIEMPRE a través de la central.
#   :15672  -> consola RabbitMQ (solo my_ip_cidr)

resource "aws_lb" "this" {
  name               = "${var.name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = var.public_subnet_ids
  idle_timeout       = 60
  tags               = { Name = "${var.name}-alb" }
}

resource "aws_lb_target_group" "api" {
  name                 = "${var.name}-api"
  port                 = 8000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 15

  health_check {
    path                = "/health"
    matcher             = "200"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
    timeout             = 5
  }
}

resource "aws_lb_listener" "api" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}

resource "aws_lb_target_group" "warehouse" {
  for_each = var.warehouses

  name                 = "${var.name}-${lower(each.key)}"
  port                 = each.value.api_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 15

  health_check {
    path                = "/health"
    matcher             = "200"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    interval            = 15
    timeout             = 5
  }
}

# IP fuera de la VPC del ALB (VPC de la bodega, por peering) => availability_zone = "all".
resource "aws_lb_target_group_attachment" "warehouse" {
  for_each = var.warehouses

  target_group_arn  = aws_lb_target_group.warehouse[each.key].arn
  target_id         = each.value.private_ip
  port              = each.value.api_port
  availability_zone = "all"
}

resource "aws_lb_listener" "warehouse" {
  for_each = var.warehouses

  load_balancer_arn = aws_lb.this.arn
  port              = each.value.listener_port
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.warehouse[each.key].arn
  }
}

resource "aws_lb_target_group" "rabbit_ui" {
  name                 = "${var.name}-mq-ui"
  port                 = 15672
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 10

  health_check {
    path    = "/"
    matcher = "200"
  }
}

resource "aws_lb_listener" "rabbit_ui" {
  load_balancer_arn = aws_lb.this.arn
  port              = 15672
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.rabbit_ui.arn
  }
}
