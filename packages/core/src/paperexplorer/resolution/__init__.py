"""Resolving References: matching each to the Work it names (design-resolution.md)."""

from paperexplorer.resolution.matching import (
    Candidate,
    Level,
    Match,
    match,
    normalize_doi,
)

__all__ = ["Candidate", "Level", "Match", "match", "normalize_doi"]
