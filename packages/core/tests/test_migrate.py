import psycopg
from psycopg.rows import TupleRow

from paperexplorer.store import migrate


def test_first_run_applies_all_migrations_and_enables_pgvector(
    db: psycopg.Connection[TupleRow],
) -> None:
    applied = migrate(db)

    assert applied[0] == "0001_extensions.sql"
    row = db.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'").fetchone()
    assert row is not None


def test_second_run_applies_nothing(db: psycopg.Connection[TupleRow]) -> None:
    migrate(db)

    assert migrate(db) == []
