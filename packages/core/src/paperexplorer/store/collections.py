"""Collections and which Works are filed in them, as the last Library sync found them."""

from collections.abc import Iterable, Sequence

import psycopg
from psycopg.rows import TupleRow, class_row
from pydantic import BaseModel

from paperexplorer.domain import COLLECTION_DEPTH, Collection, WorkId
from paperexplorer.store.works import DONE_ALL

# The Library Items, each with whether every Stage in `done` is done.
_LIBRARY = f"SELECT id, {DONE_ALL} AS done FROM works WHERE zotero_key IS NOT NULL"


class BranchCount(BaseModel):
    """How many Works a branch holds, each once, and how many of them have every Stage done."""

    works: int
    ingested: int


class CollectionCount(Collection, BranchCount):
    """A Collection with the counts of its whole branch."""


class CollectionCounts(BaseModel):
    """Every Collection with its branch's counts, the whole Library's, and those filed nowhere."""

    collections: list[CollectionCount]
    library: BranchCount
    unfiled: BranchCount


class Collections:
    """Repository of the Library's Collections and each Work's filing; the caller commits."""

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn

    def record(self, collection: Collection) -> None:
        """Add a Collection, or replace its name and parent with what Zotero says now."""
        self._conn.execute(
            "INSERT INTO collections (key, name, parent_key) VALUES (%s, %s, %s)"
            " ON CONFLICT (key) DO UPDATE"
            " SET name = EXCLUDED.name, parent_key = EXCLUDED.parent_key",
            (collection.key, collection.name, collection.parent_key),
        )

    def remove(self, keys: Iterable[str]) -> int:
        """Forget these Collections, their subcollections and their filing; how many went.

        Zotero does not always name the subcollections: trashing marks only the one trashed.
        """
        return self._conn.execute(
            "WITH RECURSIVE branch (key, depth) AS ("
            " SELECT key, 0 FROM collections WHERE key = ANY(%s)"
            " UNION SELECT c.key, b.depth + 1 FROM collections c"
            " JOIN branch b ON c.parent_key = b.key WHERE b.depth < %s)"
            " DELETE FROM collections WHERE key IN (SELECT key FROM branch)",
            (list(keys), COLLECTION_DEPTH),
        ).rowcount

    def keys(self) -> set[str]:
        """The key of every stored Collection."""
        return {r[0] for r in self._conn.execute("SELECT key FROM collections").fetchall()}

    def file(self, work: WorkId, keys: Iterable[str]) -> None:
        """File the Work in exactly these Collections, skipping any key not stored."""
        self._conn.execute("DELETE FROM work_collections WHERE work_id = %s", (work,))
        self._conn.execute(
            "INSERT INTO work_collections (work_id, collection_key)"
            " SELECT %s, key FROM collections WHERE key = ANY(%s)",
            (work, list(keys)),
        )

    def list(self) -> list[Collection]:
        """Every stored Collection, by name."""
        with self._conn.cursor(row_factory=class_row(Collection)) as cur:
            return cur.execute(
                "SELECT key, name, parent_key FROM collections ORDER BY name, key"
            ).fetchall()

    def of_work(self, work: WorkId) -> list[str]:
        """The keys of the Collections the Work is filed in, sorted."""
        rows = self._conn.execute(
            "SELECT collection_key FROM work_collections WHERE work_id = %s"
            " ORDER BY collection_key",
            (work,),
        ).fetchall()
        return [r[0] for r in rows]

    def of_works(self, works: Iterable[WorkId]) -> dict[WorkId, list[str]]:
        """The keys of the Collections each Work is filed in, sorted; none if filed nowhere."""
        found: dict[WorkId, list[str]] = {w: [] for w in works}
        rows = self._conn.execute(
            "SELECT work_id, collection_key FROM work_collections WHERE work_id = ANY(%s)"
            " ORDER BY collection_key",
            (list(found),),
        ).fetchall()
        for work, key in rows:
            found[WorkId(work)].append(key)
        return found

    def counts(self, *, done: Sequence[str]) -> CollectionCounts:
        """Each Collection by name, its branch's Works counted once; Ingested is `done` all done."""
        stages = (list(done), len(done))
        with self._conn.cursor(row_factory=class_row(CollectionCount)) as cur:
            collections = cur.execute(
                "WITH RECURSIVE branch (top, key, depth) AS ("
                " SELECT key, key, 0 FROM collections"
                " UNION SELECT b.top, c.key, b.depth + 1 FROM collections c"
                " JOIN branch b ON c.parent_key = b.key WHERE b.depth < %s),"
                f" library AS ({_LIBRARY})"
                " SELECT c.key, c.name, c.parent_key, count(DISTINCT l.id) AS works,"
                " count(DISTINCT l.id) FILTER (WHERE l.done) AS ingested"
                " FROM collections c JOIN branch b ON b.top = c.key"
                " LEFT JOIN work_collections f ON f.collection_key = b.key"
                " LEFT JOIN library l ON l.id = f.work_id"
                " GROUP BY c.key ORDER BY c.name, c.key",
                (COLLECTION_DEPTH, *stages),
            ).fetchall()
        row = self._conn.execute(
            f"WITH library AS ({_LIBRARY}),"
            " unfiled AS (SELECT * FROM library l WHERE NOT EXISTS"
            " (SELECT 1 FROM work_collections f WHERE f.work_id = l.id))"
            " SELECT (SELECT count(*) FROM library), (SELECT count(*) FROM library WHERE done),"
            " (SELECT count(*) FROM unfiled), (SELECT count(*) FROM unfiled WHERE done)",
            stages,
        ).fetchone()
        assert row is not None
        return CollectionCounts(
            collections=collections,
            library=BranchCount(works=row[0], ingested=row[1]),
            unfiled=BranchCount(works=row[2], ingested=row[3]),
        )
