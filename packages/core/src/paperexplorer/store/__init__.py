"""Postgres access: hand-written SQL behind small repositories (ADR 0003)."""

from paperexplorer.store.chunks import ChunkEmbedding, Chunks, Placing, ScoredChunk, StoredChunk
from paperexplorer.store.citations import CitationDetail, CitationGraphs, StoredCitation
from paperexplorer.store.migrate import migrate
from paperexplorer.store.stages import StageState, StageStatus, StageStatuses
from paperexplorer.store.texts import WorkTexts
from paperexplorer.store.works import DuplicateWorkError, Works

__all__ = [
    "ChunkEmbedding",
    "Chunks",
    "CitationDetail",
    "CitationGraphs",
    "DuplicateWorkError",
    "Placing",
    "ScoredChunk",
    "StageState",
    "StageStatus",
    "StageStatuses",
    "StoredChunk",
    "StoredCitation",
    "WorkTexts",
    "Works",
    "migrate",
]
