"""Resolving References: matching each to the Work it names (design-resolution.md)."""

from paperexplorer.domain import Level
from paperexplorer.resolution.local import resolve
from paperexplorer.resolution.matching import (
    Candidate,
    DoiConflict,
    Match,
    agrees,
    match,
    normalize_doi,
)

__all__ = [
    "Candidate",
    "DoiConflict",
    "Level",
    "Match",
    "agrees",
    "match",
    "normalize_doi",
    "resolve",
]
