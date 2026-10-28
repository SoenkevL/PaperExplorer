"""The Embedder port: Vectors for texts, from an OpenAI-compatible server or, in tests, a fake."""

from paperexplorer.domain import Vector
from paperexplorer.embedding.fake import FakeEmbedder
from paperexplorer.embedding.openai_compatible import EmbeddingError, OpenAICompatibleEmbedder
from paperexplorer.embedding.port import DIMENSIONS, Embedder

__all__ = [
    "DIMENSIONS",
    "Embedder",
    "EmbeddingError",
    "FakeEmbedder",
    "OpenAICompatibleEmbedder",
    "Vector",
]
