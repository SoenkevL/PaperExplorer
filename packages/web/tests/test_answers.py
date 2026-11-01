from paperexplorer_api.schemas import Answer, AnswerSentence, Pointer, Quote
from paperexplorer_web.answers import answer_runs


def _pointer(block: int, support: str = "close") -> Pointer:
    return Pointer.model_validate(
        {"block_id": block, "start": 4, "end": 30, "support": support, "chunk_start": 0,
         "chunk_end": 80}
    )  # fmt: skip


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
