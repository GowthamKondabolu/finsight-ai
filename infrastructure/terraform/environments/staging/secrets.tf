resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${local.name_prefix}-database-url"
  description             = "SQLAlchemy URL hydrated during the controlled staging deployment"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "api_auth_token" {
  name                    = "${local.name_prefix}-api-auth-token"
  description             = "Bearer token used only by the server-side analyst proxy"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "experiment_assignment" {
  name                    = "${local.name_prefix}-experiment-assignment"
  description             = "HMAC secret for anonymous controlled-experiment assignment"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "openai_api_key" {
  name                    = "${local.name_prefix}-openai-api-key"
  description             = "Optional OpenAI API key; Terraform never manages the secret value"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "otel_export_headers" {
  name                    = "${local.name_prefix}-otel-export-headers"
  description             = "Optional OTLP exporter authorization headers"
  recovery_window_in_days = 7
}
