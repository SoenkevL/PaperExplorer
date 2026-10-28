# PaperExplorer

Read a paper by following its citations into the actual words of the works it cites: click `[12]` and see the passages of Work 12 that the citing sentence most likely relies on. Ask the paper questions in a sidebar: a local model answers, every claim points back to the sentence it rests on, and only words that match the authors' text exactly are shown as theirs.

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
ollama pull hf.co/yuxinlu1/gemma-4-12B-agentic-fable5-composer2.5-v2-3.5x-tau2-GGUF:Q6_K
ollama create paperexplorer-chat -f scripts/paperexplorer-chat.Modelfile   # the chat model
export PE_LANGFUSE_RELEASE=$(git rev-parse --short HEAD)   # the containers have no git
docker compose up -d        # db, grobid :8070, api :8000, web :8080
docker compose run --rm pipeline migrate
for stage in fetch parse chunk embed extract_citations; do
  docker compose run --rm pipeline run "$stage" YAAC9XCX   # the Seed's Zotero item key
done
```

Then open `http://tower:8080`: the Seed's page links every Citation to a panel with its Reference, and its sidebar (a drawer on narrower screens) answers Questions about it; a chip after a sentence of an Answer scrolls the reader to the sentence that bears it out.

Search the Seed's Chunks (hybrid: bge-m3 Vectors and English full text, fused by RRF):

```sh
docker compose run --rm pipeline search YAAC9XCX "How was brain entropy measured?" -k 8
```

Ask the Seed a Question: the answering agent streams its draft, then lists the Answer's Pointers and Quotes:

```sh
uv run paperexplorer ask YAAC9XCX "What are the shortcomings of this work?"
```

Or over the running API, as the web app will: start a Conversation about a Work, then stream a Question's events (`step`, `delta`, then `answer` or `error`):

```sh
curl -X POST localhost:8000/works/<work_id>/conversations          # {"id": "<conversation_id>"}
curl -N -X POST localhost:8000/conversations/<conversation_id>/questions \
  -H 'content-type: application/json' -d '{"text": "What are the shortcomings of this work?"}'
```

Tower-only tests (real Zotero storage, GROBID, the Seed) run there with `uv run pytest -m tower`.
`scripts/tower-check.sh` runs those, the gate, the Seed and the pipeline image in one go and writes `tower-check.log` to commit back to the branch.
