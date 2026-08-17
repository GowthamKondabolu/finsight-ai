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
  description = "GitHub Environment and AWS deployment environment."
  type        = string
  default     = "staging"

  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "environment must be staging or production."
  }
}

variable "aws_region" {
  description = "AWS Region that stores Terraform state and application resources."
  type        = string
  default     = "us-east-1"
}

variable "state_bucket_name" {
  description = "Globally unique S3 bucket name for Terraform state."
  type        = string
}

variable "github_owner" {
  description = "Case-sensitive GitHub account name."
  type        = string
  default     = "GowthamKondabolu"
}

variable "github_owner_id" {
  description = "Immutable numeric GitHub account ID used in OIDC subject claims."
  type        = string
  default     = "316645184"
}

variable "github_repository" {
  description = "GitHub repository name."
  type        = string
  default     = "finsight-ai"
}

variable "github_repository_id" {
  description = "Immutable numeric GitHub repository ID used in OIDC subject claims."
  type        = string
  default     = "1335295291"
}
