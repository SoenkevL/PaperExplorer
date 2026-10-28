"""Pointer and Quote resolution of an Answer, over hand-built Chunks with the `FakeEmbedder`.

The fake's cosine is the number of content words two texts share over the square root of the
product of their counts of distinct content words (stop words such as "the", "was" and "of" left
out). Every test resolves at `THRESHOLD`, so a sentence is close where that share clears it.
"""

import pytest
from pydantic import ValidationError

from paperexplorer.asking import resolve_answer
from paperexplorer.domain import AnswerCounts, Chunk, ChunkId, Quote, Support, WorkId
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.parsing import Span, TextOrigin
from paperexplorer.store import StoredChunk

THRESHOLD = 0.5


def _chunk(id: int, sentences: list[str], *, block: int = 0, at: int = 0) -> StoredChunk:
    """A Chunk of `sentences` joined by spaces, starting at code point `at` of block `block`."""
    spans: list[Span] = []
    start = at
    for sentence in sentences:
        spans.append(Span(start=start, end=start + len(sentence)))
        start += len(sentence) + 1
    text = " ".join(sentences)
    return StoredChunk(
        id=ChunkId(id),
        work=WorkId(1),
        chunk=Chunk(
            position=id,
            block=block,
            span=Span(start=at, end=at + len(text)),
            text=text,
            sentences=spans,
            heading_path=["2 Methods"],
            page=1,
            boxes=[],
            tokens=len(text.split()),
            text_origin=TextOrigin.PRINTED,
        ),
    )


METHODS = _chunk(
    7,
    [
        "Participants rested quietly for ten minutes.",
        "Entropy was computed from each EEG recording.",
        "Sessions took place in the afternoon.",
    ],
    block=4,
    at=10,
)


def test_a_sentence_with_a_handle_points_at_the_closest_sentence_of_its_chunk() -> None:
    answer = resolve_answer(
        "Entropy was computed from the EEG recordings. [#1]",
        {1: METHODS},
        [METHODS],
        FakeEmbedder(),
        THRESHOLD,
    )

    assert answer.text == "Entropy was computed from the EEG recordings."
    [sentence] = answer.sentences
    assert (sentence.start, sentence.end) == (0, len(answer.text))
    [pointer] = sentence.pointers
    entropy = METHODS.chunk.sentences[1]
    assert (pointer.block_id, pointer.start, pointer.end) == (4, entropy.start, entropy.end)
    assert pointer.support is Support.CLOSE
    assert (pointer.chunk_id, pointer.chunk_start) == (7, 10)
    assert pointer.chunk_end == METHODS.chunk.span.end


RESULTS = _chunk(
    8,
    [
        "Entropy rose after psilocybin in every EEG recording.",
        "Placebo left it unchanged.",
    ],
    block=6,
)


def test_a_pointer_moves_to_a_closer_sentence_in_another_chunk_read_this_turn() -> None:
    answer = resolve_answer(
        "Entropy rose after psilocybin. [#1]",
        {1: METHODS, 2: RESULTS},
        [METHODS, RESULTS],
        FakeEmbedder(),
        THRESHOLD,
    )

    [pointer] = answer.sentences[0].pointers
    rose = RESULTS.chunk.sentences[0]
    assert (pointer.block_id, pointer.start, pointer.end) == (6, rose.start, rose.end)
    assert (pointer.chunk_id, pointer.support) == (8, Support.CLOSE)


def test_a_pointer_with_no_close_sentence_anywhere_is_weak_and_leads_to_its_own_chunk() -> None:
    answer = resolve_answer(
        "Entropy was measured during sleep. [#1]",
        {1: METHODS, 2: RESULTS},
        [METHODS, RESULTS],
        FakeEmbedder(),
        THRESHOLD,
    )

    [pointer] = answer.sentences[0].pointers
    entropy = METHODS.chunk.sentences[1]
    assert (pointer.block_id, pointer.start, pointer.end) == (4, entropy.start, entropy.end)
    assert pointer.support is Support.WEAK
    assert pointer.similarity == pytest.approx(1 / (4 * 5) ** 0.5)  # 1 word of 4 and 5


def test_a_pointer_never_moves_to_a_chunk_the_model_did_not_read_this_turn() -> None:
    answer = resolve_answer(
        "Entropy rose after psilocybin. [#1]",
        {1: METHODS, 2: RESULTS},
        [METHODS],
        FakeEmbedder(),
        THRESHOLD,
    )

    [pointer] = answer.sentences[0].pointers
    assert (pointer.chunk_id, pointer.support) == (7, Support.WEAK)


DOSING = _chunk(3, ["Psilocybin was given at noon to every participant."], block=2)


def _texts(answer_text: str, spans: list[tuple[int, int]]) -> list[str]:
    return [answer_text[start:end] for start, end in spans]


def test_a_handle_belongs_to_the_sentence_it_follows_and_unknown_handles_are_dropped() -> None:
    answer = resolve_answer(
        "Psilocybin was given at noon. [#1] Entropy was computed from the EEG recordings [#2]."
        " Nothing else is known. [#9]\n[#1]\n- Placebo left it unchanged",
        {1: DOSING, 2: METHODS},
        [DOSING, METHODS],
        FakeEmbedder(),
        THRESHOLD,
    )

    assert answer.text == (
        "Psilocybin was given at noon. Entropy was computed from the EEG recordings."
        " Nothing else is known.\n- Placebo left it unchanged"
    )
    assert _texts(answer.text, [(s.start, s.end) for s in answer.sentences]) == [
        "Psilocybin was given at noon.",
        "Entropy was computed from the EEG recordings.",
        "Nothing else is known.",
        "- Placebo left it unchanged",
    ]
    assert [[p.chunk_id for p in s.pointers] for s in answer.sentences] == [[3], [7], [3], []]
    assert answer.unknown_handles == [9]


def test_each_handle_of_a_sentence_gets_its_own_pointer_in_the_order_written() -> None:
    answer = resolve_answer(
        "Psilocybin was given at noon, and entropy rose after psilocybin. [#1][#2]",
        {1: DOSING, 2: RESULTS},
        [DOSING, RESULTS],
        FakeEmbedder(),
        THRESHOLD,
    )

    pointers = answer.sentences[0].pointers
    assert [(p.chunk_id, p.support) for p in pointers] == [(3, Support.CLOSE), (8, Support.CLOSE)]


# The next window of RESULTS' paragraph, sharing its last sentence.
RESULTS_NEXT = _chunk(
    9, ["Placebo left it unchanged.", "No participant reported harm."], block=6, at=54
)


def test_handles_whose_pointers_reach_the_same_place_give_one_pointer() -> None:
    assert RESULTS.chunk.sentences[1] == RESULTS_NEXT.chunk.sentences[0]

    answer = resolve_answer(
        "Placebo left it unchanged [#2, #9] [#2].",
        {2: RESULTS, 9: RESULTS_NEXT},
        [RESULTS, RESULTS_NEXT],
        FakeEmbedder(),
        THRESHOLD,
    )

    assert answer.text == "Placebo left it unchanged."
    [pointer] = answer.sentences[0].pointers
    placebo = RESULTS.chunk.sentences[1]
    assert (pointer.chunk_id, pointer.block_id, pointer.start, pointer.end) == (
        8,
        6,
        placebo.start,
        placebo.end,
    )


def test_one_embedding_call_holds_each_handled_sentence_and_each_chunk_sentence_once() -> None:
    fake = FakeEmbedder()

    resolve_answer(
        "Entropy rose after psilocybin. [#2] Placebo left it unchanged. [#9] It was noon.",
        {2: RESULTS, 9: RESULTS_NEXT},
        [METHODS, RESULTS, RESULTS_NEXT],
        fake,
        THRESHOLD,
    )

    [call] = fake.calls
    assert sorted(call) == sorted(
        [
            "Entropy rose after psilocybin.",
            "Placebo left it unchanged.",  # an Answer sentence, and a sentence of both Chunks
            "Entropy rose after psilocybin in every EEG recording.",
            "No participant reported harm.",
            *METHODS.chunk.sentence_texts(),
        ]
    )


def test_an_answer_without_handles_makes_no_embedding_call() -> None:
    fake = FakeEmbedder()

    answer = resolve_answer("The Seed does not say. [#4]", {2: RESULTS}, [RESULTS], fake, THRESHOLD)

    assert fake.calls == []
    assert answer.sentences[0].pointers == []
    assert answer.unknown_handles == [4]


def test_counts_give_pointer_coverage_of_substantive_sentences_and_pointers_per_support() -> None:
    answer = resolve_answer(
        "Entropy rose after psilocybin. [#2] Entropy was measured during sleep. [#1]"
        " Did it last for days? In short: yes. See below:\n- Placebo left it unchanged",
        {1: METHODS, 2: RESULTS},
        [METHODS, RESULTS],
        FakeEmbedder(),
        THRESHOLD,
    )

    assert [answer.text[s.start : s.end] for s in answer.sentences if s.substantive] == [
        "Entropy rose after psilocybin.",
        "Entropy was measured during sleep.",
        "- Placebo left it unchanged",
    ]  # not a question, a lead-in, or under four words
    assert answer.counts() == AnswerCounts(
        sentences=6,
        substantive_sentences=3,
        pointer_coverage=2 / 3,
        pointers_close=1,
        pointers_weak=1,
        quotes_exact=0,
        quotes_not_exact=0,
        unknown_handles=0,
    )


def test_an_answer_without_substantive_sentences_has_no_pointer_coverage() -> None:
    answer = resolve_answer("Yes.", {}, [], FakeEmbedder(), THRESHOLD)

    assert answer.counts().pointer_coverage is None


def test_a_quote_is_exact_only_with_a_whole_place() -> None:
    assert not Quote(start=0, end=4).exact
    assert Quote(start=0, end=4, block_id=6, block_start=10, block_end=14).exact
    with pytest.raises(ValidationError):
        Quote(start=0, end=4, exact=True)  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValidationError):
        Quote(start=0, end=4, block_id=6)
