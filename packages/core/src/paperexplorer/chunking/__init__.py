"""Cut a parsed Work's paragraphs and footnotes into Chunks of at most 400 bge-m3 tokens.

A Chunk never crosses a block. A block over the limit is split at sentence borders into the
fewest windows that fit, as even as can be, each sharing its last sentence with the next; a
sentence over the limit is cut at token boundaries on its own (design-v1).
"""

import logging
from bisect import bisect_left
from dataclasses import dataclass
from functools import cache
from itertools import accumulate, pairwise
from math import ceil

from paperexplorer.chunking._tokenizer import token_offsets
from paperexplorer.domain import Chunk, ChunkFlag
from paperexplorer.parsing import Block, BlockKind, ParsedWork, Span, sentence_spans

__all__ = ["MAX_TOKENS", "SHORT_TOKENS", "chunk"]

MAX_TOKENS = 400
SHORT_TOKENS = 30

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Sentence:
    """A sentence's text and the bge-m3 tokens it holds, `tokens[first:last]` of its block."""

    span: Span
    first: int
    last: int

    @property
    def tokens(self) -> int:
        return self.last - self.first


@dataclass(frozen=True)
class _Window:
    """What becomes one Chunk: whole sentences, or one piece of a sentence over the limit."""

    sentences: list[Span]
    tokens: int
    oversized: bool = False

    @property
    def span(self) -> Span:
        return Span(start=self.sentences[0].start, end=self.sentences[-1].end)


def chunk(doc: ParsedWork) -> list[Chunk]:
    """The Chunks of every paragraph and footnote of `doc`, in reading order; headings have none."""
    chunks: list[Chunk] = []
    for i, block in enumerate(doc.blocks):
        if block.kind is BlockKind.HEADING or not block.text.strip():
            continue
        for window in _windows(block, i):
            flags = [ChunkFlag.SHORT] if window.tokens < SHORT_TOKENS else []
            if window.oversized:
                flags.append(ChunkFlag.OVERSIZED_SENTENCE)
            span = window.span
            chunks.append(
                Chunk(
                    position=len(chunks),
                    block=i,
                    span=span,
                    text=block.text[span.start : span.end],
                    sentences=window.sentences,
                    heading_path=block.heading_path,
                    page=block.page,
                    boxes=block.boxes,
                    tokens=window.tokens,
                    flags=flags,
                    text_origin=doc.text_origin,
                )
            )
    return chunks


def _windows(block: Block, position: int) -> list[_Window]:
    """The block's Chunks to be: runs of sentences that fit, and the pieces of any that do not."""
    offsets = token_offsets(block.text)
    windows: list[_Window] = []
    run: list[_Sentence] = []
    for sentence in _sentences(block, [end - 1 for _, end in offsets]):
        if sentence.tokens <= MAX_TOKENS:
            run.append(sentence)
            continue
        logger.debug(
            "Block %d: a sentence of %d tokens is cut into %d Chunks",
            position,
            sentence.tokens,
            ceil(sentence.tokens / MAX_TOKENS),
        )
        windows += _fit(run) + _cut(block.text, sentence, offsets)
        run = []
    return windows + _fit(run)


def _sentences(block: Block, token_lasts: list[int]) -> list[_Sentence]:
    """The block's sentences, side by side, with every bge-m3 token in exactly one of them.

    GROBID's spans can overlap where accents sit apart from their letters; a sentence then starts
    where the one before it ends. Text before the first span or between spans joins a sentence,
    so the sentences hold all of the block's text. A token belongs where its last character is.
    """
    text = block.text
    borders = [0]
    reach = 0
    for s in sorted(sentence_spans(block), key=lambda s: s.start):
        start = max(s.start, reach)
        if borders[-1] < start < len(text):
            borders.append(start)
        reach = max(reach, s.end)
    sentences: list[_Sentence] = []
    for start, end in pairwise([*borders, len(text)]):
        span = _trimmed(text, start, end)
        if span is not None:
            first, last = bisect_left(token_lasts, start), bisect_left(token_lasts, end)
            sentences.append(_Sentence(span=span, first=first, last=last))
    return sentences


def _fit(sentences: list[_Sentence]) -> list[_Window]:
    """The fewest windows of whole sentences within the limit, as even as can be.

    Each window shares its last sentence with the next, unless that sentence and the one after it
    are over the limit together. Evenness is the smallest sum of squared window sizes.
    """
    if not sentences:
        return []
    prefix = list(accumulate((s.tokens for s in sentences), initial=0))
    n = len(sentences)

    def size(i: int, j: int) -> int:
        return prefix[j + 1] - prefix[i]

    @cache
    def best(i: int, shared: bool) -> tuple[int, int, tuple[tuple[int, int], ...]]:
        """(windows, sum of squared sizes, each window's first and last sentence) from i on."""
        options: list[tuple[int, int, tuple[tuple[int, int], ...]]] = []
        # A window that starts on a shared sentence must add one of its own.
        for j in range(i + 1 if shared else i, n):
            if size(i, j) > MAX_TOKENS:
                break
            if j == n - 1:
                options.append((1, size(i, j) ** 2, ((i, j),)))
                continue
            overlap = j > i and size(j, j + 1) <= MAX_TOKENS
            count, cost, rest = best(j if overlap else j + 1, overlap)
            options.append((count + 1, cost + size(i, j) ** 2, ((i, j), *rest)))
        return min(options)

    return [
        _Window(
            sentences=[s.span for s in sentences[i : j + 1]],
            tokens=sum(s.tokens for s in sentences[i : j + 1]),
        )
        for i, j in best(0, False)[2]
    ]


def _cut(text: str, sentence: _Sentence, offsets: list[tuple[int, int]]) -> list[_Window]:
    """A sentence over the limit, cut at token boundaries into the fewest even pieces that fit."""
    pieces = ceil(sentence.tokens / MAX_TOKENS)
    cuts = [sentence.first + round(k * sentence.tokens / pieces) for k in range(pieces + 1)]
    starts = [sentence.span.start, *(offsets[c][0] for c in cuts[1:-1]), sentence.span.end]
    windows: list[_Window] = []
    for (start, end), (first, last) in zip(pairwise(starts), pairwise(cuts), strict=True):
        span = _trimmed(text, start, end)
        if span is not None:
            windows.append(_Window(sentences=[span], tokens=last - first, oversized=True))
    return windows


def _trimmed(text: str, start: int, end: int) -> Span | None:
    """`[start, end)` without the whitespace at either end; None when nothing else is left."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return Span(start=start, end=end) if start < end else None
