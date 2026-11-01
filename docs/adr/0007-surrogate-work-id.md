# A Work is addressed by a surrogate id; its outside identifiers are unique columns

Every Work row has a `WorkId`, a Postgres identity integer. Every foreign key and every API route (`/works/{id}`) uses it. The identifiers a Work has outside PaperExplorer are unique, nullable columns of `works`: its Zotero key, DOI (lowercased), arXiv ID and OpenAlex ID. The CLI names a Work by its Zotero key and looks up or creates its row.

A Work needs an identity before anything else is known about it. The Seed enters as a Zotero key alone, and a Source will enter as a Resolution's DOI or OpenAlex ID before catalog gives it a Library Item. None of the outside identifiers is present for every Work at every Stage, so none of them can be the key. As unique columns they make the dedupe check before a Zotero create (ADR 0001) a lookup.

## Considered Options

- **The Zotero key as the key:** stable across rebuilds and readable in URLs. But every Work would need a Library Item before it gets a row, so resolve would have to create Zotero items before it could store a Resolution.
- **A DOI or OpenAlex ID as the key:** not every Work has one (preprints, grey literature), and a Work's DOI can be corrected in Zotero.

## Consequences

- **`WorkId`s are not stable across a rebuild** of the database (ADR 0001 says it must be rebuildable). A bookmark to `/works/17` can break; the Zotero key is the identity to keep outside PaperExplorer.
- ~~**Two Library Items with the same DOI are refused** (`DuplicateWorkError`) rather than becoming one Work twice.~~ Amended 2026-11-01 ([design-resolution.md](../design-resolution.md), migration `0005`): both are Works, and one stands for the DOI (the reader's choice, else the one with a PDF, else the older); the reader can merge the pair in Zotero.
- **References, Citations and Citation Links carry `work_id`** in composite foreign keys, so a Citation Link cannot point at another Work's Reference.
