"""Cut a streamed draft and a resolved Answer into pieces the Conversation sidebar renders.

Every offset of an Answer counts code points of its `text` (Python `str` indices), so the
cutting happens here, in Python, and the browser receives the pieces already cut.
"""

import re
from itertools import pairwise

from pydantic import BaseModel

from paperexplorer_api.schemas import Answer, Pointer, Quote

# The handle grammar `asking` reads (design-asking.md, Answers), written again here since the web
# app cannot import core; tests/fixtures/handle-forms.json holds the forms both are tested with.
# A group in `[]`, `()`, `{}` or `<>`, nested one deep, of numbers apart by commas, semicolons,
# spaces or `#`, at least one with a `#`; a range `#5-#7` (any dash) reads as its two ends.
_NUMBER = r"#*\d+"
_ITEM = rf"{_NUMBER}(?:[ \t]*[-\u2010-\u2014][ \t]*{_NUMBER})?"
_BRACKETS = {"[": "]", "(": ")", "{": "}", "<": ">"}


def _group(item: str) -> str:
    """A bracketed group of `item`s; without a separator, the next starts with `#` or a bracket."""
    items = rf"(?:{item})(?:(?:[ \t,;]+|(?=[#\[({{<]))(?:{item}))*"
    return "|".join(
        rf"{re.escape(o)}[ \t]*{items}[ \t]*{re.escape(c)}" for o, c in _BRACKETS.items()
    )


_HANDLE = re.compile(_group(rf"{_group(_ITEM)}|{_ITEM}"))
# What a handle is written with; any other character ends the hope that a group is one.
_HANDLE_CHARS = frozenset("#0123456789 \t,;-\u2010\u2011\u2012\u2013\u2014[](){}<>")


class DraftPiece(BaseModel):
    """A run of the draft's text, or one handle (`handle` set) shown as a placeholder chip."""

    text: str
    handle: int | None


class Draft:
    """The draft as it streams: each delta cut into text and handles.

    A handle split across deltas (`[#1` then `2]`) is held back until it is whole, so a chip
    never shows half a handle; text that turns out not to be a handle follows with the next delta.
    """

    def __init__(self) -> None:
        self._held = ""

    def add(self, delta: str) -> list[DraftPiece]:
        """The pieces of `delta` that can be shown now, after any text held from before."""
        text = self._held + delta
        cut = _unfinished(text)
        text, self._held = text[:cut], text[cut:]
        pieces: list[DraftPiece] = []
        at = 0
        for m in _HANDLE.finditer(text):
            if "#" not in m.group():
                continue  # the Seed's own marker, such as [74]
            if m.start() > at:
                pieces.append(DraftPiece(text=text[at : m.start()], handle=None))
            pieces.extend(
                DraftPiece(text=f"#{n}", handle=int(n)) for n in re.findall(r"\d+", m.group())
            )
            at = m.end()
        if at < len(text):
            pieces.append(DraftPiece(text=text[at:], handle=None))
        return pieces


def _unfinished(text: str) -> int:
    """Where a group the next delta may finish into a handle starts; else the end of `text`."""
    start = len(text)
    while start > 0 and text[start - 1] in _HANDLE_CHARS:
        start -= 1
    return next(
        (i for i in range(start, len(text)) if text[i] in _BRACKETS and _still_open(text[i:])),
        len(text),
    )


def _still_open(text: str) -> bool:
    """Whether the group `text` opens is open at its end: never closed, nested one deep at most."""
    closing: list[str] = []
    for c in text:
        if c in _BRACKETS:
            if len(closing) == 2:
                return False
            closing.append(_BRACKETS[c])
        elif c in _BRACKETS.values() and (not closing or closing.pop() != c or not closing):
            return False
    return bool(closing)


class AnswerPiece(BaseModel):
    """A run of the Answer's text, inside one Quote or outside every Quote."""

    text: str
    quote: Quote | None


class PointerChip(BaseModel):
    """A Pointer as its sentence shows it: numbered in the order of the whole Answer, from 1."""

    number: int
    pointer: Pointer


class AnswerRun(BaseModel):
    """One sentence of the Answer with its Pointers, or the text between two sentences."""

    sentence: bool
    pieces: list[AnswerPiece]
    pointers: list[PointerChip]


def answer_runs(answer: Answer) -> list[AnswerRun]:
    """The Answer's text cut at its sentences and Quotes; joined back together it is the text.

    A Quote that runs across a sentence's end is cut in two, one piece in each sentence.
    """
    text = answer.text
    spans = [(s.start, s.end) for s in answer.sentences] + [(q.start, q.end) for q in answer.quotes]
    cuts = sorted({0, len(text)} | {max(0, min(p, len(text))) for span in spans for p in span})
    runs: list[AnswerRun] = []
    current: int | None = None  # the sentence the last run belongs to
    numbered = 0
    for start, end in pairwise(cuts):
        index = next(
            (i for i, s in enumerate(answer.sentences) if s.start <= start and end <= s.end), None
        )
        quote = next((q for q in answer.quotes if q.start <= start and end <= q.end), None)
        piece = AnswerPiece(text=text[start:end], quote=quote)
        if runs and index == current:
            runs[-1].pieces.append(piece)
            continue
        chips: list[PointerChip] = []
        if index is not None:
            for pointer in answer.sentences[index].pointers:
                numbered += 1
                chips.append(PointerChip(number=numbered, pointer=pointer))
        runs.append(AnswerRun(sentence=index is not None, pieces=[piece], pointers=chips))
        current = index
    return runs
