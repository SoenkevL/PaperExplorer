"""A deterministic Embedder for tests: hashed bag of words, no model."""

import hashlib
import math
import re
from collections.abc import Sequence

from paperexplorer.domain import Vector
from paperexplorer.embedding.port import DIMENSIONS

# Words every English sentence shares; left out so that only content words make texts close.
STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "in", "is", "it",
    "its", "of", "on", "or", "that", "the", "this", "to", "was", "were", "with",
})  # fmt: skip


class FakeEmbedder:
    """Each distinct content word sets one hashed dimension; Vectors have unit length.

    The cosine of two texts is the number of content words they share over the square root of
    the product of their counts of distinct content words: texts that share no word score 0,
    the same words score 1, whatever their order or case. Words are runs of letters and digits,
    case-folded; `STOP_WORDS` are left out. Two words share a dimension about once in 1024 pairs,
    and then count as one. A text with no content word gets one fixed Vector.
    """

    def __init__(self, model: str = "fake-bag-of-words", dimensions: int = DIMENSIONS) -> None:
        self._model = model
        self._dimensions = dimensions
        self.calls: list[list[str]] = []
        """The texts of every `embed` call, in order."""

    @property
    def model(self) -> str:
        """The name stored with this fake's Vectors."""
        return self._model

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        """One unit Vector per text, from its content words alone."""
        self.calls.append(list(texts))
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> Vector:
        words = set(re.findall(r"\w+", text.casefold())) - STOP_WORDS
        dimensions = {self._dimension(w) for w in words} or {self._dimension("")}
        weight = 1 / math.sqrt(len(dimensions))
        return [weight if d in dimensions else 0.0 for d in range(self._dimensions)]

    def _dimension(self, word: str) -> int:
        digest = hashlib.blake2b(word.encode(), digest_size=8).digest()
        return int.from_bytes(digest) % self._dimensions
