"""Shared fixtures: real pgvector Postgres (ADR 0005), fixture PDFs, recorded GROBID output."""

import hashlib
import os
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import docker
import psycopg
import pytest
from psycopg.rows import TupleRow
from testcontainers.community.postgres import PostgresContainer

from paperexplorer.config import Settings
from paperexplorer.parsing import ParsedWork, parse

PGVECTOR_IMAGE = "pgvector/pgvector:pg17"
FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures"
# Recorded output only: a GROBID cache miss fails instead of calling out.
UNREACHABLE = "http://127.0.0.1:9"


def fixture_pdf(name: str) -> Path:
    """Path of `tests/fixtures/pdfs/<name>.pdf`."""
    return FIXTURES / "pdfs" / f"{name}.pdf"


@pytest.fixture
def recorded_settings(tmp_path: Path) -> Settings:
    """Settings whose GROBID cache holds the recorded output for every fixture PDF."""
    cache = tmp_path / "cache" / "grobid"
    cache.mkdir(parents=True)
    for tei in (FIXTURES / "grobid").glob("*.tei.xml"):
        pdf = fixture_pdf(tei.name.removesuffix(".tei.xml"))
        digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
        shutil.copy(tei, cache / f"{digest}.tei.xml")
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        data_dir=tmp_path,
        grobid_url=UNREACHABLE,
        tessdata_dir=FIXTURES / "tessdata",
    )


@pytest.fixture
def parse_fixture(recorded_settings: Settings) -> Callable[[str], ParsedWork]:
    """Parse a fixture PDF by name, offline."""
    return lambda name: parse(fixture_pdf(name), recorded_settings)


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
