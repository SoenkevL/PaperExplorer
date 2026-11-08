"""Hybrid search over a Scope's Chunks: nearest Vectors and English full text, fused by RRF."""

import logging
from enum import StrEnum

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
    found = Chunks(conn).search(scope.works, embedder.model, vector, text, k=k, depth=_depth(k))
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
    """The Run for this Citation over the Scope, recorded or new, or why there can be none."""
    runs = RetrievalRuns(conn)
    chunks = Chunks(conn)
    model = embedder.model
    depth = _depth(k)
    recorded = runs.find(citation.id, scope, model, k=k, depth=depth)
    if recorded is not None:
        return recorded
    sentence = citation.citation.citing_sentence
    if (reason := _unsearchable(chunks, scope, model)) is not None:
        return _none(citation, reason)
    if not sentence.strip():
        return _none(citation, NoEvidence.NO_MATCH)
    try:
        [vector] = embedder.embed([sentence])
    except EmbeddingError as e:
        logger.warning("Citation %d: the Embedder failed: %s", citation.id, e)
        return NoEvidence.EMBEDDER_UNREACHABLE
    # The Embedder may take a while, and Chunks replaced meanwhile would rank on words alone. So
    # they are looked at again, and held until the Run is recorded.
    with conn.transaction():
        chunks.hold(scope.works)
        if (reason := _unsearchable(chunks, scope, model)) is not None:
            return _none(citation, reason)
        found = chunks.search(scope.works, model, vector, sentence, k=k, depth=depth)
        if not found:
            return _none(citation, NoEvidence.NO_MATCH)
        return runs.record(citation.id, sentence, scope, model, k=k, depth=depth, evidence=found)


def _depth(k: int) -> int:
    return max(k, DEPTH)


def _unsearchable(chunks: Chunks, scope: Scope, model: str) -> NoEvidence | None:
    if any(not chunks.for_work(work) for work in scope.works):
        return NoEvidence.NO_CHUNKS
    if any(chunks.without_embedding(work, model) for work in scope.works):
        return NoEvidence.NO_VECTORS
    return None


def _none(citation: CitationDetail, reason: NoEvidence) -> NoEvidence:
    logger.warning("Citation %d has no Evidence: %s", citation.id, reason.value)
    return reason
