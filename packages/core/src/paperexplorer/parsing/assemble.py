"""GROBID's structure filled with the PDF's own text: a ParsedWork whose text is as printed."""

import itertools
import re
import statistics
from collections.abc import Callable

from paperexplorer.parsing.grobid import TeiBlock, TeiDocument, TeiReference
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
from paperexplorer.parsing.text_layer import InternalLink, Reading, TextLayer

_LABEL = re.compile(r"^\[?(\d{1,4})[\].]?$")
# A numbered marker: bracketed or superscript numbers of up to 3 digits (4 digits are years).
_NUMBERED = re.compile(r"^\W*(?:refs?\.?\s*)?\d{1,3}(?:\s*[,\u2013\-]\s*\d{1,3})*\W*$")
_NUMBER = re.compile(r"\b\d{1,3}\b")
_YEAR = re.compile(r"\b(?:1[5-9]|20)\d\d[a-z]?\b")
_DOI = re.compile(r"10\.\d{4,9}/\S+")
# A label, or what is left of one, at the start of an entry's box (`[12] `, `12. `, `. `).
_LEADING_LABEL = re.compile(r"^(?:\[?(\d{1,4})[\].]|[.\]])\s+")
_SURNAME = re.compile(r"^(?:\[?\d{1,4}[\].]?\s+)?([^,(]+?)\s*[,(]")
_LABEL_WIDTH = 45.0  # points left of an entry's text where its printed label can sit
_SAME_SIZE = 0.3  # points: printed heading sizes this close are one heading level


def assemble(tei: TeiDocument, layer: TextLayer) -> ParsedWork:
    """Read every GROBID block, marker and Reference from the text layer; add the PDF's links."""
    references = [r for tr in tei.references for r in _references(tr, layer) if r.text]
    blocks: list[Block] = []
    headings: list[tuple[str | None, float] | None] = []
    for tb in tei.blocks:
        block = _block(tb, layer, references)
        if block is None:
            continue
        blocks.append(block)
        is_heading = block.kind is BlockKind.HEADING
        headings.append((tb.number, _printed_size(layer.read(tb.boxes))) if is_heading else None)
    _nest(blocks, headings)
    return ParsedWork(text_origin=TextOrigin.PRINTED, blocks=blocks, references=references)


def _nest(blocks: list[Block], headings: list[tuple[str | None, float] | None]) -> None:
    """Give every block its Heading Path: numbered headings nest by number, others by size."""
    level_of = _size_levels([h[1] for h in headings if h is not None and not h[0]])
    stack: list[tuple[int, str]] = []
    for block, heading in zip(blocks, headings, strict=True):
        if heading is None:
            block.heading_path = [text for _, text in stack]
            continue
        number, size = heading
        digits = re.findall(r"\d+", number or "")
        level = len(digits) if digits else level_of(size)
        while stack and stack[-1][0] >= level:
            stack.pop()
        block.heading_path = [text for _, text in stack]
        stack.append((level, block.text))


def _size_levels(sizes: list[float]) -> Callable[[float], int]:
    """Map a printed heading size to its level: the largest size is level 1."""
    tops: list[float] = []
    for size in sorted(set(sizes), reverse=True):
        if not tops or size < tops[-1] - _SAME_SIZE:
            tops.append(size)
    return lambda size: next(
        (i + 1 for i, top in enumerate(tops) if size >= top - _SAME_SIZE), len(tops) or 1
    )


def _printed_size(reading: Reading) -> float:
    heights = [g.height for g in reading.sources if g is not None]
    return statistics.median(heights) if heights else 0.0


def _block(tb: TeiBlock, layer: TextLayer, references: list[Reference]) -> Block | None:
    reading = layer.read(tb.boxes)
    if not reading.text:
        return None
    positions = {(g.page, g.index): i for i, g in enumerate(reading.sources) if g is not None}
    labels = {r.id: r.label for r in references}

    linked: list[CitationMarker] = []
    page_only: list[tuple[Span, LinkTarget]] = []
    for link in layer.links:
        if not any(_overlaps(link.source, box) for box in tb.boxes):
            continue
        span = _span(layer.read([link.source]), positions)
        if span is None:
            continue
        if link.target_y is None:
            page_only.append((span, LinkTarget(page=link.target_page)))
            continue
        target = _reference_at(link, references, layer.page_sizes)
        if target is not None:  # links to figures, sections or equations are not Citations
            linked.append(
                CitationMarker(
                    span=span,
                    text=reading.text[span.start : span.end],
                    link_target=LinkTarget(page=link.target_page, reference_id=target.id),
                )
            )

    markers = list(linked)
    for sentence in tb.sentences:
        for ref in sentence.refs:
            span = _span(layer.read(ref.boxes), positions)
            if span is None:
                continue
            numbers = _NUMBER.findall(ref.text) if _NUMBERED.match(ref.text) else []
            overlapping = [m for m in linked if _spans_overlap(m.span, span)]
            if numbers:
                hits = [
                    m
                    for m in overlapping
                    if m.link_target is not None
                    and labels.get(m.link_target.reference_id or "") in numbers
                ]
            else:
                hits = sorted(overlapping, key=lambda m, s=span: -_overlap(m.span, s))[:1]
            if hits and (not numbers or len(hits) >= len(numbers)):
                for m in hits:  # the PDF link already marks this Citation; add GROBID's view
                    m.cue = ref.text
                    m.grobid_reference_id = ref.target
                    # Author-year links cover only the year: show GROBID's wider span when it
                    # holds the link's and starts at a word boundary.
                    if (
                        not numbers
                        and span.start <= m.span.start
                        and m.span.end <= span.end
                        and (span.start == 0 or not reading.text[span.start - 1].isalnum())
                    ):
                        m.span = span
                        m.text = reading.text[span.start : span.end]
                continue
            target = next((t for s, t in page_only if _spans_overlap(s, span)), None)
            markers.append(
                CitationMarker(
                    span=span,
                    text=reading.text[span.start : span.end],
                    cue=ref.text,
                    grobid_reference_id=ref.target,
                    link_target=target,
                )
            )
    markers.sort(key=lambda m: m.span.start)
    sentences = [
        s for ts in tb.sentences if (s := _span(layer.read(ts.boxes), positions)) is not None
    ]
    return Block(
        kind=tb.kind,
        heading_path=[],
        page=tb.boxes[0].page,
        boxes=tb.boxes,
        text=reading.text,
        sentences=sentences,
        citation_markers=markers,
    )


def _span(read: Reading, positions: dict[tuple[int, int], int]) -> Span | None:
    """Where the glyphs of `read` sit in a block's text, as one span."""
    found = sorted(
        positions[(g.page, g.index)]
        for g in read.sources
        if g is not None and (g.page, g.index) in positions
    )
    if not found:
        return None
    return Span(start=found[0], end=found[-1] + 1)


def _references(tr: TeiReference, layer: TextLayer) -> list[Reference]:
    """One Reference per printed entry; GROBID sometimes merges two entries into one."""
    parts = _split_at_hanging_indent(tr.boxes)
    out: list[Reference] = []
    for i, boxes in enumerate(parts):
        text = layer.read(boxes).text
        label = _label(boxes, layer)
        if lead := _LEADING_LABEL.match(text):
            label = label or lead.group(1)
            text = text[lead.end() :]
        first = i == 0
        surname = _SURNAME.match(text)
        year = _YEAR.search(text)
        doi = _DOI.search(text)
        out.append(
            Reference(
                id=tr.id if first else f"{tr.id}-{i + 1}",
                label=label,
                text=text,
                page=boxes[0].page,
                boxes=boxes,
                surnames=tr.surnames if first else ([surname.group(1).strip()] if surname else []),
                year=tr.year if first else (year.group(0) if year else None),
                title=tr.title if first else None,
                doi=tr.doi if first else (doi.group(0).rstrip(".") if doi else None),
            )
        )
    return out


def _split_at_hanging_indent(boxes: list[Box]) -> list[list[Box]]:
    """Split an entry where a line returns to the entry margin after indented lines."""
    if not boxes:
        return [boxes]
    parts: list[list[Box]] = [[boxes[0]]]
    for prev, box in itertools.pairwise(boxes):
        column = [b for b in boxes if b.page == box.page and abs(b.x - box.x) < 40]
        margin = min(b.x for b in column)
        if box.x <= margin + 2 and prev.page == box.page and prev.x > margin + 4:
            parts.append([box])
        else:
            parts[-1].append(box)
    return parts


def _label(boxes: list[Box], layer: TextLayer) -> str | None:
    """The printed label (`[12]`, `12.`) just left of an entry's first line, if any."""
    first = boxes[0]
    band = Box(
        page=first.page,
        x=first.x - _LABEL_WIDTH,
        y=first.y,
        width=_LABEL_WIDTH - 0.5,
        height=first.height,
    )
    words = layer.read([band]).text.split(" ")
    match = _LABEL.match(words[-1]) if words else None
    return match.group(1) if match else None


def _reference_at(
    link: InternalLink, references: list[Reference], sizes: dict[int, tuple[float, float]]
) -> Reference | None:
    """The Reference whose entry starts where an exact link target points."""
    assert link.target_x is not None and link.target_y is not None
    tx, ty = link.target_x, link.target_y
    near = [
        r
        for r in references
        if r.boxes
        and r.boxes[0].page == link.target_page
        and abs(r.boxes[0].x - tx) < 60
        and -6 <= r.boxes[0].y - ty <= 22
    ]
    if near:
        return min(near, key=lambda r: abs(r.boxes[0].y - ty))
    inside = [
        r
        for r in references
        if any(
            b.page == link.target_page
            and b.x - 6 <= tx + 5 <= b.x + b.width + 6
            and b.y - 6 <= ty + 5 <= b.y + b.height + 6
            for b in r.boxes
        )
    ]
    return inside[0] if inside else None


def _overlaps(a: Box, b: Box) -> bool:
    return (
        a.page == b.page
        and a.x < b.x + b.width
        and b.x < a.x + a.width
        and a.y < b.y + b.height
        and b.y < a.y + a.height
    )


def _spans_overlap(a: Span, b: Span) -> bool:
    return a.start < b.end and b.start < a.end


def _overlap(a: Span, b: Span) -> int:
    return max(0, min(a.end, b.end) - max(a.start, b.start))
