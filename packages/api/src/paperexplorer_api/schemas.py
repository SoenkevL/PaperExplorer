"""Request and response models: the API contract every frontend builds on.

Text is the authors' text exactly as extracted, except an Answer's, which a model wrote.
Character offsets count Unicode code points (Python `str` indices), not UTF-16 units:
`text[start:end]` in Python, not JavaScript.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue, StringConstraints

ConfidenceLevel = Literal["certain", "high", "low", "unlinked"]
StageState = Literal["pending", "running", "done", "failed"]
TextOrigin = Literal["printed", "ocr"]
Support = Literal["close", "weak"]


class Health(BaseModel):
    """Liveness of the API process."""

    status: Literal["ok"] = "ok"
    version: str


class Box(BaseModel):
    """A rectangle on a page, in PDF points from the page's top-left corner."""

    page: int
    x: float
    y: float
    width: float
    height: float


class CitationSpan(BaseModel):
    """Where a Citation sits in its block's text: `text[start:end]`; spans may overlap."""

    id: int
    start: int
    end: int
    confidence_level: ConfidenceLevel


class Block(BaseModel):
    """A heading, paragraph or footnote, in reading order."""

    position: int
    kind: Literal["heading", "paragraph", "footnote"]
    heading_path: list[str]
    page: int
    boxes: list[Box]
    text: str
    ocr_confidence: float | None
    citations: list[CitationSpan]


class WorkSummary(BaseModel):
    """A Work's Library Item snapshot and where each Stage stands for it."""

    id: int
    zotero_key: str | None
    title: str | None
    creators: list[str]
    year: int | None
    doi: str | None
    text_origin: TextOrigin | None
    stages: dict[str, StageState]


class WorkText(WorkSummary):
    """A Work with its text: blocks in reading order, each with its Citation spans."""

    blocks: list[Block]


class Reference(BaseModel):
    """A bibliography entry: its printed text, plus the fields the parser read from it."""

    key: str
    label: str | None
    text: str
    page: int
    surnames: list[str]
    year: str | None
    title: str | None
    doi: str | None


class CitationLink(BaseModel):
    """How firmly a Citation is tied to its Reference, and the evidence for it."""

    confidence: float
    evidence: list[Literal["pdf_link", "grobid", "label", "author_year"]]
    confidence_level: ConfidenceLevel


class CitationDetail(BaseModel):
    """One Citation: its marker, its Citing Sentence, and the Reference it points at, if any."""

    id: int
    work_id: int
    block: int
    text: str
    citing_sentence: str
    text_origin: TextOrigin
    link: CitationLink | None
    reference: Reference | None


class Conversation(BaseModel):
    """A Conversation the API holds in memory; its Questions go to `/conversations/{id}/questions`.

    A restart, or eviction once it is old, forgets it: asking it then answers 404.
    """

    id: str


class Question(BaseModel):
    """What the reader asks in a Conversation."""

    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Pointer(BaseModel):
    """Ties an Answer sentence to the sentence `text[start:end]` of block `block_id` bearing it out.

    That place lies in a Chunk spanning `text[chunk_start:chunk_end]` of the same block, which the
    reader highlights faintly around the place.
    """

    block_id: int
    start: int
    end: int
    support: Support
    chunk_start: int
    chunk_end: int


class AnswerSentence(BaseModel):
    """One sentence of an Answer, `text[start:end]` of the Answer, with its Pointers, if any."""

    start: int
    end: int
    pointers: list[Pointer]


class Quote(BaseModel):
    """Words an Answer puts in quotation marks, `text[start:end]` of the Answer.

    Exact only where they match the authors' text: then `block_start:block_end` of block
    `block_id` is that place, and all three are null for a Quote that is not exact.
    """

    start: int
    end: int
    exact: bool
    block_id: int | None
    block_start: int | None
    block_end: int | None


class Answer(BaseModel):
    """A model's reply to a Question: the text the reader sees, its sentences and its Quotes.

    `text` is the model's draft without its handles; every offset of the Answer counts into it.
    """

    text: str
    sentences: list[AnswerSentence]
    quotes: list[Quote]


class StepEvent(BaseModel):
    """The model runs a tool, with its arguments; the forced first search comes first."""

    type: Literal["step"] = "step"
    tool: str
    args: dict[str, JsonValue]


class DeltaEvent(BaseModel):
    """More of the model's draft, handles and all; the Answer replaces the draft."""

    type: Literal["delta"] = "delta"
    text: str


class RedraftEvent(BaseModel):
    """The draft gave no handle, so the model writes its Answer again with them.

    The draft so far is dropped; the deltas that follow are the new draft's.
    """

    type: Literal["redraft"] = "redraft"


class AnsweredEvent(BaseModel):
    """The resolved Answer, with its Pointers and Quotes; the turn's last event."""

    type: Literal["answer"] = "answer"
    answer: Answer


class ErrorEvent(BaseModel):
    """The turn failed and ends here; the Conversation stays as it was before the Question."""

    type: Literal["error"] = "error"
    message: str


type AnswerEvent = Annotated[
    StepEvent | DeltaEvent | RedraftEvent | AnsweredEvent | ErrorEvent, Field(discriminator="type")
]
"""One event of a Question's stream: the SSE event named by its `type`, the event as its data."""
