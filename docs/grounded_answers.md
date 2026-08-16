# Citation-grounded answers

FinSight turns hybrid retrieval results into analyst-facing claims through a constrained generation and validation pipeline. The language model proposes structured claim objects; the application owns source identity, citation rendering, arithmetic, review state, and failure behavior.

## Trust boundaries

Retrieved filing text is treated as untrusted data. It is serialized into a JSON evidence envelope and cannot modify the system instructions. Each passage receives a request-local identifier such as `E1`; exact SEC company-fact observations receive identifiers such as `F1`.

The generation provider must return a strict Pydantic schema containing:

- narrative claims with one or more supplied evidence identifiers;
- calculation proposals with an operation, ordered fact identifiers, reported value, and unit;
- explicit limitations when the evidence is incomplete.

FinSight rejects a draft that cites an unknown identifier. The model does not author the final inline citation syntax: the application renders verified statements with `[E1]` or `[F1]` references after validation.

## Responses API adapter

The production adapter uses the OpenAI Responses API and Structured Outputs with a provider-independent interface. `FINSIGHT_GENERATION_MODEL` defaults to `gpt-5.6-luna`, reasoning effort defaults to `low`, and output tokens are bounded. Responses are sent with `store=False`; secrets are loaded through masked Pydantic settings and are never returned by the API.

The implementation follows the official [text generation](https://developers.openai.com/api/docs/guides/text), [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), and [Responses migration](https://developers.openai.com/api/docs/guides/migrate-to-responses) guidance.

## Numerical validation

Arithmetic proposals are recomputed from exact PostgreSQL `NUMERIC` observations using Python `Decimal`. The supported deterministic operations are:

| Operation | Fact order | Output unit |
|---|---|---|
| `identity` | one fact | source fact unit |
| `sum` | two or more same-unit facts | source fact unit |
| `difference` | minuend, subtrahend | source fact unit |
| `ratio` | same-unit numerator, denominator | `ratio` |
| `percentage_change` | previous, current | `%` |

Division by zero, incompatible units, unknown fact IDs, incorrect arity, non-finite reported values, or mismatched results fail validation. A small fixed/relative tolerance permits display rounding. Failed numerical statements are retained in the validation report but excluded from rendered answer text.

## Human-review contract

Every response sets `requires_human_review` to true because this project supports financial analysis rather than making financial decisions. Additional review reasons are appended for insufficient retrieval, unsupported output, or failed calculations. Provider refusals and malformed structured output become safe service errors; they are never passed through as uncited prose.

## Verification

Unit tests cover schema rejection, prompt-injection boundaries, unknown citations, provider storage controls, missing credentials, exact fact queries, every arithmetic operation, invalid units and denominators, tolerance behavior, failed-calculation exclusion, API errors, and resource cleanup. A PostgreSQL integration test exercises pgvector/full-text retrieval, financial-fact loading, application citations, and Decimal validation in one path.

## Current limitations

- Evidence IDs are request-local and investigation runs are not yet persisted.
- Exact XBRL concept filters require clients to know SEC concept names.
- Citation entailment and answer faithfulness still require a versioned evaluation dataset; identifier validity alone does not prove that a passage supports a claim.
- The generation endpoint currently creates provider and database resources per request; service-lifespan pooling belongs to the deployment milestone.
- Agent workflow state, human approval actions, and audit-event persistence are planned for the LangGraph milestone.
