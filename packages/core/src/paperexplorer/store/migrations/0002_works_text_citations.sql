-- Works, their text as parsed, their References and Citations, and per-Work Stage status.
-- A Work is addressed by a surrogate id (ADR 0007); the identifiers it has outside
-- PaperExplorer are unique columns, so the dedupe check before a Zotero create is a lookup.

CREATE TABLE works (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    zotero_key text UNIQUE,
    doi text UNIQUE CHECK (doi = lower(doi)),
    arxiv_id text UNIQUE,
    openalex_id text UNIQUE,
    -- the Library Item's metadata as last read; Zotero wins when they differ (ADR 0001)
    title text,
    creators text[] NOT NULL DEFAULT '{}',
    year integer,
    pdf_attachment_key text,
    pdf_filename text,
    synced_at timestamptz,
    -- how parse read the text
    text_origin text CHECK (text_origin IN ('printed', 'ocr')),
    parser_versions jsonb NOT NULL DEFAULT '{}',
    CHECK ((pdf_attachment_key IS NULL) = (pdf_filename IS NULL))
);

-- A Work's text in reading order, as parse read it. Boxes, sentence spans and the Citation
-- markers' raw evidence are values of their block, so they are JSONB; extract_citations reads
-- the markers back to link them.
CREATE TABLE blocks (
    work_id bigint NOT NULL REFERENCES works ON DELETE CASCADE,
    position integer NOT NULL CHECK (position >= 0),
    kind text NOT NULL CHECK (kind IN ('heading', 'paragraph', 'footnote')),
    heading_path text[] NOT NULL,
    page integer NOT NULL,
    boxes jsonb NOT NULL,
    text text NOT NULL,
    sentences jsonb NOT NULL,
    citation_markers jsonb NOT NULL,
    ocr_confidence double precision,
    PRIMARY KEY (work_id, position)
);

-- The Work's bibliography as printed ("references" is a reserved word). `key` is the parser's
-- id for the entry, e.g. GROBID's `b12`.
CREATE TABLE work_references (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    work_id bigint NOT NULL REFERENCES works ON DELETE CASCADE,
    key text NOT NULL,
    position integer NOT NULL CHECK (position >= 0),
    label text,
    text text NOT NULL,
    page integer NOT NULL,
    boxes jsonb NOT NULL,
    surnames text[] NOT NULL,
    year text,
    title text,
    doi text,
    UNIQUE (work_id, key),
    UNIQUE (work_id, position),
    UNIQUE (work_id, id)
);

-- One row per Citation, in the order extract_citations found them. `span` is `[start, end)` in
-- characters of its block's text. A re-parse deletes the blocks, and these go with them.
CREATE TABLE citations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    work_id bigint NOT NULL,
    position integer NOT NULL CHECK (position >= 0),
    block integer NOT NULL,
    span int4range NOT NULL CHECK (NOT isempty(span)),
    text text NOT NULL,
    citing_sentence text NOT NULL,
    UNIQUE (work_id, position),
    UNIQUE (work_id, id),
    FOREIGN KEY (work_id, block) REFERENCES blocks (work_id, position) ON DELETE CASCADE
);

-- A Citation's tie to a Reference of the same Work (both foreign keys carry work_id).
-- An Unlinked Citation has no row here.
CREATE TABLE citation_links (
    citation_id bigint PRIMARY KEY,
    work_id bigint NOT NULL,
    reference_id bigint NOT NULL,
    confidence double precision NOT NULL CHECK (confidence > 0 AND confidence <= 1),
    evidence text[] NOT NULL CHECK (
        cardinality(evidence) > 0
        AND evidence <@ ARRAY['pdf_link', 'grobid', 'label', 'author_year']
    ),
    FOREIGN KEY (work_id, citation_id) REFERENCES citations (work_id, id) ON DELETE CASCADE,
    FOREIGN KEY (work_id, reference_id) REFERENCES work_references (work_id, id)
        ON DELETE CASCADE
);

-- The last run of each Stage per Work; a Stage with no row is pending.
CREATE TABLE stage_status (
    work_id bigint NOT NULL REFERENCES works ON DELETE CASCADE,
    stage text NOT NULL,
    state text NOT NULL CHECK (state IN ('running', 'done', 'failed')),
    started_at timestamptz NOT NULL,
    finished_at timestamptz,
    counts jsonb NOT NULL DEFAULT '{}',
    error text,
    PRIMARY KEY (work_id, stage),
    CHECK ((state = 'running') = (finished_at IS NULL)),
    CHECK ((state = 'failed') = (error IS NOT NULL))
);
