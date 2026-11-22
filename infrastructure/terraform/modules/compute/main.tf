data "aws_ami" "al2023" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-2023.*-x86_64"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

locals {
  images = {
    warehouse_api = "${var.ecr_repository_urls["warehouse-api"]}:${var.image_tag}"
    central_api   = "${var.ecr_repository_urls["central-api"]}:${var.image_tag}"
    workers       = "${var.ecr_repository_urls["workers"]}:${var.image_tag}"
  }

  warehouses = {
    "WH-001" = { id = "WH-001", slug = "wh-001", db = "wh_001", redis_port = 6379, api_port = 8001 }
    "WH-002" = { id = "WH-002", slug = "wh-002", db = "wh_002", redis_port = 6380, api_port = 8002 }
    "WH-003" = { id = "WH-003", slug = "wh-003", db = "wh_003", redis_port = 6381, api_port = 8003 }
  }

  central_workers = {
    "transfer-orchestrator" = "transfer_orchestrator"
    "projection-worker"     = "projection"
    "audit-worker"          = "audit"
    "report-worker"         = "report"
  }

  db_base_url  = "postgresql://${var.db_username}:${var.db_password}@${var.db_address}:${var.db_port}"
  rabbitmq_url = "amqp://${var.rabbitmq_user}:${var.rabbitmq_password}@${aws_instance.platform.private_ip}:5672/%2F"

  warehouses_env = join(",", [
    for k, w in local.warehouses : "${k}=http://${var.alb_dns_name}:${w.api_port}"
  ])

  service_names = concat(
    ["rabbitmq", "central-api"],
    keys(local.central_workers),
    flatten([for w in values(local.warehouses) : [
      "redis-${w.slug}", "${w.slug}-outbox", "${w.slug}-cache-worker", "${w.slug}-api"
    ]]),
  )

  common_template_vars = {
    region           = var.region
    log_group_prefix = var.log_group_prefix
    images           = local.images
    db_base_url      = local.db_base_url
  }
}

resource "aws_cloudwatch_log_group" "services" {
  for_each          = toset(local.service_names)
  name              = "${var.log_group_prefix}/${each.value}"
  retention_in_days = var.log_retention_days
}

locals {
  platform_compose = templatefile("${var.templates_dir}/platform-compose.yml.tftpl", merge(local.common_template_vars, {
    instance_name     = "platform"
    warehouses        = values(local.warehouses)
    central_workers   = local.central_workers
    rabbitmq_user     = var.rabbitmq_user
    rabbitmq_password = var.rabbitmq_password
    s3_bucket         = var.s3_bucket
    warehouses_env    = local.warehouses_env
  }))
}

resource "aws_instance" "platform" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.platform_instance_type
  subnet_id              = var.public_subnet_ids[0]
  vpc_security_group_ids = [var.apps_sg_id]
  iam_instance_profile   = var.instance_profile_name
  key_name               = var.key_name

  user_data_base64 = base64gzip(templatefile("${var.templates_dir}/user_data.sh.tftpl", {
    region          = var.region
    ecr_registry    = var.ecr_registry
    compose_version = var.compose_version
    compose         = local.platform_compose
    init_databases  = true
    databases       = ["wh_001", "wh_002", "wh_003", "central_read"]
    db_address      = var.db_address
    db_port         = var.db_port
    db_username     = var.db_username
    db_password     = var.db_password
  }))
  user_data_replace_on_change = true

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  tags       = { Name = "${var.name}-platform", Role = "platform" }
  depends_on = [aws_cloudwatch_log_group.services]
}

locals {
  warehouse_template_vars = merge(local.common_template_vars, {
    platform_ip  = aws_instance.platform.private_ip
    rabbitmq_url = local.rabbitmq_url
  })

  wh_a_compose = templatefile("${var.templates_dir}/warehouse-compose.yml.tftpl", merge(local.warehouse_template_vars, {
    instance_name = "wh-a"
    warehouses    = [local.warehouses["WH-001"], local.warehouses["WH-003"]]
  }))

  wh_b_compose = [for i in range(var.wh002_instances) :
    templatefile("${var.templates_dir}/warehouse-compose.yml.tftpl", merge(local.warehouse_template_vars, {
      instance_name = "wh-b-${i + 1}"
      warehouses    = [local.warehouses["WH-002"]]
    }))
  ]

  warehouse_user_data_vars = {
    region          = var.region
    ecr_registry    = var.ecr_registry
    compose_version = var.compose_version
    init_databases  = false
    databases       = []
    db_address      = ""
    db_port         = 0
    db_username     = ""
    db_password     = ""
  }
}

resource "aws_instance" "wh_a" {
  ami                    = data.aws_ami.al2023.id
  instance_type          = var.warehouse_instance_type
  subnet_id              = var.public_subnet_ids[1 % length(var.public_subnet_ids)]
  vpc_security_group_ids = [var.apps_sg_id]
  iam_instance_profile   = var.instance_profile_name
  key_name               = var.key_name

  user_data_base64 = base64gzip(templatefile("${var.templates_dir}/user_data.sh.tftpl",
    merge(local.warehouse_user_data_vars, { compose = local.wh_a_compose })
  ))
  user_data_replace_on_change = true

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_size = 16
    volume_type = "gp3"
  }

  tags       = { Name = "${var.name}-wh-a", Role = "wh-a", Warehouses = "WH-001,WH-003" }
  depends_on = [aws_cloudwatch_log_group.services]
}

resource "aws_instance" "wh_b" {
  count = var.wh002_instances

  ami                    = data.aws_ami.al2023.id
  instance_type          = var.warehouse_instance_type
  subnet_id              = var.public_subnet_ids[count.index % length(var.public_subnet_ids)]
  vpc_security_group_ids = [var.apps_sg_id]
  iam_instance_profile   = var.instance_profile_name
  key_name               = var.key_name

  user_data_base64 = base64gzip(templatefile("${var.templates_dir}/user_data.sh.tftpl",
    merge(local.warehouse_user_data_vars, { compose = local.wh_b_compose[count.index] })
  ))
  user_data_replace_on_change = true

  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  root_block_device {
    volume_size = 16
    volume_type = "gp3"
  }

  tags       = { Name = "${var.name}-wh-b-${count.index + 1}", Role = "wh-b", Warehouses = "WH-002" }
  depends_on = [aws_cloudwatch_log_group.services]
}

resource "aws_lb_target_group_attachment" "central" {
  target_group_arn = var.target_group_arns["central"]
  target_id        = aws_instance.platform.id
  port             = 8000
}

resource "aws_lb_target_group_attachment" "wh001" {
  target_group_arn = var.target_group_arns["wh001"]
  target_id        = aws_instance.wh_a.id
  port             = 8001
}

resource "aws_lb_target_group_attachment" "wh003" {
  target_group_arn = var.target_group_arns["wh003"]
  target_id        = aws_instance.wh_a.id
  port             = 8003
}

resource "aws_lb_target_group_attachment" "wh002" {
  count            = var.wh002_instances
  target_group_arn = var.target_group_arns["wh002"]
  target_id        = aws_instance.wh_b[count.index].id
  port             = 8002
}
