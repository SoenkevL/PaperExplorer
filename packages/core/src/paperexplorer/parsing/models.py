"""What `parse` returns: a Work's blocks, References and Citation markers, with page positions."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class TextOrigin(StrEnum):
    """Where a Work's text came from: the PDF's own text layer, or OCR of a scan."""

    PRINTED = "printed"
    OCR = "ocr"


class Box(BaseModel):
    """A rectangle on a page, in PDF points with the origin at the page's top-left corner."""

    model_config = ConfigDict(frozen=True)

    page: int
    x: float
    y: float
    width: float
    height: float


class Span(BaseModel):
    """Character offsets `[start, end)` into a block's text."""

    model_config = ConfigDict(frozen=True)

    start: int
    end: int


class BlockKind(StrEnum):
    """What a block of body text is."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    FOOTNOTE = "footnote"


class LinkTarget(BaseModel):
    """Where the PDF's own link on a Citation marker points: a Reference, or only a page."""

    model_config = ConfigDict(frozen=True)

    page: int
    reference_id: str | None = None


class CitationMarker(BaseModel):
    """A Citation marker as printed, with the evidence for the Reference it points at."""

    span: Span
    text: str
    grobid_text: str | None = None
    grobid_reference_id: str | None = None
    link_target: LinkTarget | None = None


class Block(BaseModel):
    """A heading, paragraph or footnote in reading order, with the authors' text as printed."""

    kind: BlockKind
    heading_path: list[str]
    page: int
    boxes: list[Box]
    text: str
    sentences: list[Span] = []
    citation_markers: list[CitationMarker] = []
    ocr_confidence: float | None = None


class Reference(BaseModel):
    """One entry of the Work's bibliography: its printed text plus the fields read from it."""

    id: str
    label: str | None = None
    text: str
    page: int
    boxes: list[Box] = []
    surnames: list[str] = []
    year: str | None = None
    title: str | None = None
    doi: str | None = None


class ParsedWork(BaseModel):
    """A parsed PDF: body blocks in reading order and the bibliography."""

    text_origin: TextOrigin
    blocks: list[Block]
    references: list[Reference]
    parser_versions: dict[str, str] = {}
