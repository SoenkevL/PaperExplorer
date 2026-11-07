"""Works: one row per Work, holding its Library Item's metadata snapshot (ADR 0001)."""

from collections.abc import Collection, Sequence
from typing import LiteralString, Self

import psycopg
from psycopg.rows import TupleRow, class_row, dict_row
from pydantic import BaseModel, ConfigDict, model_validator

from paperexplorer.domain import (
    COLLECTION_DEPTH,
    LibraryItem,
    RegistryRecord,
    Work,
    WorkId,
    normalize_doi,
)

_COLUMNS = """
    id, zotero_key, title, creators, year, doi, synced_at, text_origin, added_at AS added,
    CASE WHEN pdf_attachment_key IS NOT NULL THEN
        jsonb_build_object('key', pdf_attachment_key, 'filename', pdf_filename)
    END AS pdf
"""
_WORK = f"SELECT {_COLUMNS} FROM works"


class Branch(BaseModel):
    """Where the reader browses the Library: one Collection, its whole branch, or no Collection."""

    model_config = ConfigDict(frozen=True)

    collection: str | None = None
    deep: bool = False  # the Collection's subcollections too, to `COLLECTION_DEPTH`
    unfiled: bool = False

    @model_validator(mode="after")
    def _one_place(self) -> Self:
        if (self.collection is None) == (not self.unfiled):
            raise ValueError("a Branch is either one Collection or the unfiled Library Items")
        return self


class LibraryRow(BaseModel):
    """A Library Item found, and whether it sits in the Branch browsed."""

    work: Work
    in_branch: bool


_FILED_IN_BRANCH: LiteralString = (
    "EXISTS (SELECT 1 FROM work_collections f WHERE f.work_id = works.id"
    " AND f.collection_key IN (SELECT key FROM branch))"
)


def _in_branch(branch: Branch | None) -> tuple[LiteralString, LiteralString, list[object]]:
    """The CTE a Branch needs, a test of whether `works` is in it, and the CTE's parameters."""
    if branch is None:
        return "", "true", []
    if branch.unfiled:
        return "", "NOT EXISTS (SELECT 1 FROM work_collections f WHERE f.work_id = works.id)", []
    if not branch.deep:
        return (
            "WITH branch (key) AS (SELECT %s::text) ",
            _FILED_IN_BRANCH,
            [branch.collection],
        )
    return (
        "WITH RECURSIVE branch (key, depth) AS (SELECT %s::text, 0"
        " UNION SELECT c.key, b.depth + 1 FROM collections c"
        " JOIN branch b ON c.parent_key = b.key WHERE b.depth < %s) ",
        _FILED_IN_BRANCH,
        [branch.collection, COLLECTION_DEPTH],
    )


# Which of the Works holding one DOI is its Standing Item: the one the reader let stand, a Library
# Item, one with a PDF, the oldest.
_STANDING = (
    "stands_by_reader DESC, zotero_key IS NULL, pdf_attachment_key IS NULL, added_at NULLS LAST, id"
)
_CANDIDATES = 10
# Whether every Stage named in the first parameter (its length the second) is done for `works.id`.
DONE_ALL: LiteralString = (
    "(SELECT count(*) FROM stage_status s WHERE s.work_id = works.id"
    " AND s.state = 'done' AND s.stage = ANY(%s)) = %s"
)
_SEARCHED = "concat_ws(' ', title, array_to_string(creators, ' '), year, zotero_key, doi)"


def _search(query: str) -> tuple[LiteralString, list[str]]:
    """The WHERE clause for a search of the Library, and its parameters: every word must match."""
    words = [w.replace("\\", r"\\").replace("%", r"\%").replace("_", r"\_") for w in query.split()]
    where: LiteralString = " WHERE zotero_key IS NOT NULL"
    for _ in words:
        where += f" AND {_SEARCHED} ILIKE %s"
    return where, [f"%{w}%" for w in words]


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
            "UPDATE works SET title = %s, creators = %s, year = %s, doi = %s,"
            " pdf_attachment_key = %s, pdf_filename = %s, added_at = %s, synced_at = now()"
            " WHERE id = %s",
            (
                item.title,
                item.creators,
                item.year,
                normalize_doi(item.doi) if item.doi else None,
                item.pdf.key if item.pdf else None,
                item.pdf.filename if item.pdf else None,
                item.added,
                work,
            ),
        )

    def ensure_registry_work(self, record: RegistryRecord) -> WorkId:
        """The Work a registry record names, made from its metadata if no Work holds it yet.

        That is the Work standing for its DOI, else the one holding its OpenAlex ID; a new one keeps
        the registry's metadata, with no `synced_at`, until `catalog` gives it a Library Item.
        """
        doi = normalize_doi(record.doi) if record.doi else None
        found = self.standing_item(doi) if doi else None
        if found is None and record.openalex_id:
            row = self._conn.execute(
                "SELECT id FROM works WHERE openalex_id = %s", (record.openalex_id,)
            ).fetchone()
            found = WorkId(row[0]) if row else None
        if found is not None:
            if record.openalex_id:
                self._conn.execute(
                    "UPDATE works SET openalex_id = %(oa)s WHERE id = %(id)s"
                    " AND openalex_id IS NULL"
                    " AND NOT EXISTS (SELECT 1 FROM works WHERE openalex_id = %(oa)s)",
                    {"oa": record.openalex_id, "id": found},
                )
            return found
        row = self._conn.execute(
            "INSERT INTO works (doi, openalex_id, title, creators, year)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (doi, record.openalex_id, record.title, record.creators, record.year),
        ).fetchone()
        assert row is not None
        return WorkId(row[0])

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

    def library_items(
        self,
        query: str,
        limit: int,
        offset: int,
        *,
        done_first: Sequence[str],
        branch: Branch | None = None,
    ) -> list[LibraryRow]:
        """A page of the Library Items matching `query`: `done_first` all done, then the newest.

        With no query only the Branch's are listed; a query searches the whole Library, the
        Branch's matches first.
        """
        where, words = _search(query)
        cte, in_branch, cte_params = _in_branch(branch)
        only: LiteralString = "" if query.split() else f" AND {in_branch}"
        with self._conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                f"{cte}SELECT {in_branch} AS in_branch, {_COLUMNS} FROM works"
                + where
                + only
                + f" ORDER BY in_branch DESC, {DONE_ALL} DESC, added_at DESC NULLS LAST, id"
                " LIMIT %s OFFSET %s",
                [*cte_params, *words, list(done_first), len(done_first), limit, offset],
            ).fetchall()
        return [LibraryRow(in_branch=r.pop("in_branch"), work=Work.model_validate(r)) for r in rows]

    def library_count(self, query: str, branch: Branch | None = None) -> int:
        """How many Library Items match `query`, of those in the Branch if one is given."""
        where, words = _search(query)
        cte, in_branch, cte_params = _in_branch(branch)
        row = self._conn.execute(
            f"{cte}SELECT count(*) FROM works" + where + f" AND {in_branch}", [*cte_params, *words]
        ).fetchone()
        assert row is not None
        return row[0]

    def standing_item(self, doi: str) -> WorkId | None:
        """The Work of the Standing Item for a DOI, in any printed form, if anything holds it."""
        row = self._conn.execute(
            f"SELECT id FROM works WHERE doi = %s ORDER BY {_STANDING} LIMIT 1",
            (normalize_doi(doi),),
        ).fetchone()
        return WorkId(row[0]) if row else None

    def let_stand(self, work: WorkId) -> None:
        """Let this Work's Library Item stand for its DOI, over its other Duplicate Items."""
        self._conn.execute(
            "UPDATE works SET stands_by_reader = (id = %s)"
            " WHERE doi = (SELECT doi FROM works WHERE id = %s)",
            (work, work),
        )

    def duplicate_items(self) -> list[list[Work]]:
        """Each Work's Duplicate Items (Library Items holding one DOI), its Standing Item first."""
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

    def duplicate_item(self, zotero_key: str) -> Work | None:
        """The Work of this Library Item if another Library Item holds its DOI, else None."""
        found = self.find(zotero_key)
        if found is None or not any(found.id in {w.id for w in g} for g in self.duplicate_items()):
            return None
        return found

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

        A Work something uses (a Stage run, a Resolution, a Reader's Choice) stays, without a
        Library Item or PDF.
        """
        gone = self._conn.execute(
            "DELETE FROM works w WHERE zotero_key = %s"
            " AND NOT EXISTS (SELECT 1 FROM stage_status s WHERE s.work_id = w.id)"
            " AND NOT EXISTS (SELECT 1 FROM resolutions r"
            " WHERE w.id IN (r.resolved_work_id, r.doi_conflict_work_id))"
            " AND NOT EXISTS (SELECT 1 FROM readers_choices c WHERE c.chosen_work_id = w.id)",
            (zotero_key,),
        ).rowcount
        if not gone:
            self._conn.execute(
                "UPDATE works SET zotero_key = NULL, pdf_attachment_key = NULL,"
                " pdf_filename = NULL, stands_by_reader = false WHERE zotero_key = %s",
                (zotero_key,),
            )
        return bool(gone)

    def with_pdf_attachment(self, attachment_keys: Collection[str]) -> list[Work]:
        """The Works whose recorded PDF is one of these attachments."""
        with self._conn.cursor(row_factory=class_row(Work)) as cur:
            return cur.execute(
                _WORK + " WHERE pdf_attachment_key = ANY(%s) ORDER BY id", (list(attachment_keys),)
            ).fetchall()
