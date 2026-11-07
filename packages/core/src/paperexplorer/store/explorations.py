"""Explorations, the PaperExplorer Collection, and the Filings PaperExplorer made in Zotero."""

from collections.abc import Iterable

import psycopg
from psycopg.rows import TupleRow, class_row
from pydantic import BaseModel

from paperexplorer.domain import Exploration, Filing, FilingState, LibraryItem, WorkId

_EXPLORATION = (
    "SELECT id, seed_id AS seed, collection_key, name, created, seed_tagged, started_at, ended_at"
    " FROM explorations"
)
_FILING = "SELECT work_id AS work, item_key, state, error, updated_at FROM filings"


class PaperExplorerCollection(BaseModel):
    """The PaperExplorer Collection's key, and whether Zotero holds it yet."""

    key: str
    created: bool


class Explorations:
    """Repository of Explorations and their Filings; the caller commits."""

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn

    def root(self) -> PaperExplorerCollection | None:
        """The PaperExplorer Collection adopted or made, if any."""
        with self._conn.cursor(row_factory=class_row(PaperExplorerCollection)) as cur:
            return cur.execute("SELECT key, created FROM paperexplorer_collection").fetchone()

    def set_root(self, key: str, *, created: bool) -> None:
        """Record the PaperExplorer Collection's key, replacing any earlier one."""
        self._conn.execute(
            "INSERT INTO paperexplorer_collection (key, created) VALUES (%s, %s)"
            " ON CONFLICT (only_one) DO UPDATE SET key = EXCLUDED.key, created = EXCLUDED.created",
            (key, created),
        )

    def start(self, seed: WorkId, collection_key: str, name: str) -> Exploration:
        """A new Exploration of the Seed, its Collection's key made but not yet created."""
        with self._conn.cursor(row_factory=class_row(Exploration)) as cur:
            row = cur.execute(
                "INSERT INTO explorations (seed_id, collection_key, name) VALUES (%s, %s, %s)"
                " RETURNING id, seed_id AS seed, collection_key, name, created, seed_tagged,"
                " started_at, ended_at",
                (seed, collection_key, name),
            ).fetchone()
        assert row is not None
        return row

    def of_seed(self, seed: WorkId) -> Exploration | None:
        """The Seed's Exploration under way, if it has one."""
        with self._conn.cursor(row_factory=class_row(Exploration)) as cur:
            return cur.execute(
                f"{_EXPLORATION} WHERE seed_id = %s AND ended_at IS NULL", (seed,)
            ).fetchone()

    def under_way(self) -> list[Exploration]:
        """Every Exploration not ended, oldest first."""
        with self._conn.cursor(row_factory=class_row(Exploration)) as cur:
            return cur.execute(f"{_EXPLORATION} WHERE ended_at IS NULL ORDER BY id").fetchall()

    def mark_created(self, exploration: int) -> None:
        """Zotero holds the Exploration Collection now."""
        self._conn.execute("UPDATE explorations SET created = true WHERE id = %s", (exploration,))

    def mark_seed_tagged(self, exploration: int, *, tagged: bool) -> None:
        """Whether the Seed carries `pe:seed` for this Exploration."""
        self._conn.execute(
            "UPDATE explorations SET seed_tagged = %s WHERE id = %s", (tagged, exploration)
        )

    def end(self, exploration: int) -> None:
        """End the Exploration: its Filings go, its Seed's tag waits for `untag_pending`."""
        self._conn.execute("DELETE FROM filings WHERE exploration_id = %s", (exploration,))
        self._conn.execute(
            "UPDATE explorations SET ended_at = now() WHERE id = %s AND ended_at IS NULL",
            (exploration,),
        )

    def end_gone(self, held: Iterable[str]) -> list[Exploration]:
        """End the created Explorations whose Collection the Library no longer holds."""
        gone = [e for e in self.under_way() if e.created and e.collection_key not in set(held)]
        for exploration in gone:
            self.end(exploration.id)
        return gone

    def untag_pending(self) -> list[Exploration]:
        """Ended Explorations whose Seed still carries `pe:seed` and is no other's Seed."""
        with self._conn.cursor(row_factory=class_row(Exploration)) as cur:
            return cur.execute(
                f"{_EXPLORATION} e WHERE ended_at IS NOT NULL AND seed_tagged"
                " AND NOT EXISTS (SELECT 1 FROM explorations o"
                " WHERE o.seed_id = e.seed_id AND o.ended_at IS NULL)"
                " ORDER BY id"
            ).fetchall()

    def filings(self, exploration: int) -> list[Filing]:
        """The Exploration's Filings, by Work."""
        with self._conn.cursor(row_factory=class_row(Filing)) as cur:
            return cur.execute(
                f"{_FILING} WHERE exploration_id = %s ORDER BY work_id", (exploration,)
            ).fetchall()

    def record_filing(
        self,
        exploration: int,
        work: WorkId,
        item_key: str,
        state: FilingState,
        error: str | None = None,
    ) -> None:
        """Record where a Work's Filing stands, replacing what was recorded."""
        self._conn.execute(
            "INSERT INTO filings (exploration_id, work_id, item_key, state, error)"
            " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (exploration_id, work_id) DO UPDATE"
            " SET item_key = EXCLUDED.item_key, state = EXCLUDED.state,"
            " error = EXCLUDED.error, updated_at = now()",
            (exploration, work, item_key, state, error),
        )

    def forget_filing(self, exploration: int, work: WorkId) -> None:
        """Drop a Filing PaperExplorer took back."""
        self._conn.execute(
            "DELETE FROM filings WHERE exploration_id = %s AND work_id = %s", (exploration, work)
        )

    def note_items(self, items: Iterable[LibraryItem]) -> int:
        """Mark as the reader's removal each Filing whose item left its Exploration Collection."""
        removed = 0
        for item in items:
            removed += self._conn.execute(
                "UPDATE filings f SET state = 'removed_by_reader', updated_at = now()"
                " FROM explorations e WHERE e.id = f.exploration_id AND e.ended_at IS NULL"
                " AND f.item_key = %s AND f.state = 'filed' AND NOT e.collection_key = ANY(%s)",
                (item.key, item.collections),
            ).rowcount
        return removed
