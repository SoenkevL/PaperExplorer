"""A Work's Chunks in reading order, as the chunk Stage cut them, and their embeddings."""

from collections.abc import Mapping, Sequence
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


_CHUNKS = """
    SELECT c.id, c.work_id, c.position, c.block, lower(c.span) AS start, upper(c.span) AS "end",
           c.text, c.sentences, c.heading_path, c.page, c.boxes, c.tokens, c.flags, c.text_origin
    FROM chunks c
"""


def _stored(row: dict[str, Any]) -> StoredChunk:
    start, end = row.pop("start"), row.pop("end")
    return StoredChunk(
        id=row.pop("id"),
        work=row.pop("work_id"),
        chunk=Chunk.model_validate(row | {"span": Span(start=start, end=end)}),
    )


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

    def _query(self, query: LiteralString, params: tuple[object, ...]) -> list[StoredChunk]:
        with self._conn.cursor(row_factory=dict_row) as cur:
            return [_stored(row) for row in cur.execute(query, params)]
