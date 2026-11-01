"""The Library sync: every Library Item a Work, current as of the Library's last change."""

import logging

import psycopg
from psycopg.rows import TupleRow
from pydantic import BaseModel

from paperexplorer.library.port import ItemNotFoundError, Library, LibraryUnavailableError
from paperexplorer.store import LibraryVersions, Resolutions, Works

logger = logging.getLogger(__name__)


class SyncResult(BaseModel):
    """What a sync did: items recorded, Works removed, and whether it read the whole Library."""

    full: bool
    items: int
    removed: int
    moved: int = 0  # Resolutions moved to the item a merged one went into
    stale: bool = False  # the Library was unreachable; the last copy stands


def sync(conn: psycopg.Connection[TupleRow], library: Library, *, full: bool = False) -> SyncResult:
    """Bring the Works up to date with the Library, reading only its changes after the first.

    Runs in the caller's transaction. A Library that cannot be reached leaves the last copy.
    """
    versions, works = LibraryVersions(conn), Works(conn)
    since = None if full else versions.last()
    try:
        changes = library.changes(since)
    except LibraryUnavailableError as e:
        logger.warning("The Library could not be read (%s); the last copy stands", e)
        return SyncResult(full=False, items=0, removed=0, stale=True)
    items = {i.key: i for i in changes.items}
    gone = set(changes.deleted)
    # A trashed or deleted PDF arrives as its own key; its parent's version stays as it was.
    for work in works.with_pdf_attachment(gone):
        if work.zotero_key is not None and work.zotero_key not in items.keys() | gone:
            try:
                items[work.zotero_key] = library.item(work.zotero_key)
            except ItemNotFoundError:
                gone.add(work.zotero_key)
    moved = 0
    for item in items.values():
        work = works.ensure(item.key)
        works.record_library_item(work, item)
        # A merge (ours or Zotero desktop's): the References the other item had are this one's.
        for replaced in item.replaces:
            if (old := works.find(replaced)) is not None and old.id != work:
                moved += Resolutions(conn).repoint(old.id, work)
    if changes.full:
        gone |= works.zotero_keys() - items.keys()
    removed = sum(works.remove_library_item(key) for key in sorted(gone))
    versions.record(changes.version)
    logger.info(
        "Library synced to version %d: %d items recorded, %d gone, %d Works removed",
        changes.version, len(items), len(gone), removed,
    )  # fmt: skip
    return SyncResult(full=changes.full, items=len(items), removed=removed, moved=moved)
