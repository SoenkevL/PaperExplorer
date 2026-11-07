-- Collections and the Library Items filed in them, kept current by the Library sync
-- (design-collections.md). `parent_key` has no foreign key: a sync can list a child before its
-- parent, and a parent that never arrives leaves its child a root.
CREATE TABLE collections (
    key text PRIMARY KEY,
    name text NOT NULL,
    parent_key text
);
CREATE INDEX collections_parent_key ON collections (parent_key);

-- Duplicate Items each keep their own Collections, as each is its own Work row.
CREATE TABLE work_collections (
    work_id bigint NOT NULL REFERENCES works ON DELETE CASCADE,
    collection_key text NOT NULL REFERENCES collections ON DELETE CASCADE,
    PRIMARY KEY (work_id, collection_key)
);
CREATE INDEX work_collections_collection_key ON work_collections (collection_key);

-- The next sync reads the whole Library: one reading only what changed would leave the
-- Collections and Works that did not change unknown.
DELETE FROM library_sync;
