"""Shared fixtures: real pgvector Postgres (ADR 0005), fixture PDFs, recorded GROBID output."""

import hashlib
import os
import shutil
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import docker
import httpx
import psycopg
import pytest
from psycopg.rows import TupleRow
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer

from paperexplorer.config import Settings
from paperexplorer.parsing import ParsedWork, parse
from paperexplorer.store import migrate

PGVECTOR_IMAGE = "pgvector/pgvector:pg17"
GROBID_IMAGE = "grobid/grobid:0.9.1-crf"  # the image the recordings came from (compose.yaml)
FIXTURES = Path(__file__).resolve().parent / "tests" / "fixtures"
# Recorded output only: a GROBID cache miss fails instead of calling out.
UNREACHABLE = "http://127.0.0.1:9"


def _pdf(name: str) -> Path:
    return FIXTURES / "pdfs" / f"{name}.pdf"


@pytest.fixture
def fixture_pdf() -> Callable[[str], Path]:
    """Path of `tests/fixtures/pdfs/<name>.pdf`, by name."""
    return _pdf


@pytest.fixture
def recorded_settings(tmp_path: Path) -> Settings:
    """Settings whose GROBID cache holds the recorded output for every fixture PDF."""
    cache = tmp_path / "cache" / "grobid"
    cache.mkdir(parents=True)
    for tei in (FIXTURES / "grobid").glob("*.tei.xml"):
        pdf = _pdf(tei.name.removesuffix(".tei.xml"))
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
    return lambda name: parse(_pdf(name), recorded_settings)


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


@pytest.fixture
def store_db(db: psycopg.Connection[TupleRow]) -> psycopg.Connection[TupleRow]:
    """A rolled-back connection with every migration applied."""
    migrate(db)
    return db


@pytest.fixture(scope="session")
def grobid_url() -> Iterator[str]:
    """URL of a live GROBID, started once per session (the one live check of the recordings)."""
    if not _docker_available():
        pytest.skip("Docker is not running; the live GROBID check needs it")
    with DockerContainer(GROBID_IMAGE).with_exposed_ports(8070) as container:
        url = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8070)}"
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{url}/api/isalive", timeout=2).text.strip() == "true":
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            pytest.fail("GROBID did not become ready within 180 s")
        yield url
