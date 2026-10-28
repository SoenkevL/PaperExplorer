-- One embedding per Chunk, with the name of the model that made it, so a Vector is compared
-- only with Vectors of the same model. Re-chunking deletes the Chunks and their embeddings with
-- them; the embed Stage then embeds the new Chunks. 1024 dimensions: bge-m3's.
CREATE TABLE chunk_embeddings (
    chunk_id bigint PRIMARY KEY,
    work_id bigint NOT NULL,
    model text NOT NULL,
    embedding vector(1024) NOT NULL,
    FOREIGN KEY (work_id, chunk_id) REFERENCES chunks (work_id, id) ON DELETE CASCADE
);

-- the vector half of retrieval: nearest Chunks by cosine distance (`<=>`)
CREATE INDEX chunk_embeddings_hnsw ON chunk_embeddings USING hnsw (embedding vector_cosine_ops);
