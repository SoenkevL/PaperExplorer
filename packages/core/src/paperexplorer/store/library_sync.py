"""The Library version the last sync read up to."""

import psycopg
from psycopg.rows import TupleRow


class LibraryVersions:
    """The one `library_sync` row; the caller owns the transaction."""

    def __init__(self, conn: psycopg.Connection[TupleRow]) -> None:
        self._conn = conn

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
