"""The Library sync: every Library Item a Work, current as of the Library's last change."""

import logging

import httpx
import psycopg
from psycopg.rows import TupleRow
from pydantic import BaseModel

from paperexplorer.library.port import Library
from paperexplorer.store import LibraryVersions, Works

logger = logging.getLogger(__name__)


class SyncResult(BaseModel):
    """What a sync did: items recorded, Works removed, and whether it read the whole Library."""

    full: bool
    items: int
    removed: int
    stale: bool = False  # the Library was unreachable; the last copy stands


def sync(conn: psycopg.Connection[TupleRow], library: Library, *, full: bool = False) -> SyncResult:
    """Bring the Works up to date with the Library, reading only its changes after the first.

    Runs in the caller's transaction. A Library that cannot be reached leaves the last copy.
    """
    versions, works = LibraryVersions(conn), Works(conn)
    since = None if full else versions.last()
    try:
        changes = library.changes(since)
    except httpx.HTTPError as e:
        logger.warning("The Library could not be read (%s); the last copy stands", e)
        return SyncResult(full=False, items=0, removed=0, stale=True)
    for item in changes.items:
        works.record_library_item(works.ensure(item.key), item)
    gone = set(changes.deleted)
    if changes.full:
        gone |= works.zotero_keys() - {i.key for i in changes.items}
    removed = sum(works.remove_library_item(key) for key in sorted(gone))
    versions.record(changes.version)
    logger.info(
        "Library synced to version %d: %d items recorded, %d gone, %d Works removed",
        changes.version, len(changes.items), len(gone), removed,
    )  # fmt: skip
    return SyncResult(full=changes.full, items=len(changes.items), removed=removed)
