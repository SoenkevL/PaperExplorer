# Registry Checks run only when the reader asks, outside the Stage pipeline

Resolving a Reference locally, against the Library and responses already cached, is free and runs for every Reference in the `resolve` Stage. Asking Crossref and OpenAlex is not free: OpenAlex allows 100 title searches a day without a key and 1,000 with one, Crossref one list query a second on its public pool. One Seed needs about 45 title searches; following its Sources one level deeper would need about 3,200. So the registries are asked only in a Registry Check, which the reader starts for a whole Work or for one Reference, from the reader or the CLI.

A Registry Check is not a Stage. It runs the local pass, then the registry layers for what is still unmatched, and commits each response (`registry_responses`) and each Resolution as it lands, on a connection of its own. A Check stopped by a spent budget or a failure keeps everything it paid for, and the next Check starts where it ended.

## Considered Options

- **One `resolve` Stage with an `--online` flag:** one code path, but a Stage is one transaction per Work, so a run stopped by the budget would roll back its Resolutions, and checking a single Reference has no place in a per-Work Stage.
- **Registry calls inside the Stage, cache rows on their own connection:** keeps the pipeline whole and the paid-for responses, but every `explore` would spend the budget on every Reference, which the reader did not ask for.
- **Two Stages, a network-only `lookup` then a pure `resolve`:** the title layer needs the DOI layer's verdict, so the matching would run in both.

## Consequences

- `explore` resolves only from the Library and the cache. A Reference nothing local matches is an Unchecked Reference until a Check; only a Check makes one Unresolved.
- The Stage contract (one transaction, none of a failed run's writes kept) holds for every Stage; Registry Checks are the one writer outside it.
- Downstream Stages (`catalog`) read whatever Resolutions exist when they run; a Check does not make them pending.
- v1's definition of done counts Sources resolved locally or by a Check; the report lists Unchecked References beside Unresolved and low-confidence ones.
