# Answers and Vectors may come from a hosted Provider

Until now a local model answered every Question, so paper text stayed on the tower. The tower's GPU limits which models fit and how much context they get. So a Provider, local or hosted, now runs both the chat model and the Embedder: `PE_PROVIDER` picks one from the table in `inference` and `PE_PROVIDER_API_KEY` is its key. Ollama stays the default; Melious is the first hosted Provider. This amends ADR 0008's "local model" (its rule on the authors' words stands) and ADR 0009's "a settings change": a new server is one entry in that table.

## Considered Options

- **Separate settings for each server (`PE_CHAT_BASE_URL` and the rest):** already there, but a switch took several lines, compose's URLs overrode `.env`, and a server's thinking field had no setting.
- **Chat hosted, embeddings local:** keeps every Vector and the Support threshold as they are, but every Question would still need Ollama.
- **A class per Provider:** more code per Provider, when the two so far differ only in values.

## Consequences

- With a hosted Provider, Seed text and Questions leave our machines for that Provider and whichever upstream serves it. Melious's zero-retention terms do not cover every upstream, and its terms have the user warrant rights to what is uploaded (`docs/research/2026-11-melious-api.md`).
- A hosted model is a different model: the prompts, `DraftCut` and the eval's numbers were made on the local one, and carry over only once an eval says so. Each trace and eval run records which model it used.
- A hosted Provider's Vectors are stored under a prefixed name (`melious/bge-m3`), so they are never searched with another Provider's. A switch needs `embed` run for each Work, and switching back needs nothing. Whether the two `bge-m3`s agree has not been measured; dropping the prefix is a one-value change once it has.
