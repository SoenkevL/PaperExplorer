# Melious as a switchable inference API

Question: can Melious stand in for Ollama behind PaperExplorer's OpenAI-compatible chat and embedding endpoints, ideally by changing settings only? Read on 2026-11-03 from Melious's docs, hub and legal pages, its 401 answer without a key, MeliousAI's GitHub forks, and the installed pydantic-ai 2.63.0.

The first pass had no API key, so every behaviour below is documented, not observed, unless it says otherwise. Claims from outside Melious's own docs are labelled. [Probed with a key](#probed-with-a-key-2026-11-03) records what was then observed.

## Probed with a key (2026-11-03)

On `deepseek-v4-flash-0731` and `bge-m3`, streamed. The first seven rows used text written for the probe. The last sent a real paper, the `villiger2024-tandf` fixture, through the live tests with `PE_PROVIDER=melious`.

| Question | Observed |
|---|---|
| Is the model served? | Yes. `function_calling` and `json_schema` true, context 1,000,000, reasoning `hybrid` |
| Streamed thinking field | `delta.reasoning_content` |
| `reasoning_effort: "none"` | Off: `reasoning_tokens: 0`, no `reasoning_content`. `chat_template_kwargs` does the same |
| Leaked tokens in `content` | None in four answers |
| Tools | A tool call, then an answer after replaying `reasoning_content`, with `finish_reason: tool_calls` and then `stop` |
| `bge-m3` | 1024 dimensions, near unit length (1.0 to 1.0002), identical across two calls |
| Keep-alive lines | None on these short requests, so the stall behaviour is still unknown |
| Real paper | Whole Seed (1 request) and retrieval (1 request, 0 tools) each answered "84" correctly, each with one close Pointer and no unknown handle |

**Against Ollama's `bge-m3`** (Ollama 0.40.3, also 2026-11-03), 1024 dimensions on both:

| Cosine per pair, Ollama against Melious | n | min | median | max |
|---|---|---|---|---|
| Short sentences (8 languages, a technical sentence, "Why?") | 10 | 0.999902 | 0.999987 | 0.999994 |
| `villiger2024-tandf` Chunks | 32 | 0.999968 | 0.999983 | 0.999992 |

Comparing a Chunk's Ollama Vector with another Chunk's Melious Vector moves the cosine by at most 0.00124. Melious's Vectors are not always identical between calls (cosine to the first call never below 0.9999987), even under one reported upstream; Ollama's were identical over 3 calls. Other papers, and the effect of mixed Vectors on Answers and Pointers, were not measured.

`gemma-4-31b` was not probed. One Question per reading says little about Answer quality or the Support threshold; that takes an eval.

## Answer

Almost. The endpoints, auth and bge-m3 fit, and all four values are settings (`PE_CHAT_BASE_URL`, `PE_CHAT_MODEL`, `PE_CHAT_API_KEY`, and the same for `PE_EMBEDDING_*`). Two things can stop it being settings only:

1. **The thinking field.** Melious returns `reasoning_content`; `OllamaProvider` fixes the field at `reasoning`. pydantic-ai still reads the thinking, but replays it into the next tool-loop request as inline `<think>` text (see [Thinking and pydantic-ai](#thinking-and-pydantic-ai)). A profile override in `chat_model` fixes that, which is one small code change.
2. **Thinking off.** PaperExplorer relies on `reasoning_effort: "none"` for requests without tools. Melious documents that it works, but third-party measurements say it does not on every model, and an August 2026 note says so for `gemma-4-31b` itself. This needs a probe with a key.

## Base URL and auth

| | |
|---|---|
| Base URL for OpenAI clients | `https://api.melious.ai/v1` ([quickstart](https://melious.ai/docs/get-started/quickstart), [From OpenAI](https://melious.ai/docs/get-started/openai-sdk)) |
| Header | `Authorization: Bearer <key>` or `x-api-key: <key>`, either on any endpoint ([authentication](https://melious.ai/docs/concepts/authentication)) |
| Key format | 128 characters: `sk-mel-` plus 121 random characters; shown once ([authentication](https://melious.ai/docs/concepts/authentication)) |
| Getting a key | Sign in at melious.ai, then Account → API keys → Create API key ([quickstart](https://melious.ai/docs/get-started/quickstart)) |
| Scopes | `inference.chat`, `inference.embeddings`, `inference.models`; a key with no scopes has full access. A chat-only key cannot embed ([authentication](https://melious.ai/docs/concepts/authentication), [From OpenAI](https://melious.ai/docs/get-started/openai-sdk)) |

Observed: `GET /v1/models` without a key answers 401 with `{"error":{"message":"API key required. Provide as 'Authorization: Bearer <key>' or 'x-api-key: <key>'","type":"authentication_error","param":null,"code":"invalid_api_key"}}`. The live model listing is therefore not public, and there is no `/openapi.json` (404).

## Chat completions

`POST /v1/chat/completions`, OpenAI-shaped plus Melious fields ([reference](https://melious.ai/docs/reference/chat-completions)).

| Feature | Documented | Source |
|---|---|---|
| Streaming | SSE; a final usage chunk by default, then `[DONE]`. `: keep-alive` comment lines while the model thinks | [reference](https://melious.ai/docs/reference/chat-completions), [streaming](https://melious.ai/docs/guides/streaming) |
| Tools | `tools`; `tool_choice` as `auto`, `none`, `required` or a named function; arguments stream as partial JSON | [tool calling](https://melious.ai/docs/guides/tool-calling) |
| `parallel_tool_calls` | Forwarded, not enforced: a turn may still hold several calls | [tool calling](https://melious.ai/docs/guides/tool-calling) |
| `strict` on tools | **Not documented for Chat Completions**, only for Responses function tools. Moot with `OllamaProvider`, which turns strict off | [tool calling](https://melious.ai/docs/guides/tool-calling), [responses](https://melious.ai/docs/reference/responses) |
| Structured output | `json_object` on almost every chat model; `json_schema` only where `_meta.capabilities.json_schema` is true, else a 400 | [structured outputs](https://melious.ai/docs/guides/structured-outputs) |
| `temperature` | `[0, 2]`; "Set this or `top_p`, not both" | [reference](https://melious.ai/docs/reference/chat-completions) |
| `reasoning_effort` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, folded to three levels; `none` turns reasoning off | [reference](https://melious.ai/docs/reference/chat-completions#reasoning) |
| `chat_template_kwargs` | `{"enable_thinking": false}` also turns it off; `reasoning_effort` wins if both are sent | [reference](https://melious.ai/docs/reference/chat-completions#reasoning) |
| Thinking | `choices[].message.reasoning_content`, a string alongside `content`. `usage.reasoning_tokens` counts it, billed as output | [reference](https://melious.ai/docs/reference/chat-completions) |

**Unverified:** the docs name `reasoning_content` on the non-streamed message only. The streamed field (`delta.reasoning_content`, presumably) is not documented, and nothing says whether any model leaks chat-template tokens or inline tags into `content`.

### Is `none` really off?

The docs say it is. Sources outside the docs disagree per model, and none of these could be checked here:

- The upstream [models.dev Melious provider file](https://github.com/anomalyco/models.dev/blob/dev/providers/melious/provider.toml) (third party, measured 2026-10-28) says the catalogue's `reasoning_type` is not reliable, because some models typed hybrid ignore the off switch. It confirms an off state by `usage.reasoning_tokens = 0` with no `reasoning_content`.
- [Its `glm-5.3` file](https://github.com/anomalyco/models.dev/blob/dev/providers/melious/models/glm-5.3.toml) measures `none` at 1–17 reasoning tokens, so not off.
- The `melious-provider` branch of [MeliousAI's models.dev fork](https://github.com/MeliousAI/models.dev/blob/melious-provider/providers/melious/models/gemma-4-31b.toml) (August 2026) says of `gemma-4-31b`: "No off state: an explicit \"none\" still returns reasoning".
- That is older than the current docs, which have since added the toggle, so it may be stale. The hub lists this model as reasoning "Hybrid, Default On" ([hub](https://melious.ai/hub/models/gemma-4-31b)).

## Thinking and pydantic-ai

There is no Melious provider in pydantic-ai 2.63.0 (`pydantic_ai/providers/`). What `OllamaProvider` does against Melious, from its source and an offline run of `OpenAIChatModel._map_messages` with no network:

- **Reading works.** `_process_thinking` and its streaming twin try the profile's field, then `reasoning`, then `reasoning_content`, so `reasoning_content` becomes a `ThinkingPart` with `id="reasoning_content"` (`models/openai.py`).
- **Replay degrades.** In `auto` send-back mode, a part whose `id` differs from the profile's field falls back to tags. Result of the run, for an assistant turn with a tool call:

| Provider and profile | Assistant message replayed | Effect |
|---|---|---|
| `OllamaProvider` | `content: "<think>\nTHOUGHT\n</think>"` | Thinking becomes inline text |
| `OllamaProvider`, `profile=OpenAIModelProfile(openai_chat_thinking_field="reasoning_content", openai_supports_strict_tool_definition=False)` | `reasoning_content: "THOUGHT"`, `content: null` | Same as DeepSeek-style APIs |
| `OpenAIProvider` | `reasoning_content: "THOUGHT"`, `content: null` | Strict handling not checked: the run bypasses the step that infers strict |

PaperExplorer strips thinking between turns (`_without_thinking`), but within a turn pydantic-ai replays it across tool steps, so this path is live. The second row is the smallest fix: a partial `profile=` is merged over the provider's profile, so the rest of Ollama's stays (`Model.profile`, `models/__init__.py`).

**A model name changes the profile.** `OllamaProvider.model_profile` picks by name prefix: `gemma-4-31b` matches `google_model_profile`, while today's alias `paperexplorer-chat` matches nothing. The tool-schema handling can therefore differ between the two even with the same provider. Not tested.

## Embeddings

`POST /v1/embeddings`, OpenAI-compatible, arrays run as one batched call, `dimensions` only where the model supports truncation ([reference](https://melious.ai/docs/reference/embeddings)).

**bge-m3 is offered**, id `bge-m3`, 8K context, 2 providers, €0.01 per 1M tokens ([hub](https://melious.ai/hub/models/bge-m3)). Melious publishes no dimension for it. BAAI's [model card](https://huggingface.co/BAAI/bge-m3) gives 1024, which matches `vector(1024)` in `0004_chunk_embeddings.sql`.

Other embedding models on the hub, none needed:

| Model id | Context | Input per 1M | Dimensions |
|---|---|---|---|
| [`qwen3-embedding-8b`](https://melious.ai/hub/models/qwen3-embedding-8b) | 32K | €0.01 | not listed |
| [`bge-multilingual-gemma2`](https://melious.ai/hub/models/bge-multilingual-gemma2) | 8K | €0.01 | not listed |
| [`multilingual-e5-large`](https://melious.ai/hub/models/multilingual-e5-large) | 514 | €0.03 | 1024 |
| [`multilingual-e5-large-instruct`](https://melious.ai/hub/models/multilingual-e5-large-instruct) | 512 | €0.03 | 1024 |
| [`paraphrase-multilingual-mpnet`](https://melious.ai/hub/models/paraphrase-multilingual-mpnet) | 128 | €0.01 | not listed |

Two cautions for this repo:

- **Not normalised.** Embeddings are "generally not normalized to unit length" ([reference](https://melious.ai/docs/reference/embeddings)). That is safe here: search uses pgvector's cosine distance (`store/chunks.py`) and Support scales Vectors to length 1 (`asking/answers.py`).
- **Same name, shared Vectors.** Vectors are stored per model name, and both call it `bge-m3`, so a Seed embedded on one is queried on the other. They were measured to agree closely ([Probed with a key](#probed-with-a-key-2026-11-03)); `support_threshold` (0.7, set by an eval on Ollama) has not been re-evaluated on Melious's.

## Models

Melious keeps no static list in its docs; the hub and `GET /v1/models?include_meta=true` are the sources, the latter behind a key ([models](https://melious.ai/docs/concepts/models)). `_meta` carries `capabilities`, `context_length`, `max_output_tokens`, `reasoning_type` and `pricing` ([models reference](https://melious.ai/docs/reference/models)).

**Gemma:** two Gemma 4 models, neither the local one. The tower runs a 12B community fine-tune (`CHAT_BASE` in `scripts/setup-tower.sh`) at 73,728 context. Both below are larger, with far more context.

Chat models with tools, from the data embedded in each [hub page](https://melious.ai/hub/models). All of these list `function_calling`, `streaming` and `structured_output` as true; `json_schema` is not on the hub, so it needs the keyed listing. Prices are the default `balanced` route, € per 1M input / output:

| Model id | Context | Reasoning | Providers | Price |
|---|---|---|---|---|
| **`gemma-4-31b`** | 256K | hybrid, default on | 4 | 0.20 / 0.35 |
| `gemma-4-26b-a4b` | 256K | hybrid, default on | 1 | 0.25 / 0.50 |
| `glm-5.3-flash` | 1M | reasoning | 1 | 0.10 / 0.40 |
| `glm-5.3` | 1M | hybrid | 1 | 1.00 / 3.00 |
| `deepseek-v4-flash-0731` | 1M | hybrid | 1 | 0.10 / 0.25 |
| `deepseek-v4.1-flash` | 1M | hybrid | 1 | 0.20 / 1.00 |
| `qwen3.6-35b-a3b` | 256K | hybrid | 1 | 0.25 / 1.50 |
| `gpt-oss-120b` | 128K | reasoning | 5 | 0.22 / 0.66 |
| `mistral-small-3.2-24b-instruct` | 131K | none | 4 | not read |
| `qwen3-235b-a22b-instruct` | 250K | none | 1 | not read |

`gemma-4-31b` is the nearest stand-in and has the most routes of the Gemma pair. The hub's `price` flavour gives it €0.10 / €0.30 with €0.02 cache reads, at about 126 t/s against 382 t/s on `balanced` ([hub](https://melious.ai/hub/models/gemma-4-31b)). Flavours are model-id suffixes such as `gemma-4-31b:price` ([routing](https://melious.ai/docs/concepts/routing)), so they stay a setting.

A listed model may not serve. In August a MeliousAI [fork of pi](https://github.com/MeliousAI/pi/blob/feat/melious-provider/packages/ai/scripts/generate-models.ts) recorded eight advertised models failing every request; `qwen3.8-max` and `llama-3.3-70b-instruct` were among them. The catalogue also moves: the [pricing page](https://melious.ai/pricing) counts 25 LLMs and 6 embedding models, the [hub](https://melious.ai/hub/models) 38 models in all, the marketing "60+".

## Limits, errors, cost

**Rate limits** ([rate limits](https://melious.ai/docs/concepts/rate-limits)): per account and endpoint path in a sliding 60 s window. 120 requests by default, 240 with a balance of 10 credits or more (cached for five minutes), raisable on request. `X-RateLimit-Limit`, `-Remaining`, `-Reset` and `Retry-After` report it. At batches of 32 that is ample for embedding.

**Timeouts:** no request or stream timeout is published. A 503 can carry the code `timeout` ([errors](https://melious.ai/docs/concepts/errors)). The keep-alive lines may reset a read timeout, so `PE_CHAT_TIMEOUT_S` might never fire on a stalled model; their interval is undocumented. Request bodies are capped at 25 MB ([models](https://melious.ai/docs/concepts/models)).

**Failover:** a provider failing before any output is retried on the next one, for one response and one charge; none after output has started ([routing](https://melious.ai/docs/concepts/routing), [reference](https://melious.ai/docs/reference/chat-completions)).

**Errors** ([errors](https://melious.ai/docs/concepts/errors)):

- The normal shape is OpenAI's: `{"error": {"message", "type", "param", "code"}}`. The request id is only in the `X-Request-ID` header.
- 400 with `model_not_capable` or `context_length_exceeded`; 401 `invalid_api_key`; 403 `auth_insufficient_scope` or `model_not_allowed`; 404 `model_not_found`; 503 `server_error`, `provider_error` or `timeout`.
- **A 429 has three shapes.** A throttle says `type: rate_limit_exceeded`. A billing refusal says `type: billing_error` and retrying is pointless, yet "The OpenAI SDKs retry every 429 and every 5xx twice by default". The account limiter is a flat body, `{"code": "RATE_LIMIT_EXCEEDED", "message", "details": {"retry_after"}}`, with no `error` key.
- **In-stream errors.** After the 200, failures arrive as `data: {"type":"error","error":{...}}`, then usually `[DONE]`. Balance, cap and context failures can all arrive this way. A stream ending without `[DONE]` counts as failed ([streaming](https://melious.ai/docs/guides/streaming)). The OpenAI SDK raises on a data frame with an `error` key (`openai/_streaming.py`).

**Cost** ([pricing](https://melious.ai/pricing), [concepts](https://melious.ai/docs/concepts/pricing)): pay as you go with euro credits, no subscription or minimum, prices net of VAT. Each response carries `billing_cost`, and a failed stream may charge without one. Explicit `cache_control` is dropped, but providers cache prefixes transparently and report `usage.cached_tokens` ([models](https://melious.ai/docs/concepts/models)). As rough arithmetic only, one 40k-token whole-Seed request on `gemma-4-31b` is about €0.008 of input at the `balanced` rate, before output and repeated tool steps. Embedding 1M tokens of bge-m3 is €0.01.

## Data handling

Paper text and Questions go to Melious and on to whichever provider serves the request.

- **Company and law:** Melious AI GmbH, Saarbrücken, HRB 111794 ([llms.txt](https://melious.ai/llms.txt)). German law; a merchant's venue is Melious's seat, a consumer's the statutory one ([terms](https://melious.ai/legal/terms)). The DPA names Cologne as its own venue (clause 11.5, [DPA](https://melious.ai/legal/dpa)).
- **Where:** EU/EEA only, with no US or APAC fallback ([privacy](https://melious.ai/docs/concepts/privacy)). The DPA says "in principle" and permits third-country transfers under conditions (clauses 1.3, 6.4, [DPA](https://melious.ai/legal/dpa)). The listed inference providers are all in the EU ([subprocessors](https://melious.ai/legal/subprocessors)).
- **Training:** none, and it is a condition for being a provider ([privacy](https://melious.ai/docs/concepts/privacy)). The Terms exclude user content from usage statistics (§ 1(4), [terms](https://melious.ai/legal/terms)).
- **Logging at Melious:** operational metadata only (request id, token counts, model, provider, cost), no message text ([privacy](https://melious.ai/docs/concepts/privacy); DPA Annex 1 agrees).
- **Retention at providers is weaker than the headline.** Contractual Zero Data Retention (clause 3.6) covers only Melious's own servers, and on Verda and OVH only its dedicated hardware: "This clause does not apply to other subprocessors" ([DPA](https://melious.ai/legal/dpa), Annex 4). The docs allow that a provider "may keep short-lived operational logs" with no stated length ([privacy](https://melious.ai/docs/concepts/privacy)). Capacity overflow goes to subprocessors unless disabled per key on instruction ([DPA](https://melious.ai/legal/dpa)). No way to restrict a request to ZDR hardware is documented.
- **"End-to-end encrypted"** is a claim in [llms.txt](https://melious.ai/llms.txt) that the docs do not explain. A provider running the model has to see the text, so read it as encryption in transit until shown otherwise (my inference).
- **Provider lists differ:** 8 in the [docs](https://melious.ai/docs/concepts/providers), 10 inference providers in DPA Annex 4, 11 on the [subprocessors page](https://melious.ai/legal/subprocessors).
- **Stored content:** the API is stateless apart from Files and Batches, which PaperExplorer would not use ([privacy](https://melious.ai/docs/concepts/privacy)).

Three clauses to read before sending papers (not legal advice):

- The user warrants holding, or being authorised to use, the rights to content they upload (§ 6(1), [terms](https://melious.ai/legal/terms)). Paper text is usually someone else's copyright.
- Personal data of third parties requires registration, a concluded DPA and a legal basis (§ 5(2), [terms](https://melious.ai/legal/terms)). Author names in papers may count.
- The pricing page says the offer is "intended exclusively for businesses" under § 14 BGB ([pricing](https://melious.ai/pricing)), though the Terms also provide for consumers. Whether a private account is eligible is unclear.

## Deviations from the OpenAI API

Documented, unless marked:

- Open-weight models only; OpenAI names are not mapped ([From OpenAI](https://melious.ai/docs/get-started/openai-sdk)).
- `stream_options.include_usage` defaults to true here, false at OpenAI ([streaming](https://melious.ai/docs/guides/streaming)).
- Extra response fields `environment_impact` and `billing_cost`; `_meta` on models via `?include_meta=true` ([reference](https://melious.ai/docs/reference), [models reference](https://melious.ai/docs/reference/models)).
- `reasoning_content`, `top_k`, `min_p`, `chat_template_kwargs`, and `usage.cached_tokens` and `usage.reasoning_tokens` at the top level ([reference](https://melious.ai/docs/reference/chat-completions)).
- `finish_reason` may also be `blocked` or `blueprint_override` ([reference](https://melious.ai/docs/reference/chat-completions)).
- Non-streamed JSON modes pass through a repairer, and an unrecoverable result is returned as original text or an empty string, never an error ([structured outputs](https://melious.ai/docs/guides/structured-outputs)).
- Model-id suffixes route the request ([routing](https://melious.ai/docs/concepts/routing)).
- The flat 429 and in-stream errors above; a billing refusal is a 429 here and a 403 on Responses and Messages ([errors](https://melious.ai/docs/concepts/errors)).
- `developer` messages are accepted as `system` ([reference](https://melious.ai/docs/reference/chat-completions)), though the August pi fork recorded a rejection. pydantic-ai's profile here sends `system`, so it does not matter.

## Open questions

These need a key, except the last two.

1. Does `reasoning_effort: "none"` give `reasoning_tokens: 0` and no `reasoning_content` on `gemma-4-31b`? If not, does `chat_template_kwargs` do better? PaperExplorer's no-tools request depends on this.
2. Which field carries streamed thinking, and does `gemma-4-31b` put chat-template tokens in `content`? `asking/draft.py` assumes things about gemma's channel tokens that were learned on Ollama.
3. Does `bge-m3` return 1024 dimensions, and how close are its vectors to Ollama's for the same text? This decides whether Seeds must be re-embedded and whether `support_threshold` holds.
4. Does `gemma-4-31b` report `json_schema` and `function_calling` in the keyed listing, and does it actually serve tool calls under `tool_choice`?
5. How often do keep-alive lines come, and how does `PE_CHAT_TIMEOUT_S` behave on a stall?
6. Does the tool schema change between `paperexplorer-chat` and `gemma-4-31b` under `OllamaProvider`? Checkable offline.
7. For the user: may a private account use it, and is sending paper text and author names acceptable under the three clauses above? Does Answer quality on Melious's Gemma justify an eval, since the local model is a different fine-tune?

## Caveats

- The forks and models.dev are not Melious's documentation. The `MeliousAI/*` branches are the vendor organisation's own contributions from August 2026 and models.dev is third party. Both are used here only for live behaviour the docs omit, and each is flagged.
- Hub figures come from data embedded in the pages, read once; prices and speeds change. `_meta.pricing` gives the lowest rate across providers, not the rate a request pays ([models reference](https://melious.ai/docs/reference/models)).
- The privacy policy's body is in German, and the German text of the legal pages binds.
