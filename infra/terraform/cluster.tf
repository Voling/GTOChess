data "aws_ssm_parameter" "ecs_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"
}

resource "aws_ecs_cluster" "this" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${local.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_launch_template" "worker" {
  name_prefix   = "${local.name}-worker-"
  image_id      = data.aws_ssm_parameter.ecs_ami.value
  instance_type = var.worker_instance_types[0]

  iam_instance_profile {
    arn = aws_iam_instance_profile.instance.arn
  }

  network_interfaces {
    device_index                = 0
    associate_public_ip_address = !var.enable_nat_gateway
    security_groups             = [aws_security_group.tasks.id]
    delete_on_termination       = true
  }

  user_data = base64encode(<<-EOT
    #!/bin/bash
    echo "ECS_CLUSTER=${aws_ecs_cluster.this.name}" >> /etc/ecs/ecs.config
    echo "ECS_ENABLE_SPOT_INSTANCE_DRAINING=true" >> /etc/ecs/ecs.config
    echo "ECS_CONTAINER_STOP_TIMEOUT=2m" >> /etc/ecs/ecs.config
  EOT
  )

  monitoring {
    enabled = true
  }

  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${local.name}-worker" }
  }
}

resource "aws_autoscaling_group" "worker" {
  name                = "${local.name}-worker"
  vpc_zone_identifier = local.egress_subnets
  min_size            = var.worker_min_size
  max_size            = var.worker_max_size
  desired_capacity    = var.worker_min_size

  # Every position is an independent task and the store only appends, so an
  # interruption costs one position. That makes this the right workload for spot.
  mixed_instances_policy {
    instances_distribution {
      on_demand_base_capacity                  = 0
      on_demand_percentage_above_base_capacity = 0
      spot_allocation_strategy                 = "capacity-optimized"
    }

    launch_template {
      launch_template_specification {
        launch_template_id = aws_launch_template.worker.id
        version            = "$Latest"
      }

      dynamic "override" {
        for_each = var.worker_instance_types
        content {
          instance_type = override.value
        }
      }
    }
  }

  protect_from_scale_in = true

  tag {
    key                 = "AmazonECSManaged"
    value               = ""
    propagate_at_launch = true
  }

  lifecycle {
    ignore_changes = [desired_capacity]
  }
}

resource "aws_ecs_capacity_provider" "worker" {
  name = "${local.name}-worker"

  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.worker.arn
    managed_termination_protection = "ENABLED"

    managed_scaling {
      status                    = "ENABLED"
      target_capacity           = 100
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 2
    }
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name       = aws_ecs_cluster.this.name
  capacity_providers = [aws_ecs_capacity_provider.worker.name, "FARGATE", "FARGATE_SPOT"]

  # The API and the web tier are small and latency bound, so they sit on Fargate
  # and are not competing with the engine for cores.
  default_capacity_provider_strategy {
    capacity_provider = local.fargate_strategy
    weight            = 1
  }
}
