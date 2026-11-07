# Explorations in Zotero: settled design (grilling outcome)

## Context
Resolving References matches a Seed's References to Works and verifies their DOIs, but nothing reaches Zotero yet. CONTEXT.md defines an Exploration as mirrored by a Collection, and `architecture.md` plans `catalog`, but neither is built. Five grilling rounds on 2026-11-07 settled the first prototype: the reader starts an Exploration on a Work, its Exploration Collection appears in the PaperExplorer Collection, and its firmly resolved Sources are filed there. The reader's other Collections are never changed. ADR 0014 records the write boundary and the reconciler.

## Scope and definition of done
- In scope: starting an Exploration, the PaperExplorer Collection, Exploration Collections, creating metadata-only Library Items for Sources, Filing and Unfiling, `pe:seed`, the Stage branches after `parse`, the CLI, the API, the Work page and a mark in the landing-page tree.
- Out of scope: fetching Sources' PDFs, Text Availability tags (`pe:fullText`, `pe:abstractOnly`, `pe:noSource`), ending an Exploration from PaperExplorer, Explorations of Sources (one level deeper), widening Scope to the Exploration.
- Done when:
  1. `paperexplorer explore <key>` and the Work page's *Start Exploration* create one Exploration Collection in the PaperExplorer Collection, file the Seed with `pe:seed`, and run every Stage not done.
  2. Every firm Source is filed: a Library Match as it is, a Work found by a Registry Check through a new Library Item, never a duplicate of one the Library holds.
  3. Filing follows Resolutions after `resolve`, a Registry Check and a Reader's Choice; a Source no longer firm is unfiled if PaperExplorer filed it.
  4. A failed Stage or Zotero write leaves the Exploration partial, shown as such, and *Resume* finishes only what is missing.
  5. No write reaches a Collection outside the PaperExplorer Collection, or any field of an item but its `collections` (one key at a time) and the `pe:seed` tag; a test proves the guard refuses.

## Starting an Exploration
- Offered on any Work with a PDF (a Seed always has full text). A Seed has at most one Exploration; *Start Exploration* on a Seed that has one opens it.
- The start does, in order, each step kept as it lands:
  1. Find the PaperExplorer Collection (below).
  2. Create the Exploration Collection in it, named `<first author surname> <year> – <title>`, the title cut at 60 characters on a word. PaperExplorer keeps it by key, so the reader may rename it in Zotero.
  3. File the Seed and add `pe:seed` to it.
  4. Queue the Stages not done, as a Stage Run, and reconcile as `resolve` commits.
- The start dialog offers a Registry Check of the Unchecked References with its cost (as the Work page's button shows it), ticked by default. Ticked, the Check runs after `resolve`; a spent budget stops it as usual, and what it resolved is filed. ADR 0011 holds: the reader asks by leaving it ticked.
- The CLI: `paperexplorer explore <key> [--check]` starts or resumes; `paperexplorer exploration <key>` prints its state.

## The Stages branch after `parse`
- Today `run_chain` runs `fetch → parse → chunk → embed → extract_citations → verify_dois → resolve` and stops at the first failure, yet `extract_citations` reads only `parse`'s text. A hosted Provider's rate limit on `embed` would block every Source from being filed.
- The Stages branch after `parse` by what each reads: `chunk → embed`, `extract_citations`, and `verify_dois → resolve` (References come from `parse`'s text, so `resolve` does not read `extract_citations`). Resolving References needs only `parse` done. A failure ends its own branch only. Each Stage names the Stages it reads, and a Stage's success makes pending only those that read it, so a rerun `embed` or `extract_citations` no longer makes `resolve` pending.
- The pips keep their order. *Resume* and `remaining` run every Ingesting Stage not done with those downstream of it; a re-run from the row runs its Stage and the Ingesting Stages downstream of it; *Resolve References* is offered once the Work is parsed.

## The PaperExplorer Collection
- On first use: a top-level Collection named exactly `PaperExplorer` is adopted if there is one, else one is created. Its key is stored; from then on the name does not matter.
- Two top-level Collections so named: PaperExplorer refuses to choose and asks the reader to rename one.
- Deleted or trashed in Zotero: the sync marks every Exploration under it gone, and nothing is recreated silently. The next *Start Exploration* makes a fresh one.

## Filing
- **Which Sources.** A Reference's Work is filed when its Resolution is firm (`Resolution.firm`: certain or high, or a Reader's Choice of a Work). A low match or DOI Conflict waits for the reader's choice. Of Duplicate Items, the Standing Item is filed. Versions are distinct Works, each filed if a Reference resolves to it.
- **New Library Items.** A firm Work with no Library Item gets one (`catalog.ensure_item`): the works table by DOI, arXiv ID and OpenAlex ID, then a Zotero search by DOI, then a create from the cached Crossref or OpenAlex record (item type, title, creators, date, DOI, publication, URL; no PDF), with `Added by PaperExplorer` in `extra`. The new item's key goes on the Work's row and the next sync fills its metadata. It sits in My Library and in the Exploration Collection only.
- **The reconciler** (`catalog.reconcile(conn, library, exploration)`) is not a Stage. It compares the firm Sources and the Seed with the Filings PaperExplorer recorded, creates what is missing, files and unfiles to close the gap, and commits each Filing as its write lands, on a connection of its own. A failed write is recorded with its error and left for the next run. It runs after `resolve` commits, after each Registry Check, after each Reader's Choice, and on *Resume*. A run with nothing to change writes nothing.
- **Unfiling.** A Filing whose Source is no longer firm, or now names another Work, is unfiled. PaperExplorer unfiles only what it filed, and never deletes or trashes an item, created ones included.
- **The reader's edits in Zotero.** The sync sees a filed item leave the Exploration Collection and records the Filing as removed by the reader. The reconciler files it again only after a Reader's Choice picks that Work. Items the reader files there stay and are never unfiled.

## Ending
- Deleting or trashing an Exploration Collection in Zotero ends its Exploration at the next sync: its Filings go, and `pe:seed` comes off the Seed unless it is the Seed of another Exploration. Created items stay. *Start Exploration* on that Seed makes a fresh one.

## The write guard
- The `Library` port gains only these writes, and `ZoteroLibrary` refuses anything else:
  - `ensure_root() -> str`, `create_collection(name, parent)` with `parent` the PaperExplorer Collection;
  - `file(item, collection)` and `unfile(item, collection)`, `collection` in the PaperExplorer Collection's branch: the item's `collections` gains or loses that one key, read and written under `If-Unmodified-Since-Version`, a 412 re-read and retried once;
  - `create_item(metadata) -> str`;
  - `tag_seed(item)` and `untag_seed(item)`: only the `pe:seed` tag.
- The branch is read from the store's Collections at the moment of the write; a key outside it raises `OutsidePaperExplorerError` before any request. `merge` stays as it is: the reader confirms it.
- `InMemoryLibrary` keeps the same guard, so the core tests prove it.

## Store
- Migration `0010_explorations.sql`:
  - `paperexplorer_collection (key text PRIMARY KEY)`: one row.
  - `explorations (id bigint PRIMARY KEY, seed bigint UNIQUE REFERENCES works, collection_key text NOT NULL, started_at, ended_at)`.
  - `filings (exploration bigint REFERENCES explorations ON DELETE CASCADE, work bigint REFERENCES works, item_key text, state text, error text, updated_at, PRIMARY KEY (exploration, work))`, `state` one of `filed`, `failed`, `removed_by_reader`.
- `store.Explorations`: `start`, `of_seed`, `filings`, `record_filing`, `end`.

## The API and the web app
- `POST /works/{id}/exploration` `{check: bool}` starts or resumes; `GET /works/{id}/exploration` returns its state: the Collection's key and name, Filings by state, firm Sources not yet filed, Sources awaiting a choice, Unchecked References, the Stages not done. `ApiClient` mirrors them.
- The Work page and the row: *Start Exploration*, or *Exploration* with its state. The start dialog shows the Collection's name, the Check's cost with the box, and starts. A partial Exploration names what is missing (not embedded, 3 Filings failed) beside *Resume*.
- The landing-page tree marks each Exploration Collection, and the PaperExplorer Collection shows like any other.

## Testing
- `InMemoryLibrary` with the guard drives the reconciler: a Library Match filed, a Check's Work created once and filed, a duplicate DOI found by search not created, a low match not filed until chosen, a choice changed (unfiled, the new one filed), a reader's removal respected, a failed write retried next run, the guard refusing a Collection outside the branch.
- `ZoteroLibrary` over hand-written Web API JSON: collection create, item create, the `collections` patch with a 412, tag add and remove.
- The pipeline: `embed` failing leaves `resolve` run and the Sources filed; a rerun `embed` leaves `resolve` done.
- A `tower` test starts an Exploration on a scratch Seed in the real Library.

## Delivery
Three PRs into `dev`, each with its `CHANGELOG.md` and `docs/logbook.md` lines:
1. `feat/explorations-design`: this design, ADR 0014, CONTEXT.md's terms, `architecture.md`, and the Stage branches after `parse`.
2. `feat/exploration-filing`: migration 0010, the port's writes with the guard, both adapters, `catalog` (ensure the item, reconcile), the sync's part (a gone Exploration Collection, a reader's removal), `paperexplorer explore` and `exploration`.
3. `feat/exploration-ui`: the routes and client, the Work page and row, the start dialog with the Check, the tree mark.
