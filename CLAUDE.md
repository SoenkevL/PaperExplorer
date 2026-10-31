# PaperExplorer

A reader for following a paper's citations into the cited Works' own words. Text shown as the authors' is their text exactly as extracted; a local model may write Answers to the reader's Questions, and its words count as the authors' only where they match exactly (ADR 0008).

## Read first

- `CONTEXT.md`: the domain language. Name code, tables, routes and commits with its terms. When a term you need is missing or clashes with it, pause and settle it with the user (see Agent skills) before coding.
- `docs/architecture.md`: packages, modules, seams, pipeline stages, API contract, testing rules. Check it before adding a module, a dependency or a seam.
- `docs/adr/`: decisions that are hard to reverse. Reversing one is the user's call.
- `docs/design-v1.md`: v1 scope and its definition of done.
- `docs/design-asking.md`: asking the Seed Questions (Pointers, Quotes, the agent, tracing, eval). Read it before touching `asking`, `tracing` or the Conversation routes.

## Workflow

- Design happens with the user, in plan mode, by grilling (see Agent skills). Implementation runs autonomously; check in at milestones and whenever intent is unclear.
- Work on `feat/<slug>` branched from `dev`, then open a PR into `dev` on GitHub (remote `origin`, via `gh pr create --base dev`). Only the user merges and promotes `dev` → `main`; push only to your feature branch.
- Every PR carries: a `CHANGELOG.md` line under *Unreleased* (Keep a Changelog), a `docs/logbook.md` line for any key decision, and an ADR when a decision is hard to reverse, surprising and a real trade-off.
- Build test-first. A PR opens only on a green gate: `ruff check`, `ruff format --check`, `pyright`, `pytest`.

## Agent skills

Before the first edit, match the request to one line below and call the Skill tool with the full name. A `/command` is one only the user can run: offer it, don't skip it.

- **New feature or behaviour change**: grill in plan mode: call the Skill tool for `mattpocock-skills:grilling` and `mattpocock-skills:domain-modeling` (together, `/grill-with-docs`). Done when the frontier is empty and the user confirms. Spans sessions → offer `/to-spec`, then `/to-tickets`.
- **Building it**: `mattpocock-skills:tdd`; seams confirmed with the user before the first test.
- **Something broken, failing or slow**: `mattpocock-skills:diagnosing-bugs`; a red loop before any hypothesis.
- **A module's shape, a seam, a new package**: `mattpocock-skills:codebase-design`.
- **A term missing from or clashing with `CONTEXT.md`, or an ADR**: `mattpocock-skills:domain-modeling`.
- **A design question hard to settle on paper**: `mattpocock-skills:prototype`.
- **Facts from outside docs or APIs**: `mattpocock-skills:research`, findings in `docs/research/`.
- **Before opening a PR**: `mattpocock-skills:code-review` against `dev`.
- **Mid-merge or rebase conflict**: `mattpocock-skills:resolving-merge-conflicts`.
- **A step only the user can take** (tower, Zotero, Langfuse keys): `mattpocock-skills:wizard`.
- **Editing CLAUDE.md, a skill, or a doc agents read**: `mattpocock-skills:writing-for-agents`.
- After a milestone merges, offer `/improve-codebase-architecture`.

## Code

- Python 3.14, uv workspace. Pydantic models for every value crossing a module interface; pyright strict stays clean.
- One-line docstrings on public functions and classes; comments only for the *why*.
- SQL is hand-written in `store` (ADR 0003). Migrations are numbered `.sql` files and append-only: change the schema with a new file.
- Configuration comes from `config.Settings` (pydantic-settings); secrets come from `.env` only, with every key mirrored in `.env.example`.
- Logging: `logging.getLogger(__name__)`. INFO for stage start/finish with counts, WARNING for low-confidence Resolutions and missing text, DEBUG for detail. Console output belongs to the CLI layer.
- Data lives in top-level `data/` (gitignored); test inputs in `tests/fixtures/`. The seed paper's PDF stays out of git.

## Tests

- Postgres is real in every run: testcontainers starts `pgvector`, so Docker must be running (ADR 0005).
- `Library` and `Embedder` use their in-memory adapters; HTTP comes from hand-written fixtures or recorded cassettes.
- A failure seen in real use becomes a named regression test with its recorded response, committed before the fix.
- `@pytest.mark.tower` marks tests needing Ollama, the real Zotero account or the GPU; they run on the tower only.

## Machines

- Mac: editing, Claude Code, the full test suite.
- Tower (pop-os, 4060 Ti): `compose.yaml` services (Postgres, API, web) and Langfuse (`compose.langfuse.yaml`, UI on port 3000); Ollama and Zotero desktop run natively. Code is edited there through VS Code Remote-SSH (`ssh tower`). Postgres is reached only through an SSH tunnel.
