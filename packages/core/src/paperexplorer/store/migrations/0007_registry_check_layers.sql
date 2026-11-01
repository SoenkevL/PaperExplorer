-- Registry Checks (design-resolution.md): a Resolution's layer also names the registry that
-- matched it. Their responses go to `registry_responses` (0006) under `crossref:doi`,
-- `openalex:doi`, `openalex:title` and `crossref:bibliographic`.
ALTER TABLE resolutions DROP CONSTRAINT resolutions_layer_check;
ALTER TABLE resolutions ADD CONSTRAINT resolutions_layer_check
    CHECK (layer IN ('library', 'crossref', 'openalex'));
