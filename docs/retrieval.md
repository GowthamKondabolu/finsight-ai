# Hybrid retrieval

FinSight retrieves filing evidence through two independent PostgreSQL channels and transparently reranks their union. The system returns source-complete passages for downstream answer generation; it does not yet generate financial conclusions.

## Candidate channels

Keyword retrieval uses PostgreSQL `websearch_to_tsquery` and the persisted English `tsvector` generated from each chunk. Candidates are ordered by `ts_rank_cd`.

Semantic retrieval embeds the query with the same provider model used for stored chunks. pgvector orders compatible vectors by cosine distance and exposes cosine similarity as the raw channel score. Chunks from another embedding model are excluded rather than compared across incompatible vector spaces.

Both channels use the same bounded candidate pool and metadata constraints:

- normalized SEC CIK;
- one or more form types;
- inclusive filing-date range;
- one or more exact section names.

The public API caps final results at 50 and candidates per channel at 200.

## Reciprocal-rank-fusion reranking

Raw keyword and semantic scores are not directly comparable. FinSight therefore fuses ranks rather than normalizing unrelated score distributions.

For each chunk and channel, the contribution is:

```text
channel_weight / (rrf_k + channel_rank)
```

The default `rrf_k` is 60 and both channel weights are 1. A chunk found by both channels receives both contributions. Ties are resolved deterministically by semantic rank, keyword rank, and chunk UUID. The response retains the fused score, each raw channel score, each channel rank, and the channel names that matched.

This deterministic reranker is intentionally transparent and reproducible. A learned reranker can later be benchmarked behind a separate interface, but it should not replace this baseline without measured retrieval-quality improvements.

## Citation contract

Every result contains enough immutable source context for answer generation and reviewer verification:

- company name, ticker, and CIK;
- accession number and SEC form;
- filing and report dates;
- section name and sequence;
- chunk index and content hash;
- original SEC source URL;
- parser and token-offset metadata carried by the chunk.

If the two channels ever return different content hashes for the same chunk ID, fusion fails instead of silently choosing one version.

## API contract

`POST /v1/retrieval/search` accepts a bounded query and optional metadata filters. Production semantic retrieval requires `FINSIGHT_OPENAI_API_KEY`; an unconfigured deployment returns HTTP 503 without exposing secret details. Input-bound contradictions, such as `candidate_k < top_k` or an inverted date range, return validation errors.

## Verification

Unit tests cover query normalization, filters, SQL structure, pgvector model constraints, invalid vectors, source disagreements, fusion weights, deterministic ordering, API validation, citations, and resource cleanup. PostgreSQL integration tests exercise the computed full-text vector and pgvector cosine operator against real rows, confirm that a dual-channel passage ranks first, and verify that metadata filters exclude nonmatching forms.

## Current limitations

- Exact section-name filters require clients to know the normalized parser label.
- RRF weights and `rrf_k` are fixed demonstration defaults until an offline retrieval benchmark is committed.
- The endpoint creates provider and database resources per request; pooled application-lifespan resources will be added with the service deployment layer.
- Hybrid retrieval supplies evidence but does not establish truth, materiality, or investment significance.
- Answer faithfulness, citation correctness, latency, and retrieval relevance still require a versioned evaluation dataset.
