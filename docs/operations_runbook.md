# Operations runbook

This runbook defines first-response behavior for the FinSight API and analyst application. Thresholds are initial engineering targets and must be calibrated from production traffic before they are represented as service-level guarantees.

## Initial objectives

| Objective | Initial target | Measurement |
|---|---:|---|
| API availability | 99.5% over 30 days | Non-5xx responses excluding `/health` |
| HTTP p95 latency | under 2 seconds for non-generation routes | `finsight.http.server.duration` |
| Investigation p95 latency | observed and alerted, no claim until a production baseline exists | investigation and GenAI spans |
| Grounding contract failures | zero silent releases | API 502 count plus review state |
| Readiness | database check under configured timeout | `/ready` status and duration |

## Alert triage

### Readiness failures

1. Confirm `/health` is `200` and `/ready` is `503`.
2. Check database reachability, credentials delivery, connection limits, and migration status.
3. Run `alembic current` and compare it with `alembic heads` from a controlled task.
4. Do not bypass migrations or weaken database validation to restore traffic.

### Increased 5xx rate

1. Group spans by route and status; use the request ID to correlate structured logs.
2. Separate provider, database, grounding-contract, and application failures.
3. Verify that secrets, prompts, response content, and evidence text are absent from telemetry before sharing diagnostics.
4. Roll back the immutable application revision if the failure aligns with a deployment.

### Provider latency or errors

1. Compare embedding and generation span latency by model.
2. Check provider status, timeouts, token counts, and retry amplification.
3. Preserve the human-review gate and fail closed; never release an unvalidated fallback answer.
4. Reduce traffic or pause live generation rather than removing evidence or numerical checks.

### Token or cost anomaly

1. Compare `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens` with the prior deployment.
2. Confirm evidence limits and output-token configuration have not changed unexpectedly.
3. Calculate cost using the versioned price table in the telemetry backend.
4. Treat cost changes as operational signals, not model-quality conclusions.

## Security response

- Rotate `FINSIGHT_API_AUTH_TOKEN`, database credentials, provider keys, experiment secrets, and OTLP credentials independently.
- Never paste secret-bearing `.env` content into issues, logs, traces, or pull requests.
- Keep health/readiness public only at the internal load-balancer boundary; versioned routes require authentication.
- Investigate repeated `401`, request-size rejection, or allowlist failures without logging the supplied credential or body.
- Preserve database and workflow audit records according to the deployment retention policy.

## Recovery checks

After mitigation, verify:

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/ready
docker compose ps
```

Then run one synthetic, non-financial fixture through the analyst interface. A successful fixture confirms rendering and review controls only; it does not prove live retrieval or model quality.
