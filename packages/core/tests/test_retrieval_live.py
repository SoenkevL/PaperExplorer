"""Hybrid search over the Seed with Ollama's bge-m3, on the tower."""

import re
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer import chunking, retrieval
from paperexplorer.config import Settings
from paperexplorer.domain import Scope
from paperexplorer.embedding import OpenAICompatibleEmbedder
from paperexplorer.parsing import parse
from paperexplorer.store import Chunks, Works, WorkTexts
from paperexplorer.tracing import Tracing

SEED_ATTACHMENT = "YTS2XCEG"  # Zotero attachment of the Seed, item YAAC9XCX (licence unknown)


def _seed_pdf(settings: Settings) -> Path:
    if settings.zotero_storage_dir is None:
        pytest.skip("PE_ZOTERO_STORAGE_DIR is not set")
    return next((settings.zotero_storage_dir / SEED_ATTACHMENT).glob("*.pdf"))


@pytest.mark.tower
def test_searching_the_seed_for_a_headings_words_finds_chunks_under_that_heading(
    store_db: psycopg.Connection[TupleRow],
) -> None:
    settings = Settings()
    parsed = parse(_seed_pdf(settings), settings)
    work = Works(store_db).ensure("YAAC9XCX")
    WorkTexts(store_db).replace(work, parsed)
    chunks = Chunks(store_db)
    chunks.replace(work, chunking.chunk(parsed))
    stored = chunks.for_work(work)
    embedder = OpenAICompatibleEmbedder.from_settings(settings, Tracing.off())
    vectors = embedder.embed([s.chunk.text for s in stored])
    chunks.record_embeddings(
        work, embedder.model, {s.id: v for s, v in zip(stored, vectors, strict=True)}
    )
    paths = sorted({tuple(s.chunk.heading_path) for s in stored if s.chunk.heading_path})

    # For each Heading Path: the rank of the first of the 8 best Chunks that sits under it.
    ranks: dict[str, int | None] = {}
    for path in paths:
        words = re.sub(r"^[\dIVX.]+\s+", "", path[-1])  # "2.3 EEG" searches for "EEG"
        found = retrieval.search(store_db, embedder, words, Scope.of(work), k=8)
        under = (
            i for i, r in enumerate(found, 1) if tuple(r.chunk.heading_path[: len(path)]) == path
        )
        ranks[" > ".join(path)] = next(under, None)

    assert len(ranks) >= 5  # the Seed has 19 headings
    assert sum(rank is not None for rank in ranks.values()) >= 0.75 * len(ranks), ranks
