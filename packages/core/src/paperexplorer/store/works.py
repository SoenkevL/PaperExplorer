"""Works: one row per Work, holding its Library Item's metadata snapshot (ADR 0001)."""

import psycopg
from psycopg.rows import TupleRow, class_row

from paperexplorer.domain import LibraryItem, Work, WorkId

_WORK = """
    SELECT id, zotero_key, title, creators, year, doi, synced_at, text_origin,
           CASE WHEN pdf_attachment_key IS NOT NULL THEN
               jsonb_build_object('key', pdf_attachment_key, 'filename', pdf_filename)
           END AS pdf
    FROM works
"""


# Which of the Works holding one DOI stands for it: the reader's pick, a Library Item, one with
# a PDF, the older.
_STANDING = (
    "doi_chosen DESC, zotero_key IS NULL, pdf_attachment_key IS NULL, added_at NULLS LAST, id"
)
_CANDIDATES = 10


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
        self._conn.execute(
            "UPDATE works SET title = %s, creators = %s, year = %s, doi = lower(%s),"
            " pdf_attachment_key = %s, pdf_filename = %s, added_at = %s, synced_at = now()"
            " WHERE id = %s",
            (
                item.title,
                item.creators,
                item.year,
                item.doi,
                item.pdf.key if item.pdf else None,
                item.pdf.filename if item.pdf else None,
                item.added,
                work,
            ),
        )

    def get(self, work: WorkId) -> Work | None:
        """The Work with this id, if there is one."""
        with self._conn.cursor(row_factory=class_row(Work)) as cur:
            return cur.execute(_WORK + " WHERE id = %s", (work,)).fetchone()

    def find(self, zotero_key: str) -> Work | None:
        """The Work recorded for a Library Item, if there is one; unlike `ensure`, creates none."""
        with self._conn.cursor(row_factory=class_row(Work)) as cur:
            return cur.execute(_WORK + " WHERE zotero_key = %s", (zotero_key,)).fetchone()

    def list(self) -> list[Work]:
        """Every Work, in the order they were first seen."""
        with self._conn.cursor(row_factory=class_row(Work)) as cur:
            return cur.execute(_WORK + " ORDER BY id").fetchall()

    def with_stage_runs(self) -> list[Work]:
        """The Works a Stage ran for, in the order first seen; Library-only Works are left out."""
        with self._conn.cursor(row_factory=class_row(Work)) as cur:
            return cur.execute(
                _WORK + " WHERE EXISTS (SELECT 1 FROM stage_status s WHERE s.work_id = works.id)"
                " ORDER BY id"
            ).fetchall()

    def stand_in(self, doi: str) -> WorkId | None:
        """The Work that stands for a DOI, when two Library Items hold it."""
        row = self._conn.execute(
            f"SELECT id FROM works WHERE doi = lower(%s) ORDER BY {_STANDING} LIMIT 1", (doi,)
        ).fetchone()
        return WorkId(row[0]) if row else None

    def choose(self, work: WorkId) -> None:
        """Make this Work the one that stands for its DOI, as the reader chose."""
        self._conn.execute(
            "UPDATE works SET doi_chosen = (id = %s)"
            " WHERE doi = (SELECT doi FROM works WHERE id = %s)",
            (work, work),
        )

    def duplicates(self) -> list[list[Work]]:
        """Each DOI that two or more Library Items hold: its Works, the one standing first."""
        with self._conn.cursor(row_factory=class_row(Work)) as cur:
            rows = cur.execute(
                _WORK + " WHERE zotero_key IS NOT NULL AND doi IN ("
                "SELECT doi FROM works WHERE zotero_key IS NOT NULL"
                " GROUP BY doi HAVING count(*) > 1)"
                f" ORDER BY doi, {_STANDING}"
            ).fetchall()
        groups: dict[str | None, list[Work]] = {}
        for w in rows:
            groups.setdefault(w.doi, []).append(w)
        return list(groups.values())

    def title_candidates(self, title: str) -> list[Work]:
        """Works whose title is near this one, or holds it (a missing subtitle), nearest first."""
        with self._conn.cursor(row_factory=class_row(Work)) as cur:
            return cur.execute(
                _WORK + " WHERE title %% %(t)s OR %(t)s <%% title OR title <%% %(t)s"
                " ORDER BY greatest(similarity(title, %(t)s), word_similarity(%(t)s, title),"
                " word_similarity(title, %(t)s)) DESC, id LIMIT %(n)s",
                {"t": title, "n": _CANDIDATES},
            ).fetchall()

    def zotero_keys(self) -> set[str]:
        """The keys of every Library Item recorded as a Work."""
        rows = self._conn.execute("SELECT zotero_key FROM works WHERE zotero_key IS NOT NULL")
        return {r[0] for r in rows.fetchall()}

    def remove_library_item(self, zotero_key: str) -> bool:
        """Forget a Library Item that left the Library; True if its Work went with it.

        A Work something uses (a Stage run, a Resolution) stays, without a Library Item or PDF.
        """
        gone = self._conn.execute(
            "DELETE FROM works w WHERE zotero_key = %s"
            " AND NOT EXISTS (SELECT 1 FROM stage_status s WHERE s.work_id = w.id)"
            " AND NOT EXISTS (SELECT 1 FROM resolutions r"
            " WHERE w.id IN (r.resolved_work_id, r.doi_conflict_work_id))",
            (zotero_key,),
        ).rowcount
        if not gone:
            self._conn.execute(
                "UPDATE works SET zotero_key = NULL, pdf_attachment_key = NULL,"
                " pdf_filename = NULL, doi_chosen = false WHERE zotero_key = %s",
                (zotero_key,),
            )
        return bool(gone)
