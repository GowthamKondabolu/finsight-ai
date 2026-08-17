variable "project_name" {
  description = "Short application name used in AWS resource names."
  type        = string
  default     = "finsight"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.project_name))
    error_message = "project_name must be 3-21 lowercase letters, numbers, or hyphens."
  }
}

variable "environment" {
  description = "Deployment environment. This stack is intentionally staging-only."
  type        = string
  default     = "staging"

  validation {
    condition     = var.environment == "staging"
    error_message = "This root module is staging-only; create a separate production root module."
  }
}

variable "aws_region" {
  description = "AWS Region for the application stack."
  type        = string
  default     = "us-east-1"
}

variable "vpc_cidr" {
  description = "RFC1918 IPv4 CIDR allocated to FinSight staging."
  type        = string
  default     = "10.42.0.0/16"
}

variable "nat_gateway_count" {
  description = "One controls staging cost; two removes cross-AZ egress dependency."
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 2], var.nat_gateway_count)
    error_message = "nat_gateway_count must be 1 or 2."
  }
}

variable "certificate_arn" {
  description = "Validated ACM certificate ARN for the public HTTPS listener."
  type        = string
  default     = ""
}

variable "public_hostname" {
  description = "Public DNS name covered by certificate_arn; DNS is managed outside this stack."
  type        = string
  default     = ""

  validation {
    condition     = var.public_hostname == "" || can(regex("^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$", var.public_hostname))
    error_message = "public_hostname must be a DNS hostname without a scheme or path."
  }
}

variable "ephemeral_recording_mode" {
  description = "Use an AWS-provided HTTPS endpoint and teardown-safe settings for a same-day evidence capture."
  type        = bool
  default     = false
}

variable "deploy_services" {
  description = "Keep false during infrastructure bootstrap and database migration."
  type        = bool
  default     = false
}

variable "api_image_tag" {
  description = "Immutable API image tag, normally the 40-character Git commit SHA."
  type        = string
  default     = "bootstrap"

  validation {
    condition     = var.api_image_tag == "bootstrap" || can(regex("^[0-9a-f]{40}$", var.api_image_tag))
    error_message = "api_image_tag must be bootstrap or a lowercase 40-character Git SHA."
  }
}

variable "web_image_tag" {
  description = "Immutable web image tag, normally the 40-character Git commit SHA."
  type        = string
  default     = "bootstrap"

  validation {
    condition     = var.web_image_tag == "bootstrap" || can(regex("^[0-9a-f]{40}$", var.web_image_tag))
    error_message = "web_image_tag must be bootstrap or a lowercase 40-character Git SHA."
  }
}

variable "api_desired_count" {
  description = "Steady-state API task count."
  type        = number
  default     = 1

  validation {
    condition     = var.api_desired_count >= 1 && var.api_desired_count <= 10
    error_message = "api_desired_count must be between 1 and 10."
  }
}

variable "web_desired_count" {
  description = "Steady-state web task count."
  type        = number
  default     = 1

  validation {
    condition     = var.web_desired_count >= 1 && var.web_desired_count <= 10
    error_message = "web_desired_count must be between 1 and 10."
  }
}

variable "database_instance_class" {
  description = "RDS instance class for staging."
  type        = string
  default     = "db.t4g.micro"
}

variable "postgres_engine_version" {
  description = "RDS PostgreSQL engine version compatible with the pgvector schema."
  type        = string
  default     = "17.6"
}

variable "database_multi_az" {
  description = "Enable a synchronous standby. Recommended for production, optional in staging."
  type        = bool
  default     = false
}

variable "database_deletion_protection" {
  description = "Protect the database from Terraform deletion. Enable after the first successful staging deployment."
  type        = bool
  default     = false
}

variable "database_backup_retention_days" {
  description = "Automated RDS backup retention."
  type        = number
  default     = 7

  validation {
    condition     = var.database_backup_retention_days >= 1 && var.database_backup_retention_days <= 35
    error_message = "database_backup_retention_days must be between 1 and 35."
  }
}

variable "log_retention_days" {
  description = "CloudWatch log retention for application containers."
  type        = number
  default     = 30
}

variable "sec_user_agent" {
  description = "Policy-compliant SEC identity with monitored contact email."
  type        = string

  validation {
    condition     = strcontains(var.sec_user_agent, " ") && strcontains(var.sec_user_agent, "@") && !strcontains(lower(var.sec_user_agent), "example.com")
    error_message = "sec_user_agent must include an application name and a real contact email."
  }
}

variable "enable_openai_secret" {
  description = "Inject the OpenAI key secret after a value has been stored out of band."
  type        = bool
  default     = false
}

variable "otel_traces_endpoint" {
  description = "Optional OTLP/HTTP traces endpoint; credentials belong in Secrets Manager."
  type        = string
  default     = ""
}

variable "otel_metrics_endpoint" {
  description = "Optional OTLP/HTTP metrics endpoint; credentials belong in Secrets Manager."
  type        = string
  default     = ""
}

variable "enable_otel_headers_secret" {
  description = "Inject OTLP export headers after a value has been stored out of band."
  type        = bool
  default     = false
}

variable "alarm_email" {
  description = "Optional email endpoint for the SNS alarm topic. Subscription requires confirmation."
  type        = string
  default     = ""
}
