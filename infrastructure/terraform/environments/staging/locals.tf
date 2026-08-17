locals {
  name_prefix        = "${var.project_name}-${var.environment}"
  availability_zones = slice(data.aws_availability_zones.available.names, 0, 2)

  common_environment = [
    { name = "FINSIGHT_ENVIRONMENT", value = var.environment },
    { name = "FINSIGHT_LOG_LEVEL", value = "INFO" },
    { name = "FINSIGHT_LOG_JSON", value = "true" },
    { name = "FINSIGHT_OBSERVABILITY_ENABLED", value = "true" },
    { name = "FINSIGHT_OTEL_SERVICE_NAME", value = "finsight-api" },
    { name = "FINSIGHT_OTEL_TRACE_SAMPLE_RATIO", value = "0.25" },
    { name = "FINSIGHT_OTEL_TRACES_ENDPOINT", value = var.otel_traces_endpoint },
    { name = "FINSIGHT_OTEL_METRICS_ENDPOINT", value = var.otel_metrics_endpoint },
    { name = "FINSIGHT_SEC_USER_AGENT", value = var.sec_user_agent },
  ]

  api_secrets = concat(
    [
      { name = "FINSIGHT_DATABASE_URL", valueFrom = aws_secretsmanager_secret.database_url.arn },
      { name = "FINSIGHT_API_AUTH_TOKEN", valueFrom = aws_secretsmanager_secret.api_auth_token.arn },
      { name = "FINSIGHT_EXPERIMENT_ASSIGNMENT_SECRET", valueFrom = aws_secretsmanager_secret.experiment_assignment.arn },
    ],
    var.enable_openai_secret ? [
      { name = "FINSIGHT_OPENAI_API_KEY", valueFrom = aws_secretsmanager_secret.openai_api_key.arn },
    ] : [],
    var.enable_otel_headers_secret ? [
      { name = "FINSIGHT_OTEL_EXPORT_HEADERS", valueFrom = aws_secretsmanager_secret.otel_export_headers.arn },
    ] : [],
  )
}

check "service_inputs" {
  assert {
    condition = !var.deploy_services || (
      (var.ephemeral_recording_mode || (
        var.certificate_arn != "" &&
        var.public_hostname != ""
      )) &&
      var.api_image_tag != "bootstrap" &&
      var.web_image_tag != "bootstrap"
    )
    error_message = "Service deployment requires either recording mode or certificate/hostname inputs plus immutable API/web image SHA tags."
  }
}

check "recording_inputs" {
  assert {
    condition = !var.ephemeral_recording_mode || (
      var.certificate_arn == "" &&
      var.public_hostname == "" &&
      !var.database_multi_az &&
      !var.database_deletion_protection
    )
    error_message = "Recording mode requires empty certificate/hostname inputs, single-AZ RDS, and deletion protection disabled."
  }
}
