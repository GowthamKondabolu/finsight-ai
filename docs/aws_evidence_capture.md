# AWS evidence-capture runbook

This runbook produces reviewable proof of a temporary, production-style FinSight staging deployment while keeping the deployment short-lived and preventing a permanent hosting claim.

## Scope and claims

The evidence may say that FinSight was temporarily deployed and validated on AWS from a specific Git commit. It must not claim permanent production availability, production traffic, a production SLA, or real-world financial performance.

The public analyst interface uses public SEC information or clearly labelled synthetic fixtures. Generated answers remain decision support and require source verification.

## Before opening the deployment window

1. Rehearse ingestion, retrieval, investigation, evaluation, and feedback locally.
2. Prepare one controlled investigation question and the expected source citations.
3. Confirm the GitHub `staging` Environment requires approval and uses OIDC rather than access keys.
4. Select the AWS Free Plan or confirm available credits.
5. Create billing notifications below the maximum acceptable gross usage. Billing data can arrive late, so notifications are not the teardown mechanism.
6. Run the deployment workflow in `plan` mode with the `recording` profile and review every created resource.
7. Confirm `database_deletion_protection=false`, one NAT gateway, one task per service, single-AZ RDS, and no optional model or telemetry secret unless required.
8. Record the deployment start time and commit SHA.

## Deploy and validate

Run `Deploy FinSight staging` with:

- mode: `deploy`
- profile: `recording`
- confirmation: `DEPLOY STAGING`

Wait for the workflow to publish the CloudFront application URL. Generate only enough controlled traffic to populate health and observability evidence.

Validate:

- the CloudFront URL serves the analyst application over HTTPS;
- the ALB target group reports the web task healthy;
- ECS reports one healthy API task and one healthy web task;
- RDS is private, encrypted, single-AZ, and available;
- the API readiness endpoint succeeds through the server-side web proxy;
- one investigation returns attributable evidence and citations;
- numerical and abstention guardrails behave as documented;
- CloudWatch receives structured logs and service metrics;
- the ECR images are immutable and scanned;
- the GitHub deployment workflow completed using OIDC.

## Capture only the strongest evidence

Capture six to eight focused images:

1. Analyst investigation with evidence cards and citations.
2. ECS services with desired and running counts equal.
3. Healthy ALB target group and the CloudFront distribution.
4. Private RDS configuration without credentials or endpoint details.
5. CloudWatch request, error, latency, CPU, or memory evidence from the controlled run.
6. ECR scan summary without account identifiers.
7. Successful GitHub deployment summary.
8. Successful teardown summary.

Do not capture or publish:

- AWS account IDs, billing address, or payment information;
- secret values, API keys, bearer tokens, cookies, or environment values;
- database passwords, private endpoints, or full ARNs containing the account ID;
- personal email addresses;
- unredacted browser developer tools or terminal history.

Use image crops or opaque redaction before committing evidence. Do not rely on blur for secrets.

## Destroy immediately after capture

Run the same workflow with:

- mode: `destroy`
- profile: `recording`
- confirmation: `DESTROY STAGING`

Do not merely stop RDS or scale ECS to zero. The destroy workflow must complete and verify the primary hourly cost drivers are absent.

The bootstrap S3 state bucket and GitHub OIDC role intentionally remain because a separate Terraform state manages them. Their retained state should be reviewed separately when the AWS account is retired.

## Final verification

After teardown:

1. Confirm the workflow reports NAT, Elastic IPs, ECS, ALB, RDS, ECR, and CloudFront absent.
2. Check the AWS Resource Explorer or service consoles for resources tagged `Application=finsight` and `Environment=staging`.
3. Review Billing and Cost Management again after usage data has populated.
4. Record the teardown time and gross cost before credits.
5. Save evidence under `docs/images/aws-evidence/` only after redaction.
6. Replace this runbook's future evidence link with a dated `docs/aws_deployment_evidence.md` report.

A portfolio summary should use wording such as:

> FinSight was temporarily provisioned as a production-style AWS staging environment through Terraform for end-to-end validation and evidence capture, then destroyed the same day.
