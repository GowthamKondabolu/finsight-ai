# Evaluation artifacts

`fixtures/` contains a small synthetic dataset and constructed control/treatment outputs used to test the evaluation contracts in CI. The examples deliberately include retrieval misses, unsupported claims, a failed calculation, unsafe advice, successful grounding, safe abstention, and latency/cost trade-offs.

These files are not measurements of FinSight, SEC data, an OpenAI model, or analyst performance. Their reports must retain `benchmark_claim_allowed: false`.

Future held-out datasets derived from public SEC filings must document sampling, stable evidence identities, annotation instructions, reviewer agreement, exclusions, and licensing/provenance before results are published.
