"""The Seed's Stages: fetch, parse, chunk and embed its text, link its Citations, resolve."""

import logging
from collections import Counter

from paperexplorer import chunking, citations, parsing, resolution
from paperexplorer.citations import ConfidenceLevel, confidence_level
from paperexplorer.domain import ChunkFlag, WorkId
from paperexplorer.library import ItemNotFoundError, sync
from paperexplorer.pipeline.stage import StageContext, StageError, StageResult
from paperexplorer.store import Chunks, CitationGraphs, Resolutions, Works, WorkTexts

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


def chunk(ctx: StageContext, work: WorkId) -> StageResult:
    """Cut the parsed Work's paragraphs and footnotes into Chunks, replacing earlier ones."""
    parsed = WorkTexts(ctx.conn).get(work)
    if parsed is None:
        raise StageError(f"Work {work} has no text yet; run parse first")
    chunks = chunking.chunk(parsed)
    Chunks(ctx.conn).replace(work, chunks)
    flags = Counter(f for c in chunks for f in c.flags)
    return StageResult(counts={"chunks": len(chunks)} | {f.value: flags[f] for f in ChunkFlag})


def embed(ctx: StageContext, work: WorkId) -> StageResult:
    """Embed the Work's Chunks that have no Vector from the Embedder's model yet."""
    chunks = Chunks(ctx.conn)
    total = len(chunks.for_work(work))
    if total == 0:
        raise StageError(f"Work {work} has no Chunks yet; run chunk first")
    model = ctx.embedder.model
    missing = chunks.without_embedding(work, model)
    if not missing:
        return StageResult(counts={"chunks": total, "embedded": 0})
    stored = Works(ctx.conn).get(work)
    key = stored.zotero_key if stored is not None else None
    tags = ["pipeline"] + ([f"work:{key}"] if key else [])
    with ctx.tracing.trace(
        "stage.embed", tags=tags, metadata={"work_id": str(work), "model": model}
    ):
        vectors = ctx.embedder.embed([s.chunk.text for s in missing])
    chunks.record_embeddings(work, model, {s.id: v for s, v in zip(missing, vectors, strict=True)})
    return StageResult(counts={"chunks": total, "embedded": len(missing)})


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


def resolve(ctx: StageContext, work: WorkId) -> StageResult:
    """Match every Reference of the parsed Work to a Work, from the Library synced just before.

    No registry is asked (ADR 0011): what nothing local matches is an Unchecked Reference.
    """
    parsed = WorkTexts(ctx.conn).get(work)
    if parsed is None:
        raise StageError(f"Work {work} has no text yet; run parse first")
    sync(ctx.conn, ctx.library)
    found = resolution.resolve(ctx.conn, work, parsed.references)
    Resolutions(ctx.conn).replace(work, found)
    return StageResult(counts=resolution.counts(found))
