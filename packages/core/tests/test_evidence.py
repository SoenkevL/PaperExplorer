"""Finding a Citation's Evidence in a Source, on real Postgres with the bag-of-words `FakeEmbedder`.

The small Seed's Citations are the Citing Sentences; each Source here is built to match or not.
"""

from collections.abc import Sequence

import httpx
import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer import chunking, retrieval
from paperexplorer.domain import Scope, Vector, Work, WorkId
from paperexplorer.embedding import EmbeddingError, FakeEmbedder
from paperexplorer.parsing import Block, BlockKind, ParsedWork, Span, TextOrigin
from paperexplorer.store import (
    Chunks,
    CitationDetail,
    CitationGraphs,
    RetrievalRun,
    Works,
    WorkTexts,
)

Conn = psycopg.Connection[TupleRow]


def _citation(conn: Conn, seed: Work) -> CitationDetail:
    """The Seed's first Citation."""
    first = CitationGraphs(conn).for_work(seed.id)[0]
    found = CitationGraphs(conn).get(first.id)
    assert found is not None
    return found


def _source(conn: Conn, key: str, texts: list[str], *, embed: bool = True) -> WorkId:
    """A Source of one paragraph per text, each its own Chunk, embedded unless told otherwise."""
    parsed = ParsedWork(
        text_origin=TextOrigin.PRINTED,
        blocks=[
            Block(
                kind=BlockKind.PARAGRAPH,
                heading_path=["1 Part"],
                page=1,
                boxes=[],
                text=text,
                sentences=[Span(start=0, end=len(text))],
            )
            for text in texts
        ],
        references=[],
    )
    work = Works(conn).ensure(key)
    WorkTexts(conn).replace(work, parsed)
    Chunks(conn).replace(work, chunking.chunk(parsed))
    if embed:
        embedder = FakeEmbedder()
        stored = Chunks(conn).for_work(work)
        vectors = embedder.embed([s.chunk.text for s in stored])
        Chunks(conn).record_embeddings(
            work, embedder.model, {s.id: v for s, v in zip(stored, vectors, strict=True)}
        )
    return work


def test_a_citations_evidence_is_the_sources_chunks_nearest_its_citing_sentence(
    store_db: Conn, small_seed: Work
) -> None:
    citation = _citation(store_db, small_seed)
    source = _source(
        store_db,
        "SRC1",
        ["Bananas ripen slowly in cold storage.", citation.citation.citing_sentence],
    )

    run = retrieval.find_evidence(store_db, FakeEmbedder(), citation, Scope.of(source))

    assert isinstance(run, RetrievalRun)
    assert run.citing_sentence == citation.citation.citing_sentence
    assert run.evidence[0].chunk.text == citation.citation.citing_sentence
    assert run.evidence[0].chunk.block == 1


def test_a_recorded_run_comes_back_without_asking_the_embedder(
    store_db: Conn, small_seed: Work
) -> None:
    citation = _citation(store_db, small_seed)
    source = _source(store_db, "SRC1", ["One text here.", citation.citation.citing_sentence])
    embedder = FakeEmbedder()

    first = retrieval.find_evidence(store_db, embedder, citation, Scope.of(source))
    second = retrieval.find_evidence(store_db, embedder, citation, Scope.of(source))

    assert len(embedder.calls) == 1
    assert second == first


def test_replacing_a_sources_chunks_searches_again(store_db: Conn, small_seed: Work) -> None:
    citation = _citation(store_db, small_seed)
    source = _source(store_db, "SRC1", [citation.citation.citing_sentence])
    embedder = FakeEmbedder()
    retrieval.find_evidence(store_db, embedder, citation, Scope.of(source))

    _source(store_db, "SRC1", [citation.citation.citing_sentence, "A new paragraph."])
    run = retrieval.find_evidence(store_db, embedder, citation, Scope.of(source))

    assert len(embedder.calls) == 2
    assert isinstance(run, RetrievalRun)
    assert len(run.evidence) == 2


def test_another_source_has_a_run_of_its_own(store_db: Conn, small_seed: Work) -> None:
    citation = _citation(store_db, small_seed)
    sentence = citation.citation.citing_sentence
    one = _source(store_db, "SRC1", [sentence])
    other = _source(store_db, "SRC2", [sentence])

    first = retrieval.find_evidence(store_db, FakeEmbedder(), citation, Scope.of(one))
    second = retrieval.find_evidence(store_db, FakeEmbedder(), citation, Scope.of(other))

    assert isinstance(first, RetrievalRun) and isinstance(second, RetrievalRun)
    assert first.id != second.id
    assert second.evidence[0].work == other


def test_a_source_with_no_chunks_says_so(store_db: Conn, small_seed: Work) -> None:
    citation = _citation(store_db, small_seed)
    source = Works(store_db).ensure("SRC1")

    found = retrieval.find_evidence(store_db, FakeEmbedder(), citation, Scope.of(source))

    assert found is retrieval.NoEvidence.NO_CHUNKS


def test_a_source_with_a_chunk_lacking_a_vector_says_so_and_never_searches_on_words_alone(
    store_db: Conn, small_seed: Work
) -> None:
    citation = _citation(store_db, small_seed)
    source = _source(store_db, "SRC1", [citation.citation.citing_sentence], embed=False)
    embedder = FakeEmbedder()

    found = retrieval.find_evidence(store_db, embedder, citation, Scope.of(source))

    assert found is retrieval.NoEvidence.NO_VECTORS
    assert embedder.calls == []


@pytest.mark.parametrize("failure", [EmbeddingError("answered 500"), httpx.ConnectError("refused")])
def test_an_unreachable_embedder_says_so_and_records_nothing(
    store_db: Conn, small_seed: Work, failure: Exception
) -> None:
    citation = _citation(store_db, small_seed)
    source = _source(store_db, "SRC1", [citation.citation.citing_sentence])

    class Failing(FakeEmbedder):
        def embed(self, texts: Sequence[str]) -> list[Vector]:
            raise failure

    found = retrieval.find_evidence(store_db, Failing(), citation, Scope.of(source))
    assert found is retrieval.NoEvidence.EMBEDDER_UNREACHABLE

    again = retrieval.find_evidence(store_db, FakeEmbedder(), citation, Scope.of(source))
    assert isinstance(again, RetrievalRun)


def test_a_citing_sentence_with_no_words_finds_no_match(store_db: Conn, small_seed: Work) -> None:
    citation = _citation(store_db, small_seed)
    citation.citation.citing_sentence = "  "
    source = _source(store_db, "SRC1", ["Some text."])

    found = retrieval.find_evidence(store_db, FakeEmbedder(), citation, Scope.of(source))

    assert found is retrieval.NoEvidence.NO_MATCH
