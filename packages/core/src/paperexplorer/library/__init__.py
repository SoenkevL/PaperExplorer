"""The Library port: Library Items and their PDFs, from Zotero or, in tests, from memory."""

from paperexplorer.library.memory import InMemoryLibrary
from paperexplorer.library.port import ItemNotFoundError, Library, LibraryUnavailableError
from paperexplorer.library.sync import (
    NotDuplicateItemsError,
    SyncResult,
    check_duplicates,
    merge,
    sync,
)
from paperexplorer.library.zotero import ZoteroLibrary

__all__ = [
    "InMemoryLibrary",
    "ItemNotFoundError",
    "Library",
    "LibraryUnavailableError",
    "NotDuplicateItemsError",
    "SyncResult",
    "ZoteroLibrary",
    "check_duplicates",
    "merge",
    "sync",
]
