"""Turn a PDF into a ParsedWork: GROBID for structure, the PDF text layer for wording (ADR 0006)."""

import logging
from pathlib import Path

from paperexplorer.config import Settings
from paperexplorer.parsing.assemble import assemble
from paperexplorer.parsing.grobid import NoTextLayerError, fetch_tei, read_tei
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
from paperexplorer.parsing.ocr import read_scan
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
    """Blocks, References and Citation markers of `pdf`, with the text exactly as printed.

    A scan has no text to print from: it is read by OCR and marked as OCR Text.
    """
    logger.info("Parsing %s", pdf.name)
    with TextLayer(pdf) as layer:
        if not layer.is_scan():
            try:
                work = assemble(read_tei(fetch_tei(pdf, settings)), layer)
            except NoTextLayerError:
                work = None
            if work is not None:
                logger.info(
                    "Parsed %s: %d blocks, %d References, %d Citation markers",
                    pdf.name,
                    len(work.blocks),
                    len(work.references),
                    sum(len(b.citation_markers) for b in work.blocks),
                )
                return work
    logger.warning("%s has no text layer; reading it by OCR, its text is OCR Text", pdf.name)
    return read_scan(pdf, settings)
