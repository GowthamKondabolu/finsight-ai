resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.name_prefix}/api"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "web" {
  name              = "/ecs/${local.name_prefix}/web"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "main" {
  name = local.name_prefix

  setting {
    name  = "containerInsights"
    value = "enhanced"
  }
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name_prefix}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = "${aws_ecr_repository.api.repository_url}:${var.api_image_tag}"
      essential = true
      user      = "10001:10001"

      readonlyRootFilesystem = true

      linuxParameters = {
        capabilities = {
          drop = ["ALL"]
        }
        initProcessEnabled = true
      }

      portMappings = [{
        name          = "api"
        containerPort = 8000
        hostPort      = 8000
        protocol      = "tcp"
        appProtocol   = "http"
      }]

      mountPoints = [{
        sourceVolume  = "tmp"
        containerPath = "/tmp"
        readOnly      = false
      }]

      environment = local.common_environment
      secrets     = local.api_secrets

      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=2).read()\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.api.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "api"
        }
      }
    },
  ])
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${local.name_prefix}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  volume {
    name = "tmp"
  }

  volume {
    name = "next-cache"
  }

  container_definitions = jsonencode([
    {
      name      = "web"
      image     = "${aws_ecr_repository.web.repository_url}:${var.web_image_tag}"
      essential = true
      user      = "10001:10001"

      readonlyRootFilesystem = true

      linuxParameters = {
        capabilities = {
          drop = ["ALL"]
        }
        initProcessEnabled = true
      }

      portMappings = [{
        name          = "web"
        containerPort = 3000
        hostPort      = 3000
        protocol      = "tcp"
        appProtocol   = "http"
      }]

      mountPoints = [
        {
          sourceVolume  = "tmp"
          containerPath = "/tmp"
          readOnly      = false
        },
        {
          sourceVolume  = "next-cache"
          containerPath = "/app/.next/cache"
          readOnly      = false
        },
      ]

      environment = [
        { name = "FINSIGHT_API_BASE_URL", value = "http://api.${aws_service_discovery_private_dns_namespace.main.name}:8000" },
        { name = "NEXT_TELEMETRY_DISABLED", value = "1" },
      ]

      secrets = [
        { name = "FINSIGHT_API_AUTH_TOKEN", valueFrom = aws_secretsmanager_secret.api_auth_token.arn },
      ]

      healthCheck = {
        command     = ["CMD-SHELL", "node -e \"fetch('http://127.0.0.1:3000').then(r=>{if(!r.ok)process.exit(1)}).catch(()=>process.exit(1))\""]
        interval    = 30
        timeout     = 5
        retries     = 3
        startPeriod = 30
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.web.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "web"
        }
      }
    },
  ])
}

resource "aws_ecs_service" "api" {
  count = var.deploy_services ? 1 : 0

  name            = "api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  platform_version                   = "LATEST"
  enable_execute_command             = false
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = [for subnet in aws_subnet.application : subnet.id]
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false
  }

  service_registries {
    registry_arn = aws_service_discovery_service.api.arn
  }
}

resource "aws_ecs_service" "web" {
  count = var.deploy_services ? 1 : 0

  name            = "web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.web.arn
  desired_count   = var.web_desired_count
  launch_type     = "FARGATE"

  platform_version                   = "LATEST"
  enable_execute_command             = false
  health_check_grace_period_seconds  = 60
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = [for subnet in aws_subnet.application : subnet.id]
    security_groups  = [aws_security_group.web.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 3000
  }

  depends_on = [
    aws_lb_listener.http_cloudfront,
    aws_lb_listener.http_redirect,
    aws_lb_listener.https,
  ]
}

resource "aws_appautoscaling_target" "api" {
  count = var.deploy_services ? 1 : 0

  max_capacity       = 4
  min_capacity       = var.api_desired_count
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.api[0].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "api_cpu" {
  count = var.deploy_services ? 1 : 0

  name               = "${local.name_prefix}-api-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.api[0].resource_id
  scalable_dimension = aws_appautoscaling_target.api[0].scalable_dimension
  service_namespace  = aws_appautoscaling_target.api[0].service_namespace

  target_tracking_scaling_policy_configuration {
    target_value       = 60
    scale_in_cooldown  = 300
    scale_out_cooldown = 60

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}

resource "aws_appautoscaling_target" "web" {
  count = var.deploy_services ? 1 : 0

  max_capacity       = 4
  min_capacity       = var.web_desired_count
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.web[0].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

resource "aws_appautoscaling_policy" "web_cpu" {
  count = var.deploy_services ? 1 : 0

  name               = "${local.name_prefix}-web-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.web[0].resource_id
  scalable_dimension = aws_appautoscaling_target.web[0].scalable_dimension
  service_namespace  = aws_appautoscaling_target.web[0].service_namespace

  target_tracking_scaling_policy_configuration {
    target_value       = 60
    scale_in_cooldown  = 300
    scale_out_cooldown = 60

    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
  }
}
