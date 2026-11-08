"""Recorded searches for Evidence: a Citation's Citing Sentence, its Scope and the ranked Chunks."""

from collections.abc import Sequence

import psycopg
from psycopg.rows import TupleRow, dict_row
from pydantic import BaseModel

from paperexplorer.domain import CitationId, Scope
from paperexplorer.store.chunks import Chunks, Placing, ScoredChunk


class RetrievalRun(BaseModel):
    """One recorded search for Evidence, its Evidence best first."""

    id: int
    citation: CitationId
    citing_sentence: str
    model: str
    k: int
    depth: int
    evidence: list[ScoredChunk]


class RetrievalRuns:
    """Repository of Retrieval Runs; the caller owns the transaction."""

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn

    def find(
        self, citation: CitationId, scope: Scope, model: str, *, k: int, depth: int
    ) -> RetrievalRun | None:
        """The newest Run of this Citation over exactly this Scope, with these parameters."""
        with self._conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(
                "SELECT r.id, r.citation, r.citing_sentence, r.model, r.k, r.depth"
                " FROM retrieval_runs r"
                " WHERE r.citation = %s AND r.model = %s AND r.k = %s AND r.depth = %s"
                " AND %s::bigint[] = ARRAY("
                "   SELECT s.work FROM retrieval_run_works s WHERE s.run = r.id ORDER BY s.work)"
                " ORDER BY r.id DESC LIMIT 1",
                (citation, model, k, depth, sorted(scope.works)),
            ).fetchone()
            if row is None:
                return None
            ranked = cur.execute(
                "SELECT chunk, rrf, vector_rank, vector_score, full_text_rank, full_text_score"
                " FROM evidence WHERE run = %s ORDER BY rank",
                (row["id"],),
            ).fetchall()
        chunks = Chunks(self._conn)
        evidence: list[ScoredChunk] = []
        for e in ranked:
            stored = chunks.get(e["chunk"])
            if stored is None:  # a Chunk gone takes its row with it, so only a race reaches this
                return None
            evidence.append(
                ScoredChunk(
                    **dict(stored),
                    rrf=e["rrf"],
                    vector=_placing(e["vector_rank"], e["vector_score"]),
                    full_text=_placing(e["full_text_rank"], e["full_text_score"]),
                )
            )
        return RetrievalRun(**row, evidence=evidence)

    def record(
        self,
        citation: CitationId,
        citing_sentence: str,
        scope: Scope,
        model: str,
        *,
        k: int,
        depth: int,
        evidence: Sequence[ScoredChunk],
    ) -> RetrievalRun:
        """Store a search's ranking as a Run."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            row = cur.execute(
                "INSERT INTO retrieval_runs (citation, citing_sentence, model, k, depth)"
                " VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (citation, citing_sentence, model, k, depth),
            ).fetchone()
            assert row is not None
            run: int = row[0]
            cur.executemany(
                "INSERT INTO retrieval_run_works (run, work) VALUES (%s, %s)",
                [(run, work) for work in sorted(scope.works)],
            )
            cur.executemany(
                "INSERT INTO evidence (run, rank, chunk, rrf, vector_rank, vector_score,"
                " full_text_rank, full_text_score) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        run,
                        rank,
                        e.id,
                        e.rrf,
                        e.vector.rank if e.vector else None,
                        e.vector.score if e.vector else None,
                        e.full_text.rank if e.full_text else None,
                        e.full_text.score if e.full_text else None,
                    )
                    for rank, e in enumerate(evidence, start=1)
                ],
            )
        return RetrievalRun(
            id=run,
            citation=citation,
            citing_sentence=citing_sentence,
            model=model,
            k=k,
            depth=depth,
            evidence=list(evidence),
        )


def _placing(rank: int | None, score: float | None) -> Placing | None:
    return None if rank is None or score is None else Placing(rank=rank, score=score)
