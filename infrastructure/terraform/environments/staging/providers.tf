provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Application = var.project_name
      Environment = var.environment
      ManagedBy   = "Terraform"
      Repository  = "GowthamKondabolu/finsight-ai"
    }
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

data "aws_region" "current" {}
