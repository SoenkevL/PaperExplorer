"""An Answer's draft resolved: handles out, sentences split, each handle a Pointer with Support."""

import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from paperexplorer.asking.handles import GROUP, handle_numbers
from paperexplorer.asking.quotes import find_quotes
from paperexplorer.domain import Answer, AnswerSentence, Pointer, Support, Vector
from paperexplorer.embedding import Embedder
from paperexplorer.parsing import Span
from paperexplorer.store import StoredChunk

logger = logging.getLogger(__name__)

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
    *,
    bare_handles: bool = False,
) -> Answer:
    """The Answer the reader sees for a complete draft, with its Pointers and Quotes.

    `handles` maps each handle of the Conversation to its Chunk; `read` are the Chunks the model
    read in this turn. `bare_handles` reads a bare `[13]` as `[#13]`, for a Seed that numbers no
    Reference. Handles leave the text, so every offset counts into what the reader sees.
    A handle written right after a sentence's full stop belongs to that sentence; one that ties
    to no Chunk is dropped. Each Chunk a sentence cites gives one Pointer (design-asking.md):
    the cited Chunk's closest sentence if it clears `threshold`, else the closest sentence of
    another Chunk read this turn if that one does, else weak Support at the cited Chunk's
    closest. Pointers that reach the same place are one. Every sentence to compare is embedded
    in a single `embedder.embed` call, and none without a handle.
    """
    text, written = _without_handles(draft, bare_handles)
    spans = _sentences(text)
    cited: list[list[StoredChunk]] = [[] for _ in spans]
    unknown: list[int] = []
    for handle in written:
        chunk = handles.get(handle.number)
        if chunk is None:
            unknown.append(handle.number)
            continue
        i = _sentence_of(spans, handle.at)
        if all(c.id != chunk.id for c in cited[i]):
            cited[i].append(chunk)
    if unknown:
        logger.warning("An Answer wrote handles that tie to no Chunk, dropped: %s", unknown)

    pointers = _pointers(text, spans, cited, read, embedder, threshold)
    sentences = [
        AnswerSentence(
            start=s.start, end=s.end, substantive=_substantive(text[s.start : s.end]), pointers=p
        )
        for s, p in zip(spans, pointers, strict=True)
    ]
    return Answer(
        text=text,
        sentences=sentences,
        quotes=find_quotes(text, sentences, read),
        unknown_handles=unknown,
    )


def _without_handles(draft: str, bare: bool) -> tuple[str, list[_Handle]]:
    """The draft without its handles, and where in it each handle stood.

    A handle takes the spaces before it along; one that starts its line takes the whitespace
    after it instead, so a handle on a line of its own leaves no empty line.
    """
    text = ""
    written: list[_Handle] = []
    last = 0
    for m in GROUP.finditer(draft):
        if (numbers := handle_numbers(m.group(), bare=bare)) is None:
            continue  # the Seed's own marker, such as [74]
        text = (text + draft[last : m.start()]).rstrip(" \t")
        last = m.end()
        if not text or text.endswith("\n"):
            last += len(draft[last:]) - len(draft[last:].lstrip())
            if last == len(draft):
                text = text.rstrip()
        written += [_Handle(n, len(text)) for n in numbers]
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


def _pointers(
    text: str,
    spans: list[Span],
    cited: list[list[StoredChunk]],
    read: Sequence[StoredChunk],
    embedder: Embedder,
    threshold: float,
) -> list[list[Pointer]]:
    """Each sentence's Pointers: one per Chunk it cites, in the order cited."""
    pointers: list[list[Pointer]] = [[] for _ in spans]
    asked = [i for i, chunks in enumerate(cited) if chunks]
    if not asked:
        return pointers
    candidates = {c.id: c for chunks in cited for c in chunks} | {c.id: c for c in read}
    places = [p for c in candidates.values() for p in _places(c)]
    said = {i: text[spans[i].start : spans[i].end] for i in asked}
    texts = list(dict.fromkeys([*said.values(), *(p.text for p in places)]))
    unit = {t: _unit(v) for t, v in zip(texts, embedder.embed(texts), strict=True)}
    read_ids = {c.id for c in read}
    for i in asked:
        scored = [(p, math.sumprod(unit[said[i]], unit[p.text])) for p in places]
        for chunk in cited[i]:
            best = max((ps for ps in scored if ps[0].chunk.id == chunk.id), key=_similarity)
            elsewhere = max(
                (ps for ps in scored if ps[0].chunk.id != chunk.id and ps[0].chunk.id in read_ids),
                key=_similarity,
                default=None,
            )
            if best[1] < threshold and elsewhere is not None and elsewhere[1] >= threshold:
                best = elsewhere
            pointer = _pointer(*best, threshold)
            if all(_place(p) != _place(pointer) for p in pointers[i]):
                pointers[i].append(pointer)
    return pointers


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


def _unit(vector: Vector) -> Vector:
    """The Vector scaled to length 1, so that cosines are dot products; zero stays zero."""
    norm = math.sqrt(math.sumprod(vector, vector))
    return [x / norm for x in vector] if norm else vector
