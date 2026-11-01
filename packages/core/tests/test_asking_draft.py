"""The draft as shown: ended at gemma's first control token, without `<system>`, cut at handles."""

import json
import random
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from paperexplorer.asking.draft import DraftCut, DraftPiece, HandlePiece, TextPiece, answer_draft

# Every number a handle in the tests below may carry is one the Conversation knows.
KNOWN = range(1, 100)

DRAFTS = [
    ("Entropy rose. [#1]", "Entropy rose. [#1]"),
    ("Entropy rose.<|turn|>The reader", "Entropy rose."),
    ("Entropy rose. [#1]\n<channel|>More.\n<channel|>", "Entropy rose. [#1]"),
    ("<|tool_response>", ""),
    ("<system>A note.</system>Entropy rose.", "Entropy rose."),
    ("Entropy rose. <system>A note never closed", "Entropy rose."),
    ("Entropy rose <system>a</system>and fell.", "Entropy rose and fell."),
    ("Entropy rose.<", "Entropy rose."),
    ("Entropy rose.<|tool_re", "Entropy rose."),
    ("p < 0.05, x<y, a <b>bold</b> word, [#3].", "p < 0.05, x<y, a <b>bold</b> word, [#3]."),
    ("  Entropy rose.\n\n", "Entropy rose."),
]


@pytest.mark.parametrize(("draft", "answer"), DRAFTS)
def test_the_answer_ends_at_the_first_control_token_without_system_echoes(
    draft: str, answer: str
) -> None:
    assert answer_draft(draft) == answer


@pytest.mark.parametrize(("draft", "answer"), DRAFTS)
def test_a_draft_streamed_in_any_pieces_shows_what_the_answer_keeps(
    draft: str, answer: str
) -> None:
    rng = random.Random(draft)
    for _ in range(50):
        cuts = sorted(rng.sample(range(1, len(draft)), min(len(draft) - 1, rng.randint(0, 6))))
        pieces = [draft[a:b] for a, b in zip([0, *cuts], [*cuts, len(draft)], strict=True)]
        stream = DraftCut(KNOWN)

        shown = _shown([shown for piece in pieces for shown in stream.add(piece)])

        assert shown.strip() == answer, pieces
        assert not any(token in shown for token in ("<|", "|>", "<system>")), pieces


def _shown(pieces: Sequence[DraftPiece]) -> str:
    return "".join(p.text if isinstance(p, TextPiece) else f"[#{p.handle}]" for p in pieces)


def _handles(pieces: Sequence[DraftPiece]) -> list[int]:
    return [p.handle for p in pieces if isinstance(p, HandlePiece)]


def _words(pieces: Sequence[DraftPiece]) -> str:
    return "".join(p.text for p in pieces if isinstance(p, TextPiece))


# The handle forms Answers and drafts are read with, each rebuilt around a form a run recorded.
HANDLE_FORMS = json.loads(
    (Path(__file__).parents[3] / "tests" / "fixtures" / "handle-forms.json").read_text("utf-8")
)["forms"]


@pytest.mark.parametrize("bare", [False, True], ids=["numbered", "bare"])
@pytest.mark.parametrize("form", HANDLE_FORMS, ids=lambda form: form["name"])
def test_regression_eval_handle_forms_stream_as_one_handle_piece_per_number(
    form: dict[str, object], bare: bool
) -> None:
    """Eval runs 20261029T191529Z-94e980b and 20261029T202214Z-2b41752, the traces named.

    Every form `resolve_answer` reads as handles is a handle piece in the draft, whole or
    streamed a character at a time; groups it leaves as text stay text. With bare handles on,
    a form whose bare reading the table gives streams those numbers.
    """
    if bare and "bare_handles" not in form:
        pytest.skip("the table gives no bare reading of this form")
    draft = str(form["draft"])
    numbers = form["bare_handles"] if bare else form["handles"]

    whole = DraftCut(KNOWN, bare_handles=bare).add(draft)
    streamed = DraftCut(KNOWN, bare_handles=bare)
    pieces = [piece for character in draft for piece in streamed.add(character)]

    assert _handles(whole) == _handles(pieces) == numbers
    assert _words(whole) == _words(pieces)
    if not numbers:
        assert _words(whole) == draft


def test_a_handle_split_across_deltas_waits_until_it_is_whole() -> None:
    cut = DraftCut(KNOWN)

    assert cut.add("Entropy rose. [") == [TextPiece(text="Entropy rose. ")]
    assert cut.add("#1") == []
    assert cut.add("2, #") == []
    assert cut.add("4] Then") == [
        HandlePiece(handle=12),
        HandlePiece(handle=4),
        TextPiece(text=" Then"),
    ]


def test_a_bare_handle_split_across_deltas_is_a_handle_where_the_seed_numbers_no_reference() -> (
    None
):
    bare, numbered = DraftCut(KNOWN, bare_handles=True), DraftCut(KNOWN)

    assert [bare.add(d) for d in ("It rose [1", "3].")] == [
        [TextPiece(text="It rose ")],
        [HandlePiece(handle=13), TextPiece(text=".")],
    ]
    assert [numbered.add(d) for d in ("It rose [1", "3].")] == [
        [TextPiece(text="It rose ")],
        [TextPiece(text="[13].")],
    ]


def test_the_seeds_own_markers_held_back_follow_as_text() -> None:
    cut = DraftCut(KNOWN)

    assert cut.add("As printed [12] and (3), see [") == [
        TextPiece(text="As printed [12] and (3), see ")
    ]
    assert cut.add("1\u20133].") == [TextPiece(text="[1\u20133].")]


def test_a_handle_the_conversation_does_not_know_is_dropped() -> None:
    cut = DraftCut({3, 5})

    assert cut.add("It rose [#3, #9] and fell [#4\u2013#5].") == [
        TextPiece(text="It rose "),
        HandlePiece(handle=3),
        TextPiece(text=" and fell "),
        HandlePiece(handle=5),
        TextPiece(text="."),
    ]


def test_a_handle_a_control_token_follows_is_shown_and_nothing_after() -> None:
    cut = DraftCut(KNOWN)

    shown = [*cut.add("It rose [#3]<|tu"), *cut.add("rn|>[#4] The reader")]

    assert shown == [TextPiece(text="It rose "), HandlePiece(handle=3)]


def test_text_that_only_starts_like_a_handle_is_cut_at_once() -> None:
    # Whatever the model streams, looking for a handle's start must not backtrack for long.
    text = "[#1" + ", " * 24 + "and so on."
    started = time.monotonic()

    pieces = DraftCut(KNOWN).add(text)

    assert time.monotonic() - started < 0.5
    assert pieces == [TextPiece(text=text)]


def test_a_dropped_handle_takes_the_spaces_before_it_and_leaves_one_piece() -> None:
    # As `resolve_answer` drops it, so the Draft shows no gap the Answer will not have.
    assert DraftCut({3}).add("It rose [#9] and [#3] [#99] fell.") == [
        TextPiece(text="It rose and "),
        HandlePiece(handle=3),
        TextPiece(text=" fell."),
    ]
