# Architecture

Vocabulary: module, interface, seam, adapter, depth (see codebase-design skill). Domain terms: `CONTEXT.md`.

## Deployables → packages

uv workspace, three packages. Split only where the deploy target differs.

| Package | Import name | Runs as | Depends on |
|---|---|---|---|
| `packages/core` | `paperexplorer` | CLI on tower (`paperexplorer explore …`) | — |
| `packages/api` | `paperexplorer_api` | FastAPI container, structured JSON only | core |
| `packages/web` | `paperexplorer_web` | FastAPI + Jinja/HTMX container | api (schemas + typed client only) |

The web app never imports core or touches Postgres (ADR 0004).

## Deep modules in core

Each row is one module with a small interface. Internals (caches, tokenizer, regexes, SQL) stay behind it.

| Module | Interface (sketch) | Dependency kind | Test through |
|---|---|---|---|
| `domain` | pydantic models: Work, WorkId, CitationId, LibraryItem, Attachment (later: Chunk, Evidence, Scope, RetrievalRun, TextAvailability, Conversation, Question, Answer, Pointer, Quote); Reference stays in `parsing`, Citation in `citations` | in-process | — |
| `parsing` | `parse(pdf, settings) -> ParsedWork`: GROBID finds the structure, pdfium reads the printed text and PDF link targets, LiteParse OCR reads scans (ADR 0006) | GROBID (external service, output cached by PDF hash) + local libs | fixture PDFs + recorded GROBID output; one live GROBID check |
| `citations` | `extract(doc: ParsedWork) -> CitationGraph`: Citation Links weighed from PDF link, GROBID and rules; Citing Sentences | in-process | parsed fixtures and hand-built `ParsedWork`s |
| `chunking` | `chunk(doc: ParsedWork) -> list[Chunk]` | in-process (tokenizer internal) | fixture `ParsedWork`s |
| `resolution` | `resolve(ref: Reference) -> Resolution` | true external (Crossref, OpenAlex, arXiv, GROBID) | hand-written cases + regression cassettes (see Testing) |
| `library` | port `Library`: `item(key)`, `pdf(attachment)` so far (later: find, ensure collection, create, tag, attach); `ZoteroLibrary` reads the Web API with httpx | true external (Zotero) | `InMemoryLibrary` adapter; Zotero adapter over hand-written Web API JSON |
| `embedding` | port `Embedder.embed(texts) -> list[Vector]` | true external (OpenAI-compatible) | `FakeEmbedder` adapter |
| `store` | repositories per aggregate (`Works`, `WorkTexts`, `CitationGraphs`, `StageStatuses`) + `migrate()` | Postgres + pgvector (local-substitutable) | real pgvector via testcontainers |
| `retrieval` | `search(text, scope, k) -> list[ScoredChunk]` (hybrid RRF); `find_evidence(citation, scope) -> RetrievalRun` on top of it | store + embedder | store + `FakeEmbedder` |
| `asking` | `ask(conversation, question) -> AsyncIterator[AnswerEvent]`: a pydantic-ai agent over the Seed's map and retrieval tools, then Pointer and Quote resolution (ADR 0008, 0009, `design-asking.md`) | chat model (OpenAI-compatible) + store + embedder | `FunctionModel` + real Postgres + `FakeEmbedder` |
| `tracing` | `configure_tracing(settings)` and the trace helpers every model call runs inside (see Tracing) | Langfuse (tower, cloud fallback) | in-memory span exporter |
| `catalog` | `ensure_work(resolution, exploration) -> LibraryItemRef` (dedupe: works table → Library search → create) | store + library | store + `InMemoryLibrary` |
| `pipeline` | `run_stage(ctx, name, work) -> StageResult` over `STAGES`; later `explore(seed_key, …) -> ExplorationReport` | everything, injected via `StageContext` | real Postgres, `InMemoryLibrary`, recorded GROBID |
| `config`, `logs` | `Settings` (pydantic-settings), `configure_logging(settings)` | in-process | — |

Real seams (two adapters each): `Library`, `Embedder`, and the chat model through pydantic-ai's `Model` (OpenAI-compatible in use, `FunctionModel` in tests; ADR 0009). Everything else is a plain function or class; no ports without a second adapter.

## Repo layout

```
PaperExplorer/
├── CONTEXT.md  CLAUDE.md  CHANGELOG.md  README.md
├── pyproject.toml  uv.lock  .env.example  compose.yaml  compose.langfuse.yaml
├── packages/
│   ├── core/  src/paperexplorer/{config,logs,tracing,cli,domain,parsing,citations,chunking,resolution,
│   │          library,embedding,store/migrations,retrieval,asking,catalog,pipeline}/  tests/
│   ├── api/   src/paperexplorer_api/{app.py,routes/,schemas.py,client.py}  tests/
│   └── web/   src/paperexplorer_web/{app.py,templates/,static/}  tests/
├── docker/        Dockerfiles per deployable
├── tests/e2e/     cross-package tests
├── tests/fixtures/  open-access PDFs + cassettes
├── data/          gitignored: cache/pdfs, cache/parsed (top-level, never in packages)
├── scripts/       setup wizard
└── docs/          adr/, logbook.md, design-v1.md, design-asking.md, architecture.md
```

## Pipeline

`explore()` is a chain of independent `Stage`s. A Stage is a function `(ctx: StageContext, work: WorkId) -> StageResult`; it reads and writes only through the store, records its status per Work, and is safe to re-run. The chain is a list, so stages can be run singly (`paperexplorer run parse <work>`), reordered or replaced.

- `StageContext` holds the store connection, the settings and the `Library`; `StageResult` holds counts.
- `run_stage` records `running`, runs the Stage in one transaction, then records `done` with its counts, or `failed` with its error and none of its writes kept.
- A Stage that succeeds makes every later Stage pending for that Work, since what they read has changed (a re-parse cascades the old Citations away).
- Built so far: `fetch → parse → extract_citations`. The CLI names a Work by its Zotero key (ADR 0007).

```
Seed:    fetch → parse → chunk → embed → extract_citations → resolve → catalog
Sources: fetch_sources → for each Source: fetch → parse → chunk → embed
Then:    embed_citing_sentences → report
```

Sources reuse the Seed's stage functions; going one level deeper later is the same chain on another Work.

## API contract

The API returns structured JSON only, never HTML: a Work as an ordered list of blocks (heading path, page, bbox, text) with Citation spans as character offsets into the block text, plus endpoints for Evidence by Citation and Scope. Any frontend renders from this; the HTMX web app is the first consumer.

- `GET /works`: every Work's Library Item snapshot and each Stage's state (`pending`, `running`, `done`, `failed`).
- `GET /works/{id}`: the snapshot plus `blocks[]` in reading order; each block has `citations[]` of `{id, start, end, confidence_level}` (`certain`, `high`, `low`, `unlinked`). 404 until the Work is parsed.
- `GET /citations/{id}`: the Citation's marker, Citing Sentence and `text_origin`, its Citation Link (`confidence`, `evidence`, `confidence_level`) and the Reference as printed with the fields read from it; both null for an Unlinked Citation.
- Offsets count Unicode code points (Python `str` indices), not UTF-16 units. Spans may overlap or coincide: a printed range's dash stands for several Citations.
- `paperexplorer_api.schemas` imports nothing from core, so the web app depends on the contract alone.
- `POST /works/{id}/conversations`: starts a Conversation about a Work whose Chunks are embedded; returns `{id}`. Conversations live in the API's memory until Postgres and login replace that store.
- `POST /conversations/{id}/questions` with `{text}`: a `text/event-stream` of JSON events. `step` names a tool the model runs; `delta` carries draft text; `answer` carries the resolved Answer, its sentences with `pointers[]` of `{block_id, start, end, support}` and its `quotes[]` of `{start, end, exact, block_id, block_start, block_end}`; `error` ends a failed turn. Offsets count code points, as above.

## Tracing

Every model call, chat and embedding alike, runs inside a Langfuse trace and never opens a top-level trace of its own, so a request's calls are found by nesting, not by time.

- **One trace per Question**, named `question`. `session_id` is the Conversation's id; `user_id` stays empty until login. Tags: `asking`, `work:<zotero_key>`. Metadata: WorkId, Zotero key, the Question's position in the Conversation, prompt version, model, `num_ctx`.
- **Inside it:** the forced first search, each model request (a generation with token usage), each tool call, every embedding call and the Pointer and Quote resolution.
- **Scores on the trace,** sortable and filterable: Pointer coverage, counts per Support, exact and not-exact Quotes, tool calls, model requests, latency. Eval runs add `judge.grounded` and `judge.correct`.
- **Stages:** `embed` traces as `stage.embed`, tagged `pipeline` and `work:<key>`, with one embedding observation per batch.
- **Environment** is `development` (Mac), `tower` or `eval`; eval runs keep the name `question`, so live and eval traces compare directly. The release is the git SHA.

## Testing

Fake what we don't own, run what we do.

- **Pure modules** (`citations`, `chunking`, `parsing`): tests through the interface with fixture PDFs / `ParsedWork`s. Fixtures are CC BY papers (`tests/fixtures/README.md`), plus a synthetic scan and Tesseract's fast English model so OCR runs offline.
- **GROBID**: a service we run, but slow to start, so `parsing` tests read GROBID output recorded per fixture PDF (`scripts/record_grobid_fixtures.py`); one live test per run starts the pinned image with testcontainers and checks fresh output keeps the recorded structure.
- **Postgres**: always real. testcontainers starts a throwaway `pgvector` container on Mac and tower alike, or `PE_TEST_DATABASE_URL` points at an existing empty database; each test runs in a rolled-back transaction (ADR 0005). The fixtures live in the root `conftest.py`, shared by every package and `tests/e2e/`; code that commits (the CLI) gets a scratch database of its own.
- **Zotero, Embedder**: in-memory adapters at the `Library` and `Embedder` seams.
- **Chat model**: pydantic-ai's `FunctionModel` scripts tool calls and text; Pointer and Quote resolution is tested as a pure function over hand-built Chunks and Answers. A bad real Answer becomes a named regression test built from its recorded message history.
- **Tracing**: an in-memory span exporter checks trace names, `session_id`, tags and nesting. Tracing is off in every other test.
- **External HTTP** (Crossref, OpenAlex, arXiv): `resolution` keeps the matching logic in a pure internal function tested with hand-written cases for the likely and the harmful scenarios (exact DOI, no DOI, wrong-year near-miss, preprint vs published, garbage string, empty result, 429/5xx). HTTP is never live in tests.
- **Regression rule**: every real failure seen in use is recorded as a cassette (vcrpy) and kept as a named test before it is fixed.
- `@pytest.mark.tower`: only tests needing Ollama, a real Zotero account or the GPU.
