"""Request and response models: the API contract every frontend builds on.

Text is the authors' text exactly as extracted, except an Answer's, which a model wrote.
Character offsets count Unicode code points (Python `str` indices), not UTF-16 units:
`text[start:end]` in Python, not JavaScript.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, Field, JsonValue, StringConstraints, model_validator

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


class WorkBrief(BaseModel):
    """A Work a Resolution names: its Library Item, or a registry's record until it has one."""

    id: int
    zotero_key: str | None
    title: str | None
    creators: list[str]
    year: int | None
    doi: str | None


class Resolution(BaseModel):
    """What a Reference resolved to: a Work at a level, or Unchecked, or Unresolved.

    `layer` is what matched it (`reader` for a Reader's Choice, which `chosen` also marks);
    `doi_conflict` is the Work a disagreeing printed DOI names.
    """

    reference: str
    state: Literal["resolved", "unchecked", "unresolved"]
    work: WorkBrief | None
    level: Literal["certain", "high", "low"] | None
    confidence: float | None
    evidence: list[str]
    layer: Literal["library", "crossref", "openalex", "reader"] | None
    doi_conflict: WorkBrief | None
    chosen: bool


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
    doi_registration: Literal["registered", "unregistered"] | None = Field(
        default=None, description="What doi.org said of the DOI; None until verify_dois asked."
    )
    resolution: Resolution | None = Field(
        default=None, description="What it resolved to; None until resolve ran."
    )


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


class TextPiece(BaseModel):
    """A run of the Draft's text."""

    text: str


class HandlePiece(BaseModel):
    """One handle of the Draft, by its number; its Pointer comes with the Answer."""

    handle: int


type DraftPiece = TextPiece | HandlePiece
"""A piece of the Draft: `{"text": ...}` or `{"handle": n}`, one per number a handle carries."""


class DeltaEvent(BaseModel):
    """More of the Draft, cut into text and handles; the Answer replaces the Draft.

    Text that may still turn into a handle or a control token comes with a later delta; a
    handle the Conversation does not know is left out.
    """

    type: Literal["delta"] = "delta"
    pieces: list[DraftPiece]


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


class CheckCost(BaseModel):
    """What a Registry Check of a Work would ask, at most, and what is left of OpenAlex's day.

    Title and bibliographic searches are upper bounds. `budget_usd` is None when OpenAlex did
    not say.
    """

    unchecked: int
    crossref_batches: int
    openalex_batches: int
    title_searches: int
    bibliographic_searches: int
    openalex_usd: float
    budget_usd: float | None


class Check(BaseModel):
    """How to check: `refresh` asks the registries again, past the cache."""

    refresh: bool = False


class ReferenceCheckedEvent(BaseModel):
    """One Reference's Resolution, saved as the Check lands it."""

    type: Literal["reference"] = "reference"
    resolution: Resolution


class CheckFinishedEvent(BaseModel):
    """The Check went through every layer: its References by level and state; the last event."""

    type: Literal["finished"] = "finished"
    counts: dict[str, int]


class BudgetSpentEvent(BaseModel):
    """OpenAlex's day is spent: the Check stopped, all it landed kept; the last event."""

    type: Literal["budget_spent"] = "budget_spent"
    left: int
    message: str


class CheckErrorEvent(BaseModel):
    """A registry kept failing: the Check stopped, all it landed kept; the last event."""

    type: Literal["error"] = "error"
    message: str


type CheckEvent = Annotated[
    ReferenceCheckedEvent | CheckFinishedEvent | BudgetSpentEvent | CheckErrorEvent,
    Field(discriminator="type"),
]
"""One event of a Check's stream: the SSE event named by its `type`, the event as its data."""


class CheckedReference(BaseModel):
    """One Reference checked: its Resolution, or why the Check stopped before it."""

    resolution: Resolution | None
    stopped: str | None


class ReadersChoice(BaseModel):
    """The Work the reader picks for a Reference: by id, or by a Zotero key or DOI in `key`.

    Neither given is a choice of none: the Reference names no Work.
    """

    work_id: int | None = None
    key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None = None

    @model_validator(mode="after")
    def _one_way_to_name_it(self) -> Self:
        if self.work_id is not None and self.key is not None:
            raise ValueError("name the Work by work_id or by key, not both")
        return self


class DuplicateItem(WorkBrief):
    """One of a Work's Duplicate Items."""

    pdf: bool
    added: str | None  # its dateAdded, as an ISO date


class DuplicateItems(BaseModel):
    """Library Items holding one DOI; the first is the Standing Item."""

    doi: str
    items: list[DuplicateItem]


class Merge(BaseModel):
    """Merge `other` into `keep` in Zotero, as Zotero desktop does; `other` goes to the trash."""

    keep: str
    other: str


class Merged(BaseModel):
    """A merge done: how many Resolutions moved to the kept item."""

    moved: int
