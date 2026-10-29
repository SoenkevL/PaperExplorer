"""An Answer's Quotes: words in double quotation marks, exact where a Chunk read this turn has them.

Quotation marks are the model's prose (ADR 0008). Only double marks, straight or curly, open
and close a Quote; single marks stay prose, since the closing one is also the apostrophe.
"""

import re
import unicodedata
from collections.abc import Iterator, Mapping, Sequence

from paperexplorer.domain import AnswerSentence, ChunkId, Quote
from paperexplorer.store import StoredChunk

_OPENING = ('"', "\u201c")
_CLOSING = ('"', "\u201d")
# Folded for matching only (ADR 0008): ligatures to their letters, and a hyphen between letters
# with any whitespace after it, which is how a line break splits a word.
_LIGATURES = {chr(c): unicodedata.normalize("NFKC", chr(c)) for c in range(0xFB00, 0xFB07)}
_HYPHENS = ("-", "\u2010", "\u2011")
_SOFT_HYPHEN = "\u00ad"
# What the model may put inside a closing mark that the authors' text need not have there.
_TRAILING = ".,;:"


def find_quotes(
    text: str, sentences: Sequence[AnswerSentence], read: Sequence[StoredChunk]
) -> list[Quote]:
    """Each double-quoted span of `text`, exact where its words are in a Chunk read this turn.

    The Chunks its sentences point at are searched first, then the others in the order given.
    Punctuation the model put inside the closing mark (`"...unchanged,"`) is left out of an
    exact Quote when the authors' text goes on without it.
    """
    folded = {c.id: _fold(c.chunk.text) for c in read}
    quotes: list[Quote] = []
    for start, end in _quoted(text):
        pointed = [
            p.chunk_id for s in sentences if s.start < end and start < s.end for p in s.pointers
        ]
        order = sorted(read, key=lambda c: pointed.index(c.id) if c.id in pointed else len(read))
        trimmed = start + len(text[start:end].rstrip(_TRAILING + " "))
        quote = next(
            (
                q
                for e in dict.fromkeys((end, trimmed))
                if e > start and (q := _exact(text, start, e, order, folded)) is not None
            ),
            Quote(start=start, end=end),
        )
        quotes.append(quote)
    return quotes


def _exact(
    text: str,
    start: int,
    end: int,
    read: Sequence[StoredChunk],
    folded: Mapping[ChunkId, tuple[str, list[int]]],
) -> Quote | None:
    """The exact Quote of `text[start:end]` at its first place in the Chunks, if any has it."""
    words, _ = _fold(text[start:end])
    if not words:
        return None
    pattern = _bounded(words)
    for chunk in read:
        chunk_text, where = folded[chunk.id]
        m = re.search(pattern, chunk_text)
        if m is not None:
            at = chunk.chunk.span.start
            return Quote(
                start=start,
                end=end,
                block_id=chunk.chunk.block,
                block_start=at + where[m.start()],
                block_end=at + where[m.end() - 1] + 1,
            )
    return None


def _quoted(text: str) -> Iterator[tuple[int, int]]:
    """`[start, end)` of the words between each pair of double marks, line by line."""
    for line in re.finditer(r"[^\n]+", text):
        opened: int | None = None
        for i, c in enumerate(line.group(), line.start()):
            if opened is None and c in _OPENING:
                opened = i + 1
            elif opened is not None and c in _CLOSING:
                words = text[opened:i]
                if words.strip():
                    yield (
                        opened + len(words) - len(words.lstrip()),
                        opened + len(words.rstrip()),
                    )
                opened = None


def _bounded(words: str) -> str:
    """A pattern for `words` that matches neither inside a longer word nor across one."""
    before = r"(?<!\w)" if re.match(r"\w", words) else ""
    after = r"(?!\w)" if re.search(r"\w$", words) else ""
    return before + re.escape(words) + after


def _fold(text: str) -> tuple[str, list[int]]:
    """`text` folded for matching, and for each folded character the index it came from."""
    out: list[str] = []
    where: list[int] = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == _SOFT_HYPHEN:
            i += 1
            continue
        if c in _HYPHENS and i > 0 and text[i - 1].isalpha():
            j = i + 1
            while j < len(text) and text[j].isspace():
                j += 1
            if j < len(text) and text[j].isalpha():
                i = j
                continue
        if c.isspace():
            if out and out[-1] != " ":
                out.append(" ")
                where.append(i)
            i += 1
            continue
        for letter in _LIGATURES.get(c, c):
            out.append(letter)
            where.append(i)
        i += 1
    if out and out[-1] == " ":
        out.pop()
        where.pop()
    return "".join(out), where
