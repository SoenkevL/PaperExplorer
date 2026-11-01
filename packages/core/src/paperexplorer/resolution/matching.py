"""The rules that match a Reference to a Work it may name, as pure functions."""

import difflib
import re
import unicodedata
from collections.abc import Sequence

from pydantic import BaseModel

from paperexplorer.domain import Level, normalize_doi
from paperexplorer.parsing import Reference

# Until calibrated live on fixture References that print a DOI (design-resolution.md).
NEAR_TITLE = 0.9
# Words a title cut short must keep to agree with a DOI's record that it opens.
PREFIX_WORDS = 4


class Candidate(BaseModel):
    """A Work a Reference may name: a Library Item's or a registry record's metadata."""

    title: str | None = None
    surnames: list[str] = []
    year: int | None = None
    doi: str | None = None


class Match(BaseModel):
    """The candidate a Reference names, by its index, with the level and what agreed."""

    candidate: int
    level: Level
    evidence: list[str]
    confidence: float = 1.0  # the titles' similarity for a title match
    doi_conflict: int | None = None  # the candidate the printed DOI names, when it disagreed


class DoiConflict(BaseModel):
    """The printed DOI names a candidate the Reference does not agree with, and no title matched."""

    candidate: int


def normalize_title(title: str) -> str:
    """A title for comparing: lowercase, accents and punctuation stripped, spaces collapsed."""
    decomposed = unicodedata.normalize("NFKD", title)
    bare = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[\W_]+", " ", bare).split())


def agrees(ref: Reference, c: Candidate) -> bool:
    """Whether a DOI hit is the Work the Reference prints, allowing slips and a dropped subtitle.

    Titles agree when near, when their main titles are equal, or when one opens the other (at
    least `PREFIX_WORDS` whole words: a title cut short); without both titles, the first
    surname or the year must agree. A Reference that prints only its DOI has nothing to disagree.
    """
    if ref.title and c.title and normalize_title(ref.title):
        ours, theirs = normalize_title(ref.title), normalize_title(c.title)
        near = difflib.SequenceMatcher(None, ours, theirs).ratio() >= NEAR_TITLE
        return near or _main(ref.title) == _main(c.title) or _opens(ours, theirs)
    if ref.surnames or ref.year:
        return _surname_agrees(ref, c) or _year_agrees(ref, c)
    return True


def match(ref: Reference, candidates: Sequence[Candidate]) -> Match | DoiConflict | None:
    """The candidate the Reference names, if any rule ties it to one.

    A DOI hit that does not agree is a DOI Conflict: the title rules run as if no DOI were
    printed, a title match capped at low; with none, `DoiConflict` keeps the DOI's candidate.
    """
    doi = normalize_doi(ref.doi) if ref.doi else None
    conflict: int | None = None
    if doi is not None:
        for i, c in enumerate(candidates):
            if c.doi and normalize_doi(c.doi) == doi:
                if agrees(ref, c):
                    return Match(candidate=i, level=Level.CERTAIN, evidence=["doi"])
                conflict = i
                break
    found = _best_title_match(ref, candidates, printed_doi=doi if conflict is None else None)
    if conflict is None:
        return found
    if found is None:
        return DoiConflict(candidate=conflict)
    return Match(
        candidate=found.candidate,
        level=Level.LOW,
        evidence=[*found.evidence, "doi_conflict"],
        confidence=found.confidence,
        doi_conflict=conflict,
    )


def bibliographic_match(ref: Reference, candidates: Sequence[Candidate]) -> Match | None:
    """Crossref's guess from the printed string, at low.

    The first candidate whose whole title is printed in it, with its first surname or its year.
    """
    printed = f" {normalize_title(ref.text)} "
    for i, c in enumerate(candidates):
        title = normalize_title(c.title or "")
        if not title or f" {title} " not in printed:
            continue
        surname = normalize_title(c.surnames[0]) if c.surnames else ""
        agreed = [
            name
            for name, agrees in (
                ("surname", bool(surname) and f" {surname} " in printed),
                ("year", c.year is not None and f" {c.year} " in printed),
            )
            if agrees
        ]
        if agreed:
            return Match(candidate=i, level=Level.LOW, evidence=["bibliographic", *agreed])
    return None


def _best_title_match(
    ref: Reference, candidates: Sequence[Candidate], *, printed_doi: str | None
) -> Match | None:
    """The best candidate by the title rules: highest level, then closest title."""
    if not ref.title:
        return None
    best: tuple[int, float, Match] | None = None
    for i, c in enumerate(candidates):
        # A title never overrules a printed DOI: a preprint and its published Version share one.
        if printed_doi is not None and c.doi:
            continue
        found = _title_match(ref, ref.title, c, i)
        if found is None:
            continue
        rank = (_RANK[found[1].level], found[0])
        if best is None or rank > best[:2]:
            best = (*rank, found[1])
    return best[2] if best else None


_RANK = {Level.HIGH: 2, Level.LOW: 1}


def _title_match(ref: Reference, title: str, c: Candidate, i: int) -> tuple[float, Match] | None:
    """The title rules against one candidate, with the titles' similarity for ranking."""
    if not c.title:
        return None
    ours, theirs = normalize_title(title), normalize_title(c.title)
    if not ours:
        return None
    similarity = difflib.SequenceMatcher(None, ours, theirs).ratio()
    agreed = [
        name
        for name, agrees in (("surname", _surname_agrees(ref, c)), ("year", _year_agrees(ref, c)))
        if agrees
    ]
    if ours == theirs and agreed:
        return similarity, Match(
            candidate=i, level=Level.HIGH, evidence=["title", *agreed], confidence=similarity
        )
    if len(agreed) == 2 and _main(title) and _main(title) == _main(c.title):
        return similarity, Match(
            candidate=i, level=Level.HIGH, evidence=["main_title", *agreed], confidence=similarity
        )
    if similarity >= NEAR_TITLE:
        return similarity, Match(
            candidate=i, level=Level.LOW, evidence=["near_title"], confidence=similarity
        )
    return None


def _opens(ours: str, theirs: str) -> bool:
    """Whether one normalized title is the other's first words, enough of them to count."""
    short, long = sorted((ours.split(), theirs.split()), key=len)
    return len(short) >= PREFIX_WORDS and long[: len(short)] == short


def _main(title: str) -> str:
    """The main title: what comes before the colon, normalized."""
    return normalize_title(title.split(":", 1)[0])


def _surname_agrees(ref: Reference, c: Candidate) -> bool:
    if not ref.surnames or not c.surnames:
        return False
    return normalize_title(ref.surnames[0]) == normalize_title(c.surnames[0])


def _year_agrees(ref: Reference, c: Candidate) -> bool:
    printed = re.match(r"\d{4}", ref.year or "")
    return printed is not None and c.year is not None and int(printed.group()) == c.year
