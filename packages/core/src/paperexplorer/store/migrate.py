"""Apply the numbered `.sql` files in `migrations/`, each exactly once, in order."""

import logging
from importlib import resources
from typing import LiteralString, cast

import psycopg
from psycopg.rows import TupleRow

logger = logging.getLogger(__name__)

_MIGRATIONS_PACKAGE = "paperexplorer.store.migrations"


def migrate(conn: psycopg.Connection[TupleRow]) -> list[str]:
    """Apply pending migrations and return their file names; already-applied ones are skipped."""
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " name text PRIMARY KEY,"
            " applied_at timestamptz NOT NULL DEFAULT now())"
        )
    applied = {str(row[0]) for row in conn.execute("SELECT name FROM schema_migrations")}
    files = sorted(
        (f for f in resources.files(_MIGRATIONS_PACKAGE).iterdir() if f.name.endswith(".sql")),
        key=lambda f: f.name,
    )
    newly_applied: list[str] = []
    for file in files:
        if file.name in applied:
            continue
        # Packaged migration files are trusted SQL, never user input.
        statements = cast(LiteralString, file.read_text(encoding="utf-8"))
        with conn.transaction():
            conn.execute(statements)
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (file.name,))
        logger.info("Applied migration %s", file.name)
        newly_applied.append(file.name)
    logger.info("Migrations up to date (%d new)", len(newly_applied))
    return newly_applied
