# Experiment tracking and controlled A/B testing

FinSight connects the paired offline evaluation contract to an online-ready controlled-experiment system. The implementation is deliberately conservative: a hypothesis and analysis plan are registered before traffic begins, assignments are stable, exposure and outcomes are separated, and inferential results remain hidden until every arm reaches its declared primary-metric sample size.

## Registered plan

An `ExperimentPlan` fixes:

- a stable experiment key, hypothesis, schema version, and Git commit;
- the randomization unit (`user` or persistent `session`);
- exactly one control and one treatment configuration;
- integer allocation basis points totaling 10,000;
- one binary primary outcome and its practical effect threshold;
- binary or continuous latency, cost, numerical-error, and safety guardrails;
- expected baseline rate, confidence level, power, and sample size per arm;
- an assignment-salt version and optional schedule; and
- an optional SHA-256 link to the supporting offline evaluation report.

FinSight independently estimates the two-proportion sample size from the registered baseline, minimum detectable effect, alpha, and power. A plan below that estimate is rejected. Exact re-registration is idempotent; changing a plan requires a new experiment key.

The included example compares:

- **Control:** vector retrieval and one grounded generation step.
- **Treatment:** hybrid retrieval and reranking, exact SEC facts, deterministic validation, durable LangGraph review, and human approval.

The example is an engineering fixture and does not report FinSight performance.

## Assignment and privacy

The assignment API accepts a stable user or session identifier, but PostgreSQL never stores that raw value. FinSight computes an experiment-scoped HMAC-SHA-256 using a deployment secret and salt version. A second SHA-256 mapping places the digest into immutable allocation buckets. The database enforces one assignment per experiment and anonymous unit hash, so retries return the persisted arm.

Set `FINSIGHT_EXPERIMENT_ASSIGNMENT_SECRET` to at least 32 random characters. FinSight has no source-controlled default and fails closed when the secret is absent. Rotating assignment behavior requires an explicitly versioned new plan; silently changing the secret during a running experiment invalidates randomization.

## Exposure and outcomes

Assignment is not exposure. Clients record a separate exposure only after the assigned experience is rendered or executed. Outcomes are accepted only after exposure and only for preregistered metrics. Binary outcomes must be exactly zero or one.

Event keys make retries idempotent. PostgreSQL also enforces:

- one exposure per assignment;
- one outcome value per assignment and metric;
- valid exposure-versus-outcome shapes;
- append-only audit timestamps; and
- cascading cleanup when an experiment is removed in test environments.

Raw identifiers and secrets are absent from responses, reports, and telemetry tables. Metadata must not be used to reintroduce personally identifying values.

## No-peeking analysis

The analysis endpoint always reports anonymous exposure counts. It withholds the primary comparison, confidence interval, p-value, guardrail comparisons, and launch recommendation until both arms contain the preregistered number of exposed assignments with primary outcomes. Marking an underpowered run as stopped does not unlock inference.

Once ready, FinSight reports:

- per-arm observed support, mean, and confidence interval;
- treatment-minus-control absolute effect and confidence interval;
- a two-sided large-sample p-value;
- whether the preregistered practical effect was reached;
- whether any guardrail exceeded its maximum allowed degradation; and
- one of `ship_treatment`, `keep_control`, `inconclusive`, or `halt_guardrail`.

A guardrail breach overrides primary-metric improvement. The report states that approximate intervals do not correct multiple guardrail comparisons and that randomization alone cannot repair eligibility, exposure, missing-outcome, or instrumentation bias.

## Lifecycle and commands

Apply migrations, register the reviewed plan, and start it:

```bash
docker compose up -d --wait postgres
alembic upgrade head

finsight register-experiment \
  --spec experiments/fixtures/answer_workflow_v1.json \
  --start
```

Lifecycle transitions are one-way: `draft` to `running`, `running` to `stopped` or `completed`, and `stopped` to `completed`.

```bash
finsight set-experiment-status \
  --experiment-key answer-workflow-v1 \
  --status stopped

finsight analyze-experiment \
  --experiment-key answer-workflow-v1
```

Assignment, exposure, and outcome collection are available under `/v1/experiments/{experiment_key}`. See the interactive FastAPI documentation for the exact schemas.

## Causal interpretation

This system makes an online experiment auditable; it does not automatically make it causal. A credible conclusion still requires stable eligibility, no interference between units, correct exposure capture, bounded missing outcomes, no secret rerandomization, and an analysis consistent with the preregistration. Production rollout additionally requires authentication, authorization, consent and privacy review, retention policy, telemetry monitoring, and independent statistical review.
