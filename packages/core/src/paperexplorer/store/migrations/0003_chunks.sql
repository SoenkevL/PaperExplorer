-- A Work's Chunks in reading order, as the chunk Stage cut them from its paragraphs and
-- footnotes. `span` and the `sentences` are `[start, end)` in code points of the block's text,
-- so a sentence is a place in the reader as it stands. A re-parse deletes the blocks, and their
-- Chunks go with them. Embeddings get their own migration.
CREATE TABLE chunks (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    work_id bigint NOT NULL,
    position integer NOT NULL CHECK (position >= 0),
    block integer NOT NULL,
    span int4range NOT NULL CHECK (NOT isempty(span)),
    text text NOT NULL,
    sentences jsonb NOT NULL,
    heading_path text[] NOT NULL,
    page integer NOT NULL,
    boxes jsonb NOT NULL,
    tokens integer NOT NULL CHECK (tokens >= 0),
    flags text[] NOT NULL CHECK (flags <@ ARRAY['short', 'oversized_sentence']),
    text_origin text NOT NULL CHECK (text_origin IN ('printed', 'ocr')),
    -- the keyword half of retrieval: Postgres full-text search in English (design-v1)
    text_search tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, text)) STORED,
    UNIQUE (work_id, position),
    UNIQUE (work_id, id),
    FOREIGN KEY (work_id, block) REFERENCES blocks (work_id, position) ON DELETE CASCADE
);

CREATE INDEX chunks_text_search ON chunks USING gin (text_search);
-- a block's Chunks, for Pointers and for the cascade when a re-parse deletes the blocks
CREATE INDEX chunks_block ON chunks (work_id, block);
