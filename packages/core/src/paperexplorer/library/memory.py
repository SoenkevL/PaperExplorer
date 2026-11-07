"""A Library held in memory, for tests."""

from collections.abc import Iterable, Mapping
from pathlib import Path

from paperexplorer.domain import Attachment, Collection, LibraryChanges, LibraryItem
from paperexplorer.library.port import ItemNotFoundError


class InMemoryLibrary:
    """Library Items given up front; PDFs are local files keyed by attachment key.

    Every change bumps the Library's version, as Zotero's does, so `changes` can say what moved.
    """

    def __init__(self, items: Iterable[LibraryItem], pdfs: Mapping[str, Path]) -> None:
        self._version = 0
        self._items: dict[str, tuple[int, LibraryItem]] = {}
        self._deleted: dict[str, int] = {}
        self._collections: dict[str, tuple[int, Collection]] = {}
        self._deleted_collections: dict[str, int] = {}
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
        """Items and Collections put and keys removed after `since`, in the order they changed."""
        if since is None:
            return LibraryChanges(
                version=self._version,
                items=[i for _, i in self._items.values()],
                collections=[c for _, c in self._collections.values()],
                full=True,
            )
        return LibraryChanges(
            version=self._version,
            items=_after(since, self._items),
            deleted=_gone_after(since, self._deleted),
            collections=_after(since, self._collections),
            deleted_collections=_gone_after(since, self._deleted_collections),
        )

    def merge(self, keep: str, other: str) -> None:
        """`keep` takes `other`'s Collections, its PDF if it has none, and replaces it."""
        kept, gone = self.item(keep), self.item(other)
        collections = kept.collections + [c for c in gone.collections if c not in kept.collections]
        self.put(
            kept.model_copy(
                update={
                    "pdf": kept.pdf or gone.pdf,
                    "replaces": [*kept.replaces, other],
                    "collections": collections,
                }
            )
        )
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

    def put_collection(self, collection: Collection) -> None:
        """Add or replace a Collection, as creating, renaming or moving it in Zotero would."""
        self._version += 1
        self._collections[collection.key] = (self._version, collection)
        self._deleted_collections.pop(collection.key, None)

    def remove_collection(self, key: str) -> None:
        """Delete a Collection and its subcollections, each key reported, as Zotero does."""
        self._version += 1
        for child in [k for k, (_, c) in self._collections.items() if c.parent_key == key]:
            self.remove_collection(child)
        del self._collections[key]
        self._deleted_collections[key] = self._version


def _after[T](since: int, entries: dict[str, tuple[int, T]]) -> list[T]:
    """The entries changed after `since`, oldest change first."""
    return [entry for v, entry in sorted(entries.values(), key=lambda e: e[0]) if v > since]


def _gone_after(since: int, gone: dict[str, int]) -> list[str]:
    """The keys removed after `since`, oldest first."""
    return [key for key, v in sorted(gone.items(), key=lambda kv: kv[1]) if v > since]
