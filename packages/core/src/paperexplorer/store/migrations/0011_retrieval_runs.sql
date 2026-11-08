-- Recorded searches for Evidence (design-jumping.md): one Citation's Citing Sentence over a Scope.
-- `Chunks.replace` deletes the Runs whose Scope holds the Work, as their ranking no longer stands.
CREATE TABLE retrieval_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    citation bigint NOT NULL REFERENCES citations (id) ON DELETE CASCADE,
    citing_sentence text NOT NULL,
    model text NOT NULL,
    k integer NOT NULL CHECK (k > 0),
    depth integer NOT NULL CHECK (depth > 0),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX retrieval_runs_citation ON retrieval_runs (citation);

-- A Scope is a set of Works; each is a row, so a Work's Runs can be found and go with it.
CREATE TABLE retrieval_run_works (
    run bigint NOT NULL REFERENCES retrieval_runs (id) ON DELETE CASCADE,
    work bigint NOT NULL REFERENCES works (id) ON DELETE CASCADE,
    PRIMARY KEY (run, work)
);

CREATE INDEX retrieval_run_works_work ON retrieval_run_works (work);

-- The ranked Chunks a Run returned, `rank` counted from 1; a ranking that missed one leaves its
-- place empty.
CREATE TABLE evidence (
    run bigint NOT NULL REFERENCES retrieval_runs (id) ON DELETE CASCADE,
    rank integer NOT NULL CHECK (rank > 0),
    chunk bigint NOT NULL REFERENCES chunks (id) ON DELETE CASCADE,
    rrf double precision NOT NULL,
    vector_rank integer,
    vector_score double precision,
    full_text_rank integer,
    full_text_score double precision,
    PRIMARY KEY (run, rank)
);

CREATE INDEX evidence_chunk ON evidence (chunk);
