-- Every Library Item is a Work, kept current by the Library sync (design-resolution.md).

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Duplicate Items (Library Items holding one DOI) each get a row, and one is the Standing Item.
-- This amends ADR 0007, which refused the pair. `stands_by_reader` marks the one the reader let
-- stand; else the one with a PDF stands, then the oldest (`added_at`, the item's dateAdded).
ALTER TABLE works DROP CONSTRAINT works_doi_key;
CREATE INDEX works_doi ON works (doi);
ALTER TABLE works ADD COLUMN added_at timestamptz;
ALTER TABLE works ADD COLUMN stands_by_reader boolean NOT NULL DEFAULT false;

-- Title candidates for a Reference; pg_trgm ignores case and punctuation, `resolution` scores.
CREATE INDEX works_title_trgm ON works USING gin (title gin_trgm_ops);

-- The Library version the last sync read up to; one row.
CREATE TABLE library_sync (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    version bigint NOT NULL,
    synced_at timestamptz NOT NULL
);

-- One Resolution per Reference: matched to a Work, Unchecked or Unresolved. Keyed by the
-- Reference's parser key, so a re-parse replaces them with its References. `doi_conflict_work_id`
-- is the Work a disagreeing printed DOI names (a DOI Conflict). The Works they name stay when
-- their Library Item goes (no cascade), so the sync keeps them without one.
CREATE TABLE resolutions (
    work_id bigint NOT NULL,
    reference_key text NOT NULL,
    state text NOT NULL CHECK (state IN ('resolved', 'unchecked', 'unresolved')),
    resolved_work_id bigint REFERENCES works,
    level text CHECK (level IN ('certain', 'high', 'low')),
    confidence double precision CHECK (confidence > 0 AND confidence <= 1),
    evidence text[] NOT NULL DEFAULT '{}',
    layer text CHECK (layer IN ('library')),
    doi_conflict_work_id bigint REFERENCES works,
    resolved_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (work_id, reference_key),
    FOREIGN KEY (work_id, reference_key) REFERENCES work_references (work_id, key)
        ON DELETE CASCADE,
    CHECK ((state = 'resolved') = (resolved_work_id IS NOT NULL AND level IS NOT NULL))
);
