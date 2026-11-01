"""Pointer and Quote resolution of an Answer, over hand-built Chunks with the `FakeEmbedder`.

The fake's cosine is the number of content words two texts share over the square root of the
product of their counts of distinct content words (stop words such as "the", "was" and "of" left
out). Every test resolves at `THRESHOLD`, so a sentence is close where that share clears it.
"""

import json
import random
import re
import unicodedata
from pathlib import Path

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


def _quoted(answer_text: str, quote: Quote) -> str:
    return answer_text[quote.start : quote.end]


def _printed(chunk: StoredChunk, quote: Quote) -> str:
    """The block's text at an exact Quote's place, read from the Chunk that holds it."""
    assert quote.block_start is not None and quote.block_end is not None
    start = chunk.chunk.span.start
    return chunk.chunk.text[quote.block_start - start : quote.block_end - start]


def test_words_in_double_quotes_that_match_a_chunk_read_this_turn_are_an_exact_quote() -> None:
    answer = resolve_answer(
        "The Methods say “Entropy was computed from each EEG recording” [#1]."
        ' Results add "Placebo left it unchanged." [#2]',
        {1: METHODS, 2: RESULTS},
        [METHODS, RESULTS],
        FakeEmbedder(),
        THRESHOLD,
    )

    curly, straight = answer.quotes
    assert _quoted(answer.text, curly) == "Entropy was computed from each EEG recording"
    assert (curly.exact, curly.block_id) == (True, 4)
    assert curly.block_start == 10 + METHODS.chunk.text.index("Entropy was computed")
    assert _printed(METHODS, curly) == "Entropy was computed from each EEG recording"
    assert _quoted(answer.text, straight) == "Placebo left it unchanged."
    assert (straight.exact, straight.block_id) == (True, 6)
    assert _printed(RESULTS, straight) == "Placebo left it unchanged."


def test_a_quote_counts_as_exact_in_any_chunk_read_this_turn_whatever_its_handle_says() -> None:
    answer = resolve_answer(
        "The dose came “at noon to every participant” [#1].",
        {1: METHODS, 3: DOSING},
        [METHODS, DOSING],
        FakeEmbedder(),
        THRESHOLD,
    )

    [quote] = answer.quotes
    assert (quote.exact, quote.block_id) == (True, 2)
    assert _printed(DOSING, quote) == "at noon to every participant"


@pytest.mark.parametrize(
    "draft",
    [
        "They “measured entropy in every EEG recording” [#1].",  # words the Chunk never has
        "They write “entropy was computed from each EEG recording” [#1].",  # not as printed
        "They write “Entropy was compute” [#1].",  # inside a longer word
        "They write “Psilocybin was given at noon” [#3].",  # a Chunk not read this turn
        "They write “\u00ad” [#1].",  # nothing left to match once folded
    ],
)
def test_a_quote_without_the_authors_exact_words_in_a_chunk_read_this_turn_is_not_exact(
    draft: str,
) -> None:
    answer = resolve_answer(draft, {1: METHODS, 3: DOSING}, [METHODS], FakeEmbedder(), THRESHOLD)

    [quote] = answer.quotes
    assert not quote.exact
    assert (quote.block_id, quote.block_start, quote.block_end) == (None, None, None)
    assert answer.text[quote.start - 1] == "“" and answer.text[quote.end] == "”"


def test_only_double_marks_closed_on_their_line_make_a_quote() -> None:
    answer = resolve_answer(
        'Its \u2018set and setting\u2019 mattered, "as the authors put it. An open “quote.\n'
        "“Placebo left it unchanged” [#2], and the participants\u2019"
        " “\u2018setting\u2019 mattered”.",
        {2: RESULTS},
        [RESULTS],
        FakeEmbedder(),
        THRESHOLD,
    )

    assert [(_quoted(answer.text, q), q.exact) for q in answer.quotes] == [
        ("Placebo left it unchanged", True),
        ("\u2018setting\u2019 mattered", False),
    ]
    assert (answer.counts().quotes_exact, answer.counts().quotes_not_exact) == (1, 1)


# As a text layer may print it: a ligature, a compound whose line-break hyphen was taken for a
# soft one ("cause-|effect"), and a line-break hyphen pdfium did not mark ("self-| organizing").
PRINTED = _chunk(
    11, ["The ﬁrst causeeffect structure was self- organizing in every trial."], block=9, at=3
)


def test_ligatures_line_break_hyphens_and_whitespace_are_folded_for_matching_only() -> None:
    answer = resolve_answer(
        "It reads “The first cause-effect structure was self-organizing” [#1]"
        " and “was self- organizing in every\u00a0 trial.” [#1]",
        {1: PRINTED},
        [PRINTED],
        FakeEmbedder(),
        THRESHOLD,
    )

    first, second = answer.quotes
    assert first.exact and second.exact
    assert _printed(PRINTED, first) == "The ﬁrst causeeffect structure was self- organizing"
    assert (first.block_start, first.block_end) == (3, 3 + len(_printed(PRINTED, first)))
    assert _printed(PRINTED, second) == "was self- organizing in every trial."


def test_punctuation_the_model_put_inside_the_closing_mark_stays_outside_an_exact_quote() -> None:
    answer = resolve_answer(
        "“Entropy was computed from each EEG recording,” they write. [#1]",
        {1: METHODS},
        [METHODS],
        FakeEmbedder(),
        THRESHOLD,
    )

    [quote] = answer.quotes
    assert quote.exact
    assert _quoted(answer.text, quote) == "Entropy was computed from each EEG recording"
    assert _printed(METHODS, quote) == "Entropy was computed from each EEG recording"


ECHO = _chunk(12, ["As before, entropy rose after psilocybin."], block=13)


def test_a_quote_found_in_several_chunks_links_to_the_one_its_sentence_points_at() -> None:
    answer = resolve_answer(
        "The discussion repeats as before that entropy “rose after psilocybin” [#12].",
        {2: RESULTS, 12: ECHO},
        [RESULTS, ECHO],
        FakeEmbedder(),
        THRESHOLD,
    )

    assert [p.chunk_id for p in answer.sentences[0].pointers] == [12]
    [quote] = answer.quotes
    assert (quote.exact, quote.block_id) == (True, 13)


READ = [METHODS, RESULTS, RESULTS_NEXT, DOSING, PRINTED, ECHO]


def _letters(text: str) -> str:
    """Letters and digits in order, ligatures spelled out: what an exact Quote cannot change."""
    return re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", text))


def _drafts() -> list[str]:
    """Answers quoting runs of the read Chunks' words, half of them with one character changed."""
    rng = random.Random(2026)
    drafts: list[str] = []
    for _ in range(300):
        words = rng.choice(READ).chunk.text.split(" ")
        i = rng.randrange(len(words))
        quoted = " ".join(words[i : rng.randrange(i, min(len(words), i + 6)) + 1])
        if rng.random() < 0.5:
            k = rng.randrange(len(quoted))
            quoted = quoted[:k] + rng.choice("aeEoxz.,-ﬁ ") + quoted[k + 1 :]
        drafts.append(f"They write “{quoted}” [#{rng.choice([1, 2, 3])}].")
    return drafts


def test_no_quote_is_exact_unless_the_authors_printed_its_words_at_its_place() -> None:
    exact = 0
    for draft in _drafts():
        answer = resolve_answer(
            draft, {1: METHODS, 2: RESULTS, 3: DOSING}, READ, FakeEmbedder(), THRESHOLD
        )
        for quote in answer.quotes:
            if not quote.exact:
                continue
            exact += 1
            assert quote.block_start is not None and quote.block_end is not None
            holder = next(
                c
                for c in READ
                if c.chunk.block == quote.block_id
                and c.chunk.span.start <= quote.block_start < quote.block_end <= c.chunk.span.end
            )
            assert _letters(_printed(holder, quote)) == _letters(_quoted(answer.text, quote)), draft
    assert exact >= 100  # the guard saw real matches, not only misses


# The handle forms Answers and drafts are read with, each rebuilt around a form a run recorded.
HANDLE_FORMS = json.loads(
    (Path(__file__).parents[3] / "tests" / "fixtures" / "handle-forms.json").read_text("utf-8")
)["forms"]


@pytest.mark.parametrize("form", HANDLE_FORMS, ids=lambda form: form["name"])
def test_regression_eval_handle_forms_are_read_with_every_number_they_carry(
    form: dict[str, object],
) -> None:
    """Eval runs 20261029T191529Z-94e980b and 20261029T202214Z-2b41752, the traces named.

    The model wrote handles in parentheses, braces and angle brackets, nested, with ranges and
    with `#` as a separator; read as `[#n]` only, they stayed in the text and gave no Pointer.
    Groups without a `#` are the Seed's own markers and stay text.
    """
    draft, numbers, text = str(form["draft"]), form["handles"], str(form["text"])

    unknown = resolve_answer(draft, {}, [], FakeEmbedder(), THRESHOLD)
    tied = resolve_answer(
        draft, {n: METHODS for n in unknown.unknown_handles}, [METHODS], FakeEmbedder(), THRESHOLD
    )

    assert unknown.unknown_handles == numbers
    assert unknown.text == text
    # Every sentence of a draft with handles carries one here, and gets its Pointer.
    assert [bool(s.pointers) for s in tied.sentences] == [bool(numbers)] * len(tied.sentences)
    if "bare_handles" in form:
        bare = resolve_answer(draft, {}, [], FakeEmbedder(), THRESHOLD, bare_handles=True)
        assert (bare.unknown_handles, bare.text) == (form["bare_handles"], form["bare_text"])


@pytest.mark.parametrize(
    ("draft", "numbers", "text"),
    [
        ("Entropy rose [13].", [13], "Entropy rose."),
        ("It rose [13, 14], then fell [13\u201315].", [13, 14, 13, 15], "It rose, then fell."),
        ("It rose. [3][#4]", [3, 4], "It rose."),
        # Only square brackets holding numbers: the Seed's abstract numbers its themes (1)…(4).
        ("Its themes are (1) entropy and (2) richness.", [], None),
        ("It rose {13} and <14>, then [<13>] and [13 a].", [], None),
    ],
)
def test_a_bare_number_in_square_brackets_is_a_handle_where_the_seed_numbers_no_reference(
    draft: str, numbers: list[int], text: str | None
) -> None:
    bare = resolve_answer(draft, {}, [], FakeEmbedder(), THRESHOLD, bare_handles=True)
    numbered = resolve_answer(draft, {}, [], FakeEmbedder(), THRESHOLD)

    assert (bare.unknown_handles, bare.text) == (numbers, text or draft)
    # Where the Seed numbers its References, `[13]` is its own marker and stays text.
    assert numbered.text == (draft if "#" not in draft else "It rose. [3]")
    assert numbered.unknown_handles == ([4] if "#" in draft else [])


def test_a_bare_handle_points_like_any_other() -> None:
    answer = resolve_answer(
        "Entropy was computed from the EEG recordings [1].",
        {1: METHODS},
        [METHODS],
        FakeEmbedder(),
        THRESHOLD,
        bare_handles=True,
    )

    [sentence] = answer.sentences
    [pointer] = sentence.pointers
    assert (pointer.chunk_id, pointer.support) == (7, Support.CLOSE)
