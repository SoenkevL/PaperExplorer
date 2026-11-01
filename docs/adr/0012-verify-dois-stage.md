# Printed DOIs are verified at doi.org in an optional Stage, which never blocks the pipeline

ADR 0011 keeps the network out of the Stage pipeline because Crossref's and OpenAlex's limits are spent only when the reader asks. doi.org's Handle API has no such limits: it is free and keyless, and it says for certain whether a DOI is registered. All 210 fixture DOIs took 1.4 s at 20 requests in flight ([research](../research/2026-11-doi-existence.md)). A printed DOI that doi.org does not know is an Unregistered DOI. GROBID makes them when a DOI is cut short or joined wrongly, and they block a title match that would otherwise be found.

So the `verify_dois` Stage, between `extract_citations` and `resolve`, asks doi.org about every printed DOI of a Work and caches each answer in `registry_responses` (source `doi:handle`). `resolve` reads the verdicts: it resolves a Reference with an Unregistered DOI as if no DOI were printed, and a Registry Check skips layer 1 for it.

The Stage is optional. It can be run on its own (`paperexplorer run verify_dois <key>`) and does not have to run at all. When doi.org cannot be reached, the Stage still ends `done`: it logs a warning with the count of unverified DOIs, and an unverified DOI counts as printed, as before. Running it again later verifies the rest and makes `resolve` pending through the usual cascade. It never fails the pipeline because of the network.

Amends ADR 0011: the Stages' only network call is doi.org, and Crossref and OpenAlex are still asked only in a Registry Check.

## Considered Options

- **The check as step 0 of `resolve`:** one Stage, but `resolve` would then need the network, and it cannot be rerun alone to fill in DOIs that went unverified.
- **The check only on DOIs both Registry Check batches miss:** almost free, but it waits for a Registry Check, so the local pass never benefits.
- **No check:** an Unregistered DOI blocks its title match until a Registry Check finds it in neither registry.

## Consequences

- Processing a Work needs the network for one optional Stage. Offline, everything still runs; the Stage reports what it could not verify.
- `registry_responses` comes in with this Stage, ahead of the Registry Check, which reuses it.
- A DOI registered late is asked about again after 30 days, like any miss.
