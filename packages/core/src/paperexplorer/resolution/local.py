"""The local pass: References matched against the Library's Works, then cached registry responses.

No registry is called here; a Registry Check fills the cache and runs this pass again.
"""

import logging
from collections.abc import Sequence

import psycopg
from psycopg.rows import TupleRow

from paperexplorer.domain import (
    Layer,
    Level,
    Resolution,
    ResolutionState,
    Work,
    WorkId,
    normalize_doi,
)
from paperexplorer.parsing import Reference
from paperexplorer.resolution.layers import from_registries
from paperexplorer.resolution.matching import Candidate, DoiConflict, match
from paperexplorer.resolution.verification import unregistered
from paperexplorer.store import RegistryResponses, Works

logger = logging.getLogger(__name__)


def resolve(
    conn: psycopg.Connection[TupleRow], citing: WorkId, refs: Sequence[Reference]
) -> list[Resolution]:
    """One Resolution per Reference of `citing`, from the Library and the cached responses.

    Library candidates are the Work that stands for the printed DOI and the Works whose title is
    near the printed one; a match to a Duplicate Item goes to its Standing Item. A Reference the
    Library leaves unmatched, or matched at low, goes through the cached responses layer by layer
    (DOI, title search, bibliographic); with one of them not yet asked, it stays Unchecked. A
    Reference whose DOI is an Unregistered DOI is matched as if it printed none (ADR 0012).
    """
    works, cache = Works(conn), RegistryResponses(conn)
    resolutions: list[Resolution] = []
    for ref, note in as_resolved(conn, refs):
        library = _from_library(works, citing, ref)
        if library.firm:
            resolution = library
        else:
            resolution = _better(
                library, from_registries(works, cache, citing, ref, library.doi_conflict)
            )
        if note:
            resolution = resolution.model_copy(update={"evidence": [*resolution.evidence, note]})
        resolutions.append(resolution)
    for r in resolutions:
        if r.doi_conflict is not None and r.state is ResolutionState.RESOLVED:
            logger.warning("Reference %s prints a DOI its title disagrees with", r.reference)
        if r.level is Level.LOW:
            logger.warning("Reference %s resolved at low confidence: %s", r.reference, r.evidence)
    return resolutions


def as_resolved(
    conn: psycopg.Connection[TupleRow], refs: Sequence[Reference]
) -> list[tuple[Reference, str | None]]:
    """Each Reference as it is matched, with a note when its Unregistered DOI was set aside."""
    gone = unregistered(conn, [r.doi for r in refs if r.doi])
    matched: list[tuple[Reference, str | None]] = []
    for ref in refs:
        doi = normalize_doi(ref.doi) if ref.doi else None
        if doi in gone:
            matched.append((ref.model_copy(update={"doi": None}), f"unregistered DOI {doi}"))
        else:
            matched.append((ref, None))
    return matched


def _from_library(works: Works, citing: WorkId, ref: Reference) -> Resolution:
    pool: dict[WorkId, Work] = {}
    stands = works.standing_item(normalize_doi(ref.doi)) if ref.doi else None
    if stands is not None and (work := works.get(stands)) is not None:
        pool[work.id] = work
    for work in works.title_candidates(ref.title) if ref.title else []:
        pool.setdefault(work.id, work)
    # Only Library Items: a Work kept after its item left the Library is not in it.
    candidates = [w for w in pool.values() if w.id != citing and w.zotero_key is not None]
    found = match(ref, [_candidate(w) for w in candidates])
    if found is None:
        return Resolution(reference=ref.id, state=ResolutionState.UNCHECKED)
    if isinstance(found, DoiConflict):
        return Resolution(
            reference=ref.id,
            state=ResolutionState.UNCHECKED,
            doi_conflict=candidates[found.candidate].id,
        )
    return Resolution(
        reference=ref.id,
        state=ResolutionState.RESOLVED,
        work=_standing(works, candidates[found.candidate]),
        level=found.level,
        confidence=found.confidence,
        evidence=found.evidence,
        layer=Layer.LIBRARY,
        doi_conflict=None if found.doi_conflict is None else candidates[found.doi_conflict].id,
    )


def _better(library: Resolution, registry: Resolution) -> Resolution:
    """A low Library match stands unless a registry matched higher."""
    if library.state is not ResolutionState.RESOLVED:
        return registry
    if registry.firm:
        return registry
    return library


def _standing(works: Works, work: Work) -> WorkId:
    """The Work of the Standing Item for this one's DOI, or this one."""
    return (works.standing_item(work.doi) if work.doi else None) or work.id


def _candidate(work: Work) -> Candidate:
    """A Work as the matching rules see it; creators are `Last, First`."""
    surnames = [c.split(",", 1)[0].strip() for c in work.creators]
    return Candidate(title=work.title, surnames=surnames, year=work.year, doi=work.doi)


def counts(resolutions: Sequence[Resolution]) -> dict[str, int]:
    """References by level, Unchecked and Unresolved ones and DOI Conflicts, as reported."""
    return (
        {"references": len(resolutions)}
        | {lv.value: sum(r.level is lv for r in resolutions) for lv in Level}
        | {
            "unchecked": sum(r.state is ResolutionState.UNCHECKED for r in resolutions),
            "unresolved": sum(r.state is ResolutionState.UNRESOLVED for r in resolutions),
            "doi_conflicts": sum(r.doi_conflict is not None for r in resolutions),
        }
    )
