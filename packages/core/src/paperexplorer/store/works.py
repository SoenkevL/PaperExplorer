"""Works: one row per Work, holding its Library Item's metadata snapshot (ADR 0001)."""

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import TupleRow, class_row

from paperexplorer.domain import LibraryItem, Work, WorkId

_WORK = """
    SELECT id, zotero_key, title, creators, year, doi, synced_at, text_origin,
           CASE WHEN pdf_attachment_key IS NOT NULL THEN
               jsonb_build_object('key', pdf_attachment_key, 'filename', pdf_filename)
           END AS pdf
    FROM works
"""


class DuplicateWorkError(Exception):
    """Two Library Items claim the same identifier, so they would be one Work twice."""


class Works:
    """Repository of Works; the caller owns the transaction."""

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn

    def ensure(self, zotero_key: str) -> WorkId:
        """The Work recorded for a Library Item, created empty on first sight."""
        row = self._conn.execute(
            "INSERT INTO works (zotero_key) VALUES (%s)"
            " ON CONFLICT (zotero_key) DO UPDATE SET zotero_key = EXCLUDED.zotero_key"
            " RETURNING id",
            (zotero_key,),
        ).fetchone()
        assert row is not None
        return WorkId(row[0])

    def record_library_item(self, work: WorkId, item: LibraryItem) -> None:
        """Replace the Work's metadata snapshot with what its Library Item says now."""
        try:
            with self._conn.transaction():
                self._conn.execute(
                    "UPDATE works SET title = %s, creators = %s, year = %s, doi = lower(%s),"
                    " pdf_attachment_key = %s, pdf_filename = %s, synced_at = now()"
                    " WHERE id = %s",
                    (
                        item.title,
                        item.creators,
                        item.year,
                        item.doi,
                        item.pdf.key if item.pdf else None,
                        item.pdf.filename if item.pdf else None,
                        work,
                    ),
                )
        except UniqueViolation as e:
            row = self._conn.execute(
                "SELECT zotero_key FROM works WHERE doi = lower(%s)", (item.doi,)
            ).fetchone()
            other = row[0] if row else "another Work"
            raise DuplicateWorkError(f"{item.key} has DOI {item.doi}, which {other} has too") from e

    def get(self, work: WorkId) -> Work | None:
        """The Work with this id, if there is one."""
        with self._conn.cursor(row_factory=class_row(Work)) as cur:
            return cur.execute(_WORK + " WHERE id = %s", (work,)).fetchone()

    def list(self) -> list[Work]:
        """Every Work, in the order they were first seen."""
        with self._conn.cursor(row_factory=class_row(Work)) as cur:
            return cur.execute(_WORK + " ORDER BY id").fetchall()
