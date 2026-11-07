-- Explorations and their Filings in Zotero (design-explorations.md, ADR 0014). A Collection
-- PaperExplorer creates has its key made here first and `created` set once Zotero holds it, so
-- a create whose answer was lost is posted again with the same key, never as a duplicate.

-- The one PaperExplorer Collection, adopted or created.
CREATE TABLE paperexplorer_collection (
    only_one boolean PRIMARY KEY DEFAULT true CHECK (only_one),
    key text NOT NULL,
    created boolean NOT NULL
);

CREATE TABLE explorations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seed_id bigint NOT NULL REFERENCES works ON DELETE CASCADE,
    collection_key text NOT NULL,
    name text NOT NULL,
    created boolean NOT NULL DEFAULT false,
    seed_tagged boolean NOT NULL DEFAULT false,
    started_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz
);
-- A Seed has at most one Exploration under way.
CREATE UNIQUE INDEX explorations_one_per_seed ON explorations (seed_id) WHERE ended_at IS NULL;

-- What PaperExplorer filed, or means to: `pending` has its item's key but no confirmed write.
CREATE TABLE filings (
    exploration_id bigint NOT NULL REFERENCES explorations ON DELETE CASCADE,
    work_id bigint NOT NULL REFERENCES works ON DELETE CASCADE,
    item_key text NOT NULL,
    state text NOT NULL CHECK (state IN ('pending', 'filed', 'failed', 'removed_by_reader')),
    error text,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (exploration_id, work_id)
);
CREATE INDEX filings_item_key ON filings (item_key);
