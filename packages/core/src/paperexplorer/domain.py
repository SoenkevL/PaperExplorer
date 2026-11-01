"""Domain records shared across modules: Works, Library Items, Resolutions, Chunks, Answers."""

from datetime import datetime
from enum import StrEnum
from typing import NewType, Self

from pydantic import BaseModel, ConfigDict, computed_field, model_validator

from paperexplorer.parsing.models import Box, Span, TextOrigin

WorkId = NewType("WorkId", int)
CitationId = NewType("CitationId", int)
ChunkId = NewType("ChunkId", int)

type Vector = list[float]
"""An embedding of one text, as an `Embedder` returns it."""


class Attachment(BaseModel):
    """A PDF attached to a Library Item: its own Zotero key and file name."""

    model_config = ConfigDict(frozen=True)

    key: str
    filename: str


class LibraryItem(BaseModel):
    """The Zotero record of a Work: its metadata and, if any, its attached PDF."""

    key: str
    title: str | None = None
    creators: list[str] = []
    year: int | None = None
    doi: str | None = None
    pdf: Attachment | None = None
    added: datetime | None = None  # when it entered the Library; the older of two stands


class LibraryChanges(BaseModel):
    """What changed in the Library since a version: items as they are now, keys that went.

    A full read (`full`) lists every Library Item, so any key it leaves out has gone too.
    """

    version: int
    items: list[LibraryItem]
    deleted: list[str] = []
    full: bool = False


class Work(BaseModel):
    """A Work as PaperExplorer holds it: its Library Item's snapshot and how its text was read."""

    id: WorkId
    zotero_key: str | None
    title: str | None
    creators: list[str]
    year: int | None
    doi: str | None
    pdf: Attachment | None
    synced_at: datetime | None
    text_origin: TextOrigin | None
    added: datetime | None = None  # its Library Item's dateAdded


class Level(StrEnum):
    """How firmly a Resolution ties a Reference to its Work."""

    CERTAIN = "certain"  # the printed DOI, agreeing with the Reference
    HIGH = "high"
    LOW = "low"  # a near title, or a title that won a DOI Conflict


class ResolutionState(StrEnum):
    """Where a Reference stands: matched to a Work, not yet looked up, or looked up in vain."""

    RESOLVED = "resolved"
    UNCHECKED = "unchecked"  # an Unchecked Reference
    UNRESOLVED = "unresolved"  # an Unresolved Reference: only a Registry Check makes one


class Layer(StrEnum):
    """What matched a Reference to its Work."""

    LIBRARY = "library"


class Resolution(BaseModel):
    """A Reference of a Work, by its key, matched to a Work or not, and how firmly.

    `confidence` is 1 for a DOI that agrees, else the titles' similarity. `doi_conflict` is the
    Work the printed DOI names when it disagreed with the Reference (a DOI Conflict).
    """

    reference: str
    state: ResolutionState
    work: WorkId | None = None
    level: Level | None = None
    confidence: float | None = None
    evidence: list[str] = []
    layer: Layer | None = None
    doi_conflict: WorkId | None = None

    @model_validator(mode="after")
    def _resolved_has_its_work(self) -> Self:
        resolved = self.state is ResolutionState.RESOLVED
        if resolved != (self.work is not None and self.level is not None):
            raise ValueError("a resolved Reference needs its Work and level; no other has one")
        return self


class ChunkFlag(StrEnum):
    """What sets a Chunk apart from the rest."""

    SHORT = "short"  # under 30 tokens: too little to say much on its own
    OVERSIZED_SENTENCE = "oversized_sentence"  # cut at a token from one sentence over the limit


class Chunk(BaseModel):
    """A contiguous span of one paragraph or footnote, as `chunking` cuts it for retrieval.

    `span` and each of `sentences` are `[start, end)` code-point offsets into the text of block
    `block`, so a sentence is already a place in the reader. `position` is the Chunk's place in
    its Work's reading order; `tokens` counts bge-m3 tokens. Page and boxes are the block's.
    """

    position: int
    block: int
    span: Span
    text: str
    sentences: list[Span]
    heading_path: list[str]
    page: int
    boxes: list[Box]
    tokens: int
    flags: list[ChunkFlag] = []
    text_origin: TextOrigin

    def sentence_texts(self) -> list[str]:
        """Each sentence's text, as printed."""
        return [
            self.text[s.start - self.span.start : s.end - self.span.start] for s in self.sentences
        ]


class Scope(BaseModel):
    """The set of Works a search runs over; for now always one Work, the Seed."""

    model_config = ConfigDict(frozen=True)

    works: frozenset[WorkId]

    @classmethod
    def of(cls, work: WorkId) -> Scope:
        """The Scope of one Work alone."""
        return cls(works=frozenset({work}))


class Support(StrEnum):
    """How well a Pointer's place bears out its sentence."""

    CLOSE = "close"
    WEAK = "weak"


class Pointer(BaseModel):
    """Ties one sentence of an Answer to the sentence of a Chunk the model read that bears it out.

    The place is `[start, end)` in code points of block `block_id`'s text: one of the sentences
    of Chunk `chunk_id`, which spans `[chunk_start, chunk_end)` of that block. `similarity` is the
    cosine of the two sentences' Vectors; Support is close when it clears the Support threshold.
    """

    model_config = ConfigDict(frozen=True)

    block_id: int
    start: int
    end: int
    support: Support
    chunk_id: ChunkId
    chunk_start: int
    chunk_end: int
    similarity: float


class AnswerSentence(BaseModel):
    """One sentence of an Answer, `[start, end)` in code points of its text, with its Pointers.

    A sentence the model tied to no Chunk has none. `substantive` sentences count towards Pointer
    coverage.
    """

    model_config = ConfigDict(frozen=True)

    start: int
    end: int
    substantive: bool
    pointers: list[Pointer] = []


class Quote(BaseModel):
    """Words an Answer puts in quotation marks: `[start, end)` in code points of its text.

    A Quote is exact only where its words match the text of a Chunk the model read in that turn;
    `block_id`, `block_start` and `block_end` are then that place, in code points of the block's
    text as printed. `exact` follows from the place, so no Quote is exact without one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: int
    end: int
    block_id: int | None = None
    block_start: int | None = None
    block_end: int | None = None

    @computed_field
    @property
    def exact(self) -> bool:
        """Whether the words match the authors' text at the Quote's place."""
        return self.block_id is not None

    @model_validator(mode="after")
    def _place_is_whole_or_absent(self) -> Self:
        place = (self.block_id, self.block_start, self.block_end)
        if any(p is None for p in place) and any(p is not None for p in place):
            raise ValueError("a Quote's place needs block_id, block_start and block_end")
        return self


class AnswerCounts(BaseModel):
    """What an Answer's Pointers and Quotes add up to: the scores on its `question` trace.

    `pointer_coverage` is the share of substantive sentences with a Pointer, None when there is
    no substantive sentence.
    """

    sentences: int
    substantive_sentences: int
    pointer_coverage: float | None
    pointers_close: int
    pointers_weak: int
    quotes_exact: int
    quotes_not_exact: int
    unknown_handles: int


class Answer(BaseModel):
    """A model's reply to a Question, resolved: the text the reader sees, its sentences, Quotes.

    `text` is the model's draft without its handles; every offset counts code points of it.
    `unknown_handles` are the handles the model wrote that tie to no Chunk; they were dropped.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    sentences: list[AnswerSentence]
    quotes: list[Quote]
    unknown_handles: list[int] = []

    def counts(self) -> AnswerCounts:
        """Pointer coverage, Pointers per Support and exact and not-exact Quotes."""
        substantive = [s for s in self.sentences if s.substantive]
        pointers = [p for s in self.sentences for p in s.pointers]
        exact = sum(q.exact for q in self.quotes)
        return AnswerCounts(
            sentences=len(self.sentences),
            substantive_sentences=len(substantive),
            pointer_coverage=(
                sum(bool(s.pointers) for s in substantive) / len(substantive)
                if substantive
                else None
            ),
            pointers_close=sum(p.support is Support.CLOSE for p in pointers),
            pointers_weak=sum(p.support is Support.WEAK for p in pointers),
            quotes_exact=exact,
            quotes_not_exact=len(self.quotes) - exact,
            unknown_handles=len(self.unknown_handles),
        )
