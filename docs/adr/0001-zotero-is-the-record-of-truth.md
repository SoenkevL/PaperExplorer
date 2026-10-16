# Zotero is the record of truth; Postgres holds only derived data

Bibliographic metadata, PDFs and Exploration collections live in Zotero, written through its Web API; Postgres holds Chunks, embeddings, References, Citations, Retrieval Runs and pipeline status, plus a metadata snapshot (`synced_at`) for filtering. Zotero is already the reader's reference manager, so a second authoritative store would drift. When the two disagree, Zotero wins and a `sync` refreshes the snapshot.

## Consequences

- Zotero's API does not deduplicate on create, so Postgres keeps an identifier table (DOI / arXiv / OpenAlex → Zotero key) checked before every create, with a Zotero search as final check.
- Everything in Postgres and `data/cache/` must be rebuildable from Zotero plus the pipeline.
