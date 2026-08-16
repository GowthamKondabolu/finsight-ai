# SEC company-facts ingestion

FinSight ingests the SEC Company Facts API as structured financial evidence alongside filing text. The goal is to support numerical verification with exact, period-aware observations instead of asking a language model to infer values from prose.

## Source contract

The client requests `data.sec.gov/api/xbrl/companyfacts/CIK##########.json` using the same identifiable user agent, request pacing, retry, and validation controls as filing ingestion. The response CIK must match the requested issuer.

Typed validation covers:

- issuer CIK and entity name;
- taxonomy and XBRL concept definitions;
- labels, descriptions, and units;
- exact decimal values;
- instant or duration dates;
- filed date, fiscal year and period, form, accession number, and frame.

FinSight defaults to the `us-gaap` and `dei` taxonomies. Callers can explicitly select other taxonomies exposed by the issuer.

## Observation identity

Every normalized observation receives a SHA-256 key derived from its immutable SEC source identity:

- taxonomy;
- concept;
- unit;
- accession number;
- start and end dates;
- filed date;
- fiscal year and period;
- form type;
- SEC frame.

The numeric value is deliberately not part of this identity. This prevents a changed upstream value for the same source context from being misrepresented as a separate period. The current repository uses conflict-safe inserts, so an explicit audited refresh policy is required before accepting upstream revisions.

## Storage model

The `financial_facts` table stores unconstrained exact PostgreSQL `NUMERIC` values and links every observation to one company. A unique observation key provides idempotency. Indexes support common issuer, taxonomy, concept, period, accession, and end-date filters.

The source metadata records the SEC Company Facts provider and entity name. Company deletion cascades to its normalized observations.

## Transaction behavior

Both submissions metadata and company facts are fetched and validated before opening a write transaction. The transaction refreshes issuer metadata and inserts selected observations in bounded 1,000-row batches with `ON CONFLICT DO NOTHING`. The batch size stays below PostgreSQL parameter limits, while the CLI reports new and existing counts to make repeat behavior observable.

## Safety and limitations

- Processing is bounded to 100,000 selected observations per response.
- Non-numeric values fail validation; FinSight never silently coerces them to zero.
- Exact decimals prevent binary floating-point drift but do not resolve accounting semantics.
- Different units, frames, durations, and fiscal contexts must not be compared without validation.
- Company Facts data can contain amendments, restatements, duplicate contexts, and issuer-specific taxonomies.
- The current milestone stores source observations; standardized metric definitions, period alignment, amendment policy, and calculation validation will be added with the numerical reasoning layer.

## Verification

Unit tests cover payload validation, instant and duration facts, taxonomy filtering, deterministic identities, exact values, resource limits, repository conflicts, service orchestration, and CLI behavior. PostgreSQL integration tests execute the mocked HTTP-to-database workflow twice and verify exact decimal persistence and idempotency. CI applies every migration and runs the full integration suite against PostgreSQL with pgvector enabled.
