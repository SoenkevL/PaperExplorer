# PaperExplorer — settled design (grilling outcome)

## Context
The repo holds only `Paper Explorer Plan 1.md`. Four grilling rounds turned it into the design below. The next step is to write it down as domain docs and ADRs, then set up the repo and build the first test-driven slice.
The goal is a local-first tool for reading a paper and following its citations into the cited sources' actual words, with as little generative AI as possible.

## v1 scope and definition of done
One seed paper plus one level of sources. Notes come after v1; asking the Seed Questions is in v1 and comes before resolution ([design-asking.md](design-asking.md), 2026-10-28). v1 is done when:
1. `paperexplorer explore <zoteroItemKey>` runs every stage for the seed paper and can be restarted safely.
2. The Exploration's Zotero collection holds every resolved source, tagged `pe:fullText` / `pe:abstractOnly` / `pe:noSource`, and no duplicates were created.
3. Open-access PDFs are attached in Zotero, and the sources' full texts are chunked and embedded.
4. The seed paper renders in the browser on the Mac. Every `[n]` and APA citation is clickable and opens a side panel with the top 5 chunks, their scores, and a Scope toggle (cited Work / whole Exploration).
5. A report lists unmatched and low-confidence references.
6. A sidebar beside the Seed answers Questions about it; every Pointer leads back to the sentence it rests on, and the eval meets the bar in `design-asking.md`.

The seed paper is "A critical review of brain entropy as a biomarker of the psychedelic state", from your Zotero library.

## Settled decisions
**Ingest**
- Parsing (ADR 0006, superseding the tool choice of ADR 0002): GROBID (CRF image, local service) finds headings, paragraphs, sentences, References and Citation markers; the PDF's own text layer, read with pdfium, gives their text exactly as printed and the PDF's internal link targets. Scans are read by LiteParse's OCR and marked as OCR Text.
- References: GROBID splits the bibliography into entries (an entry merged across a hanging indent is split again); for scans, rules split it by labels or hanging indent. Each entry is resolved with Crossref `query.bibliographic`, then enriched from OpenAlex.
- Inline citations: numbered `[12]` style and author-year. Each Citation Link to a Reference carries a confidence and its evidence: the PDF's exact link target (certain), GROBID and a rule agreeing (high), or one source alone (reported as low confidence).
- Resolving sources: OpenAlex first, including Unpaywall open-access links and abstracts, then arXiv as a fallback.
- When no full text is available, the source's abstract is indexed instead, and it still gets a proper Zotero item tagged `abstractOnly` or `noSource`.

**Zotero and storage**
- Zotero is the record of truth for metadata, PDFs (premium plan, 2 GB) and collections, and the code reaches it through the Web API with `pyzotero`.
- There is one flat collection per exploration, with the Seed tagged `pe:seed`; an item can sit in several collections without being duplicated. Preprint and published versions are separate Works; the published one is preferred when a reference is ambiguous.
- Duplicate check: a Postgres `works` table maps DOI, arXiv ID and OpenAlex ID to the Zotero key. It is filled from your library once, and a Zotero search runs as a final check before any item is created.
- PDFs are read from the optional `ZOTERO_STORAGE_DIR` setting (a read-only mount of the Zotero data folder on the tower). If that's missing, PDFs are downloaded via the Web API into `data/cache/pdfs/`.
- LiteParse output is cached in `data/cache/parsed/`. The whole cache can be deleted and rebuilt.
- Postgres holds only derived data (chunks, embeddings, references, citations, retrieval runs, pipeline status), plus a metadata snapshot with a `synced_at` time used for filtering. When the two disagree, Zotero wins, and a `sync` command refreshes the snapshot.
- References and citations always point from one work to another, so following the trail one level deeper later runs the same stages with no schema change.

**Chunking and retrieval**
- A chunk is a paragraph. Paragraphs over 400 tokens are split at sentence borders (GROBID's sentences; for a scan, end punctuation) into the fewest windows that fit, balanced in size, with one sentence of overlap. Tokens are counted with the bge-m3 tokenizer.
- Edge cases: a single sentence over 400 tokens is hard-split at a token boundary and flagged `oversized_sentence`. Paragraphs under about 30 tokens become their own chunks, flagged `short`, and are never merged across paragraph borders.
- Each chunk stores its heading path, page number and position on the page.
- Embeddings: bge-m3, behind an `Embedder` interface that talks to an **OpenAI-compatible** `/v1/embeddings` endpoint (`base_url`, `model` and key are settings). Ollama serves it for now, and any compatible server can replace it later. The model name is stored with each vector.
- Search: the query is the citing sentence with citation markers removed. It runs through pgvector with an HNSW index and through Postgres full-text search (`english`), and the two rankings are merged by Reciprocal Rank Fusion (k=60) in one SQL query that uses CTEs.
- The scores from both searches are kept.
- By default the search is limited to the cited work, with a toggle to widen the Scope to the whole Exploration.
- Results are computed on click and stored as a `retrieval_run`. Citing-sentence embeddings are computed ahead of time.

**Apps**
- The pipeline is a CLI whose stages can be restarted, with their status tracked in the database. This can be upgraded later.
- The API is FastAPI returning JSON. The web app is a separate FastAPI container using Jinja and HTMX.
- Rendering turns the LiteParse Markdown into HTML, with citation markers becoming HTMX links. Positions on the page are kept so a "view original" toggle can come later.
- Database access uses psycopg 3 with hand-written SQL, results mapped into pydantic models, and numbered `.sql` migration files.

**Infrastructure**
- The tower runs docker compose with Postgres (pgvector), the API and the web app. Ollama and Zotero desktop (file sync) run natively on the tower; Ollama listens on the home network so containers and the Mac can embed.
- The web app and API are reachable on your home network with no login. Postgres is only reachable from inside Docker or through an SSH tunnel.
- Secrets live in a gitignored `.env`, with a committed `.env.example`, and are loaded through `pydantic-settings`.
- Logging uses Python's built-in `logging`, configured in one module, with a logger per module. Output is text or JSON lines depending on a setting, and `--verbose` switches on DEBUG.

**Engineering**
- Python 3.14, a uv workspace, ruff, pyright in strict mode, pytest and pre-commit, written in a pydantic style.
- Git branches: `main` is stable, `dev` is where work comes together, and `feat/*` branches enter `dev` only by PR. You merge, and you promote `dev` to `main`. The remote is a GitHub repository; PRs are opened with the `gh` CLI.
- The changelog follows Keep a Changelog. ADRs live in `docs/adr/`, and `docs/logbook.md` indexes them in one line each.
- Tests:
  - Unit tests run on the Mac, offline, against recorded HTTP responses (pytest-recording) and fake `Embedder` and `ZoteroClient` implementations.
  - Tests that need real services are marked `@pytest.mark.tower`.
  - Committed fixtures are one open-access arXiv paper in numbered style and one in APA style.
  - The seed paper's PDF is **not committed**, because its licence is unknown. It is used only in a local "golden" test on the tower, read from Zotero.

## Next steps (in order)
1. **domain-modeling**: `CONTEXT.md` with Work, Exploration, Chunk, Reference, Citation, CitationLink and RetrievalRun, plus ADRs 0001–00xx taken from the decisions above.
2. **codebase-design**: package boundaries for core, db, ingest, pipeline, api and web, and where the fakes and test mocks plug in. `data/` sits at the top level.
3. **writing-for-agents**: `CLAUDE.md` with the conventions above.
4. Repo skeleton: git init, `main` and `dev` branches, uv workspace, tooling, compose file. Delivered as a PR into `dev`.
5. **wizard**: a setup script for the steps only you can do on the tower (GitHub repo and remote, Docker with GPU support, Zotero desktop and API key).
6. **tdd**: first slice, the seed PDF rendered with clickable citations that show reference metadata.

## Verification
- Steps 1–3: you review `CONTEXT.md`, the ADRs and `CLAUDE.md` before any code is written.
- Step 4: `uv sync`, `ruff check`, `pyright` and `pytest` all pass on the Mac, and `docker compose config` validates on the tower.
- v1 overall: the five-point definition of done above, run against the seed paper on the tower.
