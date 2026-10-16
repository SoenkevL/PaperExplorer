"""Shared fixtures: a real, throwaway pgvector Postgres per test session (ADR 0005)."""

import os
from collections.abc import Iterator

import docker
import psycopg
import pytest
from psycopg.rows import TupleRow
from testcontainers.community.postgres import PostgresContainer

PGVECTOR_IMAGE = "pgvector/pgvector:pg17"


def _docker_available() -> bool:
    try:
        docker.from_env().ping()
    except Exception:
        return False
    return True


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """URL of a session-wide pgvector Postgres.

    `PE_TEST_DATABASE_URL` points at an existing empty database instead of starting a container.
    """
    if url := os.environ.get("PE_TEST_DATABASE_URL"):
        yield url
        return
    if not _docker_available():
        pytest.skip("Docker is not running; store tests need it (ADR 0005)")
    with PostgresContainer(PGVECTOR_IMAGE, driver=None) as pg:
        yield pg.get_connection_url()


@pytest.fixture
def db(postgres_url: str) -> Iterator[psycopg.Connection[TupleRow]]:
    """A connection whose changes are rolled back after each test."""
    with psycopg.connect(postgres_url) as conn, conn.transaction(force_rollback=True):
        yield conn
