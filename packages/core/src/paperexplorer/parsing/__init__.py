"""Turn a PDF into a ParsedWork: GROBID for structure, the PDF text layer for wording (ADR 0006)."""

import logging
from pathlib import Path

from paperexplorer.config import Settings
from paperexplorer.parsing.assemble import assemble
from paperexplorer.parsing.grobid import fetch_tei, read_tei
from paperexplorer.parsing.models import (
    Block,
    BlockKind,
    Box,
    CitationMarker,
    LinkTarget,
    ParsedWork,
    Reference,
    Span,
    TextOrigin,
)
from paperexplorer.parsing.text_layer import TextLayer

__all__ = [
    "Block",
    "BlockKind",
    "Box",
    "CitationMarker",
    "LinkTarget",
    "ParsedWork",
    "Reference",
    "Span",
    "TextOrigin",
    "parse",
]

logger = logging.getLogger(__name__)


def parse(pdf: Path, settings: Settings) -> ParsedWork:
    """Blocks, References and Citation markers of `pdf`, with the text exactly as printed."""
    tei = read_tei(fetch_tei(pdf, settings))
    with TextLayer(pdf) as layer:
        return assemble(tei, layer)
