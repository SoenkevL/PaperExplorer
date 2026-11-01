-- A Reader's Choice: the Work the reader picked for a Reference, or none (design-resolution.md).
-- Keyed by the citing Work and the Reference's printed text, not its parser key, so a re-parse
-- that prints the Reference the same keeps it; `resolutions` go with a re-parse, a choice must
-- not. A null `chosen_work_id` is a choice of none. A Resolution the reader set has layer
-- `reader`.
CREATE TABLE readers_choices (
    work_id bigint NOT NULL REFERENCES works ON DELETE CASCADE,
    printed text NOT NULL,
    chosen_work_id bigint REFERENCES works,
    chosen_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (work_id, printed)
);

ALTER TABLE resolutions DROP CONSTRAINT resolutions_layer_check;
ALTER TABLE resolutions ADD CONSTRAINT resolutions_layer_check
    CHECK (layer IN ('library', 'crossref', 'openalex', 'reader'));
