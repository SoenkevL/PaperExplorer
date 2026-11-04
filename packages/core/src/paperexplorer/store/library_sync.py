"""The Library version the last sync read up to."""

import psycopg
from psycopg.rows import TupleRow

# Any fixed number: Postgres advisory locks have no names, and this is the only one here.
_SYNC_LOCK = 7_210_001


class LibraryVersions:
    """The one `library_sync` row; the caller owns the transaction."""

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn

    def lock(self) -> None:
        """Wait for any other sync, then hold them off until this transaction ends."""
        self._conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SYNC_LOCK,))

    def last(self) -> int | None:
        """The version the last sync read up to; None before the first."""
        row = self._conn.execute("SELECT version FROM library_sync").fetchone()
        return row[0] if row else None

    def record(self, version: int) -> None:
        """Remember that the Works now hold the Library as of this version."""
        self._conn.execute(
            "INSERT INTO library_sync (version, synced_at) VALUES (%s, now())"
            " ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, synced_at = now()",
            (version,),
        )
