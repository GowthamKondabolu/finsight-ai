# SEC document processing

FinSight converts every newly downloaded SEC primary filing into deterministic, retrieval-ready sections and chunks before committing the filing transaction. This stage preserves enough provenance to reproduce a chunk and connect future answers to the original filing.

## Processing contract

1. Verify that the downloaded document is non-empty and no larger than 25 MiB.
2. Parse HTML with a deterministic local parser and remove scripts, styles, `noscript`, and SVG content.
3. Recognize HTML headings and SEC `PART` or `ITEM` labels as section boundaries.
4. Normalize narrative blocks and flatten table rows into pipe-delimited text while preserving row order.
5. Bound a filing to at most 250 extracted sections.
6. Tokenize section content with `cl100k_base`.
7. Create windows of at most 500 tokens with a 50-token overlap.
8. Persist the sections, chunks, hashes, token offsets, and processor metadata in the same transaction as a newly created filing.

The parser contract is versioned as `sec-html-v1`. A future parser change must use a new version so stored representations remain auditable.

## Persisted provenance

| Level | Persisted fields |
|---|---|
| Filing | SEC source URL, document SHA-256, content type and length, parser version, tokenizer, section count, chunk count |
| Section | Filing identifier, ordered sequence, section name, normalized content, content SHA-256, character count, document hash |
| Chunk | Section identifier, chunk index, normalized content, content SHA-256, token count, token start and end offsets |

The source document hash comes from the exact downloaded bytes. Section and chunk hashes come from their normalized UTF-8 text. Reprocessing the same bytes with the same parser version and settings therefore produces the same representation.

## Transaction and idempotency behavior

Network I/O and CPU-bound parsing happen before the database write transaction. The transaction then upserts the company, inserts the filing if its globally unique SEC accession number is new, and persists all derived sections and chunks. If another worker has already inserted that accession number, FinSight verifies the immutable filing identity and does not create duplicate derived records.

## Safety limits

- A document larger than 25 MiB is rejected before parsing.
- Markup without usable text is rejected.
- More than 250 sections are rejected.
- Chunk size and overlap must be positive and non-overlapping in configuration terms: overlap is required to be smaller than the window.
- Scripts and active styling content never become retrieval text.
- Filing, section, and chunk database constraints enforce stable ordering and uniqueness.

These are engineering safeguards, not guarantees that SEC markup is semantically interpreted correctly.

## Known limitations

- SEC filings vary widely in HTML quality. Version 1 uses transparent heading heuristics rather than issuer-specific templates.
- Tables are flattened into readable rows; merged cells, footnote relationships, and complete table semantics are not reconstructed yet.
- Token offsets refer to normalized section text, not byte offsets in the original HTML.
- Existing accession numbers are treated as immutable. Reprocessing historical filings after a parser upgrade will require an explicit versioned backfill workflow.

## Verification

Unit tests cover deterministic output, SEC item headings, tables, overlap, hashes, noise removal, malformed inputs, resource limits, and invalid chunk settings. PostgreSQL integration tests run the mocked EDGAR-to-database workflow twice and verify that sections and chunks are persisted once with their provenance metadata. CI applies Alembic migrations and runs those integration tests against PostgreSQL with pgvector enabled.
