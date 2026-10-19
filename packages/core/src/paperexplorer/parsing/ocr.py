"""Scans: no text layer, so LiteParse's OCR reads the words and rules rebuild the structure.

OCR Text is never exactly what the authors printed: every block carries its OCR confidence.
LiteParse's own layout blocks are mostly single lines on scans, and GROBID cannot read scans,
so lines, headings, paragraphs and bibliography entries are rebuilt from word positions.
"""

import re
import statistics
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from liteparse.parser import LiteParse
from liteparse.types import ParsedPage, TextItem

from paperexplorer.config import Settings
from paperexplorer.parsing.models import (
    Block,
    BlockKind,
    Box,
    CitationMarker,
    ParsedWork,
    Reference,
    Span,
    TextOrigin,
)

_EMPHASIS = re.compile(r"(?<!\\)[*_]{1,2}")
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|])")
_REFERENCES_HEADING = re.compile(
    r"^\W*(?:\d+\.?\s*)?"
    r"(?:references(?: and notes)?|bibliography|literature cited|works cited)\W*$",
    re.IGNORECASE,
)
_END_HEADING = re.compile(
    r"^(?:acknowledg|author contributions|competing interests|supplementary|supporting information"
    r"|appendix|figure legends|funding|declaration|conflicts? of interest|disclosure)",
    re.IGNORECASE,
)
_LABEL = re.compile(r"^\s*(?:\[(\d{1,3})\]|(\d{1,3})\.?(?=\s+[A-Z]))")
_YEAR = re.compile(r"\b(?:1[5-9]|20)\d\d[a-z]?\b")
_DOI = re.compile(r"10\.\d{4,9}/\S+")
_SURNAME = re.compile(r"^([^,(]+?)\s*[,(]")
_SENTENCE_END = (".", "!", "?", ":")
_YEAR_TEXT = r"(?:1[5-9]|20)\d\d[a-z]?"
_NAME = r"[A-Z][\w'\u2019\-]+"
_AUTHORS = rf"{_NAME}(?:,?\s+(?:{_NAME},?\s*)*(?:et\s+al\.?|and|&)\s*(?:{_NAME})?)?"
_NARRATIVE = re.compile(rf"(?P<authors>{_AUTHORS})\s+\((?P<year>{_YEAR_TEXT})\)")
_GROUP = re.compile(rf"\(([^()]*?{_YEAR_TEXT}[^()]*)\)")
_PART = re.compile(
    rf"^(?P<authors>[^0-9]*?[A-Za-z.])[,\s]+(?P<years>{_YEAR_TEXT}(?:\s*,\s*{_YEAR_TEXT})*)\s*$"
)
_BRACKETS = re.compile(r"\[(\d{1,3}(?:\s*[,\u2013\-]\s*\d{1,3})*)\]")
_LEAD_IN = re.compile(r"^(?:\s|e\.g\.,?|i\.e\.,?|see(?: also)?|cf\.)*", re.IGNORECASE)
_SAME_LINE = 3.0  # points: OCR words this close vertically sit on one line
_WORD_GAP = 12.0  # points: a wider horizontal gap starts another column's line
_MARGIN = 3.0  # points: a line this close to its column's margin starts an entry
_INDENT = 6.0  # points: an indented line starts a paragraph after a sentence end
_HEADING_LENGTH = 60  # characters: headings are short lines
_PARAGRAPH_GAP = 1.6  # a vertical gap this many line heights wide starts a paragraph


@dataclass(frozen=True, slots=True)
class _Line:
    page: int
    column: int
    x: float
    y: float
    width: float
    height: float
    text: str
    confidences: tuple[float, ...]


def read_scan(pdf: Path, settings: Settings) -> ParsedWork:
    """OCR every page; body paragraphs keep their OCR confidence, the bibliography is split."""
    parser = LiteParse(
        output_format="json",
        ocr_enabled=True,
        tessdata_path=str(settings.tessdata_dir) if settings.tessdata_dir else None,
        extract_links=False,
        quiet=True,
    )
    lines = [line for page in parser.parse(pdf).pages for line in _page_lines(page)]
    if not lines:
        return ParsedWork(text_origin=TextOrigin.OCR, blocks=[], references=[])
    headings = {i for i in range(len(lines)) if _is_heading(lines, i)}
    start = max(
        (i for i, line in enumerate(lines) if _REFERENCES_HEADING.match(line.text)), default=None
    )
    end = len(lines)
    if start is not None:
        end = next(
            (i for i in sorted(headings) if i > start and _END_HEADING.match(lines[i].text)),
            len(lines),
        )
    body = list(range(len(lines))) if start is None else [*range(start), *range(end, len(lines))]
    return ParsedWork(
        text_origin=TextOrigin.OCR,
        blocks=_paragraphs([lines[i] for i in body], {i for i in headings if i in body}, body),
        references=[] if start is None else _references(lines[start + 1 : end]),
        parser_versions={"liteparse": version("liteparse")},
    )


def _page_lines(page: ParsedPage) -> list[_Line]:
    """The page's OCR words merged into lines, column by column, top to bottom."""
    rows: list[list[TextItem]] = []
    words = (t for t in page.text_items if t.confidence is not None and t.text.strip())
    for word in sorted(words, key=lambda t: t.y):
        if rows and abs(word.y - rows[-1][0].y) <= _SAME_LINE:
            rows[-1].append(word)
        else:
            rows.append([word])
    lines: list[_Line] = []
    for row in rows:
        current: list[TextItem] = []
        for word in [*sorted(row, key=lambda t: t.x), None]:
            if current and (
                word is None or word.x - (current[-1].x + current[-1].width) > _WORD_GAP
            ):
                lines.append(_line(page, current))
                current = []
            if word is not None:
                current.append(word)
    return sorted(lines, key=lambda line: (line.column, line.y, line.x))


def _line(page: ParsedPage, words: list[TextItem]) -> _Line:
    x = words[0].x
    return _Line(
        page=page.page_num,
        column=1 if x >= page.width / 2 - 10 else 0,
        x=x,
        y=min(w.y for w in words),
        width=words[-1].x + words[-1].width - x,
        height=statistics.median(w.height for w in words),
        text=_plain(" ".join(w.text for w in words)),
        confidences=tuple(w.confidence for w in words if w.confidence is not None),
    )


def _is_heading(lines: list[_Line], i: int) -> bool:
    """A short capitalised line between a finished sentence and a capitalised line.

    OCR word heights vary too much with ascenders and brackets to tell headings by size.
    """
    line = lines[i]
    if len(line.text) >= _HEADING_LENGTH or not line.text[:1].isupper():
        return False
    if line.text.endswith((*_SENTENCE_END, ",", ";", "-", "\u2013")):
        return False
    prev = lines[i - 1] if i > 0 else None
    after_sentence = (
        prev is None
        or (prev.page, prev.column) != (line.page, line.column)
        or prev.text.endswith(_SENTENCE_END)
    )
    nxt = lines[i + 1] if i + 1 < len(lines) else None
    return after_sentence and (nxt is None or nxt.text[:1].isupper())


def _paragraphs(lines: list[_Line], headings: set[int], positions: list[int]) -> list[Block]:
    """Group lines into headings and paragraphs; a paragraph may continue across columns."""
    blocks: list[list[_Line]] = []
    kinds: list[BlockKind] = []
    margins: dict[tuple[int, int], float] = {}
    for line in lines:
        key = (line.page, line.column)
        margins[key] = min(margins.get(key, line.x), line.x)
    for line, position in zip(lines, positions, strict=True):
        if position in headings:
            blocks.append([line])
            kinds.append(BlockKind.HEADING)
            continue
        prev = blocks[-1][-1] if blocks and kinds[-1] is BlockKind.PARAGRAPH else None
        ended = prev is not None and prev.text.endswith(_SENTENCE_END)
        if prev is None:
            new = True
        elif (prev.page, prev.column) != (line.page, line.column):
            new = ended  # an unfinished sentence continues in the next column or page
        else:
            gap = line.y - (prev.y + prev.height)
            indented = line.x > margins[(line.page, line.column)] + _INDENT
            new = gap > prev.height * _PARAGRAPH_GAP or (ended and indented)
        if new:
            blocks.append([line])
            kinds.append(BlockKind.PARAGRAPH)
        else:
            blocks[-1].append(line)
    out: list[Block] = []
    heading: list[str] = []
    for kind, group in zip(kinds, blocks, strict=True):
        text = _join(group)
        if kind is BlockKind.HEADING:
            heading = [text]
        confidences = [c for line in group for c in line.confidences]
        out.append(
            Block(
                kind=kind,
                heading_path=[] if kind is BlockKind.HEADING else heading,
                page=group[0].page,
                boxes=[
                    Box(page=line.page, x=line.x, y=line.y, width=line.width, height=line.height)
                    for line in group
                ],
                text=text,
                citation_markers=[] if kind is BlockKind.HEADING else _markers(text),
                ocr_confidence=statistics.fmean(confidences) if confidences else None,
            )
        )
    return out


def _markers(text: str) -> list[CitationMarker]:
    """Citation markers found by pattern: narrative, parenthetical author-year, numbered."""
    found: list[CitationMarker] = []
    for m in _NARRATIVE.finditer(text):
        found.append(_marker(text, m.start(), m.end(), f"{m['authors']}, {m['year']}"))
    for group in _GROUP.finditer(text):
        offset = group.start(1)
        for part in re.finditer(r"[^;]+", group.group(1)):
            lead = _LEAD_IN.match(part.group(0))
            start = offset + part.start() + (lead.end() if lead else 0)
            body = text[start : offset + part.end()]
            m = _PART.match(body)
            if m is None:
                continue
            authors = m["authors"].strip()
            for i, year in enumerate(re.finditer(_YEAR_TEXT, m["years"])):
                year_start = start + m.start("years") + year.start()
                span_start = start if i == 0 else year_start
                found.append(
                    _marker(
                        text,
                        span_start,
                        year_start + len(year.group(0)),
                        f"{authors}, {year.group(0)}",
                    )
                )
    for group in _BRACKETS.finditer(text):
        for number, start, end in _numbers(group.group(1), group.start(1)):
            found.append(_marker(text, start, end, f"[{number}]"))
    found.sort(key=lambda m: m.span.start)
    return [
        m
        for i, m in enumerate(found)
        if i == 0 or m.span.start >= found[i - 1].span.end or m.span != found[i - 1].span
    ]


def _numbers(listed: str, offset: int) -> list[tuple[int, int, int]]:
    """Each number of a list like `1, 3-5` and its printed span; inner range numbers share it."""
    out: list[tuple[int, int, int]] = []
    for item in re.finditer(r"(\d{1,3})(?:\s*[\u2013\-]\s*(\d{1,3}))?", listed):
        first = int(item.group(1))
        last = int(item.group(2)) if item.group(2) else first
        for n in range(first, last + 1):
            out.append((n, offset + item.start(), offset + item.end()))
    return out


def _marker(text: str, start: int, end: int, cue: str) -> CitationMarker:
    return CitationMarker(span=Span(start=start, end=end), text=text[start:end], cue=cue)


def _references(lines: list[_Line]) -> list[Reference]:
    if not lines:
        return []
    first = _LABEL.match(lines[0].text)
    entries: list[list[_Line]] = []
    if first:
        expect = int(first.group(1) or first.group(2))
        for line in lines:
            label = _LABEL.match(line.text)
            number = int(label.group(1) or label.group(2)) if label else None
            if number is not None and expect <= number <= expect + 2:  # OCR may drop a label
                entries.append([line])
                expect = number + 1
            elif entries:
                entries[-1].append(line)
    else:
        margins: dict[tuple[int, int], float] = {}
        for line in lines:
            key = (line.page, line.column)
            margins[key] = min(margins.get(key, line.x), line.x)
        for line in lines:
            if line.x <= margins[(line.page, line.column)] + _MARGIN or not entries:
                entries.append([line])
            else:
                entries[-1].append(line)
    return [_reference(i, entry, bool(first)) for i, entry in enumerate(entries)]


def _reference(index: int, entry: list[_Line], labelled: bool) -> Reference:
    text = _join(entry)
    label = _LABEL.match(text) if labelled else None
    if label:
        text = text[label.end() :].strip()
    surname, year, doi = _SURNAME.match(text), _YEAR.search(text), _DOI.search(text)
    return Reference(
        id=f"r{index}",
        label=(label.group(1) or label.group(2)) if label else None,
        text=text,
        page=entry[0].page,
        boxes=[
            Box(page=line.page, x=line.x, y=line.y, width=line.width, height=line.height)
            for line in entry
        ],
        surnames=[surname.group(1).strip()] if surname else [],
        year=year.group(0) if year else None,
        doi=doi.group(0).rstrip(".") if doi else None,
    )


def _join(lines: list[_Line]) -> str:
    """Lines joined with spaces; a line-end dash joins the next line directly, keeping the dash."""
    text = ""
    for line in lines:
        direct = text.endswith(("-", "\u2013")) and line.text[:1].isalnum()
        text = f"{text}{line.text}" if direct else f"{text} {line.text}".strip()
    return text


def _plain(text: str) -> str:
    """LiteParse's text without its Markdown emphasis and escapes."""
    return re.sub(r"\s+", " ", _ESCAPE.sub(r"\1", _EMPHASIS.sub("", text))).strip()
