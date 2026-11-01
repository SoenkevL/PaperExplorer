"""The registry layers as the local pass reads them: from cached responses, never the network.

A Registry Check asks the registries for these same keys, layer by layer, then runs the local
pass again; so the Check and the `resolve` Stage decide by one set of rules.
"""

from pydantic import BaseModel, JsonValue

from paperexplorer.domain import (
    Layer,
    Level,
    RegistryRecord,
    RegistrySource,
    Resolution,
    ResolutionState,
    WorkId,
    normalize_doi,
)
from paperexplorer.parsing import Reference
from paperexplorer.resolution.matching import (
    Match,
    agrees,
    bibliographic_match,
    match,
    normalize_title,
)
from paperexplorer.resolution.records import candidate, from_crossref, from_openalex
from paperexplorer.store import RegistryResponses, Works


def doi_key(ref: Reference) -> str | None:
    """The key a Reference's printed DOI is cached under."""
    return normalize_doi(ref.doi) if ref.doi else None


def title_key(ref: Reference) -> str | None:
    """The key, and the query sent, for a Reference's title search."""
    return normalize_title(ref.title) if ref.title and normalize_title(ref.title) else None


def bibliographic_query(ref: Reference) -> str:
    """The printed string as Crossref's bibliographic query is sent it."""
    return " ".join(ref.text.split())


def bibliographic_key(ref: Reference) -> str:
    """The key a Reference's bibliographic query is cached under."""
    return bibliographic_query(ref).lower()


def needs_bibliographic(ref: Reference, title_match: Resolution | None) -> bool:
    """Whether layer 3 is for this Reference: no usable title, or only a low title match."""
    return title_key(ref) is None or (title_match is not None and title_match.level is Level.LOW)


class _Found(BaseModel):
    """A search result a Reference names: how it matched, its Work, the layer that found it."""

    match: Match
    work: WorkId
    layer: Layer


def from_registries(
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
        if crossref is None or openalex is None:
            return unchecked
        record = _doi_record(crossref.response if crossref.hit else None, openalex.response)
        if record is not None:
            work = works.ensure_registry_work(record)
            layer = Layer.CROSSREF if crossref.hit else Layer.OPENALEX
            if agrees(ref, candidate(record)) and work != citing:
                return Resolution(
                    reference=ref.id, state=ResolutionState.RESOLVED, work=work,
                    level=Level.CERTAIN, confidence=1.0, evidence=["doi"], layer=layer,
                )  # fmt: skip
            if work != citing:
                conflict = conflict or work
    bare = ref.model_copy(update={"doi": None})
    found: _Found | None = None
    if (title := title_key(ref)) is not None:
        rows = cache.get(RegistrySource.OPENALEX_TITLE, [title]).get(title)
        if rows is None:
            return unchecked
        found = _pick(works, citing, bare, rows.response, Layer.OPENALEX)
    if title is None or (found is not None and found.match.level is Level.LOW):
        key = bibliographic_key(ref)
        rows = cache.get(RegistrySource.CROSSREF_BIBLIOGRAPHIC, [key]).get(key)
        if rows is None and found is None:
            return unchecked
        guess = _pick(works, citing, bare, rows.response, Layer.CROSSREF) if rows else None
        if found is None:
            found = guess
        elif guess is not None and guess.work == found.work:
            # Layer 3 can only find, never raise: agreeing, it adds to the evidence.
            evidence = [*found.match.evidence, "bibliographic"]
            found = found.model_copy(
                update={"match": found.match.model_copy(update={"evidence": evidence})}
            )
    if found is None:
        return Resolution(reference=ref.id, state=ResolutionState.UNRESOLVED, doi_conflict=conflict)
    evidence = found.match.evidence
    return Resolution(
        reference=ref.id,
        state=ResolutionState.RESOLVED,
        work=found.work,
        level=Level.LOW if conflict is not None else found.match.level,
        confidence=found.match.confidence,
        evidence=[*evidence, "doi_conflict"] if conflict is not None else evidence,
        layer=found.layer,
        doi_conflict=conflict,
    )


def _doi_record(crossref: JsonValue, openalex: JsonValue) -> RegistryRecord | None:
    """The record a DOI names: Crossref decides which Work it is, OpenAlex adds its ID."""
    from_oa = from_openalex(openalex) if isinstance(openalex, dict) else None
    if isinstance(crossref, dict):
        record = from_crossref(crossref)
        return record.model_copy(update={"openalex_id": from_oa.openalex_id if from_oa else None})
    return from_oa


def _pick(
    works: Works, citing: WorkId, ref: Reference, rows: JsonValue, layer: Layer
) -> _Found | None:
    """The search result the Reference names, by the title rules or the bibliographic one.

    OpenAlex's title search goes by the title rules, Crossref's bibliographic query by its own.
    """
    parse = from_openalex if layer is Layer.OPENALEX else from_crossref
    records = [parse(r) for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    candidates = [candidate(r) for r in records]
    found = (
        match(ref, candidates) if layer is Layer.OPENALEX else bibliographic_match(ref, candidates)
    )
    if not isinstance(found, Match):
        return None
    work = works.ensure_registry_work(records[found.candidate])
    return None if work == citing else _Found(match=found, work=work, layer=layer)
