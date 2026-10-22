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
- 2026-10-17 — Ollama runs natively on the tower, not in compose; listens on the LAN; containers use host.docker.internal
- 2026-10-17 — Seed of the first Exploration: Zotero item `YAAC9XCX`
- 2026-10-18 — Zotero desktop runs as the tower's admin user; netinc (SSH user) reads `/home/Admin/Zotero/storage` read-only via ACL
- 2026-10-18 — Milestone 0 done: Mac and tower set up; tower runs db, api, web; Ollama (bge-m3) on the LAN; Seed PDF synced; tests green on both machines
- 2026-10-19 — Parsing: GROBID (CRF) finds the structure, pdfium reads the printed text and PDF link targets, LiteParse OCR only for scans → [ADR 0006](adr/0006-grobid-structure-pdf-text-ocr-for-scans.md), [research](research/2026-10-parsing-spike.md)
- 2026-10-19 — GROBID's CRF image, not the deep-learning image: fewer correct Citation Links and 14.8 GB
- 2026-10-19 — Citation Links weigh evidence in layers: exact PDF link 1.0, GROBID + rule 0.9, one source reported as low confidence
- 2026-10-19 — Printed text is changed in two ways only: whitespace to single spaces, TeX-split accents composed
- 2026-10-19 — Tests read recorded GROBID output; one live GROBID check per run; OCR tests use Tesseract's fast model, the pipeline the best one
- 2026-10-19 — A Work is addressed by a surrogate `WorkId`; its Zotero key, DOI, arXiv and OpenAlex IDs are unique columns → [ADR 0007](adr/0007-surrogate-work-id.md)
- 2026-10-19 — Store: one row per block, Reference, Citation and Citation Link; per-block value lists (boxes, sentence spans, raw marker evidence) are JSONB
- 2026-10-19 — The Library port is read-only for now (`item`, `pdf`); the Seed arrives through a `fetch` Stage
- 2026-10-19 — Zotero is read with plain httpx (three GETs), not pyzotero: lighter, and testable with hand-written JSON; revisit when catalog uploads files
- 2026-10-19 — A Stage that succeeds makes every later Stage pending for its Work; a failed Stage keeps none of its writes
- 2026-10-19 — htmx 2.0.11 is vendored into the web app (no CDN); the API's character offsets count code points, not UTF-16 units
