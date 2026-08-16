# AWS deployment architecture

FinSight's staging reference architecture deploys the hardened API and analyst application images from this repository without exposing FastAPI directly to the internet.

```mermaid
flowchart TD
    U["Analyst browser"] --> ALB["TLS application load balancer"]
    ALB --> WEB["Private Next.js Fargate service"]
    WEB --> API["Private FastAPI Fargate service"]
    API --> DB["Isolated RDS PostgreSQL"]
    API --> EXT["SEC, OpenAI, and OTLP over NAT"]
```

## Trust boundaries

- The public ALB accepts HTTP only to redirect to HTTPS. Its HTTPS listener requires an ACM certificate and forwards solely to the web target group.
- Web and API tasks have no public IP addresses. The server-side Next.js proxy reaches FastAPI through private Cloud Map DNS and supplies the bearer token from Secrets Manager.
- FastAPI accepts port 8000 only from the web security group. PostgreSQL accepts port 5432 only from the API security group.
- Database subnets have no internet route. Application subnets use NAT for SEC EDGAR, model-provider, ECR, CloudWatch, Secrets Manager, and optional OTLP access.
- ECS runtime roles have no AWS API permissions. The separate execution role can pull images, write logs, and read only named application secrets.
- RDS storage is encrypted, not public, backed up for seven days, and uses an RDS-managed rotating master secret.

## Deployment safety

The workflow is manual and bound to the protected GitHub `staging` Environment. It uses the repository's immutable OIDC subject, an exact confirmation phrase, remote state locking, immutable image tags, a migration-before-service sequence, ECS deployment circuit breakers, and service stability checks.

Terraform validation runs for infrastructure pull requests without AWS credentials. The pipeline also scans Terraform for critical misconfigurations. Applying infrastructure is deliberately separate from merging code.

The protected Environment supplies the SEC identity at deployment time through `FINSIGHT_SEC_USER_AGENT`; no personal contact address is committed to Terraform defaults.

## Observability and rollback

ECS enhanced Container Insights, structured application logs, optional OTLP traces and metrics, RDS logs, CloudWatch alarms, and SNS alert routing support diagnosis. ECS circuit breakers automatically roll back failed service deployments.

For an operator rollback, rerun the deployment workflow from a known-good commit or update the services to known-good immutable task-definition revisions. Do not roll back database migrations until the migration's downgrade behavior and data impact have been reviewed.

## Current limitations

- The repository provides a staging implementation, not proof of a live public AWS deployment.
- DNS records and ACM certificate validation are owned outside the stack so no domain is modified implicitly.
- The staging application currently shares the RDS master credential used by Alembic. Production requires separate migration and least-privilege application roles.
- Cross-region recovery, WAF, CloudFront, identity-aware user authentication, private AWS service endpoints, and budget alarms are documented promotion decisions rather than unreviewed defaults.
- A generated answer remains decision support, not an autonomous financial action; the existing citation, numerical, abstention, and human-review controls still apply in AWS.

See [Terraform operations](../infrastructure/terraform/README.md), [Container deployment](container_deployment.md), [Production observability](observability.md), and the [Operations runbook](operations_runbook.md).
