"""From Library Item to clickable Citations: Stages → store → API → web, all in one process."""

import html
import re
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow

from paperexplorer.config import Settings
from paperexplorer.domain import Attachment, CitationId, LibraryItem, WorkId
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.library import InMemoryLibrary
from paperexplorer.pipeline import STAGES, StageContext, run_stage
from paperexplorer.store import CitationGraphs, Works
from paperexplorer.tracing import Tracing
from paperexplorer_api.app import create_app as create_api
from paperexplorer_api.client import ApiClient
from paperexplorer_web.app import create_app as create_web

Conn = psycopg.Connection[TupleRow]
ITEMS = {
    "villiger2024-tandf": LibraryItem(
        key="VILL2024",
        title="Personal psychedelic experience of psychedelic therapists during training",
        pdf=Attachment(key="ATTVILL1", filename="villiger.pdf"),
    ),
    "scan-villiger2024": LibraryItem(
        key="SCAN2345",
        title="Three scanned pages",
        pdf=Attachment(key="ATTSCAN1", filename="s.pdf"),
    ),
}


@pytest.fixture
def explored(
    store_db: Conn, recorded_settings: Settings, fixture_pdf: Callable[[str], Path]
) -> dict[str, WorkId]:
    library = InMemoryLibrary(
        ITEMS.values(), {i.pdf.key: fixture_pdf(name) for name, i in ITEMS.items() if i.pdf}
    )
    ctx = StageContext(
        conn=store_db,
        settings=recorded_settings,
        library=library,
        embedder=FakeEmbedder(),
        tracing=Tracing.off(),
    )
    works: dict[str, WorkId] = {}
    for name, item in ITEMS.items():
        works[name] = Works(store_db).ensure(item.key)
        for stage in STAGES:
            run_stage(ctx, stage, works[name])
    return works


@pytest.fixture
def reader(store_db: Conn) -> TestClient:
    api = ApiClient(TestClient(create_api(lambda: nullcontext(store_db))))
    return TestClient(create_web(api))


def _panel_targets(page: str) -> list[str]:
    return [
        html.unescape(u) for u in re.findall(r'<a class="citation[^"]*"[^>]*hx-get="([^"]+)"', page)
    ]


def test_every_stored_citation_of_the_work_can_be_opened(
    store_db: Conn, explored: dict[str, WorkId], reader: TestClient
) -> None:
    work = explored["villiger2024-tandf"]

    page = reader.get(f"/works/{work}").text

    linked = {int(c) for url in _panel_targets(page) for c in re.findall(r"c=(\d+)", url)}
    stored = {s.id for s in CitationGraphs(store_db).for_work(work)}
    assert len(stored) == 90
    assert linked == stored


def test_a_clicked_citation_shows_its_reference_as_printed(
    store_db: Conn, explored: dict[str, WorkId], reader: TestClient
) -> None:
    work = explored["villiger2024-tandf"]
    first = _panel_targets(reader.get(f"/works/{work}").text)[0]

    panel = reader.get(first).text

    citation_id = CitationId(int(re.findall(r"c=(\d+)", first)[0]))
    detail = CitationGraphs(store_db).get(citation_id)
    assert detail is not None
    assert detail.reference is not None
    assert detail.reference.text in html.unescape(panel)


def test_a_scanned_work_is_marked_as_ocr_text(
    explored: dict[str, WorkId], reader: TestClient
) -> None:
    page = reader.get(f"/works/{explored['scan-villiger2024']}").text

    assert "This is OCR Text" in page
    assert _panel_targets(page)
