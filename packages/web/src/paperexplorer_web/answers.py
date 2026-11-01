"""Cut a resolved Answer into the runs the Conversation sidebar renders.

Every offset of an Answer counts code points of its `text` (Python `str` indices), so the
cutting happens here, in Python, and the browser receives the pieces already cut. The draft
comes from the API already cut into text and handles (`DeltaEvent.pieces`).
"""

from itertools import pairwise

from pydantic import BaseModel

from paperexplorer_api.schemas import Answer, Pointer, Quote


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
