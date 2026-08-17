# FinSight AWS infrastructure

This directory contains an explicit, review-first AWS reference deployment. Terraform can validate and plan the stack without creating resources. Only the manually dispatched `Deploy FinSight staging` workflow can apply the staging stack, and the GitHub `staging` Environment should require approval.

## Layout

- `bootstrap/` creates the encrypted, versioned S3 state bucket and a GitHub Actions OIDC deployment role.
- `environments/staging/` creates the FinSight staging runtime.
- No production root module is included. Production should use a separate AWS account, state key, approval environment, and deliberately stronger availability settings.

The bootstrap stack intentionally uses local state because it creates the remote state bucket itself. Protect the resulting bootstrap state as a credential-bearing administrative artifact. Do not commit it.

## One-time bootstrap

Run this only from an authenticated administrative workstation after reviewing the policy in `bootstrap/main.tf`:

```bash
cd infrastructure/terraform/bootstrap
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
```

The OIDC trust is restricted to this repository's immutable GitHub owner and repository IDs and the `staging` Environment. Recycled repository names therefore cannot satisfy the trust policy.

An AWS account can have only one GitHub Actions OIDC provider for `token.actions.githubusercontent.com`. If the account already has one, import it into the bootstrap state instead of attempting to create a duplicate:

```bash
terraform import aws_iam_openid_connect_provider.github \
  arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com
```

Create a protected GitHub Environment named `staging`, then configure these Environment variables:

| Variable | Source |
|---|---|
| `AWS_ACCOUNT_ID` | AWS account that owns staging |
| `AWS_DEPLOY_ROLE_ARN` | Bootstrap output `deployment_role_arn` |
| `AWS_REGION` | Bootstrap region, for example `us-east-1` |
| `TF_STATE_BUCKET` | Bootstrap output `state_bucket_name` |
| `TF_STATE_KEY` | `finsight/staging/terraform.tfstate` |
| `FINSIGHT_CERTIFICATE_ARN` | Validated ACM certificate in the same Region; leave unset for the recording profile |
| `FINSIGHT_PUBLIC_HOSTNAME` | Public hostname covered by the ACM certificate; leave unset for the recording profile |
| `FINSIGHT_SEC_USER_AGENT` | Identifiable SEC application name and monitored contact email |
| `FINSIGHT_ALARM_EMAIL` | Optional monitored alert address |
| `FINSIGHT_ENABLE_OPENAI_SECRET` | `false` until the AWS secret has a value |
| `FINSIGHT_ENABLE_OTEL_HEADERS_SECRET` | `false` until the AWS secret has a value |
| `FINSIGHT_OTEL_TRACES_ENDPOINT` | Optional credential-free OTLP/HTTP URL |
| `FINSIGHT_OTEL_METRICS_ENDPOINT` | Optional credential-free OTLP/HTTP URL |

Require reviewers for the `staging` Environment. Limit deployment branches to `main`. Do not configure AWS access keys as GitHub secrets; the workflow requests a short-lived OIDC token.

## Deployment sequence

Run `Deploy FinSight staging` in `plan` mode first. Review the plan and estimated AWS cost. When approved, run `deploy` and enter `DEPLOY STAGING`.

Select the `recording` profile for a same-day evidence capture without a custom domain. It keeps the private ECS and RDS topology, adds an AWS-provided CloudFront HTTPS endpoint, disables service autoscaling, shortens backup and log retention, and makes generated images and secrets immediately removable. The `standard` profile retains the externally managed hostname and ACM certificate path.

The workflow then performs this order:

1. Authenticate to AWS using GitHub OIDC.
2. Apply networking, RDS, ECR, task definitions, Secrets Manager containers, and alarms with ECS services disabled.
3. Build and push API and web images under the immutable Git commit SHA.
4. Generate the API and experiment secrets in AWS if they do not exist.
5. Synchronize the application database URL with the RDS-managed rotating master credential.
6. Run `alembic upgrade head` as a one-shot Fargate task in private subnets.
7. Enable the API and web ECS services and wait for a stable deployment.

Terraform never receives an application secret value, so those values do not enter Terraform configuration or state. ECS obtains them through its execution role. When a secret changes, run a new deployment because running ECS tasks retain the value injected at startup.

After evidence capture, rerun the workflow with the same profile, choose `destroy`, and enter `DESTROY STAGING`. The workflow destroys the staging state and then verifies that NAT gateways, Elastic IP addresses, ECS, ALB, RDS, ECR, and the temporary CloudFront distribution are absent. The bootstrap state bucket and GitHub OIDC role remain intentionally because they are managed by the separate bootstrap state.

The staging workflow currently uses the RDS-managed master credential for migrations and application access. Before promoting the design to production, provision a separately rotated, least-privilege application role and retain the master credential only for migrations.

## Cost and availability controls

The defaults are intentionally modest but are not free:

- one NAT gateway;
- one task per service with CPU target tracking;
- one `db.t4g.micro` RDS instance with 20 GiB gp3 storage;
- 30-day application log retention;
- seven-day database backups;
- no RDS Performance Insights;
- no WAF, CloudFront, or paid interface VPC endpoints.

NAT gateway, Fargate, RDS, ALB, CloudWatch, Secrets Manager, Cloud Map, public IPv4, data transfer, ECR, and model-provider usage can all incur charges. Use AWS Budgets outside this stack before applying it.

For a temporary recording, rehearse locally before applying, keep one task per service, create billing notifications before deployment, capture evidence in one session, and destroy the same day. AWS Budgets can report late and are not a real-time hard cap, so the explicit teardown workflow is the primary control. Follow [the evidence-capture runbook](../../docs/aws_evidence_capture.md).

Production should use at least two NAT gateways, Multi-AZ RDS, deletion protection, final snapshots, two tasks per service, Route 53 health-aware DNS, WAF, stronger backup retention, and a tested cross-account recovery plan.

## State and recovery

The S3 backend uses native `.tflock` state locking. DynamoDB locking is not used because it is deprecated. Bucket versioning and the deny-insecure-transport policy protect state history, but access to the deployment role still needs normal AWS monitoring and review.

Never run `terraform destroy` casually. The bootstrap state bucket has `prevent_destroy`; the staging database can additionally enable `database_deletion_protection` after the first successful deployment.
