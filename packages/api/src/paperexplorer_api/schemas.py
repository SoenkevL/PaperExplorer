"""Response models: the API contract every frontend builds on.

Text is the authors' text exactly as extracted. Character offsets count Unicode code points
(Python `str` indices), not UTF-16 units: `text[start:end]` in Python, not JavaScript.
"""

from typing import Literal

from pydantic import BaseModel

ConfidenceLevel = Literal["certain", "high", "low", "unlinked"]
StageState = Literal["pending", "running", "done", "failed"]
TextOrigin = Literal["printed", "ocr"]


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
