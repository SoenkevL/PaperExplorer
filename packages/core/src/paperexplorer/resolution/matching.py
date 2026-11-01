"""The rules that match a Reference to a Work it may name, as pure functions."""

import difflib
import re
import unicodedata
from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel

from paperexplorer.parsing import Reference

# Until calibrated live on fixture References that print a DOI (design-resolution.md).
NEAR_TITLE = 0.9


class Level(StrEnum):
    """How firmly a Resolution ties a Reference to its Work."""

    CERTAIN = "certain"  # the printed DOI
    HIGH = "high"
    LOW = "low"


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


def normalize_doi(doi: str) -> str:
    """A DOI as a key: lowercase, no `https://doi.org/` or `doi:`, no trailing `.` or `,`."""
    key = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        key = key.removeprefix(prefix)
    return key.strip().rstrip(".,")


def normalize_title(title: str) -> str:
    """A title for comparing: lowercase, accents and punctuation stripped, spaces collapsed."""
    decomposed = unicodedata.normalize("NFKD", title)
    bare = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[\W_]+", " ", bare).split())


def match(ref: Reference, candidates: Sequence[Candidate]) -> Match | None:
    """The candidate the Reference names, if any rule ties it to one."""
    doi = normalize_doi(ref.doi) if ref.doi else None
    if doi is not None:
        for i, c in enumerate(candidates):
            if c.doi and normalize_doi(c.doi) == doi:
                return Match(candidate=i, level=Level.CERTAIN, evidence=["doi"])
    if not ref.title:
        return None
    best: tuple[int, float, Match] | None = None
    for i, c in enumerate(candidates):
        # A title never overrules a printed DOI: a preprint and its published Version share one.
        if doi is not None and c.doi:
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
        return similarity, Match(candidate=i, level=Level.HIGH, evidence=["title", *agreed])
    if len(agreed) == 2 and _main(title) and _main(title) == _main(c.title):
        return similarity, Match(candidate=i, level=Level.HIGH, evidence=["main_title", *agreed])
    if similarity >= NEAR_TITLE:
        return similarity, Match(candidate=i, level=Level.LOW, evidence=["near_title"])
    return None


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
