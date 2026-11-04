"""The real Embedder: the Provider's bge-m3, through its OpenAI-compatible endpoint."""

import math

import pytest

from paperexplorer.config import Settings
from paperexplorer.domain import Vector
from paperexplorer.embedding import DIMENSIONS
from paperexplorer.inference import embedder as provider_embedder
from paperexplorer.tracing import Tracing


def cosine(a: Vector, b: Vector) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


@pytest.mark.tower
def test_bge_m3_gives_1024_dimensions_and_puts_like_meanings_closer() -> None:
    settings = Settings(embedding_batch_size=2)
    embedder = provider_embedder(settings, Tracing.off())

    question, answer, paraphrase, unrelated = embedder.embed(
        [
            "How did the study measure brain entropy?",
            "Entropy was computed from the EEG signal of each participant.",
            "Each participant's EEG recording was used to quantify neural entropy.",
            "Bake the bread for forty minutes until the crust is golden.",
        ]
    )

    assert embedder.model.endswith("bge-m3")
    assert {len(v) for v in (question, answer, paraphrase, unrelated)} == {DIMENSIONS}
    assert cosine(answer, paraphrase) > cosine(answer, question) > cosine(answer, unrelated)
    assert cosine(question, answer) > cosine(question, unrelated)
