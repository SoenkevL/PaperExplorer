"""Printed DOIs verified at the DOI registry, each asked once, the answers kept (ADR 0012)."""

import logging
from collections.abc import Sequence

import psycopg
from psycopg.rows import TupleRow

from paperexplorer.domain import normalize_doi
from paperexplorer.parsing import Reference
from paperexplorer.resolution.registration import DoiRegistry, Registration
from paperexplorer.store import RegistryResponses

logger = logging.getLogger(__name__)

SOURCE = "doi:handle"


def verify(
    conn: psycopg.Connection[TupleRow], registry: DoiRegistry, refs: Sequence[Reference]
) -> dict[str, int]:
    """Ask the registry about each printed DOI not cached yet; count DOIs by Registration.

    A DOI the registry did not answer keeps its cached verdict, if any, else is unverified.
    """
    responses = RegistryResponses(conn)
    dois = sorted({normalize_doi(r.doi) for r in refs if r.doi})
    fresh = responses.fresh(SOURCE, dois)
    asked = [d for d in dois if d not in fresh]
    records = registry.records(asked) if asked else {}
    for doi, record in records.items():
        if record.registration is not Registration.UNVERIFIED:
            hit = record.registration is Registration.REGISTERED
            responses.record(SOURCE, doi, record.model_dump(mode="json"), hit=hit)
    hits = responses.hits(SOURCE, dois)
    registered = sum(hits.get(d) is True for d in dois)
    unregistered = sum(hits.get(d) is False for d in dois)
    counts = {
        "dois": len(dois),
        "registered": registered,
        "unregistered": unregistered,
        "unverified": len(dois) - registered - unregistered,
    }
    if counts["unverified"]:
        logger.warning(
            "%d DOIs unverified, doi.org unreachable; rerun verify_dois", counts["unverified"]
        )
    logger.info("Verified %d DOIs asking %d: %s", len(dois), len(asked), counts)
    return counts


def registration(conn: psycopg.Connection[TupleRow], doi: str) -> Registration:
    """What the registry last said of this DOI; `unverified` when it was never answered."""
    hit = RegistryResponses(conn).hits(SOURCE, [normalize_doi(doi)]).get(normalize_doi(doi))
    if hit is None:
        return Registration.UNVERIFIED
    return Registration.REGISTERED if hit else Registration.UNREGISTERED


def unregistered(conn: psycopg.Connection[TupleRow], dois: Sequence[str]) -> set[str]:
    """The DOIs, normalized, that the registry last said are not registered."""
    hits = RegistryResponses(conn).hits(SOURCE, [normalize_doi(d) for d in dois])
    return {doi for doi, hit in hits.items() if not hit}
