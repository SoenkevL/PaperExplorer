# Asking the Seed: settled design (grilling outcome)

## Context
Before resolution and Sources, the reader can ask the Seed Questions in a sidebar beside the reader. A local model answers in its own prose, and every claim it ties to the Seed points back to the sentence that bears it out. Five grilling rounds (2026-10-27/28) settled the design below. ADR 0008 sets what the model may write; ADR 0009 picks the framework.

## Scope and definition of done
- Scope is the Seed alone. Sources, their text and Evidence stay a separate concern, built after resolution.
- The Seed's own References, as printed, and its Citations are part of the paper and in scope.
- This work is point 6 of v1's definition of done (`design-v1.md`). It is done when:
  1. `chunk` and `embed` run for the Seed, and `retrieval` answers hybrid searches over its Chunks.
  2. The sidebar answers Questions about the Seed as they stream, with Pointers and Quotes as below.
  3. Every model call is traced in Langfuse, grouped as `architecture.md` describes.
  4. The eval meets its bar.

## Answers
- Natural prose from the Seed alone. When the Seed does not cover a Question, the Answer says so (ADR 0008).
- **Handles.** The model ties a sentence to a Chunk by writing its handle, `[#n]`, shaped unlike the Seed's own `[12]` or `(Smith, 2019)`. A Chunk keeps its handle for the whole Conversation; unknown handles are dropped. A sentence without a handle stays unmarked.
- **Pointers.** Once the Answer's text is complete, each sentence with a handle is resolved, comparing bge-m3 embeddings of the Answer sentence and each Chunk sentence (GROBID's sentence spans):
  1. the best-matching sentence in the cited Chunk: close Support if it clears the threshold;
  2. otherwise the best match in the other Chunks the model read in this turn: close if it clears the threshold, and the Pointer moves there;
  3. otherwise weak Support, leading to the best sentence of the cited Chunk.
- **Quotes.** Quotation marks are the model's prose. Each quoted span is searched in every Chunk the model read in this turn, whatever its handle says, with ligatures and line-break hyphens folded for matching only. An exact Quote is shown in the authors' style and links to its place; any other Quote is marked not exact.
- **Two marks, not one scale.** The Pointer chip shows Support (close, weak: the reader's high and low colours); quoted words show exactness (exact: the reader's certain colour). A legend in the sidebar explains them. Thresholds are set from the eval.
- **Clicking a Pointer** scrolls the reader to the block and highlights the matched sentence strongly and the rest of the Chunk faintly. Citations in the block stay clickable.

## The agent
- pydantic-ai (ADR 0009) in the core module `asking`, against Ollama's OpenAI-compatible endpoint. Settings: `PE_CHAT_BASE_URL`, `PE_CHAT_MODEL`.
- **Model:** `paperexplorer-chat`, a Modelfile alias created by `scripts/setup-tower.sh`: the community gemma-4 12B fine-tune `hf.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF:Q6_K` (digest `f87405c6d8ad`, 9.8 GB) with `num_ctx` 73728. If `ollama ps` shows it not fully on the GPU beside bge-m3: `OLLAMA_KV_CACHE_TYPE=q8_0` with flash attention first, then a smaller `num_ctx`.
- **Always in context:** the system prompt (kept in git, its version on every trace), the Seed's map (its heading tree with a handle per section, plus its abstract) and the Conversation so far.
- **Tools:**
  - `search(query, k=8)`: hybrid RRF over the Seed's Chunks; each result has its handle, Heading Path, page and text.
  - `read_section(heading)`: the Chunks under one heading in reading order, up to about 3k tokens, continuable.
  - `read_around(handle, n=1)`: the neighbouring Chunks.
  - `reference(handle, marker)`: the Reference as printed behind a Citation marker in a Chunk, with its Citation Link's confidence.
  - `citing_sentences(reference)`: every Citing Sentence of that Reference in the Seed.
- The server runs `search(question)` before the model's first request and adds it to the history as the model's own tool call. A Question gets at most 6 model requests.

## Conversations, API and web
- Memory lives in the context only. The API holds Conversations in process memory behind a small repository interface and evicts old ones; a restart clears them. Postgres with login replaces that store later.
- Routes and stream events: `architecture.md`.
- Layout: Conversation sidebar left, reader middle, Citation panel right; the sidebar folds into a drawer below about 92rem.
- The draft streams with handles as placeholder chips; then the resolved Answer replaces it. The htmx SSE extension is vendored like htmx.

## Retrieval
As `design-v1.md`: paragraph Chunks of at most 400 bge-m3 tokens, HNSW plus English full-text search, RRF with k=60, both scores kept. No reranker; one may come later as an experiment measured against the eval.

## Tracing
- Langfuse v4, self-hosted on the tower in `compose.langfuse.yaml`, memory-capped at about 6 GB with 90-day retention.
- Langfuse Cloud (EU) is the fallback. Each process picks its host once at start, the tower if its health endpoint answers, and logs a WARNING when it falls back; switching is only a URL.
- There is no live sync. `scripts/sync_langfuse.py`, adapted from Langfuse's data-migration cookbook, copies a time window of cloud traces to the tower when run by hand.
- The Langfuse SDK with pydantic-ai's OpenTelemetry instrumentation. Settings `PE_LANGFUSE_*`; tracing is off when they are unset and never blocks an Answer.
- Trace shape: `architecture.md`.

## Eval
- About 20 Questions on the Seed, written by Claude from the parsed Seed: measurements, methods, shortcomings, follow-ups, and Questions the Seed does not answer. Each lists the places (Heading Path, page) a good Answer must point at.
- `pydantic-evals` runs on the tower and measures hit@8, Pointer coverage (substantive sentences with a Pointer), Support levels, exact and not-exact Quotes, and latency.
- Claude reads every Answer against the Seed and records a verdict per Question, also as Langfuse scores; only uncertain cases go to the user.
- **Bar:** hit@8 ≥ 0.8, Pointer coverage ≥ 0.8, at least 80% of Answers judged grounded and correct, and no Quote marked exact that is not (by construction).
- One baseline run puts the whole Seed in context, to see what retrieval costs.
- The Questions, verdicts and metrics are committed. Full reports, which quote the Seed, stay in `data/`: its licence is unknown.

## Order of work
1. Docs: ADRs 0008 and 0009, `CONTEXT.md`, this design, `design-v1.md`, `architecture.md`, logbook.
2. Tracing: settings, Langfuse client, in-memory exporter test, `compose.langfuse.yaml`, fallback, sync script.
3. Retrieval milestone: `chunk` Stage with its migration; `Embedder` with the `embed` Stage; `retrieval`. Check in with the user.
4. Asking milestone: Modelfile, settings and `asking` (agent, tools, Pointer and Quote resolution); API Conversations with SSE; web sidebar; eval set and report. Check in with the eval report.

## Facts to check while building
- The Seed's length in tokens (estimated 20–35k with its bibliography).
- Whether 72k context fits on the 4060 Ti beside bge-m3.
- The tower's free disk for Langfuse.
- What the cookbook's copy keeps: trace IDs, scores, sessions.
