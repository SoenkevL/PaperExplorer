# Collections on the landing page: settled design (grilling outcome)

## Context
The landing page lists the Library flat, 50 Library Items at a time. Zotero groups them in Collections, nested to any depth, and the reader wants to find Works that way. A grilling round on 2026-11-05 settled the design below, against the mockup "Library by Collection" (claude.ai artifact `LHQ38yDrt99P9vYmpBpGLq`, private to the user). Library sync stores no Collections today, so the work starts there.

## Scope and definition of done
- In scope: storing Collections at sync, serving them from the API, the Collections view with its navigator pane, Library-wide search that lists the selected branch first, and Stage pips on every row.
- Out of scope: writing Collections to Zotero (creating, renaming, filing), and marking a Collection as an Exploration. Explorations are not modelled yet; their mark comes with them.
- Done when:
  1. A sync, full or incremental, leaves the store holding every Collection with its parent and every Library Item's Collections, removing those Zotero deleted or trashed.
  2. The landing page opens on the Collections view: a pane with the Collection tree at any depth, beside the Works of the selected Collection, navigable by mouse, keyboard and the browser's Back and Forward.
  3. Searching from any selection searches the whole Library, the selected branch's matches first.
  4. The List view is today's list with the new rows, and the switch between the views is remembered.

## Collections in the store
- Migration `0009_collections.sql`:
  - `collections (key text PRIMARY KEY, name text NOT NULL, parent_key text)`. `parent_key` has no foreign key: a sync can list a child before its parent.
  - `work_collections (work_id bigint REFERENCES works ON DELETE CASCADE, collection_key text REFERENCES collections ON DELETE CASCADE, PRIMARY KEY (work_id, collection_key))`.
  - Duplicate Items each keep their own Collections, as each is its own `works` row.
- **The port.** `LibraryItem` gains `collections: list[str]` (Zotero's `data.collections`). `LibraryChanges` gains `collections: list[Collection]` (changed or new, each with key, name and parent key) and `deleted_collections: list[str]`. `Collection` is a Pydantic model in `domain`.
- **`ZoteroLibrary`** reads `GET /collections?since=<version>` (paged like `/items`), takes deleted keys from the `collections` list of the `/deleted` answer it already reads, and treats a Collection with `data.deleted` true (Zotero's trash) as deleted. A full read lists every Collection, so the sync removes any key it leaves out. The shapes come from Zotero's docs and source (`docs/research/2026-11-zotero-collections.md`); the fixtures in `tests/fixtures/zotero/` are hand-written from them, and a `tower` test reads the real Library.
- **`InMemoryLibrary`** holds Collections beside items, with `put_collection` and `remove_collection` for tests.
- **`sync`** upserts the changed Collections, deletes the gone ones with every subcollection the store holds under them (their rows in `work_collections` cascade), and replaces a changed item's `work_collections` rows wholesale, skipping a key the store does not hold. Zotero does not always name the subcollections: trashing a Collection marks only that one, and a delete through the Web API logs only its key (`docs/research/2026-11-zotero-collections.md`); a full read leaves out every Collection under a trashed one. A subcollection edited while its parent sits in the trash comes back as a root, with the WARNING below. A Work whose last Collection went shows under *Not in a collection*; its Stages and Resolutions stay as they are.
- A `parent_key` naming no stored Collection renders as a root, with a WARNING at sync. A cycle cannot come from Zotero; the recursive queries below still stop at a depth of 32.

## The API
- `GET /collections` returns every Collection, flat: `key`, `name`, `parent_key`, `works` (distinct Works in its branch) and `ingested` (those with every Ingesting Stage done). Two extra entries carry the counts for the whole Library and for Library Items in no Collection. The counts come from one recursive CTE in `store`.
- `GET /library` gains:
  - `collection=<key>`: the Works filed in it; with `deep=true`, in its whole branch, each once.
  - `unfiled=true`: Library Items in no Collection.
  - With `q` as well, the search covers the whole Library. Each `LibraryWork` gains `in_branch: bool`, rows in the branch come first, and the page gains `branch_total`, so the web app knows where *Elsewhere in the Library* starts on whichever page it falls.
  - Each `LibraryWork` gains `collections: list[str]`, its Collections' keys, for the "also in" line.
- `ApiClient` gains `collections()` and the new `library` parameters.

## The landing page
- **Two views, one switch.** A segmented control beside the search: *List* and *Collections*. The first visit opens Collections with *Whole Library* selected. The choice, the last Collection and the "Show Works in subcollections" box live in cookies (`pe.library_view`, `pe.collection`, `pe.deep`), so the server renders the right view with no flash. A `?collection=` in the URL wins over the cookie.
- **The address.** `/?collection=KEY` (with `&deep=1`, `&q=`) selects a Collection; `/?unfiled=1` selects *Not in a collection*. Clicking a Collection swaps the Works side with htmx and pushes the URL (`hx-push-url`), so Back, Forward, reload and a shared link land on the same view.
- **The navigator pane** (left, about 16.5rem): *Whole Library* first, then the root Collections in Zotero's order (by name), then *Not in a collection*. A Jinja recursive macro renders a WAI-ARIA tree (`role="tree"`, `treeitem`, `group`, `aria-level`, `aria-expanded`, `aria-selected`). Each item shows its name and its branch's Work count. The selected Collection's ancestors render open; other open branches are kept per browser in `localStorage` (`pe.tree.open`) and restored by script.
- **Keyboard**, in a small `static/library-tree.js` (roving `tabindex`, selection follows focus):
  - ↑ / ↓: the previous or next visible Collection.
  - →: opens a closed Collection; on an open one, moves to its first subcollection.
  - ←: closes an open Collection; on a closed one or a leaf, moves to its parent.
  - Home / End: the first or last visible item. A printable key: the next item whose name starts with it. `*`: opens every sibling. Enter or Space: selects. Tab leaves the pane for the Works.
- **The Works side** shows one Collection at a time:
  - Breadcrumbs from *Library* down to the Collection, each a link.
  - The name, and a meter: Works Ingested over Works in the branch.
  - The subcollections as chips, each with its branch count and how many subcollections it holds; a chip selects it.
  - "Show Works in subcollections": off by default. Off lists the Works filed directly; on lists the branch, each Work once.
  - The rows, 50 at a time with *Load more*, as now. A Work filed elsewhere too gets an "also in" line naming those Collections, each a link; its full path shows on hover.
  - A Collection with nothing filed directly says so and points to its subcollections and the checkbox.
- **Search** from the Collections view covers the whole Library. Matches in the selected branch (whatever the checkbox) come first on a faint accent tint; the rest follow under a divider, *Elsewhere in the Library · N*. The pane dims Collections with no match and marks those with one, with their match count. With *Whole Library* selected there is no divider.
- **Narrow windows** (under 48rem): the pane hides behind a *Collections* button at the start of the breadcrumbs and opens above the Works.

## Rows, in both views
- The seven Stages become pips: five Ingesting, a gap, two Resolving. Filled is done, a ring pending, a pulse running (steady under `prefers-reduced-motion`), red failed. Each pip's `title` and the strip's `aria-label` name every Stage and its state.
- A done Stage that may run again keeps its re-run: its pip is a button with the same `hx-confirm` as today's ↻. A failed Stage's error still prints under the row.
- *no PDF*, the run note (`queued`, `running`), *Cancel*, *Ingest*, *Resume* and *Resolve References* stay as they are.
- The row's markup is one template, `library_row.html`, shared by both views and by the row poll.

## Testing
- `InMemoryLibrary` with Collections drives the sync tests: a nested tree, an item in two Collections, a Collection deleted with its subcollections, a trashed one, a child listed before its parent, a parent key that never arrives.
- `ZoteroLibrary` over recorded `/collections` and `/deleted` responses.
- `store` against real Postgres: branch counts with a Work in two sibling Collections counted once, `deep`, `unfiled`, and the branch-first search order across pages.
- The web app: the tree's ARIA attributes and open ancestors, the cookie defaults and the URL winning over them, the divider at `branch_total` on page one and on a later page, and the pip markup.

## Delivery
Three PRs into `dev`, each with its `CHANGELOG.md` and `docs/logbook.md` lines:
1. `feat/collections-sync`: migration 0009, `Collection` in `domain`, the port, both adapters, `sync`.
2. `feat/collections-api`: the store queries, `GET /collections`, the new `GET /library` parameters, the client.
3. `feat/collections-pane`: the views, the pane and its script, search order, the rows' pips.
