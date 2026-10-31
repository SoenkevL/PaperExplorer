# A Question is answered from the whole Seed when it fits; search only beyond it, and no tools in it

Until now every Question was answered by retrieval: the Seed's map in context, a forced search, then up to six requests with five reading tools. The eval of 2026-10-29 ([report](../eval-2026-10-29.md)) also ran a baseline with the whole Seed in context, and after the fix of run 2 the baseline did better on every measure:

| | Retrieval | Whole Seed |
|---|---|---|
| Judged grounded and correct | 68% (15 of 22) | 86% (19 of 22) |
| Pointer coverage (covered Questions) | 0.55 | 0.67 |
| Latency, mean | 36.4 s | 22.6 s |
| First draft text, median | 16.3 s | 8.2 s |
| Model requests, tool calls (mean) | 3.7, 3.0 | 1.0, 0.0 |

The Seed's body is 18,380 tokens for the chat model, a quarter of its 73,728-token context. Retrieval pays off only for text that does not fit.

So a Question reads the Seed whole when the Seed fits, and by retrieval otherwise (`AskingContext.reading`, `auto` by default). The Seed fits when the sum of its Chunks' bge-m3 tokens is at most `PE_WHOLE_SEED_MAX_TOKENS`, 40,000 by default and validated to stay below `PE_CHAT_NUM_CTX`. The rest of the context is left for gemma counting more tokens than bge-m3, the system prompt, the Conversation, and the model's thinking and Answer. The whole-Seed request is the measured baseline, unchanged: the system prompt, the note that no tools apply, every Chunk under its handle, no tools and no search, and the note that it has no tools at the end of the request. Its prompt version stays `19164886ec73`, so what goes live is what scored 86%.

## Considered Options

- **Retrieval always (as before):** worse and slower on the eval, by every measure above.
- **The whole Seed with its reading tools,** so that the model could still look up a Reference or a section: rejected. Retrieval's misses were its reading habit (10 of 22 turns used all six requests), and the baseline made no tool calls once its note came last. Tools would bring that habit back, and they were not measured.
- **The whole Seed always:** a Seed with its Sources, or a long book, does not fit the context; retrieval stays for them.
- **The References in context too:** about 185 more entries for the Seed, not measured; left out for now.

## Consequences

- **The Seed's References are not in context.** A Question about a numbered Reference, "what is [12]?", gets no answer from the printed bibliography while the Seed is read whole. The reader shows every Reference one click away, in the Citation panel.
- **Retrieval stays as the fallback,** for a Seed over the budget now and for Sources later, when an Exploration's text no longer fits. Its tools and forced search are unchanged.
- **The eval asks in two modes:** `live` (`auto`, as the reader asks) and `retrieval` (forced), so the fallback stays measured; the bar is checked on `live`, and hit@8 comes from the forced search.
- **A whole-Seed turn runs no search,** so the sidebar shows no step before the first draft (about 8 s); it says it is waiting instead. Its trace is tagged `whole-seed` and carries the metadata `reading`.
