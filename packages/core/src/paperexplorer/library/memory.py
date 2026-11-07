"""A Library held in memory, for tests."""

from collections.abc import Iterable, Mapping
from pathlib import Path

from paperexplorer.domain import (
    COLLECTION_DEPTH,
    Attachment,
    Collection,
    ItemDraft,
    LibraryChanges,
    LibraryItem,
)
from paperexplorer.library.port import (
    PAPEREXPLORER,
    SEED_TAG,
    ItemNotFoundError,
    OutsidePaperExplorerError,
)


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
        self.tags: dict[str, list[str]] = {}
        """Each item's tags; only `tag_seed` and `untag_seed` change them."""
        self.drafts: dict[str, ItemDraft] = {}
        """The draft each item PaperExplorer created was made from."""
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

    def create_paperexplorer_collection(self, key: str) -> None:
        """A top-level Collection named PaperExplorer, unless this key exists already."""
        if key not in self._collections:
            self.put_collection(Collection(key=key, name=PAPEREXPLORER))

    def create_collection(self, key: str, name: str, *, within: str) -> None:
        """A Collection in the PaperExplorer Collection, unless this key exists already."""
        self._check_root(within)
        if key not in self._collections:
            self.put_collection(Collection(key=key, name=name, parent_key=within))

    def create_item(self, key: str, draft: ItemDraft, *, collection: str, within: str) -> None:
        """A Library Item from the draft, filed in `collection`, unless this key exists already."""
        self._check(collection, within)
        if key in self._items:
            return
        year = draft.date[:4] if draft.date else ""
        self.drafts[key] = draft
        self.put(
            LibraryItem(
                key=key,
                title=draft.title,
                creators=draft.creators,
                year=int(year) if year.isdigit() else None,
                doi=draft.doi,
                collections=[collection],
            )
        )

    def file(self, item: str, collection: str, *, within: str) -> None:
        """The item filed in `collection` too."""
        self._check(collection, within)
        held = self.item(item)
        if collection not in held.collections:
            self.put(held.model_copy(update={"collections": [*held.collections, collection]}))

    def unfile(self, item: str, collection: str, *, within: str) -> None:
        """The item no longer filed in `collection`."""
        self._check(collection, within)
        held = self.item(item)
        if collection in held.collections:
            kept = [c for c in held.collections if c != collection]
            self.put(held.model_copy(update={"collections": kept}))

    def tag_seed(self, item: str) -> None:
        """The item tagged `pe:seed`."""
        self.item(item)
        tags = self.tags.setdefault(item, [])
        if SEED_TAG not in tags:
            tags.append(SEED_TAG)

    def untag_seed(self, item: str) -> None:
        """The item no longer tagged `pe:seed`."""
        self.item(item)
        self.tags[item] = [t for t in self.tags.get(item, []) if t != SEED_TAG]

    def _check_root(self, within: str) -> None:
        held = self._collections.get(within)
        if held is None or held[1].parent_key is not None or held[1].name != PAPEREXPLORER:
            raise OutsidePaperExplorerError(f"{within} is not the top-level {PAPEREXPLORER}")

    def _check(self, collection: str, within: str) -> None:
        """Raise unless `collection` sits under `within`, a top-level Collection."""
        self._check_root(within)
        key: str | None = collection
        for _ in range(COLLECTION_DEPTH):
            held = self._collections.get(key) if key else None
            if held is None:
                break
            key = held[1].parent_key
            if key == within:
                return
        raise OutsidePaperExplorerError(f"{collection} is not in the PaperExplorer Collection")


def _after[T](since: int, entries: dict[str, tuple[int, T]]) -> list[T]:
    """The entries changed after `since`, oldest change first."""
    return [entry for v, entry in sorted(entries.values(), key=lambda e: e[0]) if v > since]


def _gone_after(since: int, gone: dict[str, int]) -> list[str]:
    """The keys removed after `since`, oldest first."""
    return [key for key, v in sorted(gone.items(), key=lambda kv: kv[1]) if v > since]
