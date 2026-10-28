"""Hybrid search over a Scope's Chunks, on real Postgres with the bag-of-words `FakeEmbedder`.

The fake's cosine is the share of distinct content words two texts have in common, while full
text search matches English stems: "entropies" shares no word with "entropy", but its stem.
"""

import math
import random
from collections.abc import Callable

import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer import chunking, retrieval
from paperexplorer.domain import ChunkId, Scope, Vector, WorkId
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.parsing import Block, BlockKind, ParsedWork, Span, TextOrigin
from paperexplorer.store import Chunks, StoredChunk, Works, WorkTexts

Conn = psycopg.Connection[TupleRow]

QUESTION = "How did the study measure brain entropy?"


def _work(conn: Conn, key: str, texts: list[str]) -> tuple[WorkId, list[StoredChunk]]:
    """A Work of one short paragraph per text, each its own Chunk, on page 1, 2, 3, ..."""
    parsed = ParsedWork(
        text_origin=TextOrigin.PRINTED,
        blocks=[
            Block(
                kind=BlockKind.PARAGRAPH,
                heading_path=["2 Methods", f"2.{i + 1} Part"],
                page=i + 1,
                boxes=[],
                text=text,
                sentences=[Span(start=0, end=len(text))],
            )
            for i, text in enumerate(texts)
        ],
        references=[],
    )
    work = Works(conn).ensure(key)
    WorkTexts(conn).replace(work, parsed)
    Chunks(conn).replace(work, chunking.chunk(parsed))
    stored = Chunks(conn).for_work(work)
    assert [s.chunk.text for s in stored] == texts
    return work, stored


def _embed(
    conn: Conn,
    chunks: list[StoredChunk],
    vectors: list[Vector] | None = None,
    model: str = "fake-bag-of-words",
) -> None:
    """Record the fake's Vectors of the Chunks' own texts, or the `vectors` given."""
    vectors = vectors or FakeEmbedder().embed([s.chunk.text for s in chunks])
    for s, v in zip(chunks, vectors, strict=True):
        Chunks(conn).record_embeddings(s.work, model, {s.id: v})


def test_a_chunk_without_a_vector_is_found_by_the_stems_it_shares_with_a_question(
    store_db: Conn,
) -> None:
    work, chunks = _work(
        store_db,
        "W1",
        [
            "Participants rested quietly for ten minutes.",
            "Entropies were computed from each recording.",
            "Sessions took place in the afternoon.",
        ],
    )
    _embed(store_db, [chunks[0], chunks[2]])

    results = retrieval.search(store_db, FakeEmbedder(), QUESTION, Scope.of(work), k=3)

    found = next(r for r in results if r.id == chunks[1].id)
    assert found.vector is None
    assert found.full_text is not None
    assert found.full_text.rank == 1
    assert found.full_text.score > 0
    assert found.rrf == pytest.approx(1 / 61)
    assert found.chunk == chunks[1].chunk


def test_a_chunk_whose_vector_is_nearest_but_shares_no_word_is_found_by_its_vector_alone(
    store_db: Conn,
) -> None:
    work, chunks = _work(
        store_db,
        "W1",
        [
            "Participants rested quietly for ten minutes.",
            "Sessions took place in the afternoon.",
            "Entropy of the brain signal rose.",
        ],
    )
    # A Vector stands in for meaning: the second Chunk means what the question asks.
    _embed(store_db, [chunks[0], chunks[2]])
    _embed(store_db, [chunks[1]], FakeEmbedder().embed([QUESTION]))

    results = retrieval.search(store_db, FakeEmbedder(), QUESTION, Scope.of(work), k=3)

    found = next(r for r in results if r.id == chunks[1].id)
    assert found.full_text is None
    assert found.vector is not None
    assert found.vector.rank == 1
    assert found.vector.score == pytest.approx(1, abs=1e-6)
    assert found.rrf == pytest.approx(1 / 61)


def test_second_in_both_rankings_beats_first_in_one_and_ties_go_in_reading_order(
    store_db: Conn,
) -> None:
    query = "entropy psilocybin"
    work, [rest, words, meaning, both, room] = _work(
        store_db,
        "W1",
        [
            "Participants rested quietly for ten minutes.",
            "Entropy, entropy and again entropy after psilocybin, psilocybin.",
            "Sessions took place in the afternoon.",
            "Entropy rose after psilocybin in some sessions.",
            "The room was quiet.",
        ],
    )
    _embed(store_db, [rest, both, room])
    _embed(store_db, [meaning], FakeEmbedder().embed([query]))

    results = retrieval.search(store_db, FakeEmbedder(), query, Scope.of(work), k=5)

    assert [r.id for r in results] == [both.id, words.id, meaning.id, rest.id, room.id]
    ranks = [(r.vector and r.vector.rank, r.full_text and r.full_text.rank) for r in results]
    assert ranks == [(2, 2), (None, 1), (1, None), (3, None), (4, None)]
    assert [r.rrf for r in results] == pytest.approx([2 / 62, 1 / 61, 1 / 61, 1 / 63, 1 / 64])


def test_a_search_finds_only_the_chunks_of_its_scope(store_db: Conn) -> None:
    texts = ["Entropy rose after psilocybin.", "Sessions took place in the afternoon."]
    first, ours = _work(store_db, "W1", texts)
    second, theirs = _work(store_db, "W2", texts)
    _embed(store_db, ours + theirs)

    in_first = retrieval.search(store_db, FakeEmbedder(), QUESTION, Scope.of(first))
    in_second = retrieval.search(store_db, FakeEmbedder(), QUESTION, Scope.of(second))

    assert [r.id for r in in_first] == [s.id for s in ours]
    assert [r.id for r in in_second] == [s.id for s in theirs]


def test_vectors_from_another_model_are_ignored(store_db: Conn) -> None:
    work, [rest, afternoon] = _work(
        store_db,
        "W1",
        ["Participants rested quietly for ten minutes.", "Sessions took place in the afternoon."],
    )
    _embed(store_db, [rest])
    _embed(store_db, [afternoon], FakeEmbedder().embed([QUESTION]), model="older-model")

    current = retrieval.search(store_db, FakeEmbedder(), QUESTION, Scope.of(work))
    older = retrieval.search(store_db, FakeEmbedder("older-model"), QUESTION, Scope.of(work))

    assert [r.id for r in current] == [rest.id]
    assert [r.id for r in older] == [afternoon.id]


def test_a_scope_with_fewer_chunks_than_k_gives_each_of_them_once(store_db: Conn) -> None:
    work, chunks = _work(
        store_db,
        "W1",
        [
            "Participants rested quietly for ten minutes.",
            "Entropy of the brain signal rose.",
            "Sessions took place in the afternoon.",
        ],
    )
    _embed(store_db, chunks)

    results = retrieval.search(store_db, FakeEmbedder(), QUESTION, Scope.of(work), k=8)

    assert sorted(r.id for r in results) == [s.id for s in chunks]


@pytest.mark.parametrize("text", ["", "  \n "])
def test_an_empty_query_finds_nothing_and_embeds_nothing(store_db: Conn, text: str) -> None:
    work, chunks = _work(store_db, "W1", ["Entropy of the brain signal rose."])
    _embed(store_db, chunks)
    embedder = FakeEmbedder()

    assert retrieval.search(store_db, embedder, text, Scope.of(work)) == []
    assert embedder.calls == []


def test_a_query_of_stop_words_only_is_ranked_by_vectors_alone(store_db: Conn) -> None:
    work, chunks = _work(
        store_db,
        "W1",
        [
            "Participants rested quietly for ten minutes.",
            "Entropy of the brain signal rose.",
            "Sessions took place in the afternoon.",
        ],
    )
    _embed(store_db, chunks)

    results = retrieval.search(store_db, FakeEmbedder(), "What is it, and why?", Scope.of(work))

    assert [r.id for r in results] == [s.id for s in chunks]  # all equally far: reading order
    assert all(r.full_text is None and r.vector is not None for r in results)


def test_a_search_for_fewer_chunks_gives_the_first_chunks_of_a_search_for_more(
    store_db: Conn, parse_fixture: Callable[[str], ParsedWork]
) -> None:
    parsed = parse_fixture("villiger2024-tandf")
    work = Works(store_db).ensure("villiger")
    WorkTexts(store_db).replace(work, parsed)
    Chunks(store_db).replace(work, chunking.chunk(parsed))
    _embed(store_db, Chunks(store_db).for_work(work))
    question = "What did psychedelic therapists learn from their own experience in training?"

    def ranked(k: int) -> list[tuple[ChunkId, float]]:
        found = retrieval.search(store_db, FakeEmbedder(), question, Scope.of(work), k=k)
        return [(r.id, r.rrf) for r in found]

    assert len(ranked(20)) == 20
    assert ranked(3) == ranked(8)[:3]
    assert ranked(8) == ranked(20)[:8]
    rrfs = [rrf for _, rrf in ranked(20)]
    assert rrfs == sorted(rrfs, reverse=True)


def _near(vector: Vector, spread: float, rng: random.Random) -> Vector:
    """A unit Vector `spread` away from `vector` in a random direction."""
    noise = [rng.gauss(0, 1) for _ in vector]
    norm = math.sqrt(sum(x * x for x in noise))
    moved = [v + spread * x / norm for v, x in zip(vector, noise, strict=True)]
    length = math.sqrt(sum(x * x for x in moved))
    return [x / length for x in moved]


def test_a_scopes_chunks_are_found_by_vector_though_other_works_hold_nearer_vectors(
    store_db: Conn,
) -> None:
    query = "entropy psilocybin"
    [meaning] = FakeEmbedder().embed([query])
    rng = random.Random(7)
    # Spread-out Vectors, as a real model gives: the fake's put many Chunks at one distance,
    # which the HNSW graph search does not get past.
    _, theirs = _work(store_db, "W2", [f"Session {n} took place." for n in range(60)])
    work, ours = _work(store_db, "W1", ["Participants rested.", "The room was quiet."])
    _embed(store_db, theirs, [_near(meaning, 0.5, rng) for _ in theirs])
    _embed(store_db, ours, [_near(meaning, 2, rng) for _ in ours])
    # With many Works Postgres takes the HNSW index, which finds the nearest Vectors of every
    # Work before the Scope's filter applies; turning off sequential scans makes it take it here.
    store_db.execute("SET LOCAL enable_seqscan = off")

    results = retrieval.search(store_db, FakeEmbedder(), query, Scope.of(work))

    assert [r.id for r in results] == [s.id for s in ours]
    assert all(r.vector is not None for r in results)
