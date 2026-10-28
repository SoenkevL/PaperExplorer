"""A Work's Chunks in reading order, as the chunk Stage cut them, their embeddings and search."""

from collections.abc import Collection, Mapping, Sequence
from typing import Any, LiteralString

import psycopg
from psycopg.rows import TupleRow, dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from paperexplorer.domain import Chunk, ChunkId, Vector, WorkId
from paperexplorer.parsing import Span


class StoredChunk(BaseModel):
    """A Chunk with its Work and the id the store gave it."""

    id: ChunkId
    work: WorkId
    chunk: Chunk


class ChunkEmbedding(BaseModel):
    """A Chunk's Vector and the model that made it."""

    chunk: ChunkId
    model: str
    vector: Vector


class Placing(BaseModel):
    """Where one ranking put a Chunk: its rank, counted from 1, and the score it ranked by."""

    rank: int
    score: float


class ScoredChunk(StoredChunk):
    """A Chunk a search found, with its places in the vector and full-text rankings and their RRF.

    `vector.score` is the cosine similarity of the Chunk's Vector to the query's, `full_text.score`
    Postgres's `ts_rank`; a ranking that did not list the Chunk leaves its place empty. `rrf` sums
    1 / (60 + rank) over the rankings that list it.
    """

    rrf: float
    vector: Placing | None
    full_text: Placing | None


_COLUMNS = """
    c.id, c.work_id, c.position, c.block, lower(c.span) AS start, upper(c.span) AS "end",
    c.text, c.sentences, c.heading_path, c.page, c.boxes, c.tokens, c.flags, c.text_origin
"""
_CHUNKS = "SELECT" + _COLUMNS + "FROM chunks c"

# Hybrid search (design-v1): the nearest Vectors of one model and the English full-text matches,
# each ranked `depth` deep, fused by Reciprocal Rank Fusion with k=60. Ties go in reading order.
_SEARCH = (
    """
    WITH query AS (
        -- Any of the text's stems, not all: a question's words are rarely all in one Chunk.
        -- A tsquery's text quotes each lexeme, so ' & ' in it is only ever the AND operator.
        SELECT replace(plainto_tsquery('english', %(text)s)::text, ' & ', ' | ')::tsquery AS q
    ),
    nearest AS (
        -- ORDER BY the distance alone, then LIMIT: the shape the HNSW index serves
        SELECT e.chunk_id, 1 - (e.embedding <=> %(vector)s::vector) AS score
        FROM chunk_embeddings e
        WHERE e.work_id = ANY(%(works)s::bigint[]) AND e.model = %(model)s
        ORDER BY e.embedding <=> %(vector)s::vector
        LIMIT %(depth)s
    ),
    by_vector AS (
        SELECT n.chunk_id, n.score,
               row_number() OVER (ORDER BY n.score DESC, c.work_id, c.position) AS rank
        FROM nearest n JOIN chunks c ON c.id = n.chunk_id
    ),
    by_full_text AS (
        SELECT c.id AS chunk_id, ts_rank(c.text_search, query.q) AS score,
               row_number() OVER (
                   ORDER BY ts_rank(c.text_search, query.q) DESC, c.work_id, c.position
               ) AS rank
        FROM chunks c CROSS JOIN query
        WHERE c.work_id = ANY(%(works)s::bigint[]) AND c.text_search @@ query.q
        ORDER BY rank
        LIMIT %(depth)s
    ),
    fused AS (
        SELECT coalesce(v.chunk_id, t.chunk_id) AS chunk_id,
               v.rank AS vector_rank, v.score AS vector_score,
               t.rank AS full_text_rank, t.score AS full_text_score,
               coalesce(1 / (60 + v.rank)::float8, 0) + coalesce(1 / (60 + t.rank)::float8, 0)
                   AS rrf
        FROM by_vector v FULL JOIN by_full_text t ON t.chunk_id = v.chunk_id
    )
    SELECT"""
    + _COLUMNS
    + """, f.rrf, f.vector_rank, f.vector_score, f.full_text_rank, f.full_text_score
    FROM fused f JOIN chunks c ON c.id = f.chunk_id
    ORDER BY f.rrf DESC, c.work_id, c.position
    LIMIT %(k)s
"""
)


def _stored(row: dict[str, Any]) -> StoredChunk:
    start, end = row.pop("start"), row.pop("end")
    return StoredChunk(
        id=row.pop("id"),
        work=row.pop("work_id"),
        chunk=Chunk.model_validate(row | {"span": Span(start=start, end=end)}),
    )


def _placing(rank: int | None, score: float | None) -> Placing | None:
    return None if rank is None or score is None else Placing(rank=rank, score=score)


def _scored(row: dict[str, Any]) -> ScoredChunk:
    rrf = row.pop("rrf")
    vector = _placing(row.pop("vector_rank"), row.pop("vector_score"))
    full_text = _placing(row.pop("full_text_rank"), row.pop("full_text_score"))
    return ScoredChunk(**dict(_stored(row)), rrf=rrf, vector=vector, full_text=full_text)


class Chunks:
    """Repository of Chunks per Work; the caller owns the transaction."""

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn

    def replace(self, work: WorkId, chunks: Sequence[Chunk]) -> None:
        """Store `chunks` as the Work's, dropping any cut before; each must name a stored block."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute("DELETE FROM chunks WHERE work_id = %s", (work,))
            cur.executemany(
                "INSERT INTO chunks (work_id, position, block, span, text, sentences,"
                " heading_path, page, boxes, tokens, flags, text_origin)"
                " VALUES (%s, %s, %s, int4range(%s, %s), %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        work,
                        c.position,
                        c.block,
                        c.span.start,
                        c.span.end,
                        c.text,
                        Jsonb([s.model_dump(mode="json") for s in c.sentences]),
                        c.heading_path,
                        c.page,
                        Jsonb([b.model_dump(mode="json") for b in c.boxes]),
                        c.tokens,
                        [f.value for f in c.flags],
                        c.text_origin.value,
                    )
                    for c in chunks
                ],
            )

    def for_work(self, work: WorkId) -> list[StoredChunk]:
        """The Work's Chunks in reading order."""
        return self._query(_CHUNKS + " WHERE c.work_id = %s ORDER BY c.position", (work,))

    def get(self, chunk: ChunkId) -> StoredChunk | None:
        """One Chunk, if it exists."""
        found = self._query(_CHUNKS + " WHERE c.id = %s", (chunk,))
        return found[0] if found else None

    def around(self, chunk: ChunkId, n: int = 1) -> list[StoredChunk]:
        """The Chunk and up to `n` Chunks either side of it in its Work, in reading order."""
        return self._query(
            _CHUNKS + " JOIN chunks a ON a.work_id = c.work_id"
            " WHERE a.id = %s AND c.position BETWEEN a.position - %s AND a.position + %s"
            " ORDER BY c.position",
            (chunk, n, n),
        )

    def under(self, work: WorkId, heading_path: Sequence[str]) -> list[StoredChunk]:
        """The Work's Chunks whose Heading Path starts with `heading_path`, in reading order."""
        return self._query(
            _CHUNKS + " WHERE c.work_id = %s AND c.heading_path[1:%s] = %s::text[]"
            " ORDER BY c.position",
            (work, len(heading_path), list(heading_path)),
        )

    def record_embeddings(
        self, work: WorkId, model: str, vectors: Mapping[ChunkId, Vector]
    ) -> None:
        """Store `model`'s Vector for each of the Work's Chunks given, replacing any earlier one."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO chunk_embeddings (chunk_id, work_id, model, embedding)"
                " VALUES (%s, %s, %s, %s::vector)"
                " ON CONFLICT (chunk_id) DO UPDATE"
                " SET model = excluded.model, embedding = excluded.embedding",
                [(chunk, work, model, vector) for chunk, vector in vectors.items()],
            )

    def without_embedding(self, work: WorkId, model: str) -> list[StoredChunk]:
        """The Work's Chunks that have no Vector from `model`, in reading order."""
        return self._query(
            _CHUNKS + " LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id AND e.model = %s"
            " WHERE c.work_id = %s AND e.chunk_id IS NULL ORDER BY c.position",
            (model, work),
        )

    def embeddings(self, work: WorkId) -> list[ChunkEmbedding]:
        """The Work's embedded Chunks' Vectors, in reading order."""
        with self._conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                "SELECT e.chunk_id AS chunk, e.model, e.embedding::real[] AS vector"
                " FROM chunk_embeddings e JOIN chunks c ON c.id = e.chunk_id"
                " WHERE e.work_id = %s ORDER BY c.position",
                (work,),
            )
            return [ChunkEmbedding.model_validate(row) for row in rows]

    def search(
        self,
        works: Collection[WorkId],
        model: str,
        vector: Vector,
        text: str,
        *,
        k: int,
        depth: int,
    ) -> list[ScoredChunk]:
        """The `k` Chunks of `works` best by RRF of two rankings, each `depth` deep.

        One ranks by cosine distance from `vector` among `model`'s Vectors, the other by English
        full text matching any stem of `text`.
        """
        params = {
            "works": list(works),
            "model": model,
            "vector": vector,
            "text": text,
            "k": k,
            "depth": depth,
        }
        with self._conn.transaction(), self._conn.cursor(row_factory=dict_row) as cur:
            # A Scope filter is applied after the HNSW index finds its nearest Vectors; scanning
            # on until `depth` of them pass keeps a small Scope among many Works from coming back
            # short. The order may relax slightly; by_vector ranks them strictly again.
            cur.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
            return [_scored(row) for row in cur.execute(_SEARCH, params)]

    def _query(self, query: LiteralString, params: tuple[object, ...]) -> list[StoredChunk]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            return [_stored(row) for row in cur.execute(query, params)]
