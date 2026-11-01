"""What the pipeline needs from a library: its items, their changes, PDFs, and merging two."""

from pathlib import Path
from typing import Protocol

from paperexplorer.domain import Attachment, LibraryChanges, LibraryItem


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

    def changes(self, since: int | None) -> LibraryChanges:
        """The Library Items changed and the keys deleted since a version; `None` reads it all."""
        ...

    def merge(self, keep: str, other: str) -> None:
        """Merge `other` into `keep`, as Zotero desktop does, and move `other` to the trash."""
        ...
