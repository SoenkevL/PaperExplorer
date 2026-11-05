"""Cut a block's text at its Citation spans, so each piece can be rendered as text or a link."""

from collections.abc import Sequence
from itertools import pairwise

from pydantic import BaseModel

from paperexplorer_api.schemas import CitationSpan, ConfidenceLevel

_WEAKEST_FIRST: list[ConfidenceLevel] = ["unlinked", "low", "high", "certain"]


class Segment(BaseModel):
    """A run of the block's text and the Citations whose spans cover all of it.

    `library_match` is drawn only on a firm link, so a doubtful one keeps its warning.
    """

    text: str
    citations: list[int]
    confidence_level: ConfidenceLevel | None
    library_match: bool = False


def segments(text: str, spans: Sequence[CitationSpan]) -> list[Segment]:
    """The text cut at every span boundary; joined back together the pieces are `text` again.

    Spans may overlap or coincide (a printed range's dash stands for several Citations), so a
    piece can belong to several Citations; it carries the weakest of their links, and is a
    Library Match when any of them is.
    """
    cuts = sorted(
        {0, len(text)} | {max(0, min(p, len(text))) for s in spans for p in (s.start, s.end)}
    )
    pieces: list[Segment] = []
    for start, end in pairwise(cuts):
        covering = [s for s in spans if s.start <= start and end <= s.end]
        levels = {s.confidence_level for s in covering}
        level: ConfidenceLevel | None = next((lv for lv in _WEAKEST_FIRST if lv in levels), None)
        pieces.append(
            Segment(
                text=text[start:end],
                citations=[s.id for s in covering],
                confidence_level=level,
                library_match=level in ("certain", "high")
                and any(s.library_match for s in covering),
            )
        )
    return pieces
