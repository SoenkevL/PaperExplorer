"""The Library port: Library Items and their PDFs, from Zotero or, in tests, from memory."""

from paperexplorer.library.memory import InMemoryLibrary
from paperexplorer.library.port import (
    PAPEREXPLORER,
    SEED_TAG,
    ItemNotFoundError,
    Library,
    LibraryUnavailableError,
    LibraryWriteError,
    OutsidePaperExplorerError,
    new_key,
)
from paperexplorer.library.sync import (
    NotDuplicateItemsError,
    SyncResult,
    check_duplicates,
    merge,
    sync,
)
from paperexplorer.library.zotero import ZoteroLibrary

__all__ = [
    "PAPEREXPLORER",
    "SEED_TAG",
    "InMemoryLibrary",
    "ItemNotFoundError",
    "Library",
    "LibraryUnavailableError",
    "LibraryWriteError",
    "NotDuplicateItemsError",
    "OutsidePaperExplorerError",
    "SyncResult",
    "ZoteroLibrary",
    "check_duplicates",
    "merge",
    "new_key",
    "sync",
]
