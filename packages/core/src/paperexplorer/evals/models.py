"""What the eval reads and records: its Questions with their places, and one turn of asking."""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from paperexplorer.asking import Reading
from paperexplorer.domain import Answer, Chunk, ChunkId, Support


class Kind(StrEnum):
    """What a Question asks of the Seed (design-asking.md, Eval)."""

    MEASUREMENT = "measurement"
    METHOD = "method"
    SHORTCOMING = "shortcoming"
    FOLLOW_UP = "follow-up"
    UNCOVERED = "uncovered"  # the Seed does not answer it


class Place(BaseModel):
    """Where in the Seed a Chunk sits: its Heading Path and page, as `chunk` gives them."""

    model_config = ConfigDict(frozen=True)

    heading_path: tuple[str, ...]
    page: int

    @classmethod
    def of(cls, chunk: Chunk) -> Self:
        """The place of a Chunk."""
        return cls(heading_path=tuple(chunk.heading_path), page=chunk.page)

    def __hash__(self) -> int:
        return hash((self.heading_path, self.page))

    def __str__(self) -> str:
        return f"p{self.page} {' > '.join(self.heading_path) or '(no heading)'}"


class QuestionMeta(BaseModel):
    """A Question's kind and the places a good Answer points at; an uncovered one lists none."""

    kind: Kind
    places: list[Place] = []

    @property
    def covered(self) -> bool:
        """Whether the Seed answers the Question."""
        return self.kind is not Kind.UNCOVERED

    @model_validator(mode="after")
    def _places_only_when_covered(self) -> Self:
        if self.covered and not self.places:
            raise ValueError("a Question the Seed answers lists the places a good Answer points at")
        if not self.covered and self.places:
            raise ValueError("an uncovered Question lists no places")
        return self


class Mode(StrEnum):
    """How the Question is asked: as the reader asks it (live), or by retrieval, forced.

    `baseline`, the whole Seed forced, is the mode of runs before ADR 0010: their committed
    summaries still read, and in them `retrieval` was the live agent.
    """

    LIVE = "live"  # as the API asks: the whole Seed when it fits, else retrieval
    RETRIEVAL = "retrieval"  # the forced search, then the tools: the fallback for larger Seeds
    BASELINE = "baseline"

    @property
    def reading(self) -> Reading:
        """The reading a turn in this mode is asked with."""
        return _READINGS[self]

    @classmethod
    def of(cls, reading: Reading) -> Mode:
        """The mode whose turns are asked with `reading`."""
        return next(mode for mode, r in _READINGS.items() if r is reading)


_READINGS = {
    Mode.LIVE: Reading.AUTO,
    Mode.RETRIEVAL: Reading.RETRIEVAL,
    Mode.BASELINE: Reading.WHOLE_SEED,
}


class Hit(BaseModel):
    """One Chunk the forced search found: its rank, place and RRF."""

    rank: int
    chunk_id: ChunkId
    place: Place
    rrf: float


class PointerCheck(BaseModel):
    """One Pointer of an Answer: its sentence (index), Support, cosine and where it landed.

    `matched` is the Chunk sentence at its place, the Seed's words: only in the full report.
    """

    sentence: int
    support: Support
    similarity: float
    chunk_id: ChunkId
    place: Place
    matched: str


class QuoteCheck(BaseModel):
    """One Quote of an Answer: its words, whether it is exact, where, and whether that holds.

    `verified` (exact Quotes only) says the words are the authors' text at the Quote's place,
    folded as Quote matching folds them (ADR 0008): the check of "no Quote marked exact that is
    not". `words` are the model's: only in the full report.
    """

    words: str
    exact: bool
    place: Place | None
    verified: bool | None


class Turn(BaseModel):
    """One Question asked of the Seed in one mode, in a fresh Conversation: all it produced.

    `repeat` counts the times the Question was asked in this mode, from 1 (runs before repeats
    asked once). `reading` is how the turn read the Seed (None when it failed before it chose;
    runs before ADR 0010 did not record it). `search` is the forced search
    (`retrieval.search(question, k=8)`) of a turn that read by retrieval, else None.
    `redrafted` says the Answer is the model's draft written again with handles.
    `model_requests` is None when the turn failed; `tools` are the model's own calls in order.
    """

    mode: Mode
    repeat: int = 1
    reading: Reading | None = None
    question: str
    conversation_id: str
    trace_id: str | None
    answer: Answer | None
    error: str | None
    search: list[Hit] | None
    pointers: list[PointerCheck]
    quotes: list[QuoteCheck]
    says_not_covered: bool
    redrafted: bool = False
    model_requests: int | None
    tool_calls: int
    tools: list[str]
    latency_s: float
    first_delta_s: float | None
