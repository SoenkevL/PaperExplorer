"""The local pass: References matched against the Library's Works, with no registry call."""

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
from paperexplorer.resolution.matching import Candidate, DoiConflict, match
from paperexplorer.resolution.verification import unregistered
from paperexplorer.store import Works

logger = logging.getLogger(__name__)


def resolve(
    conn: psycopg.Connection[TupleRow], citing: WorkId, refs: Sequence[Reference]
) -> list[Resolution]:
    """One Resolution per Reference of `citing`, from the Library Items the store holds.

    Candidates are the Work that stands for the printed DOI and the Works whose title is near
    the printed one; a match to a Duplicate Item goes to its Standing Item. A Reference whose
    DOI is an Unregistered DOI is matched as if it printed none (ADR 0012).
    """
    works = Works(conn)
    unknown = unregistered(conn, [r.doi for r in refs if r.doi])
    resolved: list[Resolution] = []
    for ref in refs:
        if ref.doi and normalize_doi(ref.doi) in unknown:
            found = _resolve_one(works, citing, ref.model_copy(update={"doi": None}))
            note = f"unregistered DOI {normalize_doi(ref.doi)}"
            found = found.model_copy(update={"evidence": [*found.evidence, note]})
        else:
            found = _resolve_one(works, citing, ref)
        resolved.append(found)
    return resolved


def _resolve_one(works: Works, citing: WorkId, ref: Reference) -> Resolution:
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
        logger.warning("Reference %s prints a DOI its title disagrees with", ref.id)
        return Resolution(
            reference=ref.id,
            state=ResolutionState.UNCHECKED,
            doi_conflict=candidates[found.candidate].id,
        )
    resolution = Resolution(
        reference=ref.id,
        state=ResolutionState.RESOLVED,
        work=_standing(works, candidates[found.candidate]),
        level=found.level,
        confidence=found.confidence,
        evidence=found.evidence,
        layer=Layer.LIBRARY,
        doi_conflict=None if found.doi_conflict is None else candidates[found.doi_conflict].id,
    )
    if resolution.doi_conflict is not None or found.level is Level.LOW:
        logger.warning("Reference %s resolved at low confidence: %s", ref.id, found.evidence)
    return resolution


def _standing(works: Works, work: Work) -> WorkId:
    """The Work of the Standing Item for this one's DOI, or this one."""
    return (works.standing_item(work.doi) if work.doi else None) or work.id


def _candidate(work: Work) -> Candidate:
    """A Work as the matching rules see it; creators are `Last, First`."""
    surnames = [c.split(",", 1)[0].strip() for c in work.creators]
    return Candidate(title=work.title, surnames=surnames, year=work.year, doi=work.doi)


def counts(resolutions: Sequence[Resolution]) -> dict[str, int]:
    """References by level, Unchecked ones and DOI Conflicts, as the Stage and the CLI report."""
    return (
        {"references": len(resolutions)}
        | {lv.value: sum(r.level is lv for r in resolutions) for lv in Level}
        | {
            "unchecked": sum(r.state is ResolutionState.UNCHECKED for r in resolutions),
            "doi_conflicts": sum(r.doi_conflict is not None for r in resolutions),
        }
    )
