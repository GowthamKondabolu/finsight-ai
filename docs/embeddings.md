# Embedding pipeline

FinSight converts deterministic filing chunks into vector representations for later semantic retrieval. The pipeline separates provider access, workload orchestration, and PostgreSQL persistence so each layer can be tested and replaced independently.

## Provider contract

The application depends on a small asynchronous `EmbeddingProvider` protocol:

- a stable model identifier;
- an exact vector dimension;
- an ordered batch embedding operation.

The production adapter uses OpenAI `text-embedding-3-small`. The configured 1,536 dimensions match the existing `VECTOR(1536)` pgvector column. Tests and offline evaluation can supply deterministic local providers without network access or API spend.

The adapter validates that every response:

- contains exactly one indexed vector per input;
- preserves input ordering;
- has the configured dimensions;
- contains only finite float values.

Malformed provider responses fail before any database write. The implementation follows the [official OpenAI vector embeddings documentation](https://developers.openai.com/api/docs/guides/embeddings), which documents the embeddings endpoint, 1,536 dimensions for `text-embedding-3-small`, and cosine similarity for search.

## Secret handling

`FINSIGHT_OPENAI_API_KEY` is optional for ingestion, parsing, tests, and database operations. It is required only by `finsight embed-chunks`. Pydantic stores the configured value as `SecretStr`, excludes real keys from `.env.example`, and never includes the secret in results or model provenance.

## Bounded orchestration

Each run has two limits:

- `--limit` caps the total number of processed chunks, with a hard maximum of 10,000;
- `FINSIGHT_EMBEDDING_BATCH_SIZE` caps each provider request, defaulting to 100.

The optional `--cik` filter limits selection to one normalized SEC issuer. Chunks are eligible when the vector is missing, the model identifier is missing, or the stored model differs from the configured model.

## Transaction and concurrency behavior

FinSight reads a stable batch, closes the read session, and calls the external provider without holding a database transaction. It then opens a short write transaction.

Every write matches both the chunk ID and the deterministic content hash observed before the API call. If content changes concurrently, the update does not match and the run fails with an explicit retry message. This prevents a vector generated from old content from being attached to a new chunk.

Successful writes persist both the vector and model identifier. Repeating the same run is idempotent because current model vectors are excluded from selection. A model change intentionally makes old vectors eligible for refresh.

## Verification

Unit tests cover configuration, secret masking, provider ordering, dimensions, finite values, batching, issuer normalization, idempotency, optimistic conflicts, resource cleanup, CLI behavior, and repository SQL construction. PostgreSQL integration tests persist real 1,536-dimension pgvector values and verify that a second backfill performs no work. CI runs those tests without an OpenAI API key by injecting a deterministic provider.

## Current limitations

- Embedding generation uses one model and dimension contract per deployment.
- Failed batches are retried by rerunning the idempotent command; a durable job queue is not implemented yet.
- The current milestone produces retrieval vectors but does not yet expose hybrid search or relevance evaluation.
- Model changes refresh vectors lazily through the bounded backfill command rather than an automated migration.
- API usage can incur cost and provider rate limits; operators must configure workload limits appropriately.
