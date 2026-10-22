"""A Library held in memory, for tests."""

from collections.abc import Iterable, Mapping
from pathlib import Path

from paperexplorer.domain import Attachment, LibraryItem
from paperexplorer.library.port import ItemNotFoundError


class InMemoryLibrary:
    """Library Items given up front; PDFs are local files keyed by attachment key."""

    def __init__(self, items: Iterable[LibraryItem], pdfs: Mapping[str, Path]) -> None:
        self._items = {i.key: i for i in items}
        self._pdfs = dict(pdfs)

    def item(self, key: str) -> LibraryItem:
        """The Library Item with this key."""
        try:
            return self._items[key]
        except KeyError:
            raise ItemNotFoundError(f"No Library Item {key}") from None

    def pdf(self, attachment: Attachment) -> Path:
        """The PDF file given for this attachment."""
        return self._pdfs[attachment.key]
