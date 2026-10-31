# Asking the Seed: settled design (grilling outcome)

## Context
Before resolution and Sources, the reader can ask the Seed Questions in a sidebar beside the reader. A local model answers in its own prose, and every claim it ties to the Seed points back to the sentence that bears it out. Five grilling rounds (2026-10-27/28) settled the design below. ADR 0008 sets what the model may write; ADR 0009 picks the framework; ADR 0010 answers from the whole Seed when it fits, after the eval of 2026-10-29.

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
  - **Every form with a `#` is read** (eval of 2026-10-29: the model also wrote `(#5)`, `{#92}`, `[<#3><#4>]`, `[#59##60]`, `[#65·66]`, `[#5, 6]` and ranges, and those Answers lost their Pointers). A handle is any `[]`, `()`, `{}` or `<>` group, nested one deep, holding only numbers, at least one of them with a `#`; the numbers stand apart by commas, semicolons, spaces, middle dots (`·`) or `#`.
  - A range `#5–#7`, with any dash, reads as its two ends only: in retrieval, handles number Chunks in the order they were first shown, not in reading order.
  - A group without a `#`, such as `[74]` or `(5)`, stays the Seed's own marker, in the text.
  - **A bare `[n]` is a handle in a Seed that numbers no Reference** (the live app on 2026-10-31: the model wrote `[13]` and `[14]` for `[#13]`, `[#14]`, and the Answer got no Pointer). Such a Seed cites by author and year, so it prints no `[13]` of its own; none of its References carries a printed label, which parsing sets only where it numbers the bibliography (`SeedTools.numbered_references`). Only square brackets, not nested, holding numbers as above: `[13]`, `[13, 14]`, `[13–15]`. Parentheses stay text everywhere (the Seed's abstract numbers its themes `(1)`…`(4)`), and in a numbered Seed `[12]` stays its marker. The prompt still asks for `[#n]`; the reading is only more lenient.
  - The web app's draft reads the same grammar for its placeholder chips; `tests/fixtures/handle-forms.json` holds the forms both are tested with. It cannot know the Seed's citation style, so a bare `[n]` stays text in the draft until the resolved Answer replaces it.
- **Asked once more for handles** (the redraft; eval of 2026-10-31: three live Answers carried no handle at all, half of the Pointer coverage missing). Reading the whole Seed, an Answer whose draft holds no handle of any form, bare `[n]` included, yet has a substantive sentence for a Pointer to bear out, is asked once more: one request without tools or thinking, the first draft as the model's own response, then a note for that request only, to repeat the Answer word for word with a handle after each sentence and none after a sentence saying what the Seed does not say.
  - A sentence of the prescribed form "The Seed does not say …" needs no handle, nor the sentence right after it, which the prompt asks to say what the Seed covers nearby: an Answer saying the Seed does not cover the Question is not asked again.
  - The second draft, cut and resolved, becomes the Answer, and the Conversation keeps it as the model's response; if the request fails or leaves no words, the first Answer stands.
  - The stream says so (`redraft`); the sidebar empties the draft, says Pointers are being added, and the new draft streams in its place.
- **Where the Answer ends.** The draft ends at the chat template's first control token, gemma's `<|name>` or `<name|>` (seen: `<channel|>` followed by the model's monologue, and `<|tool_response>` as the whole Answer). An echoed `<system>…</system>` block is left out and the text around it kept.
  - While the draft streams, nothing after a token is shown, and a trailing `<…` that could start one waits for the next delta.
  - The Conversation keeps the cut draft as the model's response, so the junk does not feed later Questions.
  - A draft with no words left is no Answer: the turn ends with an error ("The model gave no Answer.") and the Conversation stays as it was.
  - No stop sequence in the Modelfile: gemma-4 ends its thinking channel with `<channel|>`, so a stop there could cut off the Answer itself.
- **Pointers.** Once the Answer's text is complete, each sentence with a handle is resolved, comparing bge-m3 embeddings of the Answer sentence and each Chunk sentence (GROBID's sentence spans):
  1. the best-matching sentence in the cited Chunk: close Support if it clears the threshold;
  2. otherwise the best match in the other Chunks the model read in this turn: close if it clears the threshold, and the Pointer moves there;
  3. otherwise weak Support, leading to the best sentence of the cited Chunk.
- **Quotes.** Quotation marks are the model's prose. Each quoted span is searched in every Chunk the model read in this turn, whatever its handle says, with ligatures and line-break hyphens folded for matching only. An exact Quote is shown in the authors' style and links to its place; any other Quote is marked not exact.
- **Two marks, not one scale.** The Pointer chip shows Support (close, weak: the reader's high and low colours); quoted words show exactness (exact: the reader's certain colour). A legend in the sidebar explains them. The Support threshold, a bge-m3 cosine of 0.7, was set from the eval ([eval of 2026-10-29](eval-2026-10-29.md)) and held in the two after it ([eval of 2026-10-31](eval-2026-10-31.md), [its second run](eval-2026-10-31b.md)).
- **Clicking a Pointer** scrolls the reader to the block and highlights the matched sentence strongly and the rest of the Chunk faintly. Citations in the block stay clickable.

## The agent
- pydantic-ai (ADR 0009) in the core module `asking`, against Ollama's OpenAI-compatible endpoint. Settings: `PE_CHAT_BASE_URL`, `PE_CHAT_MODEL`.
- **Model:** `paperexplorer-chat`, a Modelfile alias created by `scripts/setup-tower.sh`: the community gemma-4 12B fine-tune `hf.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF:Q6_K` (digest `f87405c6d8ad`, 9.8 GB) with `num_ctx` 73728. Measured: 10 GB and fully on the GPU; with bge-m3 beside it, 13.0 of 16.4 GB. Should it ever spill: `OLLAMA_KV_CACHE_TYPE=q8_0` with flash attention first, then a smaller `num_ctx`.
- **Thinking:** the model reasons before it answers on a request that offers tools. Ollama's OpenAI-compatible endpoint returns that as `reasoning`, apart from `content`; it is traced and never shown in the Answer. A request that offers no tools must answer, and runs without thinking (`PE_CHAT_ANSWER_THINKING`, false): the whole Seed's request, retrieval's sixth, and the redraft. Leaked thinking is how chat-template tokens and monologue reached Answers (eval of 2026-10-29); the cut at the first control token stays as a guard. gemma-4 thinks only on or off.
  - The switch Ollama honours was probed on the tower (Ollama 0.40.3, 2026-10-31): `reasoning_effort: "none"` in the request body brings no `reasoning` back, streamed or not. pydantic-ai's unified `thinking=False` sends nothing to Ollama, whose profile says the model has no thinking setting, and a `think: false` in the body is ignored on the OpenAI-compatible endpoint.
- **Temperature:** `PE_CHAT_TEMPERATURE`, 0.5, with every chat request. Left to Ollama's default it was unset, and one pass of the eval could not tell a change from noise (retrieval judged right 68%, then 36%, with nothing changed that could invent claims). Both settings are per request and so on every trace and in the eval's run record; the Modelfile, whose parameters are not traced, is unchanged.
- **How a Question reads the Seed** (ADR 0010, `AskingContext.reading`): `auto`, the default for the API, the CLI and the web, reads the whole Seed when the sum of its Chunks' bge-m3 tokens is at most `PE_WHOLE_SEED_MAX_TOKENS` (40,000, validated to stay below `PE_CHAT_NUM_CTX`), and searches it otherwise. The eval forces one or the other (`whole_seed`, `retrieval`).
  - **The whole Seed** is the eval's measured baseline: the system prompt, then a note that no tools and no search apply, then every Chunk in reading order under its handle, all of them read. No tools are offered and no search runs. The trace is tagged `whole-seed`.
  - **The Question last.** The request closes with one user turn: the note that there are no tools, then "The reader's Question:", then the Question (eval of 2026-10-31: as a turn of its own after the Question, the note was taken for the reader's message, and two Answers answered a Question nobody had asked). The turn is built for that request only; the Conversation keeps the bare Question. Retrieval's last-request note still follows the tool results, where it was measured to work.
  - **Retrieval**, the fallback for a Seed over the budget and later for Sources, is as below: the Seed's map, the forced search, the tools.
  - The budget leaves the rest of the 73,728-token context for gemma counting more tokens than bge-m3, the system prompt, the Conversation, and the model's thinking and Answer. The Seed's body is about 18k.
  - The Seed's References are not in the whole Seed's context: a Question about a numbered Reference is a known gap (ADR 0010); the reader shows each Reference one click away.
- **Always in context:** the system prompt (kept in git, its version on every trace), the Seed (in retrieval, its map: its heading tree with a handle per section, plus its abstract) and the Conversation so far.
- **Tools** (retrieval only):
  - `search(query, k=8)`: hybrid RRF over the Seed's Chunks; each result has its handle, Heading Path, page and text.
  - `read_section(heading)`: the Chunks under one heading in reading order, up to about 3k tokens, continuable.
  - `read_around(handle, n=1)`: the neighbouring Chunks.
  - `reference(handle, marker)`: the Reference as printed behind a Citation marker in a Chunk, with its Citation Link's confidence.
  - `citing_sentences(reference)`: every Citing Sentence of that Reference in the Seed.
- In retrieval the server runs `search(question)` before the model's first request and adds it to the history as the model's own tool call. A Question gets at most 6 model requests.
- **Settled while building** (`architecture.md`, The agent):
  - The map's abstract is the Seed's Chunks before its first heading: GROBID's abstract arrives as paragraphs without a Heading Path (else the Chunks under a first heading "Abstract"), up to 1,500 tokens. They carry handles and count as read every turn, since they are always in context.
  - A section's handle is a § number in the map (`§3 2.1 Data · p. 4`), one per Heading Path prefix the Chunks sit under, apart from the Chunks' `[#n]` so the model cannot cite a section as a Chunk. `read_section` takes the § number or the heading, and reads on in parts of about 3,000 tokens.
  - At the request limit, the sixth request offers no tools and tells the model it is its last, so it answers from what it read. A model that still calls a tool ends the turn with an `error`; the Conversation stays as before the Question.
  - That note comes last in the request, after everything the model read, never among the instructions (eval of 2026-10-29: among them, the model called a tool on all 8 last requests; replayed with the note last, it answered 23 of 24). The whole Seed's request closes with its own note that it has no tools, then the Question (The agent, above). A chat request whose stream stays silent for `PE_CHAT_TIMEOUT_S` (120 s) fails the turn.
  - The model's thinking is traced with its request but left out of the Conversation's messages, so earlier turns' reasoning does not fill the context.
  - The prompt's version is the first 12 hex digits of the SHA-256 of its file and of the notes the agent adds (the last request's, the whole Seed's), so an edit cannot forget it.
  - `Conversation` lives in `asking`, as it holds pydantic-ai's messages; `ask` takes an `AskingContext` (connection, Embedder, chat model, settings, tracing), as `run_stage` takes a `StageContext`.

## Conversations, API and web
- Memory lives in the context only. The API holds Conversations in process memory behind a small repository interface and evicts old ones; a restart clears them. Postgres with login replaces that store later.
- Routes and stream events: `architecture.md`.
- Layout: Conversation sidebar left, reader middle, Citation panel right; the sidebar folds into a drawer below about 92rem.
- The draft streams with handles as placeholder chips; then the resolved Answer replaces it. The htmx SSE extension is vendored like htmx.
- **Settled while building the API** (`architecture.md`, API contract and The API's Conversations):
  - Starting a Conversation about a Work with no Chunks, or with a Chunk lacking a Vector from the API's Embedder model, answers 409 and says which Stage to run. It checks the Chunks, not the `embed` Stage's status, since retrieval only finds Vectors of its own model.
  - A second Question while the Conversation still answers gets 409 before any stream starts; the reader waits for the Answer, or closes the stream, which cancels the turn.
  - Conversations are evicted least recently used beyond `PE_CONVERSATIONS_MAX` (50) and when unused for `PE_CONVERSATION_IDLE_MINUTES` (120). An evicted Conversation answers 404, as an unknown one does, and the sidebar starts a new one.
  - Every turn runs on one asking thread with its own event loop and its own store connection, so the tools' blocking calls never hold up the API's other requests and streams.
  - The stream is FastAPI's SSE: event name = `type`, data = the event's JSON, a `: ping` comment after 15 s of silence while the model thinks.
  - The API's Answer carries what the reader shows: Pointers with their place, Support and Chunk span (`chunk_start`, `chunk_end`); Quotes with exactness and place. The cosine, Chunk id, substantive flags and unknown handles stay in the traces and the eval.
  - `ApiClient.ask` is a synchronous generator, for the synchronous web app. EventSource (and so the htmx SSE extension) only GETs, so the web app relays: the browser's EventSource GETs a web route that POSTs the Question through `ApiClient.ask` and re-sends each event as HTML. Closing that generator closes the API's stream and so cancels the turn.
- **Settled while building the sidebar** (`architecture.md`, The web app's Conversation sidebar):
  - The sidebar posts its Question to the web app, which holds it in memory under a random id until the page's EventSource GETs that id's stream, once; the Question's text never goes into a URL. A stream asked for twice says to ask again.
  - The first Question starts the Conversation, and its id lives in the page's form. When the API has forgotten it (404), the relay starts a new one, says so above the reply and puts the new id in the form; when the Work is not embedded (409), the Question is not asked and the sidebar says why; while it still answers (409), the sidebar says to wait.
  - The relay's events carry HTML: `step`, `delta`, `conversation` (a new Conversation took over), then `answer` or `error`, which replace the reply and with it the EventSource, so the browser never asks again on its own. Handles split across deltas wait until they are whole before they become chips.
  - The Answer is cut at its code-point offsets in Python and rendered as plain text with its line breaks (`pre-wrap`); every piece of model text is escaped. Close Support and exact Quotes use the reader's link colour, which its certain and high Citations share; weak Support uses its low colour.
  - A Pointer's place is painted with the CSS Custom Highlight API, so the block's DOM, and its Citation links, stay as they are.
  - htmx's SSE extension 2.2.4 is vendored beside htmx 2.0.11, from the npm tarball.

## Retrieval
As `design-v1.md`: paragraph Chunks of at most 400 bge-m3 tokens, HNSW plus English full-text search, RRF with k=60, both scores kept. No reranker; one may come later as an experiment measured against the eval.

## Tracing
- Langfuse v4, self-hosted on the tower in `compose.langfuse.yaml`, memory-capped at about 6 GB of RAM (about 1.4 GB idle).
- Traces are kept until their store on disk reaches 6 GB (`PE_LANGFUSE_TRACE_CAP_GB`), or until someone deletes them; there is no time-based retention. Langfuse's own retention counts days, so a pruning job of ours (`scripts/langfuse_prune.py`, every six hours from the tower's crontab) measures what ClickHouse and MinIO hold on disk and, over the cap, deletes the oldest tenth of the traces through the Public API. ClickHouse frees space only after its merges, so the next run measures again before deleting more. ClickHouse's diagnostic logs are off and its `query_log`, which Langfuse reads, keeps 7 days, so the cap counts traces.
- Langfuse Cloud (EU) is the fallback. Each process picks its host once at start, the tower if its health endpoint answers within 2 s, and logs a WARNING when it falls back. One key pair serves both: the tower's project is initialised with the cloud project's keys, so switching is only a URL.
- There is no live sync. `scripts/sync_langfuse.py`, adapted from Langfuse's data-migration cookbook, copies a time window of cloud traces to the tower when run by hand. A copy keeps trace and observation IDs, times to the microsecond, nesting, input, output, metadata, model, usage and cost, and each trace's name, session, tags, environment and release; scores keep their IDs and times. Only the metadata key `scope.name` changes, to `langfuse-sdk`. Copying a window twice is safe.
- The Langfuse Python SDK v5 (it needs Langfuse ≥ 4.59) with pydantic-ai's OpenTelemetry instrumentation, handed the tracer provider of `tracing`. Settings `PE_LANGFUSE_*`; tracing is off when the keys are unset and never blocks an Answer. `paperexplorer trace-check` sends one trace and says where it went.
- The scripts use only Langfuse v4's APIs (Observations v2, Scores v3, OTLP ingestion): Langfuse Cloud removes the v3 endpoints on 2026-11-16.
- Trace shape: `architecture.md`.

## Eval
- About 20 Questions on the Seed, written by Claude from the parsed Seed: measurements, methods, shortcomings, follow-ups, and Questions the Seed does not answer. Each lists the places (Heading Path, page) a good Answer must point at.
- `pydantic-evals` runs on the tower and measures hit@8, Pointer coverage (substantive sentences with a Pointer), Support levels, exact and not-exact Quotes, and latency.
- Claude reads every Answer against the Seed and records a verdict per Question, also as Langfuse scores; only uncertain cases go to the user.
- **Bar:** hit@8 ≥ 0.8, Pointer coverage ≥ 0.8, at least 80% of Answers judged grounded and correct, and no Quote marked exact that is not (by construction).
- Each Question is asked in two modes: `live`, as the reader asks it (`auto`: the whole Seed when it fits), and `retrieval`, forced, so the fallback stays measured. The bar is checked on `live`; hit@8 comes from the forced search, a check on the fallback. Runs before ADR 0010 asked `retrieval` (then the live agent) and a `baseline` with the whole Seed; their committed summaries stay as they are, a record.
- The Questions, verdicts and metrics are committed. Full reports, which quote the Seed, stay in `data/`: its licence is unknown.
- **Settled while building the harness** (`architecture.md`, The eval):
  - 22 Questions in `evals/seed-questions.yaml`, a pydantic-evals dataset written from the Seed as `parse` and `chunk` read it: 4 on its measurements, 4 on methods, 5 on shortcomings, 4 on follow-ups, and 5 it does not answer, on varied topics (dosing, an experiment of its own, heart rate, analysis software, EU regulation). A place is a Heading Path and page exactly as `chunk` gives them; the command refuses places where no Chunk sits.
  - The eval lives in core (`paperexplorer.evals`, `paperexplorer eval`), since it runs `asking.ask` against the store as `paperexplorer ask` does; `scripts/tower-eval.sh` runs it on the tower. The Question set and the committed summaries live in a top-level `evals/`.
  - Every Question is asked in a fresh Conversation, one at a time. For a turn that read by retrieval, hit@8 re-runs the forced search, `retrieval.search(question, k=8)`, outside the trace: the same call, so the same Chunks.
  - A mode is a reading of `ask` (`AskingContext.reading`), so the trace, scores and resolution are the live code's. Each turn records the reading it used.
  - Traces go to the `eval` environment, named `question` and tagged `run:<run id>`; `AnsweredEvent` carries the trace's id, which the eval records for the judges' scores in stage 8b.
  - Pointer coverage for the bar pools the substantive sentences of the covered Questions' Answers, and hit@8 counts covered Questions: an uncovered Question's "The Seed does not say …" is substantive and rightly unpointed.
  - "No Quote marked exact that is not" is checked, not assumed: each exact Quote's words are compared with the printed text at its place, folded on their own.
  - Whether an uncovered Question's Answer says so is a heuristic on its words ("the Seed does not say …" and its kin), reported for every turn and checked for uncovered Questions; stage 8b's reading decides.
- **Judged** (stage 8b; the report: [eval of 2026-10-29](eval-2026-10-29.md)):
  - Every Answer of a run is read against the Seed and judged grounded and correct, with a note in the judge's words, in `evals/runs/<run id>.verdicts.yaml` beside the run's summary, and posted as Langfuse scores `judge.grounded` and `judge.correct` on its trace (`scripts/post_verdicts.py`). Each Pointer's place is read too, as apt, partly or not, which is what set the Support threshold.
  - Grounded: every claim is borne out by the Seed (by the place a Pointer leads to, else by what the model read); a true unpointed sentence is left to Pointer coverage. Correct: the substance on the point asked is right. A failed turn is neither.
  - Where parsing lost words, an Answer is judged by the Chunk as stored, which is what the model read (settled by the user after 2026-10-29).
- **Re-run with whole-Seed answering** (stage 9b; the report: [eval of 2026-10-31](eval-2026-10-31.md)): live read the whole Seed on all 22 turns. The bar is not met, on Pointer coverage alone (0.797, one sentence short of 0.80); 82% of live Answers are judged grounded and correct, hit@8 is 1.00 and no Quote is wrongly exact. Point 4 of the definition of done is not reached.
- **Repeats** (stage 10a): one pass of 22 Questions cannot tell a change of a few points from the run-to-run spread, so `paperexplorer eval --repeat N` asks each Question live N times, each in a fresh Conversation; retrieval, the fallback, once. The bar is pooled over all live turns, the coverage measure unchanged, and each repeat's values and their spread stand beside it. Each turn records its repeat and whether it was redrafted; a run records the chat temperature and the thinking switch. Verdicts are keyed by Question, mode and repeat; the summaries and verdicts of runs that asked once read as repeat 1.
- **Re-run after stage 10a** (stage 10b; the report: [second eval of 2026-10-31](eval-2026-10-31b.md)): live asked three times per Question, retrieval once. The bar is not met, on Pointer coverage alone: 0.69 pooled over the 66 live turns (0.69, 0.71 and 0.68 per repeat), against 0.797 in run 3. The redraft went through 7 of 18 times; the 11 that failed, on a call to a tool that does not exist or an empty response, are the only live Answers left without a Pointer. 85% of live Answers are judged grounded and correct (86%, 82%, 86%), 91% by retrieval; hit@8 is 1.00 and no Answer quoted the Seed. Point 4 of the definition of done is not reached.

## Order of work
1. Docs: ADRs 0008 and 0009, `CONTEXT.md`, this design, `design-v1.md`, `architecture.md`, logbook.
2. Tracing: settings, Langfuse client, in-memory exporter test, `compose.langfuse.yaml`, fallback, copy script, pruning job.
3. Retrieval milestone: `chunk` Stage with its migration; `Embedder` with the `embed` Stage; `retrieval`. Check in with the user.
4. Asking milestone: Modelfile, settings and `asking` (agent, tools, Pointer and Quote resolution); API Conversations with SSE; web sidebar; eval set and report. Check in with the eval report.

## Measured on the tower (2026-10-28)
- The Seed's body is 113 blocks, 91.6k characters, 18,380 tokens for the chat model; its 185 References come on top.
- The chat model loads in 15 s and reads a prompt at about 1,500 tokens/s: the whole body in context costs about 12 s before the first token.
- 72k context fits fully on the GPU beside bge-m3 (above). When bge-m3 was loaded first, loading the chat model evicted it once; it reloads on the next embedding call.
- 703 GB free on the tower's root disk, where Docker keeps its volumes.
- Langfuse 4.71.0 idles at about 1.4 GB of RAM (web 0.7, worker 0.35, ClickHouse 0.2 GB).

## Still to check while building
- Settled: the release on traces from the compose containers, which have no git. `compose.yaml` passes `PE_LANGFUSE_RELEASE` from the shell into `api` and `pipeline`; `tower-check.sh` and `setup-tower.sh` set it with `git rev-parse --short HEAD`. A build argument would not do: the `.env` every container reads sets it empty, and that overrides an image's own value.
