-- Registry Checks: every response kept, so no DOI and no query is sent twice (design-resolution.md).

-- One row per source and key: a normalized DOI or a normalized query. `body` is the record of a
-- DOI (a batch split per DOI) or the list a search returned; a miss has none. Misses are asked
-- again after 30 days; hits stay until a Check is told to refresh.
CREATE TABLE registry_responses (
    source text NOT NULL
        CHECK (source IN ('crossref:doi', 'openalex:doi', 'openalex:title', 'crossref:bibliographic')),
    key text NOT NULL,
    hit boolean NOT NULL,
    body jsonb,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, key),
    CHECK (hit = (body IS NOT NULL))
);

-- A Resolution's layer now names the registry that matched it, too.
ALTER TABLE resolutions DROP CONSTRAINT resolutions_layer_check;
ALTER TABLE resolutions ADD CONSTRAINT resolutions_layer_check
    CHECK (layer IN ('library', 'crossref', 'openalex'));
