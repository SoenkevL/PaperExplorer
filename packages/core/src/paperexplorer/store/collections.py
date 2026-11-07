"""Collections and which Works are filed in them, as the last Library sync found them."""

from collections.abc import Iterable

import psycopg
from psycopg.rows import TupleRow, class_row

from paperexplorer.domain import COLLECTION_DEPTH, Collection, WorkId


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
