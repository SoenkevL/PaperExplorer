"""Resolving References: matching each to the Work it names (design-resolution.md)."""

from paperexplorer.domain import Level, normalize_doi
from paperexplorer.resolution.local import counts, resolve
from paperexplorer.resolution.matching import (
    Candidate,
    DoiConflict,
    Match,
    agrees,
    match,
)

__all__ = [
    "Candidate",
    "DoiConflict",
    "Level",
    "Match",
    "agrees",
    "counts",
    "match",
    "normalize_doi",
    "resolve",
]
