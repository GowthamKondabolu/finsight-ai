# MCP evidence tools

FinSight exposes a small read-only tool surface using the official Model Context Protocol Python SDK v2. The MCP server lets compatible agents retrieve the same citation-complete filing passages and exact SEC facts used by the REST investigation pipeline without granting database mutation or approval capabilities.

## Run the server

Install the project and configure PostgreSQL and the OpenAI API key as described in the README, then start the stdio transport:

```bash
finsight-mcp
```

The console command is suitable for an MCP client that launches local stdio servers. Transport configuration belongs in that client; secrets remain in FinSight environment settings rather than MCP arguments.

## Tool contracts

| Tool | Purpose | Key bounds |
|---|---|---|
| `search_sec_filing_evidence` | Hybrid keyword/vector search with filing citations, ranks, scores, and hashes | `top_k` 1–20; `candidate_k` up to 200; optional CIK, form, and section filters |
| `list_sec_company_facts` | Exact normalized SEC XBRL observations with accession and period provenance | One CIK; up to 30 concept filters; result limit 1–100 |

Both tools declare MCP annotations `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`, and `openWorldHint: false`. Inputs reject blank or duplicate exact filters, invalid CIKs, and contradictory result bounds.

Search results preserve the immutable chunk hash, fused score, channel ranks and scores, filing accession, filing date, section, chunk position, and SEC URL. Fact results preserve exact decimal values, units, periods, filing dates, accession numbers, and deterministic observation keys.

## Trust boundary

The server intentionally does not expose ingestion, embedding writes, workflow approval, or arbitrary SQL. Human approval remains a first-class REST workflow action so it can later sit behind authentication, role checks, and analyst UI controls. An MCP agent can gather evidence, but it cannot authorize release of a financial conclusion.

Provider and database resources are scoped to each production tool call and released on success or failure. In-memory MCP client tests validate structured output and advertised annotations without network access.

## Current limitations

- Filing search requires configured embeddings and an OpenAI API key; exact fact lookup requires only PostgreSQL.
- The initial server uses stdio transport. Authenticated remote HTTP transport is deferred to the deployment milestone.
- MCP tool-call telemetry and per-client authorization policies will be added with observability and cloud deployment.
