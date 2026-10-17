# Logbook

Key decisions, newest last. Hard-to-reverse ones link to an ADR.

- 2026-10-15 — Zotero is the record of truth; Postgres holds derived data only → [ADR 0001](adr/0001-zotero-is-the-record-of-truth.md)
- 2026-10-15 — Parse with LiteParse + rules; no generative model touches displayed text → [ADR 0002](adr/0002-rule-based-parsing-not-llm.md)
- 2026-10-15 — Hand-written SQL (psycopg 3), no ORM → [ADR 0003](adr/0003-hand-written-sql-no-orm.md)
- 2026-10-15 — Separate FastAPI JSON API and Jinja/HTMX web app → [ADR 0004](adr/0004-separate-api-and-htmx-web.md)
- 2026-10-15 — v1 = one Seed + one level of Sources; Seed must have full text
- 2026-10-15 — Citation styles: numeric `[n]` and APA author–year
- 2026-10-15 — Chunks: paragraphs, split at sentence borders into ≤400-token windows with one-sentence overlap
- 2026-10-15 — Embeddings: bge-m3 via any OpenAI-compatible endpoint (Ollama for now)
- 2026-10-15 — Retrieval: vector + FTS fused by RRF (k=60), on click, recorded as Retrieval Runs; Scope = cited Work or Exploration
- 2026-10-15 — One flat Zotero collection per Exploration; Seed tagged `pe:seed`; preprint and published versions are separate Works, published preferred when ambiguous
- 2026-10-15 — Postgres reachable only via SSH tunnel; Python 3.14, uv, ruff, pyright strict
- 2026-10-16 — Three uv packages: core (library + CLI), api (JSON), web (HTMX; imports only api schemas/client) → [architecture](architecture.md)
- 2026-10-16 — Real seams only at `Library` and `Embedder`; everything else plain functions
- 2026-10-16 — Real Postgres in tests via testcontainers, no store fake → [ADR 0005](adr/0005-real-postgres-in-tests.md)
- 2026-10-16 — External HTTP: hand-written cases for likely/harmful scenarios first; every real failure becomes a recorded regression test
- 2026-10-16 — API returns structured JSON (blocks + Citation offsets), never HTML
- 2026-10-16 — Pipeline = chain of independent, re-runnable Stages
- 2026-10-17 — Remote moves from Codeberg to GitHub: Codeberg restricts LLM-generated code. PRs via `gh`
