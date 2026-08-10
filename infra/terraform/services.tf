locals {
  backend_image = "${aws_ecr_repository.backend.repository_url}:${var.image_tag}"

  data_mounts = local.uses_efs ? [
    { sourceVolume = "data", containerPath = "/data", readOnly = false }
  ] : []

  common_environment = concat([
    { name = "GTOCHESS_ENGINE_PATH", value = "/opt/stockfish/stockfish" },
    { name = "GTOCHESS_DATA_DIR", value = "/data" },
    { name = "GTOCHESS_REDIS_URL", value = "redis://${aws_elasticache_replication_group.this.primary_endpoint_address}:6379/0" },
    { name = "GTOCHESS_ENGINE_HASH_MB", value = tostring(var.engine_hash_mb) },
    { name = "GTOCHESS_COGNITO_USER_POOL_ID", value = aws_cognito_user_pool.this.id },
    { name = "GTOCHESS_COGNITO_CLIENT_ID", value = aws_cognito_user_pool_client.this.id },
    { name = "GTOCHESS_COGNITO_REGION", value = var.region },
    { name = "GTOCHESS_COGNITO_DOMAIN", value = aws_cognito_user_pool_domain.this.domain },
    { name = "GTOCHESS_AUTH_REQUIRED", value = "true" },
    ],
    # Set, the stores go to S3 and DATA_DIR above is only the games export.
    var.enable_object_storage
    ? [{ name = "GTOCHESS_S3_BUCKET", value = aws_s3_bucket.data[0].bucket }]
    : [],
  )

  common_secrets = concat(
    [{ name = "ANTHROPIC_API_KEY", valueFrom = aws_secretsmanager_secret.anthropic.arn }],
    var.enable_database
    ? [{ name = "GTOCHESS_DATABASE_URL", valueFrom = aws_secretsmanager_secret.database[0].arn }]
    : [],
  )

  fargate_strategy = var.use_fargate_spot ? "FARGATE_SPOT" : "FARGATE"
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  dynamic "volume" {
    for_each = local.uses_efs ? [1] : []
    content {
      name = "data"

      efs_volume_configuration {
        file_system_id     = aws_efs_file_system.data[0].id
        transit_encryption = "ENABLED"

        authorization_config {
          access_point_id = aws_efs_access_point.data[0].id
          iam             = "ENABLED"
        }
      }
    }
  }

  container_definitions = jsonencode([{
    name         = "api"
    image        = local.backend_image
    essential    = true
    environment  = local.common_environment
    secrets      = local.common_secrets
    portMappings = [{ containerPort = 8000, protocol = "tcp" }]
    mountPoints  = local.data_mounts
    command = [
      "uvicorn", "gtochess.api.main:app",
      "--host", "0.0.0.0", "--port", "8000",
    ]
    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request;urllib.request.urlopen('http://localhost:8000/health')\""]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "api"
      }
    }
  }])
}

# Two worker families. The default queue runs prefork, which cannot fork the
# engine pool; the measure queue runs solo so the sweep can own the whole host.
resource "aws_ecs_task_definition" "worker" {
  family = "${local.name}-worker"
  # Bridge rather than awsvpc: awsvpc gives the task its own ENI, and an EC2 task
  # ENI takes no public address, so with the NAT off it would have no way out.
  # On the host network it inherits the instance's route to the internet.
  network_mode             = "bridge"
  requires_compatibilities = ["EC2"]
  cpu                      = var.worker_cpu
  memory                   = var.worker_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  dynamic "volume" {
    for_each = local.uses_efs ? [1] : []
    content {
      name = "data"

      efs_volume_configuration {
        file_system_id     = aws_efs_file_system.data[0].id
        transit_encryption = "ENABLED"

        authorization_config {
          access_point_id = aws_efs_access_point.data[0].id
          iam             = "ENABLED"
        }
      }
    }
  }

  container_definitions = jsonencode([{
    name        = "worker"
    image       = local.backend_image
    essential   = true
    environment = local.common_environment
    secrets     = local.common_secrets
    mountPoints = local.data_mounts
    command = [
      "celery", "-A", "gtochess.jobs.app", "worker",
      "-Q", "default", "--loglevel=info", "--concurrency=2",
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "worker"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "measure" {
  family                   = "${local.name}-measure"
  network_mode             = "bridge"
  requires_compatibilities = ["EC2"]
  cpu                      = var.measure_cpu
  memory                   = var.measure_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  dynamic "volume" {
    for_each = local.uses_efs ? [1] : []
    content {
      name = "data"

      efs_volume_configuration {
        file_system_id     = aws_efs_file_system.data[0].id
        transit_encryption = "ENABLED"

        authorization_config {
          access_point_id = aws_efs_access_point.data[0].id
          iam             = "ENABLED"
        }
      }
    }
  }

  container_definitions = jsonencode([{
    name        = "measure"
    image       = local.backend_image
    essential   = true
    environment = local.common_environment
    secrets     = local.common_secrets
    mountPoints = local.data_mounts
    command = [
      "celery", "-A", "gtochess.jobs.app", "worker",
      "-Q", "measure", "--pool=solo", "--loglevel=info",
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.this.name
        "awslogs-region"        = var.region
        "awslogs-stream-prefix" = "measure"
      }
    }
  }])
}

resource "aws_ecs_service" "api" {
  name            = "${local.name}-api"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count

  capacity_provider_strategy {
    capacity_provider = local.fargate_strategy
    weight            = 1
  }

  network_configuration {
    subnets          = local.egress_subnets
    security_groups  = [aws_security_group.tasks.id]
    assign_public_ip = local.public_task_ip == "ENABLED"
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.http]
}

# Zero by default. A desired count of one made the capacity provider launch a
# c7a.2xlarge and hold it, about $117 a month to wait for work that had not been
# asked for. Celery jobs queue in Redis until a worker exists.
#
# Deliberately not ignore_changes: the floor is the point, so an apply puts it
# back. Drain a queue by raising worker_desired_count, not by poking the service,
# or the next apply will undo it.
resource "aws_ecs_service" "worker" {
  name            = "${local.name}-worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = var.worker_desired_count

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.worker.name
    weight            = 1
  }
}

resource "aws_ecs_service" "measure" {
  name            = "${local.name}-measure"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.measure.arn
  desired_count   = 0

  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.worker.name
    weight            = 1
  }

  lifecycle {
    ignore_changes = [desired_count]
  }
}

resource "aws_appautoscaling_target" "api" {
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.this.name}/${aws_ecs_service.api.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = var.api_desired_count
  max_capacity       = 10
}

resource "aws_appautoscaling_policy" "api_cpu" {
  name               = "${local.name}-api-cpu"
  policy_type        = "TargetTrackingScaling"
  service_namespace  = aws_appautoscaling_target.api.service_namespace
  resource_id        = aws_appautoscaling_target.api.resource_id
  scalable_dimension = aws_appautoscaling_target.api.scalable_dimension

  target_tracking_scaling_policy_configuration {
    target_value = 60

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

