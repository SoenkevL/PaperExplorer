"""Pointer Support with Ollama's bge-m3 at the provisional Support threshold, on the tower.

No chat model: the Answers are written here. The paraphrase is of a sentence of the CC BY
Villiger fixture, since the Seed's text may not be committed (its licence is unknown).
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from paperexplorer import chunking
from paperexplorer.asking import resolve_answer
from paperexplorer.config import Settings
from paperexplorer.domain import Chunk, ChunkId, Support, WorkId
from paperexplorer.embedding import OpenAICompatibleEmbedder
from paperexplorer.parsing import ParsedWork, parse
from paperexplorer.store import StoredChunk
from paperexplorer.tracing import Tracing

SEED_ATTACHMENT = "YTS2XCEG"  # Zotero attachment of the Seed, item YAAC9XCX (licence unknown)
UNRELATED = "Bake the bread for forty minutes until the crust is golden."


def _stored(chunks: list[Chunk]) -> list[StoredChunk]:
    return [StoredChunk(id=ChunkId(i), work=WorkId(1), chunk=c) for i, c in enumerate(chunks)]


@pytest.mark.tower
def test_a_paraphrase_of_a_sentence_is_close_to_it_and_an_unrelated_sentence_is_weak(
    parse_fixture: Callable[[str], ParsedWork],
) -> None:
    settings = Settings()
    embedder = OpenAICompatibleEmbedder.from_settings(settings, Tracing.off())
    read = _stored(chunking.chunk(parse_fixture("villiger2024-tandf")))
    cited = next(c for c in read if "84 ongoing clinical studies" in c.chunk.text)
    [said] = [
        s
        for s, text in zip(cited.chunk.sentences, cited.chunk.sentence_texts(), strict=True)
        if text.startswith("In February 2024, there were 84 ongoing clinical studies")
    ]

    answer = resolve_answer(
        "As of early 2024, psilocybin was being tested in 84 ongoing clinical studies, four of"
        f" them phase 3 trials. [#1] {UNRELATED} [#1]",
        {1: cited},
        read,
        embedder,
        settings.support_threshold,
    )

    [paraphrase], [unrelated] = (s.pointers for s in answer.sentences)
    assert paraphrase.support is Support.CLOSE, paraphrase
    assert (paraphrase.chunk_id, paraphrase.start, paraphrase.end) == (
        cited.id,
        said.start,
        said.end,
    )
    assert unrelated.support is Support.WEAK, unrelated
    assert unrelated.chunk_id == cited.id


@pytest.mark.tower
def test_an_unrelated_sentence_is_weak_against_every_chunk_of_the_seed() -> None:
    settings = Settings()
    if settings.zotero_storage_dir is None:
        pytest.skip("PE_ZOTERO_STORAGE_DIR is not set")
    pdf: Path = next((settings.zotero_storage_dir / SEED_ATTACHMENT).glob("*.pdf"))
    embedder = OpenAICompatibleEmbedder.from_settings(settings, Tracing.off())
    read = _stored(chunking.chunk(parse(pdf, settings)))

    answer = resolve_answer(
        f"{UNRELATED} [#1]", {1: read[0]}, read, embedder, settings.support_threshold
    )

    [pointer] = answer.sentences[0].pointers
    assert pointer.support is Support.WEAK, pointer
