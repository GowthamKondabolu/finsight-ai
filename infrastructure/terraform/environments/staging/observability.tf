resource "aws_sns_topic" "alarms" {
  name = "${local.name_prefix}-alarms"
}

resource "aws_sns_topic_subscription" "email" {
  count = var.alarm_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.alarm_email
}

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name          = "${local.name_prefix}-alb-5xx"
  alarm_description   = "Public analyst application is returning elevated 5xx responses"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  threshold           = 5
  period              = 300
  statistic           = "Sum"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]

  dimensions = {
    LoadBalancer = aws_lb.web.arn_suffix
  }
}

resource "aws_cloudwatch_metric_alarm" "database_cpu" {
  alarm_name          = "${local.name_prefix}-database-cpu"
  alarm_description   = "RDS CPU has remained above 80 percent"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = 80
  period              = 300
  statistic           = "Average"
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  treat_missing_data  = "missing"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }
}

resource "aws_cloudwatch_metric_alarm" "database_storage" {
  alarm_name          = "${local.name_prefix}-database-storage"
  alarm_description   = "RDS free storage is below 5 GiB"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  threshold           = 5368709120
  period              = 300
  statistic           = "Average"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  treat_missing_data  = "missing"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]

  dimensions = {
    DBInstanceIdentifier = aws_db_instance.main.identifier
  }
}

resource "aws_cloudwatch_metric_alarm" "api_cpu" {
  count = var.deploy_services ? 1 : 0

  alarm_name          = "${local.name_prefix}-api-cpu"
  alarm_description   = "API service CPU has remained above 85 percent"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = 85
  period              = 300
  statistic           = "Average"
  namespace           = "AWS/ECS"
  metric_name         = "CPUUtilization"
  treat_missing_data  = "missing"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.api[0].name
  }
}

resource "aws_cloudwatch_metric_alarm" "web_cpu" {
  count = var.deploy_services ? 1 : 0

  alarm_name          = "${local.name_prefix}-web-cpu"
  alarm_description   = "Web service CPU has remained above 85 percent"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  threshold           = 85
  period              = 300
  statistic           = "Average"
  namespace           = "AWS/ECS"
  metric_name         = "CPUUtilization"
  treat_missing_data  = "missing"
  alarm_actions       = [aws_sns_topic.alarms.arn]
  ok_actions          = [aws_sns_topic.alarms.arn]

  dimensions = {
    ClusterName = aws_ecs_cluster.main.name
    ServiceName = aws_ecs_service.web[0].name
  }
}
