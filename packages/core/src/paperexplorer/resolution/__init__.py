"""Resolving References: matching each to the Work it names (design-resolution.md)."""

from paperexplorer.domain import Level, normalize_doi
from paperexplorer.resolution.check import (
    BudgetSpent,
    ReferenceChecked,
    RegistryCheckEvent,
    RegistryCheckFinished,
    registry_check,
)
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
from paperexplorer.resolution.registries import BudgetSpentError, Registries, RegistryError
from paperexplorer.resolution.verification import registration, unregistered, verify

__all__ = [
    "BudgetSpent",
    "BudgetSpentError",
    "Candidate",
    "DoiConflict",
    "DoiOrgRegistry",
    "DoiRecord",
    "DoiRegistry",
    "InMemoryDoiRegistry",
    "Level",
    "Match",
    "ReferenceChecked",
    "Registration",
    "Registries",
    "RegistryCheckEvent",
    "RegistryCheckFinished",
    "RegistryError",
    "agrees",
    "counts",
    "match",
    "normalize_doi",
    "registration",
    "registry_check",
    "resolve",
    "unregistered",
    "verify",
]
