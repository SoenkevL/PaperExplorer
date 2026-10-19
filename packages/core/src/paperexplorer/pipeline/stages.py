"""The Seed's first Stages: fetch its Library Item, parse its PDF, link its Citations."""

import logging
from collections import Counter

from paperexplorer import citations, parsing
from paperexplorer.citations import ConfidenceLevel, confidence_level
from paperexplorer.domain import WorkId
from paperexplorer.library import ItemNotFoundError
from paperexplorer.pipeline.stage import StageContext, StageError, StageResult
from paperexplorer.store import CitationGraphs, Works, WorkTexts

logger = logging.getLogger(__name__)


def fetch(ctx: StageContext, work: WorkId) -> StageResult:
    """Record the Work's Library Item and make sure its PDF is on this machine."""
    works = Works(ctx.conn)
    stored = works.get(work)
    if stored is None or stored.zotero_key is None:
        raise StageError(f"Work {work} has no Library Item to fetch")
    try:
        item = ctx.library.item(stored.zotero_key)
    except ItemNotFoundError as e:
        raise StageError(str(e)) from e
    works.record_library_item(work, item)
    if item.pdf is None:
        raise StageError(f"{item.key} has no PDF stored in Zotero; a Seed needs its full text")
    ctx.library.pdf(item.pdf)
    return StageResult(counts={"pdfs": 1})


def parse(ctx: StageContext, work: WorkId) -> StageResult:
    """Parse the Work's PDF into blocks and References, replacing any earlier parse."""
    stored = Works(ctx.conn).get(work)
    if stored is None or stored.pdf is None:
        raise StageError(f"Work {work} has no PDF yet; run fetch first")
    parsed = parsing.parse(ctx.library.pdf(stored.pdf), ctx.settings)
    WorkTexts(ctx.conn).replace(work, parsed)
    return StageResult(
        counts={
            "blocks": len(parsed.blocks),
            "references": len(parsed.references),
            "citation_markers": sum(len(b.citation_markers) for b in parsed.blocks),
        }
    )


def extract_citations(ctx: StageContext, work: WorkId) -> StageResult:
    """Link every Citation of the parsed Work to its Reference, replacing earlier links."""
    parsed = WorkTexts(ctx.conn).get(work)
    if parsed is None:
        raise StageError(f"Work {work} has no text yet; run parse first")
    graph = citations.extract(parsed)
    CitationGraphs(ctx.conn).replace(work, graph)
    levels = Counter(confidence_level(c.link) for c in graph.citations)
    for c in graph.unlinked() + graph.low_confidence():
        logger.debug("Weak Citation %r in block %d: %s", c.text, c.block, c.link)
    return StageResult(
        counts={"citations": len(graph.citations)}
        | {lv.value: levels[lv] for lv in ConfidenceLevel}
    )
