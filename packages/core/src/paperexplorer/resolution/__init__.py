"""Resolving References: matching each to the Work it names (design-resolution.md)."""

from paperexplorer.resolution.matching import (
    Candidate,
    DoiConflict,
    Level,
    Match,
    agrees,
    match,
    normalize_doi,
)

__all__ = ["Candidate", "DoiConflict", "Level", "Match", "agrees", "match", "normalize_doi"]
