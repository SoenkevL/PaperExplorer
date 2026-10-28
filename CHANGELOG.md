# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Chunks: `chunking.chunk` cuts a parsed Work's paragraphs and footnotes into Chunks of at most 400 bge-m3 tokens at sentence borders, windows of one paragraph sharing a sentence, flagged `short` or `oversized_sentence`; migration 0003 with the `chunks` table and its English full-text index; the `Chunks` repository; the `chunk` Stage (`paperexplorer run chunk <zotero_key>`), which tower-check runs on the Seed.
- Tracing: `tracing.configure_tracing` sends traces to the tower's Langfuse, or to Langfuse Cloud when the tower does not answer; `Tracing.trace` opens a trace whose name, session, tags and metadata reach every model call inside it. `paperexplorer trace-check` sends one and says where it went.
- `compose.langfuse.yaml`: Langfuse v4.71.0 on the tower, about 6 GB of RAM, its project initialised with the `PE_LANGFUSE_*` keys; the tower wizard sets it up as stage 8.
- `scripts/langfuse_prune.py` keeps the tower's traces under 6 GB on disk, oldest first; `scripts/sync_langfuse.py` copies a time window of traces and scores from Langfuse Cloud to the tower.
- Design for asking the Seed Questions: `docs/design-asking.md`, ADR 0008 (a model may answer; words count as the authors' only when they match exactly), ADR 0009 (pydantic-ai), the Conversation terms in `CONTEXT.md`, and point 6 of v1's definition of done.
- Repo skeleton: uv workspace with `core`, `api` and `web` packages; ruff, pyright strict, pytest, pre-commit.
- Settings (`PE_*` env), logging setup (text/JSON), `paperexplorer` CLI with `version`.
- SQL migration runner with pgvector migration; store tests on real Postgres via testcontainers.
- API `/health` with typed client; web index page showing API status.
- `compose.yaml` for the tower (Postgres, API, web, pipeline; Ollama runs natively on the host) and Dockerfiles.
- `scripts/setup-mac.sh`: interactive Mac setup (connect the GitHub repo and open the first PR, uv, pre-commit, Docker + tests, Zotero key).
- `scripts/setup-tower.sh`: tower setup driven from the Mac over SSH (tools, clone, Ollama + bge-m3, Zotero storage and Seed PDF, `.env`, compose stack, tests, VS Code Remote-SSH).
- Design docs: `CONTEXT.md`, `CLAUDE.md`, architecture, ADRs 0001–0005, logbook.
- Parsing: `parse(pdf, settings)` turns a PDF into blocks, References and Citation markers with the text exactly as printed: GROBID for structure, the PDF's text layer (pdfium) for wording and link targets, LiteParse OCR for scans (ADR 0006).
- Citations: `extract(doc)` links each Citation to its Reference with a confidence and evidence, builds Citing Sentences, and reports Unlinked and low-confidence Citations.
- `paperexplorer parse <pdf>` prints what a PDF parses into; compose `grobid` service; the pipeline image ships Tesseract's English model.
- CC BY test fixtures with recorded GROBID output, a synthetic scan, and a live GROBID check.
- `scripts/tower-check.sh`: checks a branch on the tower (gate, GROBID, tower tests, the Seed, pipeline image with OCR) and logs it for review from the Mac.
- The Seed in the browser: every Citation is a link that opens a side panel with its Reference as printed, its Citation Link's confidence and evidence, and its Citing Sentence; low-confidence and Unlinked Citations and OCR Text are marked.
- Store: migration 0002 with Works (Library Item snapshot, ADR 0007), blocks, References, Citations, Citation Links and per-Work Stage status; repositories `Works`, `WorkTexts`, `CitationGraphs`, `StageStatuses`.
- Library port with `InMemoryLibrary` and a Zotero Web API adapter (PDFs from Zotero storage, else downloaded once).
- Pipeline Stages `fetch`, `parse` and `extract_citations`, re-runnable with their status recorded; `paperexplorer migrate` and `paperexplorer run <stage> <zotero_key>`.
- API: `GET /works`, `GET /works/{id}` (blocks with Citation spans as character offsets) and `GET /citations/{id}`, with a typed client.
- `scripts/check_reader.py`, run by tower-check: the running reader links every Citation of the Seed.

### Fixed
- An optional folder left empty in `.env` (`PE_ZOTERO_STORAGE_DIR=`, `PE_TESSDATA_DIR=`) read as the current folder; it is now unset.
- Parsing: a Citation whose author part and year are linked separately in the PDF (LaTeX natbib) became two Citations of one Reference; it is now one (Wegner fixture: 311 → 153 Citations).
- Parsing: numbers beside an author-year bibliography (margin line numbers, the end of the column to the left) were read as Reference labels; labels are now read only where they number the bibliography.
- Parsing: a one-line bibliography entry that GROBID merged with the next one was not split; the split now also starts an entry at a margin line followed by indented lines, and the first part keeps only its own authors (the Seed: 184 → 185 References).
- Citations: a Citation whose GROBID link points at an entry by other authors was linked to it at low confidence (the Seed: six `Carhart-Harris, 2025` Citations shown with Huxley 1959); GROBID's link now counts only when the entry's first author is one of the surnames the marker prints, else the Citation is Unlinked.
- The `api` and `web` compose services have healthchecks, so `up --wait` and `web` wait until uvicorn answers; the reader check no longer meets a just-started container dropping connections.
