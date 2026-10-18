# PaperExplorer

Read a paper by following its citations into the actual words of the works it cites: click `[12]` and see the passages of Work 12 that the citing sentence most likely relies on. Retrieval, not summarisation.

Start with `docs/design-v1.md` (what v1 is), `CONTEXT.md` (the language) and `docs/architecture.md` (how it is built). Decisions: `docs/logbook.md`.

## Develop (Mac or tower)

First time on the Mac: run `scripts/setup-mac.sh`. It walks you through GitHub, the Python environment, Docker and the Zotero key.


```sh
uv sync                     # Python 3.14 + all packages
uv run pre-commit install
uv run pytest               # needs Docker running (real Postgres via testcontainers)
uv run ruff check && uv run ruff format --check && uv run pyright
```

## Run (tower)

```sh
cp .env.example .env        # fill in secrets
docker compose up -d        # db, ollama, api :8000, web :8080
docker compose exec ollama ollama pull bge-m3
docker compose run --rm pipeline version
```
