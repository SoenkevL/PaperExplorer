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
    RegistrySource,
    Resolution,
    ResolutionState,
    Work,
    WorkId,
    normalize_doi,
)
from paperexplorer.parsing import Reference
from paperexplorer.resolution.matching import (
    Candidate,
    DoiConflict,
    Match,
    agrees,
    bibliographic_match,
    match,
    normalize_title,
)
from paperexplorer.resolution.records import candidate, from_crossref, from_openalex
from paperexplorer.store import RegistryResponses, Works

logger = logging.getLogger(__name__)


def resolve(
    conn: psycopg.Connection[TupleRow], citing: WorkId, refs: Sequence[Reference]
) -> list[Resolution]:
    """One Resolution per Reference of `citing`, from the Library and the cached responses.

    Library candidates are the Work that stands for the printed DOI and the Works whose title is
    near the printed one; a match to a Duplicate Item goes to its Standing Item. A Reference the
    Library leaves unmatched, or matched at low, goes through the cached responses layer by layer
    (DOI, title search, bibliographic); with one of them not yet asked, it stays Unchecked.
    """
    works, cache = Works(conn), RegistryResponses(conn)
    resolutions: list[Resolution] = []
    for ref in refs:
        library = _from_library(works, citing, ref)
        if library.state is ResolutionState.RESOLVED and library.level is not Level.LOW:
            resolutions.append(library)
            continue
        registry = _from_registries(works, cache, citing, ref, library.doi_conflict)
        resolutions.append(_better(library, registry))
    for r in resolutions:
        if r.doi_conflict is not None and r.state is ResolutionState.RESOLVED:
            logger.warning("Reference %s prints a DOI its title disagrees with", r.reference)
        if r.level is Level.LOW:
            logger.warning("Reference %s resolved at low confidence: %s", r.reference, r.evidence)
    return resolutions


def doi_key(ref: Reference) -> str | None:
    """The key a Reference's printed DOI is cached under."""
    return normalize_doi(ref.doi) if ref.doi else None


def title_key(ref: Reference) -> str | None:
    """The key, and the query sent, for a Reference's title search."""
    return normalize_title(ref.title) if ref.title and normalize_title(ref.title) else None


def bibliographic_key(ref: Reference) -> str:
    """The key a Reference's printed string is cached under for Crossref's bibliographic query."""
    return " ".join(ref.text.split()).lower()


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


def _from_registries(
    works: Works,
    cache: RegistryResponses,
    citing: WorkId,
    ref: Reference,
    conflict: WorkId | None,
) -> Resolution:
    """The cached registry layers for one Reference; Unchecked while a layer it needs is unasked."""
    unchecked = Resolution(reference=ref.id, state=ResolutionState.UNCHECKED, doi_conflict=conflict)
    if (doi := doi_key(ref)) is not None:
        crossref = cache.get(RegistrySource.CROSSREF_DOI, [doi]).get(doi)
        openalex = cache.get(RegistrySource.OPENALEX_DOI, [doi]).get(doi)
        # Crossref decides which Work a DOI is: the printed DOI wins.
        if crossref is not None and crossref.hit:
            record, layer = from_crossref(crossref.body), Layer.CROSSREF
        elif openalex is not None and openalex.hit:
            record, layer = from_openalex(openalex.body), Layer.OPENALEX
        elif crossref is None or openalex is None:
            return unchecked
        else:
            record = layer = None
        if record is not None and layer is not None:
            work = works.record_registry_work(record)
            if agrees(ref, candidate(record)) and work != citing:
                return Resolution(
                    reference=ref.id, state=ResolutionState.RESOLVED, work=work,
                    level=Level.CERTAIN, confidence=1.0, evidence=["doi"], layer=layer,
                )  # fmt: skip
            if work != citing:
                conflict = conflict or work
    bare = ref.model_copy(update={"doi": None})
    found: tuple[Match, WorkId, Layer] | None = None
    if (title := title_key(ref)) is not None:
        rows = cache.get(RegistrySource.OPENALEX_TITLE, [title]).get(title)
        if rows is None:
            return unchecked
        found = _pick(works, citing, bare, rows.body or [], Layer.OPENALEX, title=True)
    if found is None or found[0].level is Level.LOW:
        key = bibliographic_key(ref)
        rows = cache.get(RegistrySource.CROSSREF_BIBLIOGRAPHIC, [key]).get(key)
        if rows is None and found is None:
            return unchecked
        guess = _pick(works, citing, bare, rows.body or [], Layer.CROSSREF) if rows else None
        if found is None:
            found = guess
        elif guess is not None and guess[1] == found[1]:
            found[0].evidence.append("bibliographic")
    if found is None:
        return Resolution(reference=ref.id, state=ResolutionState.UNRESOLVED, doi_conflict=conflict)
    m, work, layer = found
    return Resolution(
        reference=ref.id,
        state=ResolutionState.RESOLVED,
        work=work,
        level=Level.LOW if conflict is not None else m.level,
        confidence=m.confidence,
        evidence=[*m.evidence, "doi_conflict"] if conflict is not None else m.evidence,
        layer=layer,
        doi_conflict=conflict,
    )


def _pick(
    works: Works,
    citing: WorkId,
    ref: Reference,
    rows: list[dict[str, object]],
    layer: Layer,
    *,
    title: bool = False,
) -> tuple[Match, WorkId, Layer] | None:
    """The search result the Reference names, by the title rules or Crossref's bibliographic one."""
    records = [from_openalex(r) if layer is Layer.OPENALEX else from_crossref(r) for r in rows]
    candidates = [candidate(r) for r in records]
    found = match(ref, candidates) if title else bibliographic_match(ref, candidates)
    if not isinstance(found, Match):
        return None
    work = works.record_registry_work(records[found.candidate])
    return None if work == citing else (found, work, layer)


def _better(library: Resolution, registry: Resolution) -> Resolution:
    """A low Library match stands unless a registry matched higher."""
    if library.state is not ResolutionState.RESOLVED:
        return registry
    if registry.state is ResolutionState.RESOLVED and registry.level is not Level.LOW:
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
