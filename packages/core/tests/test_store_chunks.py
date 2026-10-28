from collections.abc import Callable

import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer.chunking import chunk
from paperexplorer.domain import Chunk, ChunkId, WorkId
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.parsing import ParsedWork
from paperexplorer.store import Chunks, StoredChunk, Works, WorkTexts

Conn = psycopg.Connection[TupleRow]
ParseFixture = Callable[[str], ParsedWork]


def _stored(conn: Conn, parsed: ParsedWork, key: str = "villiger") -> tuple[WorkId, list[Chunk]]:
    work = Works(conn).ensure(key)
    WorkTexts(conn).replace(work, parsed)
    chunks = chunk(parsed)
    Chunks(conn).replace(work, chunks)
    return work, chunks


@pytest.fixture
def villiger(parse_fixture: ParseFixture) -> ParsedWork:
    return parse_fixture("villiger2024-tandf")


@pytest.mark.parametrize("name", ["villiger2024-tandf", "scan-villiger2024"])
def test_a_works_chunks_come_back_exactly_as_stored(
    store_db: Conn, parse_fixture: ParseFixture, name: str
) -> None:
    work, chunks = _stored(store_db, parse_fixture(name), key=name)

    stored = Chunks(store_db).for_work(work)

    assert [s.chunk for s in stored] == chunks
    assert {s.work for s in stored} == {work}
    assert len({s.id for s in stored}) == len(chunks)


def test_chunking_again_replaces_the_chunks(store_db: Conn, villiger: ParsedWork) -> None:
    work, chunks = _stored(store_db, villiger)

    Chunks(store_db).replace(work, chunks[:3])

    assert [s.chunk for s in Chunks(store_db).for_work(work)] == chunks[:3]


def test_a_chunk_is_found_by_its_id(store_db: Conn, villiger: ParsedWork) -> None:
    work, _ = _stored(store_db, villiger)
    third = Chunks(store_db).for_work(work)[2]

    assert Chunks(store_db).get(third.id) == third
    assert Chunks(store_db).get(ChunkId(-1)) is None


def test_around_gives_a_chunks_neighbours_of_the_same_work_in_reading_order(
    store_db: Conn, villiger: ParsedWork
) -> None:
    work, _ = _stored(store_db, villiger)
    _stored(store_db, villiger, key="villiger-again")
    stored = Chunks(store_db).for_work(work)

    middle = Chunks(store_db).around(stored[5].id, n=2)
    first = Chunks(store_db).around(stored[0].id)

    assert middle == stored[3:8]
    assert first == stored[:2]


def test_under_gives_the_chunks_below_a_heading_path_in_reading_order(
    store_db: Conn, villiger: ParsedWork
) -> None:
    work, _ = _stored(store_db, villiger)
    stored = Chunks(store_db).for_work(work)
    heading = next(s.chunk.heading_path for s in stored if s.chunk.heading_path)

    under = Chunks(store_db).under(work, heading[:1])

    assert under == [s for s in stored if s.chunk.heading_path[:1] == heading[:1]]
    assert 0 < len(under) < len(stored)
    assert Chunks(store_db).under(work, []) == stored
    assert Chunks(store_db).under(work, [*heading, "No such heading"]) == []


def test_parsing_again_takes_the_chunks_with_the_old_blocks(
    store_db: Conn, villiger: ParsedWork
) -> None:
    work, _ = _stored(store_db, villiger)

    WorkTexts(store_db).replace(work, villiger)

    assert Chunks(store_db).for_work(work) == []


def test_a_chunk_of_a_block_the_work_does_not_have_is_refused(
    store_db: Conn, villiger: ParsedWork
) -> None:
    work, chunks = _stored(store_db, villiger)
    stray = chunks[0].model_copy(update={"block": len(villiger.blocks)})

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        Chunks(store_db).replace(work, [stray])

    assert [s.chunk for s in Chunks(store_db).for_work(work)] == chunks


def test_chunks_are_searchable_as_english_text(store_db: Conn, villiger: ParsedWork) -> None:
    work, chunks = _stored(store_db, villiger)

    rows = store_db.execute(
        "SELECT position FROM chunks"
        " WHERE work_id = %s AND text_search @@ websearch_to_tsquery('english', %s)",
        (work, "therapist"),
    ).fetchall()

    expected = [c.position for c in chunks if "therapists" in c.text.lower()]
    assert expected
    assert set(expected) <= {row[0] for row in rows}


def test_a_works_embeddings_come_back_with_their_model_in_reading_order(
    store_db: Conn, villiger: ParsedWork
) -> None:
    work, chunks = _stored(store_db, villiger)
    stored = Chunks(store_db).for_work(work)
    vectors = FakeEmbedder().embed([c.text for c in chunks])

    Chunks(store_db).record_embeddings(
        work, "bge-m3", {s.id: v for s, v in zip(stored, vectors, strict=True)}
    )

    embeddings = Chunks(store_db).embeddings(work)
    assert [(e.chunk, e.model) for e in embeddings] == [(s.id, "bge-m3") for s in stored]
    for embedding, vector in zip(embeddings, vectors, strict=True):
        assert embedding.vector == pytest.approx(vector)  # stored as float4


def _embed(conn: Conn, work: WorkId, model: str, chunks: list[StoredChunk]) -> None:
    vectors = FakeEmbedder().embed([s.chunk.text for s in chunks])
    Chunks(conn).record_embeddings(
        work, model, {s.id: v for s, v in zip(chunks, vectors, strict=True)}
    )


def test_the_chunks_without_an_embedding_from_a_model_are_listed_in_reading_order(
    store_db: Conn, villiger: ParsedWork
) -> None:
    work, _ = _stored(store_db, villiger)
    other, _ = _stored(store_db, villiger, key="villiger-again")
    stored = Chunks(store_db).for_work(work)
    _embed(store_db, work, "bge-m3", stored[:10])
    _embed(store_db, work, "older-model", stored[10:12])
    _embed(store_db, other, "bge-m3", Chunks(store_db).for_work(other))

    assert Chunks(store_db).without_embedding(work, "bge-m3") == stored[10:]
    assert Chunks(store_db).without_embedding(other, "bge-m3") == []


def test_embedding_again_replaces_the_vector_and_its_model(
    store_db: Conn, villiger: ParsedWork
) -> None:
    work, _ = _stored(store_db, villiger)
    first = Chunks(store_db).for_work(work)[:1]
    _embed(store_db, work, "older-model", first)

    Chunks(store_db).record_embeddings(work, "bge-m3", {first[0].id: [0.0] * 1023 + [1.0]})

    (embedding,) = Chunks(store_db).embeddings(work)
    assert embedding.model == "bge-m3"
    assert embedding.vector == [0.0] * 1023 + [1.0]


def test_chunking_again_deletes_the_embeddings(store_db: Conn, villiger: ParsedWork) -> None:
    work, chunks = _stored(store_db, villiger)
    _embed(store_db, work, "bge-m3", Chunks(store_db).for_work(work))

    Chunks(store_db).replace(work, chunks)

    assert Chunks(store_db).embeddings(work) == []
    assert len(Chunks(store_db).without_embedding(work, "bge-m3")) == len(chunks)


def test_an_embedding_names_a_chunk_of_its_work(store_db: Conn, villiger: ParsedWork) -> None:
    work, _ = _stored(store_db, villiger)
    other, _ = _stored(store_db, villiger, key="villiger-again")
    theirs = Chunks(store_db).for_work(other)[0]

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        Chunks(store_db).record_embeddings(work, "bge-m3", {theirs.id: [1.0] * 1024})
