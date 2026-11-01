-- Every Library Item is a Work, kept current by the Library sync (design-resolution.md).

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Two Library Items may hold one DOI: both are Works, and one stands for the DOI. This amends
-- ADR 0007, which refused the pair. `doi_chosen` marks the reader's pick; else the one with a
-- PDF stands, then the older (`added_at`, the Library Item's dateAdded).
ALTER TABLE works DROP CONSTRAINT works_doi_key;
CREATE INDEX works_doi ON works (doi);
ALTER TABLE works ADD COLUMN added_at timestamptz;
ALTER TABLE works ADD COLUMN doi_chosen boolean NOT NULL DEFAULT false;

-- Title candidates for a Reference; pg_trgm ignores case and punctuation, `resolution` scores.
CREATE INDEX works_title_trgm ON works USING gin (title gin_trgm_ops);

-- The Library version the last sync read up to; one row.
CREATE TABLE library_sync (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    version bigint NOT NULL,
    synced_at timestamptz NOT NULL
);
