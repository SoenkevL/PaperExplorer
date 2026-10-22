"""What the pipeline needs from a library: an item's metadata and a local copy of its PDF."""

from pathlib import Path
from typing import Protocol

from paperexplorer.domain import Attachment, LibraryItem


class ItemNotFoundError(LookupError):
    """The library has no item with this key."""


class Library(Protocol):
    """A reference library holding Library Items and their attached PDFs."""

    def item(self, key: str) -> LibraryItem:
        """The Library Item with this key; raises `ItemNotFoundError` if there is none."""
        ...

    def pdf(self, attachment: Attachment) -> Path:
        """A local path to the attached PDF, fetching it first if needed."""
        ...
