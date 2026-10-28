"""What retrieval and asking need from an embedding model: one Vector per text, in order."""

from collections.abc import Sequence
from typing import Protocol

from paperexplorer.domain import Vector

# bge-m3's dimensions; the store's embedding column holds Vectors this long.
DIMENSIONS = 1024


class Embedder(Protocol):
    """An embedding model; its name is stored with every Vector it gives."""

    @property
    def model(self) -> str:
        """The model's name, e.g. `bge-m3`."""
        ...

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        """One Vector per text, in the order given."""
        ...
