"""Hybrid search over a Scope's Chunks: nearest Vectors and English full text, fused by RRF."""

import logging
from enum import StrEnum

import httpx
import psycopg
from psycopg.rows import TupleRow

from paperexplorer.domain import Scope
from paperexplorer.embedding import Embedder, EmbeddingError
from paperexplorer.store import (
    Chunks,
    CitationDetail,
    Placing,
    RetrievalRun,
    RetrievalRuns,
    ScoredChunk,
)

__all__ = [
    "DEPTH",
    "EVIDENCE_K",
    "NoEvidence",
    "Placing",
    "RetrievalRun",
    "ScoredChunk",
    "find_evidence",
    "search",
]

logger = logging.getLogger(__name__)

# How deep each ranking goes before fusion, unless k asks for more. Fixed rather than tied to k,
# so a search for fewer Chunks gives the first Chunks of a search for more.
DEPTH = 50
# How many candidates a Jump offers to step through.
EVIDENCE_K = 5


def search(
    conn: psycopg.Connection[TupleRow], embedder: Embedder, text: str, scope: Scope, k: int = 8
) -> list[ScoredChunk]:
    """The `k` Chunks of the Scope that best match `text`, best first; none for an empty text.

    The text is embedded once; the Scope's Chunks are ranked by the Embedder's Vectors and by
    English full text over any of the text's stems, each ranking `max(k, DEPTH)` deep, and the
    two are fused by Reciprocal Rank Fusion (k=60). Ties go in reading order.
    """
    if not text.strip():
        return []
    [vector] = embedder.embed([text])
    found = Chunks(conn).search(scope.works, embedder.model, vector, text, k=k, depth=max(k, DEPTH))
    logger.debug("Search %r in Works %s: %d Chunks", text, sorted(scope.works), len(found))
    return found


class NoEvidence(StrEnum):
    """Why a search for a Citation's Evidence has none to show."""

    NO_CHUNKS = "no_chunks"
    NO_VECTORS = "no_vectors"
    EMBEDDER_UNREACHABLE = "embedder_unreachable"
    NO_MATCH = "no_match"


def find_evidence(
    conn: psycopg.Connection[TupleRow],
    embedder: Embedder,
    citation: CitationDetail,
    scope: Scope,
    k: int = EVIDENCE_K,
) -> RetrievalRun | NoEvidence:
    """The Run for this Citation over the Scope: a recorded one, else a new search, recorded.

    A recorded Run is read before the Embedder is asked. A search needs every Chunk of the Scope
    to have a Vector from the Embedder's model; one that cannot run, or finds nothing, says why
    and is not recorded.
    """
    runs = RetrievalRuns(conn)
    depth = max(k, DEPTH)
    recorded = runs.find(citation.id, scope, embedder.model, k=k, depth=depth)
    if recorded is not None:
        return recorded
    chunks = Chunks(conn)
    if any(not chunks.for_work(work) for work in scope.works):
        return NoEvidence.NO_CHUNKS
    if any(chunks.without_embedding(work, embedder.model) for work in scope.works):
        return NoEvidence.NO_VECTORS
    sentence = citation.citation.citing_sentence
    try:
        found = search(conn, embedder, sentence, scope, k=k)
    except (EmbeddingError, httpx.HTTPError) as e:
        logger.warning("No Evidence for Citation %d: %s", citation.id, e)
        return NoEvidence.EMBEDDER_UNREACHABLE
    if not found:
        return NoEvidence.NO_MATCH
    return runs.record(
        citation.id, sentence, scope, embedder.model, k=k, depth=depth, evidence=found
    )
