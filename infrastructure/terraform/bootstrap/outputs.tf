output "deployment_role_arn" {
  description = "Configure this as the AWS_DEPLOY_ROLE_ARN GitHub Environment variable."
  value       = aws_iam_role.github_deploy.arn
}

output "state_bucket_name" {
  description = "Configure this as the TF_STATE_BUCKET GitHub Environment variable."
  value       = aws_s3_bucket.terraform_state.id
}

output "github_oidc_subject" {
  description = "Exact immutable GitHub OIDC subject trusted by AWS."
  value       = local.github_subject
}
