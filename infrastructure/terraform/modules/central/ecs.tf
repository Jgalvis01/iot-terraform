# Bodega central = clúster ECS (Fargate) en las subnets privadas de la VPC central.

locals {
  rabbitmq_url = "amqp://${var.rabbitmq_user}:${var.rabbitmq_password}@${aws_lb.broker.dns_name}:5672/%2F"

  # La central llega a cada bodega por su IP privada fija (peering). Una bodega
  # nunca recibe las URLs de las otras.
  warehouses_env = join(",", [for id, w in var.warehouses : "${id}=http://${w.private_ip}:${w.api_port}"])

  central_env = {
    DATABASE_URL        = var.database_url
    RABBITMQ_URL        = local.rabbitmq_url
    WAREHOUSES          = local.warehouses_env
    S3_BUCKET           = var.s3_bucket
    S3_ENDPOINT_URL     = ""
    AWS_REGION          = var.region
    AWS_DEFAULT_REGION  = var.region
    STALE_AFTER_SECONDS = "60"
    LOW_STOCK_THRESHOLD = tostring(var.low_stock_threshold)
    FAIL_INJECTION      = "none"
  }

  # nombre => definición. central-api es el único detrás del ALB.
  app_services = {
    "central-api" = {
      image   = var.images.central_api
      command = ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
      cpu     = 512
      memory  = 1024
      count   = var.central_api_count
      port    = 8000
    }
    "command-relay" = {
      image   = var.images.central_api
      command = ["python", "-m", "app.command_relay"]
      cpu     = 256
      memory  = 512
      count   = 1
      port    = null
    }
    "transfer-orchestrator" = {
      image   = var.images.workers
      command = ["python", "-m", "workers.transfer_orchestrator"]
      cpu     = 256
      memory  = 512
      count   = 1
      port    = null
    }
    "projection-worker" = {
      image   = var.images.workers
      command = ["python", "-m", "workers.projection"]
      cpu     = 256
      memory  = 512
      count   = var.projection_workers
      port    = null
    }
    "audit-worker" = {
      image   = var.images.workers
      command = ["python", "-m", "workers.audit"]
      cpu     = 256
      memory  = 512
      count   = var.audit_workers
      port    = null
    }
    "report-worker" = {
      image   = var.images.workers
      command = ["python", "-m", "workers.report"]
      cpu     = 256
      memory  = 512
      count   = 1
      port    = null
    }
  }
}

resource "aws_ecs_cluster" "this" {
  name = "${var.name}-central"

  setting {
    name  = "containerInsights"
    value = var.container_insights ? "enabled" : "disabled"
  }

  tags = { Name = "${var.name}-central", Role = "central" }
}

resource "aws_cloudwatch_log_group" "services" {
  for_each          = toset(concat(["rabbitmq"], keys(local.app_services)))
  name              = "${var.log_group_prefix}/central-${each.value}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_task_definition" "app" {
  for_each = local.app_services

  family                   = "${var.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = var.lab_role_arn
  task_role_arn            = var.lab_role_arn

  container_definitions = jsonencode([{
    name         = each.key
    image        = each.value.image
    essential    = true
    command      = each.value.command
    environment  = [for k, v in merge(local.central_env, { SERVICE_NAME = each.key }) : { name = k, value = v }]
    portMappings = each.value.port == null ? [] : [{ containerPort = each.value.port, protocol = "tcp" }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.services[each.key].name
        awslogs-region        = var.region
        awslogs-stream-prefix = "ecs"
      }
    }
  }])
}

resource "aws_ecs_service" "app" {
  for_each = local.app_services

  name                   = each.key
  cluster                = aws_ecs_cluster.this.id
  task_definition        = aws_ecs_task_definition.app[each.key].arn
  desired_count          = each.value.count
  launch_type            = "FARGATE"
  enable_execute_command = true

  health_check_grace_period_seconds = each.value.port == null ? null : 60

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = each.value.port == null ? [] : [each.value.port]
    content {
      target_group_arn = aws_lb_target_group.api.arn
      container_name   = each.key
      container_port   = load_balancer.value
    }
  }

  depends_on = [aws_lb_listener.api]
}

# Autoescalado por CPU de central-api y projection-worker (picos de 1000-2000 req/s).
# Desactivado por defecto: necesita el rol vinculado de Application Auto Scaling.
resource "aws_appautoscaling_target" "this" {
  for_each = var.enable_autoscaling ? toset(["central-api", "projection-worker"]) : toset([])

  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.app[each.value].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = local.app_services[each.value].count
  max_capacity       = var.autoscaling_max
}

resource "aws_appautoscaling_policy" "cpu" {
  for_each = aws_appautoscaling_target.this

  name               = "${each.key}-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = each.value.service_namespace
  resource_id        = each.value.resource_id
  scalable_dimension = each.value.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value       = 60
    scale_in_cooldown  = 120
    scale_out_cooldown = 30
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}
