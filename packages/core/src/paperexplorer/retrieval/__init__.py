"""Hybrid search over a Scope's Chunks: nearest Vectors and English full text, fused by RRF."""

import logging

import psycopg
from psycopg.rows import TupleRow

from paperexplorer.domain import Scope
from paperexplorer.embedding import Embedder
from paperexplorer.store import Chunks, Placing, ScoredChunk

__all__ = ["DEPTH", "Placing", "ScoredChunk", "search"]

logger = logging.getLogger(__name__)

# How deep each ranking goes before fusion, unless k asks for more. Fixed rather than tied to k,
# so a search for fewer Chunks gives the first Chunks of a search for more.
DEPTH = 50


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
