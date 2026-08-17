output "application_url" {
  description = "Public HTTPS URL from CloudFront recording mode or the externally managed hostname."
  value = var.ephemeral_recording_mode ? (
    "https://${aws_cloudfront_distribution.recording[0].domain_name}"
  ) : (var.public_hostname == "" ? null : "https://${var.public_hostname}")
}

output "recording_distribution_id" {
  description = "CloudFront distribution used only by ephemeral recording mode."
  value       = var.ephemeral_recording_mode ? aws_cloudfront_distribution.recording[0].id : null
}

output "load_balancer_dns_name" {
  description = "Create an alias or CNAME from the chosen public hostname."
  value       = aws_lb.web.dns_name
}

output "api_repository_url" {
  description = "ECR repository used for immutable API images."
  value       = aws_ecr_repository.api.repository_url
}

output "web_repository_url" {
  description = "ECR repository used for immutable web images."
  value       = aws_ecr_repository.web.repository_url
}

output "ecs_cluster_name" {
  description = "Cluster used by services and one-shot migration tasks."
  value       = aws_ecs_cluster.main.name
}

output "api_task_definition_arn" {
  description = "Task definition reused for the one-shot Alembic migration."
  value       = aws_ecs_task_definition.api.arn
}

output "application_subnet_ids" {
  description = "Private subnets used by Fargate tasks."
  value       = [for subnet in aws_subnet.application : subnet.id]
}

output "api_security_group_id" {
  description = "Security group used by API and migration tasks."
  value       = aws_security_group.api.id
}

output "database_address" {
  description = "Private RDS endpoint used to hydrate the database URL secret."
  value       = aws_db_instance.main.address
}

output "database_name" {
  description = "PostgreSQL database name."
  value       = aws_db_instance.main.db_name
}

output "database_master_secret_arn" {
  description = "RDS-managed rotating master credential secret."
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}

output "database_url_secret_arn" {
  description = "Application database URL secret hydrated by deployment automation."
  value       = aws_secretsmanager_secret.database_url.arn
}

output "api_auth_token_secret_arn" {
  description = "Server-to-server bearer token secret."
  value       = aws_secretsmanager_secret.api_auth_token.arn
}

output "experiment_assignment_secret_arn" {
  description = "Controlled-experiment HMAC secret."
  value       = aws_secretsmanager_secret.experiment_assignment.arn
}

output "openai_api_key_secret_arn" {
  description = "Optional OpenAI API key secret populated outside Terraform."
  value       = aws_secretsmanager_secret.openai_api_key.arn
}

output "otel_export_headers_secret_arn" {
  description = "Optional OTLP authorization headers secret populated outside Terraform."
  value       = aws_secretsmanager_secret.otel_export_headers.arn
}

output "alarm_topic_arn" {
  description = "SNS topic receiving infrastructure alarms."
  value       = aws_sns_topic.alarms.arn
}
