# PaperExplorer writes to Zotero only in the PaperExplorer Collection, through a reconciler outside the Stages

ADR 0001 makes Zotero the record of truth for Exploration Collections, and the Library is also the reader's own, organised by hand over years. A tool that files hundreds of Sources must not disturb that. So every Exploration Collection sits in one top-level Collection, the PaperExplorer Collection, and the `Library` port can write only there. It can create Collections inside that branch and file or unfile one item in one of them, changing just that key in the item's `collections`. It can create new Library Items and add or remove the `pe:seed` tag. Any other Collection key raises before a request is sent, in `ZoteroLibrary` and `InMemoryLibrary` alike. The one other write, `merge`, the reader confirms each time.

Filing is done by a reconciler, `catalog.reconcile`, not a Stage. It compares the firm Sources with the Filings PaperExplorer recorded and closes the gap, committing each Filing as its Zotero write lands. It runs after `resolve`, after each Registry Check and after each Reader's Choice. A Stage could not do this: it is one transaction per Work, so a failed write after twenty good ones would keep none of the bookkeeping while Zotero kept the writes. And Registry Checks and Readers' Choices, which change Resolutions, are already outside the Stages (ADR 0011).

## Considered Options

- **Write anywhere, by convention:** no guard to build, but one bug in a Collection key would move the reader's own filing, and nothing in the tests would show it.
- **A `catalog` Stage at the end of the Resolving chain:** one more pip and the Stage contract, but a partial failure would lose track of the writes Zotero kept, and a Check or a Reader's Choice would have to rerun it.
- **Strict mirror, refiling and unfiling whatever differs:** simple to reason about, but it would undo the reader's own edits in Zotero; instead a Source the reader unfiled stays unfiled until they choose it again.

## Consequences

- Filing is idempotent and resumable: a run with nothing to change writes nothing, and a failed write is retried by the next run.
- PaperExplorer records each Filing, so it unfiles only what it filed. It never deletes or trashes an item.
- Tags beyond `pe:seed` (Text Availability) need this ADR amended, since they write to items the reader also files elsewhere.
- If the reader deletes the PaperExplorer Collection, its Explorations end; nothing is recreated silently.
