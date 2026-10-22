"""A Work's Citations and their Citation Links, as extract_citations found them."""

from typing import Any

import psycopg
from psycopg.rows import TupleRow, dict_row
from pydantic import BaseModel

from paperexplorer.citations import Citation, CitationGraph, CitationLink
from paperexplorer.domain import CitationId, WorkId
from paperexplorer.parsing import Reference, Span


class StoredCitation(BaseModel):
    """A Citation with the id the store gave it."""

    id: CitationId
    citation: Citation


class CitationDetail(BaseModel):
    """One Citation of a Work, with the Reference its Citation Link points at."""

    id: CitationId
    work: WorkId
    citation: Citation
    reference: Reference | None


_CITATIONS = """
    SELECT c.id, c.work_id, c.block, lower(c.span) AS start, upper(c.span) AS "end", c.text,
           c.citing_sentence, l.confidence, l.evidence, r.key AS reference_key,
           r.label, r.text AS reference_text, r.page, r.boxes, r.surnames, r.year, r.title, r.doi
    FROM citations c
    LEFT JOIN citation_links l ON l.citation_id = c.id
    LEFT JOIN work_references r ON r.id = l.reference_id
"""


def _citation(row: dict[str, Any]) -> Citation:
    link = (
        CitationLink(
            reference_id=row["reference_key"],
            confidence=row["confidence"],
            evidence=row["evidence"],
        )
        if row["reference_key"] is not None
        else None
    )
    return Citation(
        block=row["block"],
        span=Span(start=row["start"], end=row["end"]),
        text=row["text"],
        citing_sentence=row["citing_sentence"],
        link=link,
    )


class CitationGraphs:
    """Repository of Citations and Citation Links per Work; the caller owns the transaction."""

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn

    def replace(self, work: WorkId, graph: CitationGraph) -> None:
        """Store the graph's Citations as the Work's, dropping any found before.

        The References are the Work's own, stored by parse; a link to any other is refused.
        """
        references: dict[str, int] = {
            key: id_
            for key, id_ in self._conn.execute(
                "SELECT key, id FROM work_references WHERE work_id = %s", (work,)
            )
        }
        unknown = {
            c.link.reference_id
            for c in graph.citations
            if c.link and c.link.reference_id not in references
        }
        if unknown:
            raise ValueError(f"Work {work} has no References {sorted(unknown)}")
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute("DELETE FROM citations WHERE work_id = %s", (work,))
            cur.executemany(
                "INSERT INTO citations (work_id, position, block, span, text, citing_sentence)"
                " VALUES (%s, %s, %s, int4range(%s, %s), %s, %s)",
                [
                    (work, i, c.block, c.span.start, c.span.end, c.text, c.citing_sentence)
                    for i, c in enumerate(graph.citations)
                ],
            )
            cur.executemany(
                "INSERT INTO citation_links (citation_id, work_id, reference_id, confidence,"
                " evidence) SELECT id, work_id, %s, %s, %s FROM citations"
                " WHERE work_id = %s AND position = %s",
                [
                    (
                        references[c.link.reference_id],
                        c.link.confidence,
                        [e.value for e in c.link.evidence],
                        work,
                        i,
                    )
                    for i, c in enumerate(graph.citations)
                    if c.link
                ],
            )

    def for_work(self, work: WorkId) -> list[StoredCitation]:
        """The Work's Citations in the order they were found."""
        with self._conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(_CITATIONS + " WHERE c.work_id = %s ORDER BY c.position", (work,))
            return [StoredCitation(id=row["id"], citation=_citation(row)) for row in rows]

    def get(self, citation: CitationId) -> CitationDetail | None:
        """One Citation with its linked Reference, if the Citation exists."""
        with self._conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute(_CITATIONS + " WHERE c.id = %s", (citation,)).fetchone()
        if row is None:
            return None
        reference = (
            Reference(
                id=row["reference_key"],
                label=row["label"],
                text=row["reference_text"],
                page=row["page"],
                boxes=row["boxes"],
                surnames=row["surnames"],
                year=row["year"],
                title=row["title"],
                doi=row["doi"],
            )
            if row["reference_key"] is not None
            else None
        )
        return CitationDetail(
            id=row["id"], work=row["work_id"], citation=_citation(row), reference=reference
        )
