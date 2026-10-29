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
| `domain` | pydantic models: Work, WorkId, CitationId, ChunkId, LibraryItem, Attachment, Chunk, ChunkFlag, Vector, Scope, Answer (its AnswerSentences, Pointers with their Support, Quotes, and AnswerCounts) (later: Evidence, RetrievalRun, TextAvailability); Reference stays in `parsing`, Citation in `citations`, Conversation in `asking` (it holds pydantic-ai's messages) | in-process | — |
| `parsing` | `parse(pdf, settings) -> ParsedWork`: GROBID finds the structure, pdfium reads the printed text and PDF link targets, LiteParse OCR reads scans (ADR 0006) | GROBID (external service, output cached by PDF hash) + local libs | fixture PDFs + recorded GROBID output; one live GROBID check |
| `citations` | `extract(doc: ParsedWork) -> CitationGraph`: Citation Links weighed from PDF link, GROBID and rules; Citing Sentences | in-process | parsed fixtures and hand-built `ParsedWork`s |
| `chunking` | `chunk(doc: ParsedWork) -> list[Chunk]`: paragraphs and footnotes into Chunks of at most 400 bge-m3 tokens, split at sentence borders (`parsing.sentence_spans`); a Chunk's span and sentences are code-point offsets into its block's text | in-process (bge-m3 tokenizer internal, shipped xz-compressed in the package) | fixture `ParsedWork`s and hand-built blocks |
| `resolution` | `resolve(ref: Reference) -> Resolution` | true external (Crossref, OpenAlex, arXiv, GROBID) | hand-written cases + regression cassettes (see Testing) |
| `library` | port `Library`: `item(key)`, `pdf(attachment)` so far (later: find, ensure collection, create, tag, attach); `ZoteroLibrary` reads the Web API with httpx | true external (Zotero) | `InMemoryLibrary` adapter; Zotero adapter over hand-written Web API JSON |
| `embedding` | port `Embedder`: `model` (its name, stored with each Vector) and `embed(texts) -> list[Vector]`, one per text in order; `OpenAICompatibleEmbedder` POSTs batches of `PE_EMBEDDING_BATCH_SIZE` to `<PE_EMBEDDING_BASE_URL>/embeddings`, each batch one embedding observation in the current trace; `FakeEmbedder` hashes a text's content words into 1024 dimensions, so texts that share words come out closer | true external (OpenAI-compatible) | `FakeEmbedder` adapter; the OpenAI-compatible one over `httpx.MockTransport`; one live bge-m3 check |
| `store` | repositories per aggregate (`Works`, `WorkTexts`, `Chunks`, `CitationGraphs`, `StageStatuses`) + `migrate()`; `Works.find` a Work by Zotero key; `Chunks` reads a Work's Chunks in reading order, by id, `around` one (its neighbours) and `under` a Heading Path, records and reads their embeddings (`record_embeddings`, `embeddings`, `without_embedding` a model), and runs the hybrid query (`search`, see Retrieval) | Postgres + pgvector (local-substitutable) | real pgvector via testcontainers |
| `retrieval` | `search(conn, embedder, text, scope, k=8) -> list[ScoredChunk]`: the Scope's Chunks best first by RRF of a vector and a full-text ranking, each result a `StoredChunk` with both rankings' `Placing` (rank, score; empty when that ranking missed it) and its `rrf` (see Retrieval); later `find_evidence(citation, scope) -> RetrievalRun` on top of it | store + embedder | store + `FakeEmbedder`; one live bge-m3 check on the Seed |
| `asking` | `ask(ctx, conversation, question) -> AsyncIterator[AnswerEvent]`: a pydantic-ai agent over the Seed's map and five tools (`SeedTools`), `ctx` an `AskingContext` (store connection, Embedder, chat model from `chat_model(settings)`, settings, tracing); `Conversation` (id, Work, messages, handles); `resolve_answer(draft, handles, read, embedder, threshold) -> Answer`, the Pointer and Quote resolution of a complete draft (see The agent, Answers) | chat model (OpenAI-compatible) + store + embedder | `FunctionModel` (streaming) + real Postgres + `FakeEmbedder`; resolution as a pure function over hand-built Chunks; live checks of bge-m3 and of the chat model on a CC BY fixture |
| `tracing` | `configure_tracing(settings) -> Tracing`: the tower's Langfuse, else the cloud; `Tracing.trace(name, tags, session_id, metadata)` opens the trace every model call runs inside, `Tracing.embedding(model, texts)` records one embedding call under the current trace (nothing outside one), `Tracing.span(name, as_type, input)` one step of our own and `Tracing.score(name, value)` one score of the current trace (likewise), `Tracing.tracer_provider` goes to pydantic-ai; `retention.prune` keeps the tower's store under its disk cap (see Tracing) | Langfuse (tower, cloud fallback) | in-memory span exporter; Public API over `httpx.MockTransport` |
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
├── scripts/       setup wizards, tower checks, Langfuse pruning and copy, the chat model's Modelfile
└── docs/          adr/, logbook.md, design-v1.md, design-asking.md, architecture.md
```

## Pipeline

`explore()` is a chain of independent `Stage`s. A Stage is a function `(ctx: StageContext, work: WorkId) -> StageResult`; it reads and writes only through the store, records its status per Work, and is safe to re-run. The chain is a list, so stages can be run singly (`paperexplorer run parse <work>`), reordered or replaced.

- `StageContext` holds the store connection, the settings, the `Library`, the `Embedder` and `Tracing`; `StageResult` holds counts.
- `run_stage` records `running`, runs the Stage in one transaction, then records `done` with its counts, or `failed` with its error and none of its writes kept.
- A Stage that succeeds makes every later Stage pending for that Work, since what they read has changed (a re-parse cascades the old Chunks and Citations away).
- Built so far: `fetch → parse → chunk → embed → extract_citations`. The CLI names a Work by its Zotero key (ADR 0007) and builds the Embedder and tracing from `Settings`.
- `embed` embeds only the Work's Chunks without a Vector from the Embedder's model, so running it again embeds none, and a new model re-embeds them all. Re-chunking deletes the Vectors with their Chunks.

```
Seed:    fetch → parse → chunk → embed → extract_citations → resolve → catalog
Sources: fetch_sources → for each Source: fetch → parse → chunk → embed
Then:    embed_citing_sentences → report
```

Sources reuse the Seed's stage functions; going one level deeper later is the same chain on another Work.

## Retrieval

`retrieval.search` embeds the text once and hands the Vector to `Chunks.search`, one hand-written query of CTEs (ADR 0003):

- **Scope** is a set of Works; for now it is built for one Work alone, the Seed (`Scope.of(work)`). Explorations widen it later.
- **Vector ranking:** the nearest `depth` Vectors of the Embedder's model among the Scope's Works, by cosine distance (`<=>`, the HNSW index's operator). `hnsw.iterative_scan = relaxed_order` keeps the index scanning until `depth` of them pass the Scope filter; the ranking then orders them strictly. Score: cosine similarity.
- **Full-text ranking:** the Scope's Chunks whose `text_search` holds any English stem of the text (`plainto_tsquery`, its ANDs turned into ORs), by `ts_rank`. A question's stop words drop out; a text of stop words only matches nothing.
- **Fusion:** RRF with k=60 over both rankings, each `max(k, 50)` deep, so the first Chunks of a search for fewer are those of a search for more. A Chunk without a Vector from the model can still be found by its words. Ties, in either ranking and in the fusion, go in reading order (Work, then position).
- An empty text finds nothing and embeds nothing.
- `paperexplorer search <zotero_key> "<text>" [-k N] [--no-text]` prints the ranking.

## The agent

`asking.ask(ctx, conversation, question)` answers one Question (ADR 0008, 0009, `design-asking.md`) and streams `AnswerEvent`s: `StepEvent` (`tool`, `args`), `DeltaEvent` (`text`), then `AnsweredEvent` (`answer`) or `ErrorEvent` (`message`). Only an answered turn joins the Conversation. The turn runs in a task of its own, so its trace stays inside it; closing the stream cancels it.

- **Conversation:** a plain pydantic object the caller holds: `id`, `work`, `messages` (pydantic-ai's, without the model's thinking), `handles` (`[#n]` → `StoredChunk`, given when a Chunk is first shown and kept for the whole Conversation) and `questions` answered.
- **Context:** the system prompt (`asking/system-prompt.md`; `PROMPT_VERSION` is the first 12 hex digits of its SHA-256) and the Seed's map, sent as instructions with every request, then the Conversation so far. The map gives the title, every Heading Path prefix the Chunks sit under as a numbered section (`§3`, with its pages) and the Chunks before the first heading, GROBID's abstract (else those under a first heading "Abstract"), up to 1,500 tokens, with their handles. Those Chunks count as read every turn.
- **Tools** (`SeedTools`; each Chunk shown as `[#n] <Heading Path> · p. <page>` and its text, and remembered in the order shown):
  - `search(query, k=8)`: `retrieval.search` over `Scope.of(seed)`, at most 20.
  - `read_section(heading, part=1)`: the Chunks under a § number or a heading (the whole Heading Path, its last heading, or a unique part of that), in parts of about 3,000 tokens; a part that goes on says how to read the next.
  - `read_around(handle, n=1)`: the Chunk and up to 3 neighbours on either side.
  - `reference(handle, marker)`: the Citations a Chunk prints whose marker or Reference label is `marker` (else whose marker contains it), each with its Reference as printed, key, and Citation Link confidence and level, or as an Unlinked Citation.
  - `citing_sentences(reference)`: a Reference by key, printed label or words of its text; every Citing Sentence of it, each under its Chunk's handle; those Chunks count as read.
- **Forced first search:** before the first request, `search(question)` runs (a `forced search` span) and goes into the history as the model's own tool call and its result, after the Question.
- **Requests:** at most `REQUEST_LIMIT` (6) per Question. The sixth offers no tools and tells the model it is the last, so it answers from what it read; a model that still calls a tool ends the turn with an error.
- **Thinking:** Ollama returns it as `reasoning`, which pydantic-ai keeps as a thinking part: it is on the request's span in the trace, never in a `delta`, the Answer or the Conversation's messages.
- **Draft:** `delta`s carry every text part the model streams; the Answer is the last response's text, resolved with `resolve_answer` against the Chunks read this turn at `PE_SUPPORT_THRESHOLD`.
- **Model:** `chat_model(settings)` is pydantic-ai's OpenAI chat model with Ollama's provider profile (thinking in `reasoning`, tool schemas without `strict`) at `PE_CHAT_BASE_URL`, model `PE_CHAT_MODEL` (`paperexplorer-chat`, `scripts/paperexplorer-chat.Modelfile`); `PE_CHAT_NUM_CTX` repeats the Modelfile's `num_ctx` for the traces.
- The tools call the store and the Embedder in the event loop: one connection, and the Chunks read in a fixed order.
- `paperexplorer ask <zotero_key> "<question>"` streams the steps and draft, then prints the Answer, its Pointers with their places and its Quotes, and a summary line of counts.

## Answers

`asking.resolve_answer(draft, handles, read, embedder, threshold)` turns the model's complete draft into the Answer the reader sees (ADR 0008, `design-asking.md`). `handles` maps each handle of the Conversation to its `StoredChunk`; `read` are the Chunks the model read in this turn; `threshold` is `PE_SUPPORT_THRESHOLD`.

- **Handles** are `[#n]`; `[#n, #m]` reads as `[#n][#m]`. They leave the Answer's text with the spaces before them (a handle that starts its line takes the whitespace after it), so every offset counts into the text the reader sees. A handle belongs to the sentence it stands in, or the one whose full stop it follows. A handle the map does not know is dropped and listed in `Answer.unknown_handles`.
- **Sentences** end at `.`, `!` or `?` (perhaps inside closing quotes or brackets) before whitespace and a capital, and at every line break.
- **Pointers:** each Chunk a sentence cites gives one Pointer, in the order cited. The sentence's Vector is compared with each Chunk sentence's (the Chunk's own spans): the cited Chunk's closest sentence if its cosine clears the threshold (close); else the closest sentence of another Chunk read this turn if that one does (close, and the Pointer moves there); else weak, at the cited Chunk's closest sentence. Pointers of one sentence that reach the same place are one. A Pointer carries its place (`block_id`, `start`, `end`), its Support, its Chunk (`chunk_id`, `chunk_start`, `chunk_end`, for the faint highlight) and the cosine (`similarity`).
- **Embedding:** the cited sentences and every sentence of the cited and read Chunks go to the Embedder in one call, each distinct text once; an Answer without handles embeds nothing. Inside a `question` trace the call records itself.
- **Quotes** are the words between double marks, straight or curly, closed on the same line; single marks stay prose (the closing one is also the apostrophe), and inside a Quote they are its words. Each Quote is searched in every Chunk read this turn, the Chunks its sentence points at first, at word boundaries, with ligatures spelled out, a hyphen between letters (with any whitespace after it) and soft hyphens dropped, and whitespace runs as one space; for matching only. A match is an exact Quote with its place in the unfolded block text (`block_id`, `block_start`, `block_end`); `exact` follows from that place, so no Quote is exact without a match. Punctuation the model put inside the closing mark stays outside an exact Quote when the authors' text goes on without it.
- **Counts** (`Answer.counts()`, the `question` trace's scores): sentences, substantive sentences, Pointer coverage (substantive sentences with a Pointer over all substantive ones; none without any), Pointers close and weak, Quotes exact and not exact, unknown handles. A sentence is substantive unless it is a question, ends in a colon, or has under four words.

## API contract

The API returns structured JSON only, never HTML: a Work as an ordered list of blocks (heading path, page, bbox, text) with Citation spans as character offsets into the block text, plus endpoints for Evidence by Citation and Scope. Any frontend renders from this; the HTMX web app is the first consumer.

- `GET /works`: every Work's Library Item snapshot and each Stage's state (`pending`, `running`, `done`, `failed`).
- `GET /works/{id}`: the snapshot plus `blocks[]` in reading order; each block has `citations[]` of `{id, start, end, confidence_level}` (`certain`, `high`, `low`, `unlinked`). 404 until the Work is parsed.
- `GET /citations/{id}`: the Citation's marker, Citing Sentence and `text_origin`, its Citation Link (`confidence`, `evidence`, `confidence_level`) and the Reference as printed with the fields read from it; both null for an Unlinked Citation.
- Offsets count Unicode code points (Python `str` indices), not UTF-16 units. Spans may overlap or coincide: a printed range's dash stands for several Citations.
- `paperexplorer_api.schemas` imports nothing from core, so the web app depends on the contract alone.
- `POST /works/{id}/conversations`: starts a Conversation about a Work whose Chunks are embedded; returns `{id}`. Conversations live in the API's memory until Postgres and login replace that store.
- `POST /conversations/{id}/questions` with `{text}`: a `text/event-stream` of JSON events, `asking`'s `AnswerEvent`s by their `type`. `step` names a tool the model runs, with its `args` (the forced first search comes first); `delta` carries draft text; `answer` carries the resolved Answer: its `text`, the draft without its handles, which every offset of the Answer counts into; its sentences (`start`, `end`) with `pointers[]` of `{block_id, start, end, support}` and its `quotes[]` of `{start, end, exact, block_id, block_start, block_end}`, the place empty when not exact; `error` ends a failed turn with a `message`. Offsets count code points, as above.

## Tracing

Every model call, chat and embedding alike, runs inside a Langfuse trace and never opens a top-level trace of its own, so a request's calls are found by nesting, not by time.

- **One trace per Question**, named `question`. `session_id` is the Conversation's id; `user_id` stays empty until login. Tags: `asking`, `work:<zotero_key>`. Metadata: WorkId, Zotero key, the Question's position in the Conversation, prompt version, model, `num_ctx`.
- **Inside it:** `forced search` (a tool observation, its embedding inside), pydantic-ai's `invoke_agent asking` holding each model request as `chat <model>` (a generation with token usage, its thinking among its output) and each tool call as `execute_tool <tool>`, every embedding call (`embeddings <model>`), and `resolve answer` (the draft in, the Answer's counts out).
- **Metadata:** `work_id`, `zotero_key`, `position` (from 1), `prompt_version`, `model`, `num_ctx`.
- **Scores on the trace,** sortable and filterable: `Answer.counts()` by field name (`sentences`, `substantive_sentences`, `pointer_coverage` when any sentence is substantive, `pointers_close`, `pointers_weak`, `quotes_exact`, `quotes_not_exact`, `unknown_handles`), `tool_calls` (the model's, without the forced search), `model_requests` and `latency_s`. Eval runs add `judge.grounded` and `judge.correct`.
- **Stages:** `embed` traces as `stage.embed`, tagged `pipeline` and `work:<key>`, with one embedding observation per batch (named `embeddings <model>`: the batch's texts, its token usage, the count and length of its Vectors).
- **CLI search:** `paperexplorer search` traces as `search`, tagged `cli` and `work:<key>`, holding the query's embedding observation.
- An embedding call made outside any trace records nothing; with tracing off, `Tracing.embedding` returns at once.
- **Environment** is `development` (Mac), `tower` or `eval`; eval runs keep the name `question`, so live and eval traces compare directly. The release is the git SHA.
- `tracing` builds its own OpenTelemetry tracer provider, never the global one, with environment and release on its resource; instrumentation reaches Langfuse only when handed that provider.

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
