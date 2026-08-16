# Controlled experiment specifications

This directory contains immutable, reviewable input contracts for FinSight online experiments. Registering a file stores its canonical SHA-256 fingerprint, Git revision, hypothesis, traffic allocation, primary outcome, operational guardrails, effect threshold, confidence level, statistical power, and sample-size commitment.

`fixtures/answer_workflow_v1.json` is an engineering example, not an active production experiment or a performance result. Its primary metric is analyst-verified task completion. Safety-violation rate and latency are preregistered guardrails.

Never place assignment secrets, raw user identifiers, session identifiers, credentials, or production telemetry in this directory.
