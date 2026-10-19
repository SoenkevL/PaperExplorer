# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
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
