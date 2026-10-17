# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Repo skeleton: uv workspace with `core`, `api` and `web` packages; ruff, pyright strict, pytest, pre-commit.
- Settings (`PE_*` env), logging setup (text/JSON), `paperexplorer` CLI with `version`.
- SQL migration runner with pgvector migration; store tests on real Postgres via testcontainers.
- API `/health` with typed client; web index page showing API status.
- `compose.yaml` for the tower (Postgres, Ollama, API, web, pipeline) and Dockerfiles.
- `scripts/setup-mac.sh`: interactive Mac setup (GitHub repo and first PR, uv, pre-commit, Docker + tests, Zotero key).
- Design docs: `CONTEXT.md`, `CLAUDE.md`, architecture, ADRs 0001–0005, logbook.
