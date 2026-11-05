"""The Library sync: every Library Item a Work, current as of the Library's last change."""

import logging

import psycopg
from psycopg.rows import TupleRow
from pydantic import BaseModel

from paperexplorer.domain import Work
from paperexplorer.library.port import ItemNotFoundError, Library, LibraryUnavailableError
from paperexplorer.store import Collections, LibraryVersions, ReadersChoices, Resolutions, Works

logger = logging.getLogger(__name__)


class SyncResult(BaseModel):
    """What a sync did: items recorded, Works removed, and whether it read the whole Library."""

    full: bool
    items: int
    removed: int
    moved: int = 0  # Resolutions moved to the item a merged one went into
    stale: bool = False  # the Library was unreachable; the last copy stands


class NotDuplicateItemsError(ValueError):
    """The two items are not one Work's Duplicate Items: they hold different DOIs, or are one."""


def check_duplicates(keep: Work, other: Work) -> None:
    """Raise `NotDuplicateItemsError` unless these are two Library Items holding one DOI."""
    if keep.doi is None or keep.doi != other.doi or keep.id == other.id:
        raise NotDuplicateItemsError(f"{keep.zotero_key} and {other.zotero_key} do not share a DOI")


def merge(conn: psycopg.Connection[TupleRow], library: Library, keep: Work, other: Work) -> int:
    """Merge Duplicate Item `other` into `keep` in the Library, then sync; Resolutions moved.

    The caller asks the reader first: this writes to the Library, `other` to its trash.
    """
    check_duplicates(keep, other)
    assert keep.zotero_key is not None and other.zotero_key is not None
    library.merge(keep.zotero_key, other.zotero_key)
    with conn.transaction():
        moved = sync(conn, library).moved  # follows dc:replaces
    logger.info("Merged %s into %s; %d Resolutions moved", other.zotero_key, keep.zotero_key, moved)
    return moved


def sync(conn: psycopg.Connection[TupleRow], library: Library, *, full: bool = False) -> SyncResult:
    """Bring the Works up to date with the Library, reading only its changes after the first.

    Runs in the caller's transaction, which holds a lock until it ends: another sync waits for it,
    then reads only what changed after it. A Library that cannot be reached leaves the last copy.
    """
    versions, works, collections = LibraryVersions(conn), Works(conn), Collections(conn)
    versions.lock()
    since = None if full else versions.last()
    try:
        changes = library.changes(since)
    except LibraryUnavailableError as e:
        logger.warning("The Library could not be read (%s); the last copy stands", e)
        return SyncResult(full=False, items=0, removed=0, stale=True)
    for collection in changes.collections:
        collections.record(collection)
    gone_collections = set(changes.deleted_collections)
    if changes.full:
        gone_collections |= collections.keys() - {c.key for c in changes.collections}
    collections.remove(gone_collections)  # their filing goes with them
    known = collections.keys()
    for orphan in (c for c in changes.collections if c.parent_key not in known | {None}):
        logger.warning(
            "Collection %s names a parent %s the Library does not hold; it shows as a root",
            orphan.key, orphan.parent_key,
        )  # fmt: skip
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
        collections.file(work, item.collections)
        # A merge (ours or Zotero desktop's): the References and choices the other item had are
        # this one's.
        for replaced in item.replaces:
            if (old := works.find(replaced)) is not None and old.id != work:
                moved += Resolutions(conn).repoint(old.id, work)
                ReadersChoices(conn).repoint(old.id, work)
    if changes.full:
        gone |= works.zotero_keys() - items.keys()
    for key in gone:
        if (left := works.find(key)) is not None:
            collections.file(left.id, [])  # a Work kept without its Library Item is filed nowhere
    removed = sum(works.remove_library_item(key) for key in sorted(gone))
    versions.record(changes.version)
    logger.info(
        "Library synced to version %d: %d items recorded, %d gone, %d Works removed",
        changes.version, len(items), len(gone), removed,
    )  # fmt: skip
    return SyncResult(full=changes.full, items=len(items), removed=removed, moved=moved)
