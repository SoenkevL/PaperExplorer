"""Where a block's sentences are: GROBID's spans, or for a scan, breaks at end punctuation."""

import re

from paperexplorer.parsing.models import Block, Span

# A sentence ends at . ! or ? (perhaps with a footnote number: "(DMT).1 The") before a capital.
_SENTENCE_BREAK = re.compile(r"[.!?](\d{0,3})\s+(?=[A-Z(\[“\"])")


def sentence_spans(block: Block) -> list[Span]:
    """The block's sentences: GROBID's spans, else split at end punctuation (scans have none)."""
    if block.sentences:
        return block.sentences
    breaks = list(_SENTENCE_BREAK.finditer(block.text))
    starts = [0, *(m.end() for m in breaks)]
    ends = [*(m.start() + 1 + len(m.group(1)) for m in breaks), len(block.text)]
    return [Span(start=s, end=e) for s, e in zip(starts, ends, strict=True)]
