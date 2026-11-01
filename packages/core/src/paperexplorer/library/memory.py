"""A Library held in memory, for tests."""

from collections.abc import Iterable, Mapping
from pathlib import Path

from paperexplorer.domain import Attachment, LibraryChanges, LibraryItem
from paperexplorer.library.port import ItemNotFoundError


class InMemoryLibrary:
    """Library Items given up front; PDFs are local files keyed by attachment key.

    Every change bumps the Library's version, as Zotero's does, so `changes` can say what moved.
    """

    def __init__(self, items: Iterable[LibraryItem], pdfs: Mapping[str, Path]) -> None:
        self._version = 0
        self._items: dict[str, tuple[int, LibraryItem]] = {}
        self._deleted: dict[str, int] = {}
        self._pdfs = dict(pdfs)
        for item in items:
            self.put(item)

    def item(self, key: str) -> LibraryItem:
        """The Library Item with this key."""
        try:
            return self._items[key][1]
        except KeyError:
            raise ItemNotFoundError(f"No Library Item {key}") from None

    def pdf(self, attachment: Attachment) -> Path:
        """The PDF file given for this attachment."""
        return self._pdfs[attachment.key]

    def changes(self, since: int | None) -> LibraryChanges:
        """Items put and keys removed after `since`, in the order they last changed."""
        if since is None:
            return LibraryChanges(
                version=self._version, items=[i for _, i in self._items.values()], full=True
            )
        changed = sorted((v, i.key) for v, i in self._items.values() if v > since)
        return LibraryChanges(
            version=self._version,
            items=[self._items[key][1] for _, key in changed],
            deleted=[
                key for key, v in sorted(self._deleted.items(), key=lambda kv: kv[1]) if v > since
            ],
        )

    def merge(self, keep: str, other: str) -> None:
        """`keep` takes `other`'s PDF if it has none of its own; `other` is removed."""
        kept, gone = self.item(keep), self.item(other)
        self.put(kept.model_copy(update={"pdf": kept.pdf or gone.pdf}))
        self.remove(other)

    def put(self, item: LibraryItem) -> None:
        """Add or replace a Library Item, as an edit in Zotero would."""
        self._version += 1
        self._items[item.key] = (self._version, item)
        self._deleted.pop(item.key, None)

    def remove(self, key: str) -> None:
        """Delete a Library Item, as moving it to Zotero's trash would."""
        self._version += 1
        del self._items[key]
        self._deleted[key] = self._version
