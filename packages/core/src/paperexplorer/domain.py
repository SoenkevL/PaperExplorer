"""Domain records shared across modules: Works, Library Items, Chunks, Scopes and store ids."""

from datetime import datetime
from enum import StrEnum
from typing import NewType

from pydantic import BaseModel, ConfigDict

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
