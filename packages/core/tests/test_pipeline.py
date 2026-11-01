"""Stages run one at a time against the real store, a Library in memory and recorded GROBID."""

import dataclasses
import json
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx
import psycopg
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from psycopg.rows import TupleRow

from paperexplorer.chunking import chunk
from paperexplorer.config import Settings
from paperexplorer.domain import Attachment, LibraryItem, WorkId
from paperexplorer.embedding import FakeEmbedder, OpenAICompatibleEmbedder
from paperexplorer.library import InMemoryLibrary
from paperexplorer.parsing import ParsedWork
from paperexplorer.pipeline import StageContext, StageError, run_stage
from paperexplorer.resolution import InMemoryDoiRegistry
from paperexplorer.store import (
    Chunks,
    CitationGraphs,
    StageState,
    StageStatuses,
    Works,
    WorkTexts,
)
from paperexplorer.tracing import Tracing, configure_tracing

Conn = psycopg.Connection[TupleRow]

VILLIGER = LibraryItem(
    key="VILL2024",
    title="Personal psychedelic experience of psychedelic therapists during training",
    creators=["Villiger, Daniel"],
    year=2024,
    doi="10.1080/09540261.2024.2357669",
    pdf=Attachment(key="ATTVILL1", filename="villiger.pdf"),
)
NO_PDF = LibraryItem(key="NOPDF234", title="Only metadata")
CHAIN = ["fetch", "parse", "chunk", "embed", "extract_citations"]


@pytest.fixture
def ctx(
    store_db: Conn, recorded_settings: Settings, fixture_pdf: Callable[[str], Path]
) -> StageContext:
    library = InMemoryLibrary([VILLIGER, NO_PDF], {"ATTVILL1": fixture_pdf("villiger2024-tandf")})
    return StageContext(
        conn=store_db,
        settings=recorded_settings,
        library=library,
        embedder=FakeEmbedder(),
        tracing=Tracing.off(),
        doi_registry=InMemoryDoiRegistry(offline=True),
    )


def _run_all(ctx: StageContext, work: WorkId) -> dict[str, dict[str, int]]:
    return {name: run_stage(ctx, name, work).counts for name in CHAIN}


def test_the_chain_stores_a_works_metadata_text_and_citations(
    ctx: StageContext, parse_fixture: Callable[[str], ParsedWork]
) -> None:
    work = Works(ctx.conn).ensure("VILL2024")

    counts = _run_all(ctx, work)

    assert counts["parse"] == {"blocks": 33, "references": 80, "citation_markers": 90}
    assert counts["chunk"] == {"chunks": 32, "short": 3, "oversized_sentence": 0}
    assert counts["embed"] == {"chunks": 32, "embedded": 32}
    assert counts["extract_citations"] == {
        "citations": 90, "certain": 84, "high": 5, "low": 1, "unlinked": 0,
    }  # fmt: skip
    stored = Works(ctx.conn).get(work)
    assert stored is not None
    assert stored.title == VILLIGER.title
    assert WorkTexts(ctx.conn).get(work) == parse_fixture("villiger2024-tandf")
    stored_chunks = [s.chunk for s in Chunks(ctx.conn).for_work(work)]
    assert stored_chunks == chunk(parse_fixture("villiger2024-tandf"))
    embeddings = Chunks(ctx.conn).embeddings(work)
    assert {e.model for e in embeddings} == {"fake-bag-of-words"}
    for embedding, vector in zip(
        embeddings, FakeEmbedder().embed([c.text for c in stored_chunks]), strict=True
    ):
        assert embedding.vector == pytest.approx(vector)
    assert len(CitationGraphs(ctx.conn).for_work(work)) == 90
    statuses = StageStatuses(ctx.conn).for_work(work)
    assert [(s, statuses[s].state) for s in CHAIN] == [(s, StageState.DONE) for s in CHAIN]


def test_every_stage_can_run_again(ctx: StageContext) -> None:
    work = Works(ctx.conn).ensure("VILL2024")
    first = _run_all(ctx, work)

    again = _run_all(ctx, work)

    assert again == first
    assert len(Chunks(ctx.conn).for_work(work)) == 32
    assert len(CitationGraphs(ctx.conn).for_work(work)) == 90


def test_parsing_again_makes_the_chunks_and_citations_pending(ctx: StageContext) -> None:
    work = Works(ctx.conn).ensure("VILL2024")
    _run_all(ctx, work)

    run_stage(ctx, "parse", work)

    assert list(StageStatuses(ctx.conn).for_work(work)) == ["fetch", "parse"]
    assert Chunks(ctx.conn).for_work(work) == []
    assert Chunks(ctx.conn).embeddings(work) == []
    assert CitationGraphs(ctx.conn).for_work(work) == []


def test_a_failed_stage_records_why_and_keeps_none_of_its_writes(ctx: StageContext) -> None:
    work = Works(ctx.conn).ensure("NOPDF234")

    with pytest.raises(StageError, match="no PDF"):
        run_stage(ctx, "fetch", work)

    status = StageStatuses(ctx.conn).for_work(work)["fetch"]
    assert status.state is StageState.FAILED
    assert status.error is not None
    assert "no PDF" in status.error
    stored = Works(ctx.conn).get(work)
    assert stored is not None
    assert stored.title is None  # the snapshot went with the failed run


def test_an_item_missing_from_the_library_fails_fetch(ctx: StageContext) -> None:
    work = Works(ctx.conn).ensure("GONE2345")

    with pytest.raises(StageError, match="GONE2345"):
        run_stage(ctx, "fetch", work)


def test_parse_needs_fetch_first(ctx: StageContext) -> None:
    work = Works(ctx.conn).ensure("VILL2024")

    with pytest.raises(StageError, match="fetch"):
        run_stage(ctx, "parse", work)


def test_chunking_needs_the_work_parsed(ctx: StageContext) -> None:
    work = Works(ctx.conn).ensure("VILL2024")
    run_stage(ctx, "fetch", work)

    with pytest.raises(StageError, match="run parse first"):
        run_stage(ctx, "chunk", work)


def test_extracting_citations_needs_the_work_parsed(ctx: StageContext) -> None:
    work = Works(ctx.conn).ensure("VILL2024")
    run_stage(ctx, "fetch", work)

    with pytest.raises(StageError, match="parse"):
        run_stage(ctx, "extract_citations", work)


def test_embedding_again_embeds_only_the_chunks_without_a_vector_from_the_model(
    ctx: StageContext,
) -> None:
    work = Works(ctx.conn).ensure("VILL2024")
    _run_all(ctx, work)
    embedder = FakeEmbedder()

    again = run_stage(dataclasses.replace(ctx, embedder=embedder), "embed", work)
    other_model = run_stage(dataclasses.replace(ctx, embedder=FakeEmbedder("other")), "embed", work)

    assert again.counts == {"chunks": 32, "embedded": 0}
    assert embedder.calls == []
    assert other_model.counts == {"chunks": 32, "embedded": 32}
    assert {e.model for e in Chunks(ctx.conn).embeddings(work)} == {"other"}


def test_embedding_needs_the_work_chunked(ctx: StageContext) -> None:
    work = Works(ctx.conn).ensure("VILL2024")
    run_stage(ctx, "fetch", work)
    run_stage(ctx, "parse", work)

    with pytest.raises(StageError, match="run chunk first"):
        run_stage(ctx, "embed", work)


def _embeddings_server(request: httpx.Request) -> httpx.Response:
    vectors = FakeEmbedder().embed(json.loads(request.content)["input"])
    data = [{"index": i, "embedding": v} for i, v in enumerate(vectors)]
    return httpx.Response(200, json={"data": data})


def test_embed_is_traced_as_stage_embed_with_one_observation_per_batch(ctx: StageContext) -> None:
    exporter = InMemorySpanExporter()
    tracing = configure_tracing(
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            langfuse_public_key=f"pk-lf-test-{uuid.uuid4()}",
            langfuse_secret_key="sk-lf-test",
        ),
        span_exporter=exporter,
    )
    http = httpx.Client(
        base_url="http://tower/v1", transport=httpx.MockTransport(_embeddings_server)
    )
    embedder = OpenAICompatibleEmbedder(http, "bge-m3", batch_size=10, tracing=tracing)
    work = Works(ctx.conn).ensure("VILL2024")
    for name in ["fetch", "parse", "chunk"]:
        run_stage(ctx, name, work)

    run_stage(dataclasses.replace(ctx, embedder=embedder, tracing=tracing), "embed", work)
    tracing.flush()

    spans = exporter.get_finished_spans()
    (root,) = [s for s in spans if s.parent is None]
    batches = [s for s in spans if s.name == "embeddings bge-m3"]
    assert root.name == "stage.embed"
    assert root.context is not None
    assert (root.attributes or {})["langfuse.trace.tags"] == ("pipeline", "work:VILL2024")
    assert len(batches) == 4  # 32 Chunks, 10 per request
    assert {s.parent and s.parent.span_id for s in batches} == {root.context.span_id}
