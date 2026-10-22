"""A Work's text as parse read it: blocks in reading order and its printed bibliography."""

from collections.abc import Sequence

import psycopg
from psycopg.rows import TupleRow, class_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from paperexplorer.domain import WorkId
from paperexplorer.parsing import Block, ParsedWork, Reference, TextOrigin


def _json(values: Sequence[BaseModel]) -> Jsonb:
    return Jsonb([v.model_dump(mode="json") for v in values])


class WorkTexts:
    """Repository of parsed texts, one per Work; the caller owns the transaction."""

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn

    def replace(self, work: WorkId, parsed: ParsedWork) -> None:
        """Store `parsed` as the Work's text, dropping any earlier parse and what came from it."""
        with self._conn.transaction(), self._conn.cursor() as cur:
            cur.execute("DELETE FROM blocks WHERE work_id = %s", (work,))
            cur.execute("DELETE FROM work_references WHERE work_id = %s", (work,))
            cur.execute(
                "UPDATE works SET text_origin = %s, parser_versions = %s WHERE id = %s",
                (parsed.text_origin.value, Jsonb(parsed.parser_versions), work),
            )
            cur.executemany(
                "INSERT INTO blocks (work_id, position, kind, heading_path, page, boxes, text,"
                " sentences, citation_markers, ocr_confidence)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        work,
                        position,
                        b.kind.value,
                        b.heading_path,
                        b.page,
                        _json(b.boxes),
                        b.text,
                        _json(b.sentences),
                        _json(b.citation_markers),
                        b.ocr_confidence,
                    )
                    for position, b in enumerate(parsed.blocks)
                ],
            )
            cur.executemany(
                "INSERT INTO work_references (work_id, key, position, label, text, page, boxes,"
                " surnames, year, title, doi)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        work,
                        r.id,
                        position,
                        r.label,
                        r.text,
                        r.page,
                        _json(r.boxes),
                        r.surnames,
                        r.year,
                        r.title,
                        r.doi,
                    )
                    for position, r in enumerate(parsed.references)
                ],
            )

    def get(self, work: WorkId) -> ParsedWork | None:
        """The Work's text, or None before it has been parsed."""
        row = self._conn.execute(
            "SELECT text_origin, parser_versions FROM works WHERE id = %s", (work,)
        ).fetchone()
        if row is None or row[0] is None:
            return None
        with self._conn.cursor(row_factory=class_row(Block)) as cur:
            blocks = cur.execute(
                "SELECT kind, heading_path, page, boxes, text, sentences, citation_markers,"
                " ocr_confidence FROM blocks WHERE work_id = %s ORDER BY position",
                (work,),
            ).fetchall()
        with self._conn.cursor(row_factory=class_row(Reference)) as cur:
            references = cur.execute(
                "SELECT key AS id, label, text, page, boxes, surnames, year, title, doi"
                " FROM work_references WHERE work_id = %s ORDER BY position",
                (work,),
            ).fetchall()
        return ParsedWork(
            text_origin=TextOrigin(row[0]),
            blocks=blocks,
            references=references,
            parser_versions=row[1],
        )
