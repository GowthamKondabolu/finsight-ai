# Container deployment

FinSight ships separate production images for FastAPI and the Next.js analyst application. Docker Compose remains the local integration and deployment-smoke environment; the AWS staging reference uses the same images through Terraform-managed ECS Fargate task definitions.

## Image design

Both Dockerfiles use multi-stage builds so compilers, dependency caches, tests, source-control metadata, and local secrets are absent from the final images.

- The API image installs the packaged Python application into a dedicated virtual environment.
- The web image uses Next.js `output: "standalone"` and copies only the traced production server and static assets.
- Both runtime images execute as UID/GID `10001`, not root.
- Compose drops Linux capabilities, prevents privilege escalation, mounts the root filesystem read-only, and provides a bounded `/tmp` tmpfs.
- Images contain no `.env`, provider key, database credential, assignment secret, or API authentication token.
- PostgreSQL migrations run in a one-shot service before the API is allowed to start.

These choices follow Docker's [multi-stage build guidance](https://docs.docker.com/build/building/best-practices/) and the official Next.js [standalone output](https://nextjs.org/docs/app/api-reference/config/next-config-js/output) deployment model.

## Build and run

Create `.env` using the root README, then run:

```bash
docker compose --profile application build
docker compose --profile application up -d
docker compose ps
```

Expected dependency order:

```text
PostgreSQL healthy -> Alembic migration succeeds -> API ready -> web starts
```

Inspect safe service output:

```bash
docker compose logs --no-log-prefix api web
```

Open http://127.0.0.1:3000. The browser reaches FastAPI only through the allowlisted same-origin Next.js proxy. The server-side proxy supplies `FINSIGHT_API_AUTH_TOKEN`; it is never exposed through a `NEXT_PUBLIC_*` variable.

Stop containers while preserving the named PostgreSQL volume:

```bash
docker compose --profile application down
```

Use `docker compose down --volumes` only when deliberately deleting the local database.

## Build verification

```bash
docker build --tag finsight-api:local .
docker build --tag finsight-web:local apps/web
docker image inspect finsight-api:local --format '{{.Config.User}}'
docker image inspect finsight-web:local --format '{{.Config.User}}'
```

CI builds both final images and fails on fixed critical operating-system or application-library vulnerabilities reported by Trivy. Dependency auditing remains separate so Python package findings are visible even when no image is built locally.

## Cloud boundary

The AWS reference adds immutable ECR tags, private networking, managed PostgreSQL, Secrets Manager delivery, TLS, backups, autoscaling, alert routing, and circuit-breaker rollback through Terraform. It remains deployment code rather than a claim that a public environment is live. See [AWS deployment architecture](aws_deployment.md) and [Terraform operations](../infrastructure/terraform/README.md).
