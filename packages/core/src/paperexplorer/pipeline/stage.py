"""The shape every Stage shares: a function of a context and a Work, returning what it counted."""

from collections.abc import Callable
from dataclasses import dataclass

import psycopg
from psycopg.rows import TupleRow
from pydantic import BaseModel

from paperexplorer.config import Settings
from paperexplorer.domain import WorkId
from paperexplorer.embedding import Embedder
from paperexplorer.library import Library
from paperexplorer.resolution import DoiRegistry
from paperexplorer.tracing import Tracing


@dataclass(frozen=True)
class StageContext:
    """What a Stage works with: store, settings, Library, Embedder, tracing, DOI registry."""

    conn: psycopg.Connection[TupleRow]
    settings: Settings
    library: Library
    embedder: Embedder
    tracing: Tracing
    doi_registry: DoiRegistry


class StageResult(BaseModel):
    """What a Stage did, as counts, e.g. `{"blocks": 113}`."""

    counts: dict[str, int]


class StageError(Exception):
    """A Stage cannot run for this Work; the message says why and what to run first."""


type Stage = Callable[[StageContext, WorkId], StageResult]
