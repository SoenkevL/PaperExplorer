"""A small hand-built Seed for the asking tests: an abstract, three sections, two References."""

import json
import uuid

import httpx
import psycopg
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from psycopg.rows import TupleRow

from paperexplorer import chunking, citations
from paperexplorer.config import Settings
from paperexplorer.domain import LibraryItem, Work
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.parsing import (
    Block,
    BlockKind,
    CitationMarker,
    ParsedWork,
    Reference,
    Span,
    TextOrigin,
)
from paperexplorer.store import Chunks, CitationGraphs, Works, WorkTexts
from paperexplorer.tracing import Tracing, configure_tracing

SEED_KEY = "SEED0001"
ABSTRACT = "We measured brain entropy in ten volunteers after psilocybin."
INTRODUCTION = "Psychedelics raise brain entropy [1]. Earlier work used scalp recordings [2]."
RECORDING = "Entropy was computed from each EEG recording."
SESSIONS = "Sessions took place in the afternoon."
DISCUSSION = "The sample was small [1]."
REFERENCES = [
    Reference(
        id="b0",
        label="1",
        text="1. Carhart-Harris RL. The entropic brain. Front Hum Neurosci. 2014.",
        page=3,
        surnames=["Carhart-Harris"],
        year="2014",
    ),
    Reference(
        id="b1",
        label="2",
        text="2. Smith J. Scalp recordings of entropy. 2019.",
        page=3,
        surnames=["Smith"],
        year="2019",
    ),
]
# The same Seed citing by author and year: its bibliography is unnumbered, as the eval's Seed's is.
AUTHOR_YEAR_INTRODUCTION = (
    "Psychedelics raise brain entropy (Carhart-Harris, 2014). Earlier work used scalp recordings"
    " (Smith, 2019)."
)
AUTHOR_YEAR_DISCUSSION = "The sample was small (Carhart-Harris, 2014)."
UNNUMBERED_REFERENCES = [r.model_copy(update={"label": None}) for r in REFERENCES]


def _marker(text: str, marker: str, reference: str) -> CitationMarker:
    start = text.index(marker)
    return CitationMarker(
        span=Span(start=start, end=start + len(marker)),
        text=marker,
        cue=marker.strip("[]"),
        grobid_reference_id=reference,
    )


def _paragraph(
    path: list[str], page: int, text: str, markers: list[CitationMarker] | None = None
) -> Block:
    return Block(
        kind=BlockKind.PARAGRAPH,
        heading_path=path,
        page=page,
        boxes=[],
        text=text,
        citation_markers=markers or [],
    )


def _heading(path: list[str], page: int, text: str) -> Block:
    return Block(kind=BlockKind.HEADING, heading_path=path, page=page, boxes=[], text=text)


def _seed_text(
    introduction: str,
    discussion: str,
    markers: tuple[str, str],
    references: list[Reference],
) -> ParsedWork:
    first, second = markers
    return ParsedWork(
        text_origin=TextOrigin.PRINTED,
        blocks=[
            _paragraph([], 1, ABSTRACT),
            _heading([], 1, "1 Introduction"),
            _paragraph(
                ["1 Introduction"],
                1,
                introduction,
                [_marker(introduction, first, "b0"), _marker(introduction, second, "b1")],
            ),
            _heading([], 2, "2 Methods"),
            _heading(["2 Methods"], 2, "2.1 Recording"),
            _paragraph(["2 Methods", "2.1 Recording"], 2, RECORDING),
            _paragraph(["2 Methods", "2.1 Recording"], 2, SESSIONS),
            _heading([], 3, "3 Discussion"),
            _paragraph(["3 Discussion"], 3, discussion, [_marker(discussion, first, "b0")]),
        ],
        references=references,
    )


SEED_TEXT = _seed_text(INTRODUCTION, DISCUSSION, ("[1]", "[2]"), REFERENCES)
AUTHOR_YEAR_SEED_TEXT = _seed_text(
    AUTHOR_YEAR_INTRODUCTION,
    AUTHOR_YEAR_DISCUSSION,
    ("(Carhart-Harris, 2014)", "(Smith, 2019)"),
    UNNUMBERED_REFERENCES,
)


def in_memory_tracing() -> tuple[Tracing, InMemorySpanExporter, list[dict[str, object]]]:
    """Tracing into an in-memory exporter, with the scores Langfuse would receive."""
    exporter = InMemorySpanExporter()
    scores: list[dict[str, object]] = []

    def langfuse(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/public/scores":
            scores.extend(json.loads(request.content))
            return httpx.Response(200, json={})
        return httpx.Response(200, json={"status": "OK"})

    tracing = configure_tracing(
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            langfuse_public_key=f"pk-lf-test-{uuid.uuid4()}",
            langfuse_secret_key="sk-lf-test",
            langfuse_base_url="http://langfuse.test",
        ),
        http=httpx.Client(transport=httpx.MockTransport(langfuse)),
        span_exporter=exporter,
    )
    return tracing, exporter, scores


def _stored_seed(conn: psycopg.Connection[TupleRow], key: str, text: ParsedWork) -> Work:
    """A Seed stored, chunked, embedded by `FakeEmbedder` and with its Citations."""
    works = Works(conn)
    work = works.ensure(key)
    works.record_library_item(
        work,
        LibraryItem(key=key, title="Entropy after psilocybin", creators=["A. Author"], year=2024),
    )
    WorkTexts(conn).replace(work, text)
    chunks = Chunks(conn)
    chunks.replace(work, chunking.chunk(text))
    stored = chunks.for_work(work)
    vectors = FakeEmbedder().embed([s.chunk.text for s in stored])
    vectors_by_chunk = dict(zip((s.id for s in stored), vectors, strict=True))
    chunks.record_embeddings(work, "fake-bag-of-words", vectors_by_chunk)
    CitationGraphs(conn).replace(work, citations.extract(text))
    found = works.get(work)
    assert found is not None
    return found


@pytest.fixture
def small_seed(store_db: psycopg.Connection[TupleRow]) -> Work:
    """The small Seed stored, chunked, embedded by `FakeEmbedder` and with its Citations."""
    return _stored_seed(store_db, SEED_KEY, SEED_TEXT)


@pytest.fixture
def unnumbered_seed(store_db: psycopg.Connection[TupleRow]) -> Work:
    """The small Seed citing by author and year: no Reference of it carries a printed label."""
    return _stored_seed(store_db, "SEED0002", AUTHOR_YEAR_SEED_TEXT)
