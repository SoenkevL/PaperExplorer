"""Citations of a parsed Work: each tied to its Reference by a Citation Link with a confidence.

Evidence is weighed in layers: the PDF's own exact link target is certain; GROBID's link
and a rule (printed label, or surname and year) agreeing is high; one source alone is medium
and reported; no evidence leaves an Unlinked Citation. GROBID's link is no evidence when its
Reference's first author is none of the surnames the marker prints.
"""

import re
import unicodedata
from enum import StrEnum

from pydantic import BaseModel

from paperexplorer.parsing import Block, CitationMarker, ParsedWork, Reference, Span

_NUMBERED = re.compile(r"^\W*(?:refs?\.?\s*)?\d{1,3}(?:\s*[,\u2013\-]\s*\d{1,3})*\W*$")
_YEAR = re.compile(r"\b(?:1[5-9]|20)\d\d[a-z]?\b")
_NAME = r"[A-Z][\w'\u2019\-]+(?:\s(?:van|von|de|da|der|den|du|le|la)\s[A-Z][\w'\u2019\-]+)?"
_CUE_NAMES = re.compile(_NAME)
# "Surname, A. B.," or "Surname A B," as printed at the start of a bibliography entry
_ENTRY_NAMES = re.compile(rf"({_NAME}),?\s+(?:[A-Z]\.?\s?-?)+(?:,|\s&|\sand|\s\()")
# A sentence ends at . ! or ? (perhaps with a footnote number: "(DMT).1 The") before a capital.
_SENTENCE_BREAK = re.compile(r"[.!?](\d{0,3})\s+(?=[A-Z(\[\u201c\"])")
_CUT = "\x00"
_CUT_RUN = re.compile(rf"[\s;,(\[{_CUT}]*{_CUT}[\s;,)\]{_CUT}]*")
_NOT_NAMES = frozenset({"See", "Also", "For", "Review", "Et", "Al", "And", "In", "Cf"})


LOW_CONFIDENCE = 0.8  # below this a Citation Link rests on one source or on disagreement


class Evidence(StrEnum):
    """What a Citation Link rests on."""

    PDF_LINK = "pdf_link"
    GROBID = "grobid"
    LABEL = "label"
    AUTHOR_YEAR = "author_year"


class ConfidenceLevel(StrEnum):
    """How firmly a Citation is tied to a Reference, as reported to the reader."""

    CERTAIN = "certain"  # the PDF's own exact link target
    HIGH = "high"
    LOW = "low"  # one source alone, or sources that disagree
    UNLINKED = "unlinked"  # no evidence: an Unlinked Citation


class CitationLink(BaseModel):
    """A Citation's tie to the Reference it points at."""

    reference_id: str
    confidence: float
    evidence: list[Evidence]


def confidence_level(link: CitationLink | None) -> ConfidenceLevel:
    """The level a Citation Link's confidence falls in; no link means an Unlinked Citation."""
    if link is None:
        return ConfidenceLevel.UNLINKED
    if link.confidence >= 1.0:
        return ConfidenceLevel.CERTAIN
    return ConfidenceLevel.HIGH if link.confidence >= LOW_CONFIDENCE else ConfidenceLevel.LOW


class Citation(BaseModel):
    """One place in the body where the authors point at exactly one Reference."""

    block: int
    span: Span
    text: str
    citing_sentence: str
    link: CitationLink | None


class CitationGraph(BaseModel):
    """A Work's References and its Citations; a Citation without a link is an Unlinked Citation."""

    references: list[Reference]
    citations: list[Citation]

    def unlinked(self) -> list[Citation]:
        """Unlinked Citations: no evidence ties them to any Reference."""
        return [c for c in self.citations if c.link is None]

    def low_confidence(self, below: float = LOW_CONFIDENCE) -> list[Citation]:
        """Citations whose link rests on one source or on disagreeing ones (for the v1 report)."""
        return [c for c in self.citations if c.link is not None and c.link.confidence < below]


def extract(doc: ParsedWork) -> CitationGraph:
    """Link every Citation marker of `doc` to its Reference, with a confidence."""
    rules = _Rules(doc.references)
    pages = {r.id: r.page for r in doc.references}
    citations = [
        Citation(
            block=i,
            span=marker.span,
            text=marker.text,
            citing_sentence=_citing_sentence(block, marker.span),
            link=_link(marker, rules.grobid(marker), rules.match(marker, block), pages),
        )
        for i, block in enumerate(doc.blocks)
        for marker in block.citation_markers
    ]
    return CitationGraph(references=doc.references, citations=citations)


def _citing_sentence(block: Block, span: Span) -> str:
    """The sentence holding `span`, with every Citation marker in it removed."""
    sentences = block.sentences or _split_sentences(block.text)
    sentence = next(
        (s for s in sentences if s.start <= span.start < s.end), Span(start=0, end=len(block.text))
    )
    chars = list(block.text[sentence.start : sentence.end])
    for m in block.citation_markers:
        for i in range(max(m.span.start, sentence.start), min(m.span.end, sentence.end)):
            chars[i - sentence.start] = _CUT
    text = _CUT_RUN.sub(" ", "".join(chars))
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _split_sentences(text: str) -> list[Span]:
    """Sentence spans by end punctuation, for blocks without GROBID's sentences (scans)."""
    breaks = list(_SENTENCE_BREAK.finditer(text))
    starts = [0, *(m.end() for m in breaks)]
    ends = [*(m.start() + 1 + len(m.group(1)) for m in breaks), len(text)]
    return [Span(start=s, end=e) for s, e in zip(starts, ends, strict=True)]


def _link(
    marker: CitationMarker,
    grobid: str | None,
    rule: tuple[str, Evidence] | None,
    pages: dict[str, int],
) -> CitationLink | None:
    """Weigh the evidence for one marker: PDF link, then GROBID with a rule, then one source."""
    target = marker.link_target
    ruled = rule[0] if rule else None
    if target is not None and target.reference_id is not None:
        evidence = [Evidence.PDF_LINK]
        evidence += [Evidence.GROBID] if grobid == target.reference_id else []
        evidence += [rule[1]] if rule and ruled == target.reference_id else []
        return CitationLink(reference_id=target.reference_id, confidence=1.0, evidence=evidence)
    target_page = target.page if target is not None else None

    def on_page(ref: str) -> bool:
        """True without a page-only PDF link, else whether the entry starts on its page."""
        return target_page is None or pages.get(ref) == target_page

    if grobid and rule and grobid == ruled:
        return CitationLink(
            reference_id=grobid, confidence=0.9, evidence=[Evidence.GROBID, rule[1]]
        )
    if grobid and rule:  # they disagree: the PDF's target page decides, else trust neither much
        if on_page(ruled or "") and not on_page(grobid):
            return CitationLink(reference_id=ruled or "", confidence=0.5, evidence=[rule[1]])
        return CitationLink(reference_id=grobid, confidence=0.5, evidence=[Evidence.GROBID])
    if grobid:
        confidence = 0.7 if on_page(grobid) else 0.5
        return CitationLink(reference_id=grobid, confidence=confidence, evidence=[Evidence.GROBID])
    if rule:
        return CitationLink(reference_id=rule[0], confidence=0.6, evidence=[rule[1]])
    return None


class _Rules:
    """Rule-based matching of a marker's cue to a Reference."""

    def __init__(self, references: list[Reference]) -> None:
        self._segment: dict[str, int] = {}
        segment, last = 0, 0
        for r in references:  # a bibliography restarts when its labels do
            number = int(r.label) if r.label and r.label.isdigit() else None
            if number is not None and number <= last:
                segment += 1
            last = number or last
            self._segment[r.id] = segment
        self._by_label: dict[str, list[Reference]] = {}
        for r in references:
            if r.label:
                self._by_label.setdefault(r.label, []).append(r)
        self._references = references
        self._names = {r.id: _entry_names(r) for r in references}
        self._years = {r.id: r.year or _printed_year(r.text) for r in references}
        self._first_authors = {r.id: _first_author(r) for r in references}

    def grobid(self, marker: CitationMarker) -> str | None:
        """GROBID's Reference for the marker, unless it is missing or by other authors."""
        ref = marker.grobid_reference_id
        if ref is None or ref not in self._first_authors:
            return None
        names, _ = _cue(marker.cue or marker.text)
        # A loose test, since the cue may print a surname in parts: "Van Den Heuvel and Pol".
        if names and not any(_letters(n) in self._first_authors[ref] for n in names):
            return None
        return ref

    def match(self, marker: CitationMarker, block: Block) -> tuple[str, Evidence] | None:
        """The one Reference the marker's cue names, if exactly one fits."""
        cue = marker.cue or ""
        if not _NUMBERED.match(cue):
            return self._author_year(marker, block)
        numbers = re.findall(r"\d{1,3}", cue)
        if len(numbers) != 1:
            return None
        candidates = self._by_label.get(numbers[0], [])
        if len(candidates) > 1:
            linked = {
                self._segment[m.link_target.reference_id]
                for m in block.citation_markers
                if m.link_target is not None and m.link_target.reference_id in self._segment
            }
            candidates = [r for r in candidates if self._segment[r.id] in linked]
        return (candidates[0].id, Evidence.LABEL) if len(candidates) == 1 else None

    def _author_year(self, marker: CitationMarker, block: Block) -> tuple[str, Evidence] | None:
        names, year = _cue(marker.cue or marker.text)
        if (
            not names
        ):  # a bare year after an author-year names the same authors: "Smith, 2018, 2021"
            names = self._previous_names(marker, block)
        if not names or year is None:
            return None
        candidates = [
            r
            for r in self._references
            if self._names[r.id][:1] == names[:1] and _same_year(self._years[r.id], year)
        ]
        for i, name in enumerate(names[1:], start=1):  # further printed authors narrow it down
            if len(candidates) <= 1:
                break
            narrowed = [r for r in candidates if self._names[r.id][i : i + 1] == [name]]
            candidates = narrowed or candidates
        if len(candidates) > 1:  # "et al." means three or more authors, "X and Y" exactly two
            cue = marker.cue or marker.text
            if re.search(r"\bet\.?\s*al", cue):
                fits = [r for r in candidates if len(self._names[r.id]) >= 3]
            elif re.search(r"\s(?:and|&)\s", cue):
                fits = [r for r in candidates if len(self._names[r.id]) == 2]
            else:
                fits = [r for r in candidates if len(self._names[r.id]) == 1]
            candidates = fits or candidates
        if len(candidates) > 1 and year[-1].isalpha():
            candidates = [r for r in candidates if self._years[r.id] == year]
        return (candidates[0].id, Evidence.AUTHOR_YEAR) if len(candidates) == 1 else None

    def _previous_names(self, marker: CitationMarker, block: Block) -> list[str]:
        before = [m for m in block.citation_markers if m.span.end <= marker.span.start]
        if not before:
            return []
        prev = before[-1]
        between = block.text[prev.span.end : marker.span.start]
        if between.strip(" ,") or "(" in between or ";" in between:
            return []
        return _cue(prev.cue or prev.text)[0] or self._previous_names(prev, block)


def _cue(cue: str) -> tuple[list[str], str | None]:
    """The surnames and year a marker cue prints: `(Smith, Jones, et al., 2016;`."""
    years = _YEAR.findall(cue)
    head = cue[: cue.find(years[-1])] if years else cue
    names = [_fold(n) for n in _CUE_NAMES.findall(head) if n not in _NOT_NAMES]
    return names, years[-1] if years else None


def _entry_names(reference: Reference) -> list[str]:
    printed = [_fold(n) for n in _ENTRY_NAMES.findall(reference.text.split("(")[0] + "(")]
    if printed:
        return printed
    return [_fold(n) for n in reference.surnames]


def _first_author(reference: Reference) -> str:
    """The letters of the entry's first author as printed: everything before its first comma."""
    return _letters(reference.text.split(",")[0]) or _letters(reference.text)


def _letters(text: str) -> str:
    """Only the letters, folded: hyphens and line-break joins differ between cue and entry."""
    return "".join(c for c in _fold(text) if c.isalpha())


def _printed_year(text: str) -> str | None:
    found = _YEAR.search(text)
    return found.group(0) if found else None


def _same_year(entry: str | None, cue: str) -> bool:
    return entry is not None and entry[:4] == cue[:4] and (len(cue) == 4 or entry == cue)


def _fold(name: str) -> str:
    """A surname compared without case or accents (OCR and fonts disagree on both)."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()
