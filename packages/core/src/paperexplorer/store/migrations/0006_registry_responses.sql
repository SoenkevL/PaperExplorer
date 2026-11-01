-- What registries answered, so no DOI and no query is sent twice (design-resolution.md). One row
-- per source and key: `doi:handle` (doi.org, the `verify_dois` Stage) now, the Registry Check's
-- sources later. A miss is asked again after 30 days; a hit stays until refreshed.
CREATE TABLE registry_responses (
    source text NOT NULL,
    key text NOT NULL,
    response jsonb NOT NULL,
    hit boolean NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, key)
);
