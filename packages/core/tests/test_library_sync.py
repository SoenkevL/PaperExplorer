"""The Library sync: every Library Item a Work, kept current from the Library's changes."""

import logging

import httpx
import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer.domain import LibraryChanges, LibraryItem
from paperexplorer.library import InMemoryLibrary, sync
from paperexplorer.store import StageStatuses, Works

Conn = psycopg.Connection[TupleRow]


def _item(key: str, title: str | None = None) -> LibraryItem:
    return LibraryItem(key=key, title=title or f"Title of {key}", year=2020)


class CountingLibrary(InMemoryLibrary):
    """An InMemoryLibrary that records the version each `changes` call asked from."""

    def __init__(self, items: list[LibraryItem]) -> None:
        super().__init__(items, {})
        self.asked: list[int | None] = []

    def changes(self, since: int | None) -> LibraryChanges:
        self.asked.append(since)
        return super().changes(since)


def test_the_first_sync_makes_every_library_item_a_work(store_db: Conn) -> None:
    library = CountingLibrary([_item("AAAA1111"), _item("BBBB2222")])

    result = sync(store_db, library)

    works = Works(store_db).list()
    assert [(w.zotero_key, w.title, w.year) for w in works] == [
        ("AAAA1111", "Title of AAAA1111", 2020),
        ("BBBB2222", "Title of BBBB2222", 2020),
    ]
    assert all(w.synced_at is not None for w in works)
    assert library.asked == [None]
    assert (result.full, result.items, result.removed) == (True, 2, 0)


def test_a_later_sync_reads_only_the_changes_since_the_last(store_db: Conn) -> None:
    library = CountingLibrary([_item("AAAA1111"), _item("BBBB2222")])
    sync(store_db, library)
    version = InMemoryLibrary.changes(library, None).version
    library.put(_item("AAAA1111", "A corrected title"))
    library.put(_item("CCCC3333"))

    result = sync(store_db, library)

    assert library.asked[-1] == version
    assert {w.zotero_key: w.title for w in Works(store_db).list()} == {
        "AAAA1111": "A corrected title",
        "BBBB2222": "Title of BBBB2222",
        "CCCC3333": "Title of CCCC3333",
    }
    assert (result.full, result.items) == (False, 2)


def test_a_deleted_library_item_goes_when_nothing_uses_its_work(store_db: Conn) -> None:
    library = CountingLibrary([_item("AAAA1111"), _item("BBBB2222")])
    sync(store_db, library)
    library.remove("BBBB2222")

    result = sync(store_db, library)

    assert [w.zotero_key for w in Works(store_db).list()] == ["AAAA1111"]
    assert result.removed == 1


def test_a_deleted_library_item_with_a_stage_run_keeps_its_work_without_one(
    store_db: Conn,
) -> None:
    library = CountingLibrary([_item("SEED1111")])
    sync(store_db, library)
    works = Works(store_db)
    seed = works.ensure("SEED1111")
    StageStatuses(store_db).start(seed, "fetch")
    library.remove("SEED1111")

    sync(store_db, library)

    kept = works.get(seed)
    assert kept is not None
    assert (kept.zotero_key, kept.title) == (None, "Title of SEED1111")


def test_a_full_sync_also_drops_works_whose_items_left_unnoticed(store_db: Conn) -> None:
    sync(store_db, InMemoryLibrary([_item("AAAA1111"), _item("STALE222")], {}))

    result = sync(store_db, CountingLibrary([_item("AAAA1111")]), full=True)

    assert [w.zotero_key for w in Works(store_db).list()] == ["AAAA1111"]
    assert (result.full, result.removed) == (True, 1)


class UnreachableLibrary(InMemoryLibrary):
    def changes(self, since: int | None) -> LibraryChanges:
        raise httpx.ConnectError("No route to host")


def test_an_unreachable_library_leaves_the_last_copy_and_warns(
    store_db: Conn, caplog: pytest.LogCaptureFixture
) -> None:
    sync(store_db, InMemoryLibrary([_item("AAAA1111")], {}))

    with caplog.at_level(logging.WARNING):
        result = sync(store_db, UnreachableLibrary([], {}))

    assert result.stale
    assert [w.zotero_key for w in Works(store_db).list()] == ["AAAA1111"]
    assert "last copy" in caplog.text
