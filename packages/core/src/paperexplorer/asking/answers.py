"""An Answer's draft resolved: handles out, sentences split, each handle a Pointer with Support."""

import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from paperexplorer.domain import Answer, AnswerSentence, Pointer, Support, Vector
from paperexplorer.embedding import Embedder
from paperexplorer.parsing import Span
from paperexplorer.store import StoredChunk

logger = logging.getLogger(__name__)

# A handle ties a sentence to a Chunk: `[#12]`; `[#12, #14]` is read as `[#12][#14]`.
_HANDLE = re.compile(r"\[#\d+(?:\s*[,;]\s*#\d+)*\]")
# A sentence ends at . ! or ?, perhaps inside closing quotes or brackets, before a capital.
_SENTENCE_END = re.compile(r"[.!?][\"\u201d\u2019')\]]*(?=\s+[\"\u201c\u2018(\[]?[A-Z])")
_WORD = re.compile(r"[^\W_]+")
# A substantive sentence has at least this many words; shorter ones ("Yes, it does.") count
# towards no Pointer coverage.
_MIN_WORDS = 4


@dataclass(frozen=True, slots=True)
class _Handle:
    """A handle the model wrote: its number and where it stood in the text without handles."""

    number: int
    at: int


@dataclass(frozen=True, slots=True)
class _Place:
    """A sentence of a Chunk, as a candidate for a Pointer."""

    chunk: StoredChunk
    sentence: Span
    text: str


def resolve_answer(
    draft: str,
    handles: Mapping[int, StoredChunk],
    read: Sequence[StoredChunk],
    embedder: Embedder,
    threshold: float,
) -> Answer:
    """The Answer the reader sees for a complete draft, with its Pointers and Quotes."""
    text, written = _without_handles(draft)
    spans = _sentences(text)
    cited: list[list[StoredChunk]] = [[] for _ in spans]
    unknown: list[int] = []
    for handle in written:
        chunk = handles.get(handle.number)
        if chunk is None:
            unknown.append(handle.number)
            continue
        i = _sentence_of(spans, handle.at)
        if chunk not in cited[i]:
            cited[i].append(chunk)
    if unknown:
        logger.warning("An Answer wrote handles that tie to no Chunk, dropped: %s", unknown)

    pointers: list[list[Pointer]] = [[] for _ in spans]
    if any(cited):
        places = [
            p for c in _distinct([*(c for cs in cited for c in cs), *read]) for p in _places(c)
        ]
        asked = [i for i, cs in enumerate(cited) if cs]
        said = {i: text[spans[i].start : spans[i].end] for i in asked}
        distinct = list(dict.fromkeys([*said.values(), *(p.text for p in places)]))
        vectors = dict(zip(distinct, embedder.embed(distinct), strict=True))
        read_ids = {c.id for c in read}
        for i in asked:
            scored = [(p, _cosine(vectors[said[i]], vectors[p.text])) for p in places]
            for chunk in cited[i]:
                own = max(((p, s) for p, s in scored if p.chunk.id == chunk.id), key=_similarity)
                other = max(
                    (
                        (p, s)
                        for p, s in scored
                        if p.chunk.id != chunk.id and p.chunk.id in read_ids
                    ),
                    key=_similarity,
                    default=None,
                )
                if own[1] < threshold and other is not None and other[1] >= threshold:
                    own = other
                pointer = _pointer(*own, threshold)
                if all(_place(p) != _place(pointer) for p in pointers[i]):
                    pointers[i].append(pointer)

    return Answer(
        text=text,
        sentences=[
            AnswerSentence(
                start=s.start,
                end=s.end,
                substantive=_substantive(text[s.start : s.end]),
                pointers=p,
            )
            for s, p in zip(spans, pointers, strict=True)
        ],
        quotes=[],
        unknown_handles=unknown,
    )


def _without_handles(draft: str) -> tuple[str, list[_Handle]]:
    """The draft without its handles, and where in it each handle stood.

    A handle takes the spaces before it along; one that starts its line takes the whitespace
    after it instead, so a handle on a line of its own leaves no empty line.
    """
    text = ""
    written: list[_Handle] = []
    last = 0
    for m in _HANDLE.finditer(draft):
        text = (text + draft[last : m.start()]).rstrip(" \t")
        last = m.end()
        if not text or text.endswith("\n"):
            last += len(draft[last:]) - len(draft[last:].lstrip())
            if last == len(draft):
                text = text.rstrip()
        written += [_Handle(int(n), len(text)) for n in re.findall(r"\d+", m.group())]
    return text + draft[last:], written


def _sentences(text: str) -> list[Span]:
    """The sentences of `text`, line by line, without the whitespace around them."""
    spans: list[Span] = []
    for line in re.finditer(r"[^\n]+", text):
        start = line.start()
        ends = [line.start() + m.end() for m in _SENTENCE_END.finditer(line.group())]
        for end in [*ends, line.end()]:
            piece = text[start:end]
            if piece.strip():
                lead = len(piece) - len(piece.lstrip())
                spans.append(Span(start=start + lead, end=start + len(piece.rstrip())))
            start = end
    return spans


def _substantive(sentence: str) -> bool:
    """Whether a sentence states something a Pointer could bear out.

    Not a question back to the reader, not a lead-in to a list, and at least `_MIN_WORDS` words:
    "Yes." or "In short:" need no place in the Seed.
    """
    return not sentence.endswith(("?", ":")) and len(_WORD.findall(sentence)) >= _MIN_WORDS


def _sentence_of(spans: list[Span], at: int) -> int:
    """The sentence a handle at `at` belongs to: the last one that starts before it."""
    return max((i for i, s in enumerate(spans) if s.start < at), default=0)


def _distinct(chunks: Sequence[StoredChunk]) -> list[StoredChunk]:
    seen: set[int] = set()
    return [c for c in chunks if not (c.id in seen or seen.add(c.id))]


def _places(stored: StoredChunk) -> list[_Place]:
    chunk = stored.chunk
    texts = chunk.sentence_texts()
    return [_Place(stored, s, t) for s, t in zip(chunk.sentences, texts, strict=True)]


def _pointer(place: _Place, similarity: float, threshold: float) -> Pointer:
    chunk = place.chunk.chunk
    return Pointer(
        block_id=chunk.block,
        start=place.sentence.start,
        end=place.sentence.end,
        support=Support.CLOSE if similarity >= threshold else Support.WEAK,
        chunk_id=place.chunk.id,
        chunk_start=chunk.span.start,
        chunk_end=chunk.span.end,
        similarity=similarity,
    )


def _place(pointer: Pointer) -> tuple[int, int, int]:
    return pointer.block_id, pointer.start, pointer.end


def _similarity(scored: tuple[_Place, float]) -> float:
    return scored[1]


def _cosine(a: Vector, b: Vector) -> float:
    norms = math.sqrt(math.sumprod(a, a)) * math.sqrt(math.sumprod(b, b))
    return math.sumprod(a, b) / norms if norms else 0.0
