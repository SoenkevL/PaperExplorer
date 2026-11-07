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
- Before PR 1, `run_chain` ran `fetch → parse → chunk → embed → extract_citations → verify_dois → resolve` and stopped at the first failure, yet `extract_citations` reads only `parse`'s text, so a hosted Provider's rate limit on `embed` blocked every Source from being filed.
- The Stages branch after `parse` by what each reads: `chunk → embed`, `extract_citations`, and `verify_dois → resolve` (References come from `parse`'s text, so `resolve` does not read `extract_citations`). Resolving References needs only `parse` done. A failure ends its own branch only. Each Stage names the Stages it reads, and a Stage's success makes pending only those that read it, so a rerun `embed` or `extract_citations` no longer makes `resolve` pending.
- The pips keep their order. *Resume* and `remaining` run every Ingesting Stage not done with those downstream of it; a re-run from the row runs its Stage and the Ingesting Stages downstream of it; *Resolve References* is offered once the Work is parsed.

## The PaperExplorer Collection
- On first use: a top-level Collection named exactly `PaperExplorer` is adopted if there is one, else one is created. Its key is stored. The guard checks its name too, so renaming it in Zotero stops every write with an error until it is named `PaperExplorer` again; Exploration Collections may be renamed freely. The store's Collections, synced just before, say which top-level Collections there are.
- Two top-level Collections so named: PaperExplorer refuses to choose and asks the reader to rename one.
- Deleted or trashed in Zotero: the sync marks every Exploration under it gone, and nothing is recreated silently. The next *Start Exploration* makes a fresh one.

## Filing
- **Which Sources.** A Reference's Work is filed when its Resolution is firm (`Resolution.firm`: certain or high, or a Reader's Choice of a Work). A low match or DOI Conflict waits for the reader's choice. Of Duplicate Items, the Standing Item is filed. Versions are distinct Works, each filed if a Reference resolves to it.
- **New Library Items.** A firm Work with no Library Item gets one. The Web API cannot search by DOI ([research](research/2026-11-zotero-writes.md)), so the reconciler syncs first and looks the DOI up in `works`, whose Library DOIs now include a `DOI: …` line in `extra`. Only then is an item created, from the cached Crossref record for the DOI (`crossref:doi`), else OpenAlex's (`openalex:doi`), else the Work's row: item type (Crossref `journal-article` → `journalArticle`, `posted-content` → `preprint`, `book`, `book-chapter` → `bookSection`, `proceedings-article` → `conferencePaper`, else `document`; OpenAlex's types likewise), title, creators, date, DOI, journal, volume, issue, pages, URL, and `Added by PaperExplorer` in `extra`; no PDF. It is created already filed in the Exploration Collection. The new item's key goes on the Work's row and the next sync fills its metadata. A Source in Zotero's trash is not in `works`, so it gets a new item.
- **Creates run once.** Every Collection and item PaperExplorer creates gets its key made locally and stored before the request, then is posted with `"version": 0`: Zotero refuses a second create of that key (412), which counts as done, so a create whose answer was lost is never repeated as a duplicate. A Collection created is recorded in the store's Collections at once, so the next sync does not take it for one restored from the trash.
- **The reconciler** (`catalog.reconcile(conn, library, seed)`) is not a Stage. Runs take turns on a Postgres advisory lock per Exploration, held for the run. It compares the firm Sources and the Seed with the Filings PaperExplorer recorded, creates what is missing, files and unfiles to close the gap, and commits each Filing as its write lands, on a connection of its own. A failed write is recorded with its error and left for the next run. It runs after `resolve` commits, after each Registry Check, after each Reader's Choice, and on *Resume*. A run with nothing to change writes nothing.
- **Unfiling.** A Filing whose Source is no longer firm, or now names another Work, is unfiled. PaperExplorer unfiles only what it filed, and never deletes or trashes an item, created ones included.
- **The reader's edits in Zotero.** The sync sees a filed item leave the Exploration Collection and records the Filing as removed by the reader. The reconciler files it again only after a Reader's Choice picks that Work, made any time after it was filed (when the reader took it out is not known). Items the reader files there stay and are never unfiled.

## Ending
- Deleting or trashing an Exploration Collection in Zotero ends its Exploration at the next sync: its Filings go, and the next start or reconcile takes `pe:seed` off the Seed unless it is the Seed of another Exploration (the sync itself writes nothing). Created items stay. *Start Exploration* on that Seed makes a fresh one.

## The write guard
- The `Library` port gains only these writes, and `ZoteroLibrary` refuses anything else:
  - `create_paperexplorer_collection(key)`: the one top-level Collection, named `PaperExplorer`;
  - `create_collection(key, name, *, within)`: a Collection in the PaperExplorer Collection `within`;
  - `file(item, collection, *, within)` and `unfile(item, collection, *, within)`: the item's `collections` gains or loses that one key (`PATCH` replaces the whole array), read and written under `If-Unmodified-Since-Version`, a 412 re-read and retried once;
  - `create_item(key, new, *, collection, within)`: a new item filed in `collection`;
  - `tag_seed(item)` and `untag_seed(item)`: only the `pe:seed` tag, the item's other tags kept.
- Every write that names a Collection names the PaperExplorer Collection too (`within`), and the adapter asks Zotero: `within` must be a top-level Collection named `PaperExplorer`, outside the trash, and the target must have it as an ancestor (walked up to `COLLECTION_DEPTH`; what was read is trusted for a minute, so a rename, move or trash stops writes within one). A refusal is recorded on the Filing like any failed write. Otherwise `OutsidePaperExplorerError` is raised before any write. `merge` stays as it is: the reader confirms it.
- `InMemoryLibrary` keeps the same guard, so the core tests prove it.

## Store
- Migration `0010_explorations.sql`:
  - `paperexplorer_collection`: one row, its `key` and whether it was `created`.
  - `explorations (id, seed REFERENCES works, collection_key, name, created, seed_tagged, started_at, ended_at)`, one with no `ended_at` per Seed.
  - `filings (exploration REFERENCES explorations ON DELETE CASCADE, work REFERENCES works, item_key, state, error, updated_at, PRIMARY KEY (exploration, work))`, `state` one of `pending` (a key made, the write not confirmed), `filed`, `failed`, `removed_by_reader`.
- `store.Explorations`: the PaperExplorer Collection's key, `start`, `of_seed`, `filings`, `record_filing`, `forget_filing`, `end`.

## The API and the web app
- `POST /works/{id}/exploration` `{check: bool}` starts or resumes; `GET /works/{id}/exploration` returns its state: the Collection's key and name, Filings by state, firm Sources not yet filed, Sources awaiting a choice, Unchecked References, the Stages not done. `GET /works/{id}/exploration/proposal` names the Collection a start would make, for the dialog, since the web app imports no core. `ApiClient` mirrors them.
- The ticked Check runs on the Stage Run thread, as the Stage Run's last step (`StageRunThread.submit(..., then=)`), after its Stages however they went: it outlives a closed tab, and the row shows the run meanwhile instead of a stream of References. The thread reconciles as `resolve` commits (`after_stage`).
- The Work page and the row: *Start Exploration*, or *Exploration* with its state. The start dialog shows the Collection's name, the Check's cost with the box (before `parse`, it says the cost comes once the References are parsed), and starts. A partial Exploration names what is missing (`embed not done`, `1 Filing failed`) beside *Resume Exploration*, which stands in for the row's *Resume*.
- The landing-page tree marks each Exploration Collection, and the PaperExplorer Collection shows like any other.

## Testing
- `InMemoryLibrary` with the guard drives the reconciler: a Library Match filed, a Check's Work created once and filed, a DOI the Library holds (in `DOI` or in `extra`) not created, a low match not filed until chosen, a choice changed (unfiled, the new one filed), a reader's removal respected, a failed write retried next run, the guard refusing a Collection outside the branch.
- `ZoteroLibrary` over hand-written Web API JSON: collection create, item create, the `collections` patch with a 412, tag add and remove.
- The pipeline: `embed` failing leaves `resolve` run and the Sources filed; a rerun `embed` leaves `resolve` done.
- A `tower` test starts an Exploration on a scratch Seed in the real Library.

## Delivery
Three PRs into `dev`, each with its `CHANGELOG.md` and `docs/logbook.md` lines:
1. `feat/explorations-design`: this design, ADR 0014, CONTEXT.md's terms, `architecture.md`, and the Stage branches after `parse`.
2. `feat/exploration-filing`: migration 0010, the port's writes with the guard, both adapters, `catalog` (ensure the item, reconcile), the sync's part (a gone Exploration Collection, a reader's removal), `paperexplorer explore` and `exploration`.
3. `feat/exploration-ui`: the routes and client, the Work page and row, the start dialog with the Check, the tree mark.
