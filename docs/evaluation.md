# Evaluation and paired experiments

FinSight evaluates retrieval and answer quality as separate system layers. The evaluation runner consumes versioned datasets and recorded outputs; it does not call SEC services, a language model, or PostgreSQL while calculating metrics. This makes reports repeatable and allows the same cases to be compared across system configurations.

## Artifact contract

An experiment contains three immutable JSON inputs:

1. A `BenchmarkDataset` with questions, expected answer-or-abstain behavior, graded relevance judgments, and policy tags.
2. A control `SystemRun` with one observation for every case.
3. A treatment `SystemRun` covering the identical case set and dataset fingerprint.

Every dataset receives a canonical SHA-256 fingerprint. A run with another dataset ID, changed labels, missing cases, unexpected cases, duplicated identities, invalid ranks, or an outdated fingerprint is rejected before scoring.

Run records preserve:

- system, Git, model, and prompt versions;
- ordered evidence identities and transparent retrieval scores;
- answer claims, citations, and independent support judgments;
- deterministic numerical-validation results;
- versioned safety findings and reviewer decisions;
- latency, optional cost, abstention, failure, and completion state.

Faithfulness labels are deliberately external to generation. A valid citation identifier proves only that a source was available; it does not prove that the passage entails a claim. Support judgments should be produced by a blinded qualified reviewer or a separately versioned judge and audited on a human-labeled subset.

## Metrics

| Layer | Metrics | Interpretation |
|---|---|---|
| Retrieval | Recall@K, MRR, graded nDCG@K | Whether relevant evidence was found and ranked early |
| Citations | Validity, relevance precision, claim coverage | Whether citations exist, target gold evidence, and cover claims |
| Grounding | Faithfulness | Fraction of independently judged claims supported by cited evidence |
| Numerical | Validation accuracy | Fraction of recorded deterministic checks that passed |
| Safety | Violation rate, abstention accuracy | Policy failures and correct refusal on unanswerable or advisory requests |
| Outcome | Verified task completion, reviewer approval, failure rate | End-to-end usefulness under correctness and safety constraints |
| Operations | Mean plus p50/p95 latency, optional cost | Runtime and economic trade-offs |

Metric denominators are explicit. Optional judgments are excluded rather than silently treated as correct. Unjudged claims prevent verified task completion.

## Paired experiment design

The intended offline comparison is:

- **Control A:** vector retrieval followed by one grounded generation step.
- **Treatment B:** hybrid retrieval, weighted reranking, structured SEC facts, citation and numerical validation, durable LangGraph review state, and human approval.

Both systems must process the same held-out cases. FinSight reports treatment-minus-control changes, paired bootstrap confidence intervals, paired standardized effect sizes, and exact paired sign-test p-values. Retrieval metrics remain separate from answer metrics so a generation improvement cannot hide a retrieval regression.

This is a paired offline experiment, not proof of production causality. FinSight's [controlled experimentation layer](experimentation.md) now adds preregistration, deterministic sticky assignment, exposure and outcome telemetry, sample-size planning, and guardrail-aware analysis. Online causal conclusions still require correct eligibility, instrumentation, missing-data handling, and independent statistical review.

## Run the contract fixture

The committed fixture verifies the evaluation pipeline without network access:

```bash
finsight evaluate \
  --dataset evals/fixtures/synthetic_dataset_v1.json \
  --control-run evals/fixtures/control_run_v1.json \
  --treatment-run evals/fixtures/treatment_run_v1.json \
  --output artifacts/evaluation/synthetic-report.json \
  --top-k 3 \
  --bootstrap-iterations 2000 \
  --seed 17
```

The CLI prints the validated report and atomically writes the same JSON contract. Generated reports belong under `artifacts/`, which is excluded from Git.

## Benchmark publication safeguard

The fixture is intentionally labeled `synthetic_fixture`; its example outputs are constructed to exercise success and failure paths. They are not FinSight performance results.

`benchmark_claim_allowed` becomes true only when:

- the dataset is labeled `public_sec_derived`;
- both runs are labeled `offline_benchmark`;
- the dataset contains at least 20 held-out cases;
- both runs record Git commit identities; and
- every run matches the canonical dataset fingerprint and complete case set.

These checks are necessary but not sufficient. A public benchmark must also document sampling, labeling instructions, inter-rater agreement, model and prompt versions, costs, known exclusions, and all metric denominators.

## Current limitations

- The committed dataset is a contract fixture, not a representative SEC benchmark.
- Faithfulness and safety judgments are recorded inputs; judge calibration and reviewer agreement remain future work.
- Confidence intervals do not correct dataset selection or annotation bias.
- Cost is optional because provider billing metadata is not yet captured automatically.
- Offline paired results do not replace randomized online A/B testing.
