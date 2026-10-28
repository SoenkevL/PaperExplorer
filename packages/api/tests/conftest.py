"""An API app over the rolled-back test database, with Works stored through core's repositories."""

from collections.abc import Callable
from contextlib import nullcontext

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow

from paperexplorer.chunking import chunk
from paperexplorer.citations import CitationGraph, extract
from paperexplorer.domain import Attachment, LibraryItem, WorkId
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.parsing import ParsedWork
from paperexplorer.store import Chunks, CitationGraphs, StageStatuses, Works, WorkTexts
from paperexplorer_api.app import create_app

Conn = psycopg.Connection[TupleRow]

VILLIGER = LibraryItem(
    key="VILL2024",
    title="Personal psychedelic experience of psychedelic therapists during training",
    creators=["Villiger, Daniel"],
    year=2024,
    doi="10.1080/09540261.2024.2357669",
    pdf=Attachment(key="ATTVILL1", filename="villiger.pdf"),
)


class Stored:
    """A Work as the pipeline leaves it: snapshot, text, embedded Chunks, Citations, Stages."""

    def __init__(self, conn: Conn, item: LibraryItem, parsed: ParsedWork) -> None:
        self.parsed = parsed
        self.graph: CitationGraph = extract(parsed)
        self.work: WorkId = Works(conn).ensure(item.key)
        Works(conn).record_library_item(self.work, item)
        WorkTexts(conn).replace(self.work, parsed)
        Chunks(conn).replace(self.work, chunk(parsed))
        stored = Chunks(conn).for_work(self.work)
        embedder = FakeEmbedder()
        vectors = embedder.embed([s.chunk.text for s in stored])
        Chunks(conn).record_embeddings(
            self.work, embedder.model, {s.id: v for s, v in zip(stored, vectors, strict=True)}
        )
        CitationGraphs(conn).replace(self.work, self.graph)
        for stage in ("fetch", "parse", "chunk", "embed", "extract_citations"):
            StageStatuses(conn).start(self.work, stage)
            StageStatuses(conn).done(self.work, stage, {})


@pytest.fixture
def api(store_db: Conn) -> TestClient:
    return TestClient(create_app(lambda: nullcontext(store_db)))


@pytest.fixture
def store_work(store_db: Conn) -> Callable[[LibraryItem, ParsedWork], Stored]:
    return lambda item, parsed: Stored(store_db, item, parsed)


@pytest.fixture
def villiger(
    store_work: Callable[[LibraryItem, ParsedWork], Stored],
    parse_fixture: Callable[[str], ParsedWork],
) -> Stored:
    return store_work(VILLIGER, parse_fixture("villiger2024-tandf"))
