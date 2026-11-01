"""Resolving References: matching each to the Work it names (design-resolution.md)."""

from paperexplorer.domain import Level, normalize_doi
from paperexplorer.resolution.check import (
    BudgetSpent,
    CheckEvent,
    CheckFinished,
    ReferenceChecked,
    check,
)
from paperexplorer.resolution.local import counts, resolve
from paperexplorer.resolution.matching import (
    Candidate,
    DoiConflict,
    Match,
    agrees,
    match,
)
from paperexplorer.resolution.registries import BudgetSpentError, Registries, RegistryError

__all__ = [
    "BudgetSpent",
    "BudgetSpentError",
    "Candidate",
    "CheckEvent",
    "CheckFinished",
    "DoiConflict",
    "Level",
    "Match",
    "ReferenceChecked",
    "Registries",
    "RegistryError",
    "agrees",
    "check",
    "counts",
    "match",
    "normalize_doi",
    "resolve",
]
