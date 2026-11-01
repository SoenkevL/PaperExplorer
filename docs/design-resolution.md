# Resolving References: settled design (grilling outcome)

## Context
Every Reference of a Work is matched to the Work it names, fast and without exhausting the registries' limits. Six grilling rounds (2026-10-28 to 2026-11-01) settled the design below; the measurements behind it are in [research/2026-10-doi-resolution.md](research/2026-10-doi-resolution.md). ADR 0011 puts Registry Checks outside the Stage pipeline.

## Scope and definition of done
- Scope ends at Resolutions. Creating Library Items for Sources (`catalog`) and fetching their PDFs come later.
- Done when:
  1. Every Library Item is a Work, kept current by the sync, and the local `resolve` Stage runs in `explore` for every Reference of the Seed.
  2. A Registry Check resolves a whole Work or one Reference, from the reader and the CLI, within the registries' limits.
  3. No DOI and no query is ever sent to a registry twice.
  4. The reader shows each Reference's state, and duplicate DOIs in the Library can be chosen or merged from the reader and the CLI.

## Local resolution: automatic, no registry calls
The `resolve` Stage runs for every Reference of a Work. Its only network call is the Zotero update check.

- **Library sync.** Every Library Item is a Work row (ADR 0007's identity). The first sync reads the whole Library (16–32 s for 579 items); after that an incremental sync (`since=<library version>`, ~160 ms) runs before every local resolution and every Registry Check, deletions included. If Zotero is unreachable, the last copy is used and a warning logged. `paperexplorer sync` forces a full read.
- **Duplicate DOIs** (two Library Items, one DOI). The item with a PDF stands for the DOI; when both or neither have one, the older stands. The reader (index page, side panel) and the CLI (`paperexplorer duplicates`, `--use <key>`, `--merge <keep> <other>`) let you choose the other item or merge the pair. A merge writes to Zotero like Zotero desktop's merge: the kept item takes the other's collections, tags, notes and attachments and a `dc:replaces` relation; the other goes to Zotero's trash; the kept item's fields win; you confirm before anything is written. This amends ADR 0007, which refused the pair.
- **A Library Item deleted in Zotero** (not by a merge): its Work row goes when nothing uses it; when a Resolution or a Stage run does, the row stays without a Library Item. After a merge, References that pointed at the trashed item point at the kept one.
- **Matching against the Library.**
  - A printed DOI that a Library Item holds, and that agrees with the Reference: `certain`. It agrees when the titles are near (≥ 0.9) or their main titles are equal; without a printed title, when the first surname or the year agrees; a Reference that prints only its DOI has nothing to disagree with.
  - A DOI that disagrees is a **DOI Conflict**: the title rules below run as if no DOI were printed. A title match wins, capped at `low` and flagged, with the DOI's Work kept as the other option. With no title match the Reference stays Unchecked, the DOI's Work kept likewise. The same agreement rule (`resolution.agrees`) checks a Registry Check's DOI hits.
  - No printed DOI: an exact title after normalization (lowercase, accents and punctuation stripped, spaces collapsed) with the first surname or the year agreeing, or an equal main title (before the colon) with both agreeing: `high`. A near title (similarity ≥ 0.9): `low`.
  - Otherwise a title never overrules a printed DOI: a Library Item with another DOI is never a title match for it, since a preprint and its published Version often share a title. Only a DOI Conflict sets the printed DOI aside.
  - Title candidates come from a trigram index (`pg_trgm`) on the normalized title, then the scoring above.
- **Then the cached registry responses** from earlier Registry Checks, matched by the same rules.
- What is still unmatched is an Unchecked Reference.

## Registry Checks: only when the reader asks
A Registry Check (ADR 0011) looks References up in Crossref and OpenAlex. It runs the local pass first, then the layers below for what is still unmatched, and saves each Resolution as it lands.

- **Layer 1: DOI batches**, 100 DOIs per call to Crossref (`filter=doi:a,doi:b,…`) and OpenAlex (`filter=doi:a|b|…`) at the same time.
  - Crossref decides which Work a DOI is: the printed DOI wins. `posted-content` makes a preprint its own Version; its `is-preprint-of` stays in the cached response until the reader shows Versions. An alias DOI (JSTOR, APA's `//` form) is the same Work.
  - OpenAlex adds open-access links, the abstract and the OpenAlex ID, and supplies the record for DOIs Crossref does not know (DataCite, such as arXiv's).
  - Every DOI hit is checked by the same agreement rule as against the Library. Agreement: `certain`. A DOI Conflict goes on to layer 2 as if it printed no DOI; a title match there is capped at `low`, and with none left after layer 3 it is Unresolved, the DOI's record kept as the other option.
- **Layer 2: OpenAlex title search** (`filter=title.search:`), 5 candidates per Reference, no year filter, scored as against the Library.
- **Layer 3: Crossref `query.bibliographic`** on the printed string, for References with no usable title or only a `low` title match. A hit is `low`.
- Nothing matched: an Unresolved Reference.
- Never used: doi.org (it passes Crossref DOIs on to Crossref), a separate arXiv path (OpenAlex covers arXiv; no fixture prints an arXiv ID), Semantic Scholar.
- **Starting one.** The Work page's "Check N Unchecked References" button shows the cost first (batch calls, title searches, share of today's OpenAlex budget from a free singleton's headers), then streams each Reference's result. The side panel shows each Reference as resolved (level, and Library, Crossref or OpenAlex), Unchecked or Unresolved, with "Check" on Unchecked and "Check again" on Unresolved or `low`, which skips the cache for that Reference. A grouped marker shows one button per Reference. The CLI: `paperexplorer check <zotero_key> [--reference <key>] [--refresh]`.

## Speed and limits
- One limiter per host and request type, set from the settings, tightened when the `x-rate-limit-*` headers come in lower:

  | | Without | With |
  |---|---|---|
  | Crossref list queries | 1/s, 1 at a time | 3/s, 3 (`PE_CROSSREF_MAILTO`) |
  | Crossref single records | 5/s, 1 | 10/s, 3 |
  | OpenAlex | 8/s | 25/s (`PE_OPENALEX_API_KEY`) |

  Both settings exist and stay empty for now; filling them changes no code. OpenAlex runs below its published 10 and 30 because 8 parallel keyless calls already drew 429s.
- A 429 waits for its `Retry-After`; a 5xx backs off exponentially; three tries each.
- References move through the layers in order; within a layer calls run in parallel, and layer 1's two batches go to their hosts at once.
- When OpenAlex's daily budget is spent, the Check stops with "OpenAlex budget spent, N References left, try after 00:00 UTC" and keeps every Resolution and response so far.
- A Seed's first full Check: ~5–10 s. Anything after it from the cache: no registry calls.

## Store
- `registry_responses`: one row per source and key (`crossref:doi`, `openalex:doi`, `openalex:title`, `crossref:bibliographic`) with the raw JSON (a batch split per DOI), hit or miss, and `fetched_at`. Keys are normalized DOIs (lowercase; `https://doi.org/`, `doi:` and trailing `.` or `,` stripped; `//` kept) or normalized query strings. Rows commit as they land, on their own connection. Misses are retried after 30 days; hits stay until `--refresh`.
- `resolutions`: one row per Reference: resolved, Unchecked or Unresolved, with confidence, level (`certain`, `high`, `low`), evidence, the layer that matched, and the DOI's Work on a DOI Conflict. A re-parse replaces a Work's References, and their Resolutions go with them.
- `readers_choices` (PR 4): one row per Reader's Choice, keyed by the Work and the Reference's printed text rather than its row, so a re-parse that prints the Reference the same keeps it. No resolve or Check (`--refresh` included) overwrites one; it wins over the Resolution until the reader clears it.
- A Work found by a Registry Check gets a `works` row with its DOI, OpenAlex ID and the registry's title, creators and year until `catalog` gives it a Library Item; a null `synced_at` marks metadata not yet from Zotero.
- The index page and `GET /works` list only Works with a Stage run; Library-only Works appear through Resolutions.
- `resolution`'s interface is `resolve(refs) -> list[Resolution]`, one per Reference, async inside; callers run it through `asyncio.run`.
- The table is not named `lookups`: CONTEXT.md lists "Lookup" among the words to avoid for Resolution.

## Testing
- Matching rules are pure functions with hand-written cases: an exact DOI, a DOI with a misspelt title that still agrees, a DOI alone, a neighbour's DOI (a DOI Conflict, with and without a title match), a `posted-content` preprint, an alias DOI, no title, a missing subtitle, a wrong-year near-miss, a garbage string, an empty result.
- Resolution tests seed `registry_responses` rows from recorded JSON in the real Postgres; the HTTP side runs over `httpx.MockTransport` (batch splitting, 429 with `Retry-After`, 5xx, a spent budget). No vcrpy: a real failure becomes a named regression test from its recorded rows.
- `InMemoryLibrary` gains the sync and merge calls; the Zotero adapter is tested over hand-written Web API JSON.
- Thresholds are calibrated once, live, on fixture References that print a DOI: their title search should find that DOI (~100 searches, one keyless day); the responses are kept for the tests.

## Build order
Four PRs into `dev`, each test-first and through the gate, with a check-in after each:
1. `feat/resolution-design`: this design, ADR 0011, the research note, CONTEXT.md's terms, `architecture.md` and `design-v1.md` updated.
2. `feat/library-sync`: settings, migration `0005`, Library sync, every Library Item a Work, `pg_trgm`, duplicates (stand-in, choose, merge on the CLI), the local `resolve` Stage, `paperexplorer resolutions <zotero_key> [--low]`. On the tower it reports how much of the Seed the Library already resolves.
3. `feat/registry-check`: HTTP clients, limiters, budget, cache, `paperexplorer check`, threshold calibration, a live Check of the Seed timed against the estimate.
4. `feat/check-ui`: API routes for Checks (whole Work as an event stream, one Reference) and duplicates, the Work page button with its cost, the side panel's states and buttons, duplicates on the index page, and the Reader's Choice: a DOI Conflict shows both Works, and the reader picks one, another Work or none, or clears an earlier choice.
