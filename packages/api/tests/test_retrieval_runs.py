"""`POST /citations/{id}/retrieval-runs`: the Evidence a Jump lands on, or why there is none."""

from collections.abc import Callable, Sequence
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow

from paperexplorer.chunking import chunk
from paperexplorer.citations import extract
from paperexplorer.domain import (
    Attachment,
    Layer,
    Level,
    LibraryItem,
    Resolution,
    ResolutionState,
    Vector,
    WorkId,
)
from paperexplorer.embedding import EmbeddingError, FakeEmbedder
from paperexplorer.parsing import Block, BlockKind, ParsedWork, Span, TextOrigin
from paperexplorer.store import Chunks, CitationGraphs, Resolutions, Works, WorkTexts
from paperexplorer_api.client import ApiClient, ConflictError

from .conftest import AppFactory, Script, Stored

Conn = psycopg.Connection[TupleRow]


def _linked(api: TestClient, work: WorkId) -> dict[str, Any]:
    """A Citation of the Work with a certain Citation Link, as `GET /citations/{id}` gives it."""
    blocks = api.get(f"/works/{work}").json()["blocks"]
    span = next(c for b in blocks for c in b["citations"] if c["confidence_level"] == "certain")
    return api.get(f"/citations/{span['id']}").json()


def _source(conn: Conn, key: str, texts: list[str], *, chunked: bool = True) -> WorkId:
    """A Library Item with text, embedded by the fake when `chunked`."""
    work = Works(conn).ensure(key)
    Works(conn).record_library_item(
        work,
        LibraryItem(
            key=key,
            title="The Source",
            creators=["Doe, Jane"],
            year=2020,
            pdf=Attachment(key=f"A{key}", filename="source.pdf"),
        ),
    )
    parsed = ParsedWork(
        text_origin=TextOrigin.PRINTED,
        blocks=[
            Block(
                kind=BlockKind.PARAGRAPH,
                heading_path=["1 Part"],
                page=3,
                boxes=[],
                text=text,
                sentences=[Span(start=0, end=len(text))],
            )
            for text in texts
        ],
        references=[],
    )
    WorkTexts(conn).replace(work, parsed)
    if chunked:
        Chunks(conn).replace(work, chunk(parsed))
        stored = Chunks(conn).for_work(work)
        embedder = FakeEmbedder()
        vectors = embedder.embed([s.chunk.text for s in stored])
        Chunks(conn).record_embeddings(
            work, embedder.model, {s.id: v for s, v in zip(stored, vectors, strict=True)}
        )
    return work


def _resolve(
    conn: Conn, citing: WorkId, key: str, work: WorkId | None, level: Level = Level.CERTAIN
) -> None:
    Resolutions(conn).replace(
        citing,
        [
            Resolution(
                reference=key,
                state=ResolutionState.RESOLVED,
                work=work,
                level=level,
                confidence=1.0,
                layer=Layer.LIBRARY,
            )
        ],
    )


def test_a_firm_source_gives_the_evidence_for_the_citing_sentence(
    api: TestClient, store_db: Conn, villiger: Stored
) -> None:
    citation = _linked(api, villiger.work)
    sentence = citation["citing_sentence"]
    source = _source(store_db, "SRC1", ["Bananas ripen slowly in storage.", sentence])
    _resolve(store_db, villiger.work, citation["reference"]["key"], source)

    response = api.post(f"/citations/{citation['id']}/retrieval-runs")

    assert response.status_code == 200
    run = response.json()
    assert run["citation"] == citation["id"]
    assert run["citing_sentence"] == sentence
    assert run["work"]["id"] == source
    assert run["reason"] is None
    assert run["evidence"][0] == {
        "rank": 1,
        "block": 1,
        "start": 0,
        "end": len(sentence),
        "page": 3,
        "heading_path": ["1 Part"],
    }


def test_a_source_not_chunked_gives_the_reason_and_no_evidence(
    api: TestClient, store_db: Conn, villiger: Stored
) -> None:
    citation = _linked(api, villiger.work)
    source = _source(store_db, "SRC1", ["Some text."], chunked=False)
    _resolve(store_db, villiger.work, citation["reference"]["key"], source)

    run = api.post(f"/citations/{citation['id']}/retrieval-runs").json()

    assert (run["reason"], run["evidence"]) == ("no_chunks", [])


def test_a_readers_choice_can_be_jumped_to(
    api: TestClient, store_db: Conn, villiger: Stored
) -> None:
    citation = _linked(api, villiger.work)
    source = _source(store_db, "SRC1", [citation["citing_sentence"]])
    Resolutions(store_db).replace(
        villiger.work,
        [
            Resolution(
                reference=citation["reference"]["key"],
                state=ResolutionState.RESOLVED,
                work=source,
                level=Level.LOW,
                layer=Layer.READER,
            )
        ],
    )

    response = api.post(f"/citations/{citation['id']}/retrieval-runs")

    assert response.status_code == 200
    assert response.json()["work"]["id"] == source


def test_an_unknown_citation_is_not_found(api: TestClient) -> None:
    assert api.post("/citations/999999/retrieval-runs").status_code == 404


def test_a_low_resolution_cannot_be_jumped_to(
    api: TestClient, store_db: Conn, villiger: Stored
) -> None:
    citation = _linked(api, villiger.work)
    source = _source(store_db, "SRC1", ["Some text."])
    _resolve(store_db, villiger.work, citation["reference"]["key"], source, Level.LOW)

    assert api.post(f"/citations/{citation['id']}/retrieval-runs").status_code == 409


def test_a_source_with_no_text_cannot_be_jumped_to(
    api: TestClient, store_db: Conn, villiger: Stored
) -> None:
    citation = _linked(api, villiger.work)
    source = Works(store_db).ensure("SRC1")
    _resolve(store_db, villiger.work, citation["reference"]["key"], source)

    assert api.post(f"/citations/{citation['id']}/retrieval-runs").status_code == 409


def test_a_resolution_names_how_it_can_be_jumped_to(
    api: TestClient, store_db: Conn, villiger: Stored
) -> None:
    citation = _linked(api, villiger.work)
    key = citation["reference"]["key"]

    readable = _source(store_db, "SRC1", ["Some text."])
    _resolve(store_db, villiger.work, key, readable)
    named = api.get(f"/citations/{citation['id']}").json()["reference"]["resolution"]
    assert (named["firm"], named["work"]["has_text"], named["work"]["has_pdf"]) == (
        True, True, True,
    )  # fmt: skip

    _resolve(store_db, villiger.work, key, Works(store_db).ensure("SRC2"), Level.LOW)
    named = api.get(f"/citations/{citation['id']}").json()["reference"]["resolution"]
    assert (named["firm"], named["work"]["has_text"], named["work"]["has_pdf"]) == (
        False, False, False,
    )  # fmt: skip


def test_the_client_finds_evidence_and_raises_conflict_with_no_firm_source(
    api: TestClient, store_db: Conn, villiger: Stored
) -> None:
    citation = _linked(api, villiger.work)
    client = ApiClient(api)

    with pytest.raises(ConflictError):
        client.find_evidence(citation["id"])

    source = _source(store_db, "SRC1", [citation["citing_sentence"]])
    _resolve(store_db, villiger.work, citation["reference"]["key"], source)
    assert client.find_evidence(citation["id"]).evidence[0].block == 0


def test_a_source_lacking_vectors_gives_the_reason(
    api: TestClient, store_db: Conn, villiger: Stored
) -> None:
    citation = _linked(api, villiger.work)
    source = _source(store_db, "SRC1", ["Some text."], chunked=False)
    parsed = WorkTexts(store_db).get(source)
    assert parsed is not None
    Chunks(store_db).replace(source, chunk(parsed))
    _resolve(store_db, villiger.work, citation["reference"]["key"], source)

    run = api.post(f"/citations/{citation['id']}/retrieval-runs").json()

    assert (run["reason"], run["evidence"]) == ("no_vectors", [])


def test_an_embedder_out_of_reach_gives_the_reason(
    make_app: AppFactory, store_db: Conn, villiger: Stored
) -> None:
    class Unreachable(FakeEmbedder):
        def embed(self, texts: Sequence[str]) -> list[Vector]:
            raise EmbeddingError("refused")

    api = TestClient(make_app(Script().model(), embedder=Unreachable()))
    citation = _linked(api, villiger.work)
    source = _source(store_db, "SRC1", ["Some text."])
    _resolve(store_db, villiger.work, citation["reference"]["key"], source)
    stored = Chunks(store_db).for_work(source)
    vectors = FakeEmbedder().embed([s.chunk.text for s in stored])
    Chunks(store_db).record_embeddings(
        source, Unreachable().model, {s.id: v for s, v in zip(stored, vectors, strict=True)}
    )

    run = api.post(f"/citations/{citation['id']}/retrieval-runs").json()

    assert (run["reason"], run["evidence"]) == ("embedder_unreachable", [])


def test_an_unlinked_citation_cannot_be_jumped_from(
    api: TestClient, store_db: Conn, parse_fixture: Callable[[str], ParsedWork]
) -> None:
    parsed = parse_fixture("villiger2024-tandf")
    work = Works(store_db).ensure("VILL2024")
    WorkTexts(store_db).replace(work, parsed)
    graph = extract(parsed)
    graph.citations[0].link = None
    CitationGraphs(store_db).replace(work, graph)
    first = api.get(f"/works/{work}").json()["blocks"]
    span = next(c for b in first for c in b["citations"] if c["confidence_level"] == "unlinked")

    assert api.post(f"/citations/{span['id']}/retrieval-runs").status_code == 409
