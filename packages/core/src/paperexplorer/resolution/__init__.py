"""Resolving References: matching each to the Work it names (design-resolution.md)."""

from paperexplorer.domain import Level, normalize_doi
from paperexplorer.resolution.doi_org import DoiOrgRegistry
from paperexplorer.resolution.local import counts, resolve
from paperexplorer.resolution.matching import (
    Candidate,
    DoiConflict,
    Match,
    agrees,
    match,
)
from paperexplorer.resolution.registration import (
    DoiRecord,
    DoiRegistry,
    InMemoryDoiRegistry,
    Registration,
)
from paperexplorer.resolution.verification import registration, unregistered, verify

__all__ = [
    "Candidate",
    "DoiConflict",
    "DoiOrgRegistry",
    "DoiRecord",
    "DoiRegistry",
    "InMemoryDoiRegistry",
    "Level",
    "Match",
    "Registration",
    "agrees",
    "counts",
    "match",
    "normalize_doi",
    "registration",
    "resolve",
    "unregistered",
    "verify",
]
