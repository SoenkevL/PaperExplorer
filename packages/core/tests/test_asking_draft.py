"""Where the Answer ends in a draft: at gemma's first control token, without `<system>` echoes."""

import random

import pytest

from paperexplorer.asking.draft import DraftCut, answer_draft

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
    ("p < 0.05, x<y, a <b>bold</b> word, <#3>.", "p < 0.05, x<y, a <b>bold</b> word, <#3>."),
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
        stream = DraftCut()

        shown = "".join(stream.add(piece) for piece in pieces)

        assert shown.strip() == answer, pieces
        assert not any(token in shown for token in ("<|", "|>", "<system>")), pieces
