# Broker RabbitMQ como servicio ECS de la central.
#
# Persistencia: colas durables + mensajes persistentes (delivery_mode=2) + datos en EFS.
# Si la tarea del broker se cae, ECS la vuelve a crear y monta el MISMO directorio
# (nodo fijo rabbit@localhost), así que los mensajes que estaban en cola siguen allí.
# Mientras el broker está caído:
#   - las bodegas acumulan eventos en su outbox (PostgreSQL) y los envían al volver;
#   - la central guarda los comandos en command_outbox y el command-relay los envía al volver.
#
# Acceso: NLB interno (DNS estable) en 5672, alcanzable desde las bodegas por peering.

resource "aws_efs_file_system" "broker" {
  creation_token   = "${var.name}-broker"
  encrypted        = true
  performance_mode = "generalPurpose"
  throughput_mode  = "elastic"
  tags             = { Name = "${var.name}-broker-efs" }
}

resource "aws_efs_mount_target" "broker" {
  count           = length(var.private_subnet_ids)
  file_system_id  = aws_efs_file_system.broker.id
  subnet_id       = var.private_subnet_ids[count.index]
  security_groups = [aws_security_group.efs.id]
}

# uid/gid 999 = usuario rabbitmq de la imagen oficial.
resource "aws_efs_access_point" "broker" {
  file_system_id = aws_efs_file_system.broker.id

  posix_user {
    uid = 999
    gid = 999
  }

  root_directory {
    path = "/rabbitmq"
    creation_info {
      owner_uid   = 999
      owner_gid   = 999
      permissions = "0750"
    }
  }

  tags = { Name = "${var.name}-broker-ap" }
}

resource "aws_lb" "broker" {
  name                             = "${var.name}-mq"
  internal                         = true
  load_balancer_type               = "network"
  subnets                          = var.private_subnet_ids
  enable_cross_zone_load_balancing = true
  tags                             = { Name = "${var.name}-mq-nlb" }
}

resource "aws_lb_target_group" "amqp" {
  name                 = "${var.name}-amqp"
  port                 = 5672
  protocol             = "TCP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  preserve_client_ip   = false
  deregistration_delay = 10

  health_check {
    protocol            = "TCP"
    interval            = 10
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
}

resource "aws_lb_listener" "amqp" {
  load_balancer_arn = aws_lb.broker.arn
  port              = 5672
  protocol          = "TCP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.amqp.arn
  }
}

locals {
  broker_watermark_mb = floor(var.broker_memory * 0.6)
  broker_conf = join("\\n", [
    "vm_memory_high_watermark.absolute = ${local.broker_watermark_mb}MB",
    "disk_free_limit.absolute = 1GB",
    "consumer_timeout = 1800000",
  ])
}

resource "aws_ecs_task_definition" "broker" {
  family                   = "${var.name}-rabbitmq"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.broker_cpu
  memory                   = var.broker_memory
  execution_role_arn       = var.lab_role_arn
  task_role_arn            = var.lab_role_arn

  volume {
    name = "rabbitmq-data"
    efs_volume_configuration {
      file_system_id     = aws_efs_file_system.broker.id
      transit_encryption = "ENABLED"
      authorization_config {
        access_point_id = aws_efs_access_point.broker.id
        iam             = "DISABLED"
      }
    }
  }

  container_definitions = jsonencode([{
    name       = "rabbitmq"
    image      = var.broker_image
    essential  = true
    entryPoint = ["bash", "-c"]
    command = [
      "printf '${local.broker_conf}\\n' > /etc/rabbitmq/conf.d/90-aws.conf && exec docker-entrypoint.sh rabbitmq-server"
    ]
    environment = [
      { name = "RABBITMQ_DEFAULT_USER", value = var.rabbitmq_user },
      { name = "RABBITMQ_DEFAULT_PASS", value = var.rabbitmq_password },
      # Nombre de nodo fijo: el directorio de datos en EFS es el mismo en cada reinicio.
      { name = "RABBITMQ_NODENAME", value = "rabbit@localhost" },
    ]
    portMappings = [
      { containerPort = 5672, protocol = "tcp" },
      { containerPort = 15672, protocol = "tcp" },
    ]
    mountPoints = [{ sourceVolume = "rabbitmq-data", containerPath = "/var/lib/rabbitmq", readOnly = false }]
    healthCheck = {
      command     = ["CMD-SHELL", "rabbitmq-diagnostics -q ping"]
      interval    = 15
      timeout     = 10
      retries     = 5
      startPeriod = 90
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.services["rabbitmq"].name
        awslogs-region        = var.region
        awslogs-stream-prefix = "ecs"
      }
    }
  }])
}

resource "aws_ecs_service" "broker" {
  name            = "rabbitmq"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.broker.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # Nunca dos brokers a la vez sobre el mismo directorio de EFS.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  health_check_grace_period_seconds  = 120
  enable_execute_command             = true

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.amqp.arn
    container_name   = "rabbitmq"
    container_port   = 5672
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.rabbit_ui.arn
    container_name   = "rabbitmq"
    container_port   = 15672
  }

  depends_on = [aws_lb_listener.amqp, aws_lb_listener.rabbit_ui, aws_efs_mount_target.broker]
}
