"""What the pipeline needs from a library: its items, their changes, PDFs, and merging two."""

import secrets
from pathlib import Path
from typing import Protocol

from paperexplorer.domain import Attachment, ItemDraft, LibraryChanges, LibraryItem


class ItemNotFoundError(LookupError):
    """The library has no item with this key."""


class LibraryUnavailableError(Exception):
    """The library cannot be read now (unreachable, failing, or answering out of protocol)."""


class OutsidePaperExplorerError(ValueError):
    """A write named a Collection outside the PaperExplorer Collection's branch (ADR 0014)."""


class LibraryWriteError(Exception):
    """The library refused a write, or could not be reached to make it."""


PAPEREXPLORER = "PaperExplorer"  # the name of the PaperExplorer Collection
SEED_TAG = "pe:seed"


class Library(Protocol):
    """A reference library holding Library Items and their attached PDFs."""

    def item(self, key: str) -> LibraryItem:
        """The Library Item with this key; raises `ItemNotFoundError` if there is none."""
        ...

    def pdf(self, attachment: Attachment) -> Path:
        """A local path to the attached PDF, fetching it first if needed."""
        ...

    def changes(self, since: int | None) -> LibraryChanges:
        """The Library Items changed and the keys deleted since a version; `None` reads it all.

        Raises `LibraryUnavailableError` when the library cannot be read.
        """
        ...

    def merge(self, keep: str, other: str) -> None:
        """Merge `other` into `keep`, as Zotero desktop does, and move `other` to the trash."""
        ...

    # The writes below are the only ones PaperExplorer makes unasked (ADR 0014). Each create
    # takes a key made by `new_key` and runs once: a second create of that key does nothing.
    # Each write naming a Collection names the PaperExplorer Collection too (`within`) and raises
    # `OutsidePaperExplorerError` before writing unless that is a top-level Collection named
    # PaperExplorer and the target's ancestor.

    def create_paperexplorer_collection(self, key: str) -> None:
        """Create the top-level PaperExplorer Collection with this key."""
        ...

    def create_collection(self, key: str, name: str, *, within: str) -> None:
        """Create a Collection with this key and name in the PaperExplorer Collection."""
        ...

    def create_item(self, key: str, draft: ItemDraft, *, collection: str, within: str) -> None:
        """Create a Library Item with this key from the draft, filed in `collection` alone."""
        ...

    def file(self, item: str, collection: str, *, within: str) -> None:
        """Add `collection` to the item's Collections, its others kept."""
        ...

    def unfile(self, item: str, collection: str, *, within: str) -> None:
        """Take `collection` from the item's Collections, its others kept."""
        ...

    def tag_seed(self, item: str) -> None:
        """Add the `pe:seed` tag to the item, its other tags kept."""
        ...

    def untag_seed(self, item: str) -> None:
        """Take the `pe:seed` tag from the item, its other tags kept."""
        ...


_KEY_CHARS = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ"  # Zotero's key alphabet


def new_key() -> str:
    """A fresh Zotero object key, made here so a create can be repeated safely."""
    return "".join(secrets.choice(_KEY_CHARS) for _ in range(8))
