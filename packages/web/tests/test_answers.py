import json
import time
from pathlib import Path

import pytest

from paperexplorer_api.schemas import Answer, AnswerSentence, Pointer, Quote
from paperexplorer_web.answers import Draft, DraftPiece, answer_runs


def _chip(n: int) -> DraftPiece:
    return DraftPiece(text=f"#{n}", handle=n)


def _plain(text: str) -> DraftPiece:
    return DraftPiece(text=text, handle=None)


def _pointer(block: int, support: str = "close") -> Pointer:
    return Pointer.model_validate(
        {"block_id": block, "start": 4, "end": 30, "support": support, "chunk_start": 0,
         "chunk_end": 80}
    )  # fmt: skip


def test_a_drafts_handles_become_placeholder_chips() -> None:
    pieces = Draft().add("Entropy rose. [#3] It fell [#4][#5].")

    assert pieces == [
        _plain("Entropy rose. "),
        _chip(3),
        _plain(" It fell "),
        _chip(4),
        _chip(5),
        _plain("."),
    ]


def test_a_handle_group_gives_one_chip_per_handle() -> None:
    assert Draft().add("Both [#3, #5] and [#7; #8].") == [
        _plain("Both "),
        _chip(3),
        _chip(5),
        _plain(" and "),
        _chip(7),
        _chip(8),
        _plain("."),
    ]


def test_a_handle_split_across_deltas_waits_until_it_is_whole() -> None:
    draft = Draft()

    assert draft.add("Entropy rose. [") == [_plain("Entropy rose. ")]
    assert draft.add("#1") == []
    assert draft.add("2, #") == []
    assert draft.add("4] Then") == [_chip(12), _chip(4), _plain(" Then")]


def test_the_seeds_own_markers_stay_text() -> None:
    draft = Draft()

    assert draft.add("As printed [12] and (3), see [") == [_plain("As printed [12] and (3), see ")]
    assert draft.add("1\u20133].") == [_plain("[1\u20133].")]


# The handle forms core and web both read, each rebuilt around a form an eval run recorded.
HANDLE_FORMS = json.loads(
    (Path(__file__).parents[3] / "tests" / "fixtures" / "handle-forms.json").read_text("utf-8")
)["forms"]


def _chips(pieces: list[DraftPiece]) -> list[int]:
    return [p.handle for p in pieces if p.handle is not None]


def _words(pieces: list[DraftPiece]) -> str:
    return "".join(p.text for p in pieces if p.handle is None)


@pytest.mark.parametrize("form", HANDLE_FORMS, ids=lambda form: form["name"])
def test_regression_eval_handle_forms_become_one_chip_per_number(form: dict[str, object]) -> None:
    """Eval runs 20261029T191529Z-94e980b and 20261029T202214Z-2b41752, the traces named.

    Every form core reads as handles is a chip in the draft, whole or streamed a character at a
    time; groups without a `#` stay text.
    """
    draft, numbers = str(form["draft"]), form["handles"]

    whole = Draft().add(draft)
    streamed = Draft()
    pieces = [piece for character in draft for piece in streamed.add(character)]

    assert _chips(whole) == _chips(pieces) == numbers
    assert _words(whole) == _words(pieces)
    if not numbers:
        assert _words(whole) == draft


def test_text_that_only_starts_like_a_handle_is_cut_at_once() -> None:
    # Whatever the model streams, looking for a handle's start must not backtrack for long.
    text = "[#1" + ", " * 24 + "and so on."
    started = time.monotonic()

    pieces = Draft().add(text)

    assert time.monotonic() - started < 0.5
    assert pieces == [_plain(text)]


def test_an_answer_is_cut_into_its_sentences_with_their_pointers() -> None:
    text = "Entropy rose.\n\nIt was measured twice. Why?"
    answer = Answer(
        text=text,
        sentences=[
            AnswerSentence(start=0, end=13, pointers=[_pointer(3), _pointer(4, "weak")]),
            AnswerSentence(start=15, end=37, pointers=[_pointer(9)]),
            AnswerSentence(start=38, end=42, pointers=[]),
        ],
        quotes=[],
    )

    runs = answer_runs(answer)

    assert "".join(p.text for r in runs for p in r.pieces) == text
    assert [("".join(p.text for p in r.pieces), r.sentence) for r in runs] == [
        ("Entropy rose.", True),
        ("\n\n", False),
        ("It was measured twice.", True),
        (" ", False),
        ("Why?", True),
    ]
    chips = [[(c.number, c.pointer.block_id, c.pointer.support) for c in r.pointers] for r in runs]
    assert chips == [[(1, 3, "close"), (2, 4, "weak")], [], [(3, 9, "close")], [], []]


def test_quotes_are_cut_out_of_their_sentence_by_code_points() -> None:
    # The emoji and the italic H are one code point each (two UTF-16 units each in JavaScript);
    # e + U+0301 is two code points.
    text = '\U0001f9e0 Amige\u0301 found \U0001d43b "rose sharply" and "fell".'
    rose = text.index("rose")
    fell = text.index("fell")
    exact = Quote(start=rose, end=rose + 12, exact=True, block_id=2, block_start=40, block_end=52)
    loose = Quote(start=fell, end=fell + 4, exact=False, block_id=None, block_start=None,
                  block_end=None)  # fmt: skip
    answer = Answer(
        text=text,
        sentences=[AnswerSentence(start=0, end=len(text), pointers=[_pointer(2)])],
        quotes=[exact, loose],
    )

    [run] = answer_runs(answer)

    assert [(p.text, p.quote) for p in run.pieces] == [
        ('\U0001f9e0 Amige\u0301 found \U0001d43b "', None),
        ("rose sharply", exact),
        ('" and "', None),
        ("fell", loose),
        ('".', None),
    ]


def test_a_quote_across_a_sentence_end_is_split_between_both_sentences() -> None:
    text = 'They wrote "It rose. Then it fell." twice.'
    start = text.index("It rose")
    quote = Quote(start=start, end=start + 22, exact=True, block_id=1, block_start=0, block_end=22)
    answer = Answer(
        text=text,
        sentences=[
            AnswerSentence(start=0, end=20, pointers=[]),
            AnswerSentence(start=21, end=len(text), pointers=[]),
        ],
        quotes=[quote],
    )

    runs = answer_runs(answer)

    quoted = [(p.text, r.sentence) for r in runs for p in r.pieces if p.quote == quote]
    assert quoted == [("It rose.", True), (" ", False), ("Then it fell.", True)]
    assert "".join(p.text for r in runs for p in r.pieces) == text
