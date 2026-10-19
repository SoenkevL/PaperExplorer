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

Parsing a new PDF needs GROBID (tests use recorded output and start their own):

```sh
docker run -d --rm -p 127.0.0.1:8070:8070 grobid/grobid:0.9.1-crf
uv run paperexplorer parse path/to/paper.pdf
```

## Run (tower)

First time: run `scripts/setup-tower.sh` from the Mac. It checks and sets up the tower over SSH.

```sh
cp .env.example .env        # fill in secrets
ollama pull bge-m3          # Ollama runs natively on the tower
docker compose up -d        # db, grobid :8070, api :8000, web :8080
docker compose run --rm pipeline migrate
for stage in fetch parse extract_citations; do
  docker compose run --rm pipeline run "$stage" YAAC9XCX   # the Seed's Zotero item key
done
```

Then open `http://tower:8080`: the Seed's page links every Citation to a panel with its Reference.

Tower-only tests (real Zotero storage, GROBID, the Seed) run there with `uv run pytest -m tower`.
`scripts/tower-check.sh` runs those, the gate, the Seed and the pipeline image in one go and writes `tower-check.log` to commit back to the branch.
