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
