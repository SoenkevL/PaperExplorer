"""The Library sync: every Library Item a Work, kept current from the Library's changes."""

import logging

import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer.domain import Level, LibraryChanges, LibraryItem, Resolution, ResolutionState
from paperexplorer.library import InMemoryLibrary, LibraryUnavailableError, sync
from paperexplorer.parsing import ParsedWork, Reference, TextOrigin
from paperexplorer.store import ReadersChoices, Resolutions, StageStatuses, Works, WorkTexts

from .test_library import SyncingZotero, _zitem, _zpdf  # pyright: ignore[reportPrivateUsage]

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
        raise LibraryUnavailableError("No route to host")


def test_an_unreachable_library_leaves_the_last_copy_and_warns(
    store_db: Conn, caplog: pytest.LogCaptureFixture
) -> None:
    sync(store_db, InMemoryLibrary([_item("AAAA1111")], {}))

    with caplog.at_level(logging.WARNING):
        result = sync(store_db, UnreachableLibrary([], {}))

    assert result.stale
    assert [w.zotero_key for w in Works(store_db).list()] == ["AAAA1111"]
    assert "last copy" in caplog.text


def test_a_trashed_pdf_leaves_its_items_work_without_one(store_db: Conn) -> None:
    zotero = SyncingZotero(100, [_zitem("ITEM1111", 10), _zpdf("PDF11111", 11, parent="ITEM1111")])
    sync(store_db, zotero.library())
    zotero.trashed.add("PDF11111")  # its parent's version stays as it was
    zotero.touch("PDF11111")

    sync(store_db, zotero.library())

    work = Works(store_db).find("ITEM1111")
    assert work is not None and work.pdf is None


def test_a_merge_moves_the_resolutions_to_the_kept_item_and_its_work_goes(store_db: Conn) -> None:
    library = CountingLibrary([_item("SEED1111"), _item("KEEP1111"), _item("GONE2222")])
    sync(store_db, library)
    works = Works(store_db)
    seed, gone = works.ensure("SEED1111"), works.ensure("GONE2222")
    WorkTexts(store_db).replace(
        seed,
        ParsedWork(
            text_origin=TextOrigin.PRINTED,
            blocks=[],
            references=[Reference(id="b0", text="x", page=1)],
        ),
    )
    Resolutions(store_db).replace(
        seed,
        [Resolution(reference="b0", state=ResolutionState.RESOLVED, work=gone, level=Level.HIGH)],
    )
    library.merge("KEEP1111", "GONE2222")  # as Zotero desktop would: dc:replaces on the kept one

    result = sync(store_db, library)

    [moved] = Resolutions(store_db).for_work(seed)
    assert moved.work == works.ensure("KEEP1111")
    assert works.find("GONE2222") is None
    assert result.moved == 1


def test_a_merge_moves_a_readers_choice_to_the_kept_item(store_db: Conn) -> None:
    library = CountingLibrary([_item("SEED1111"), _item("KEEP1111"), _item("GONE2222")])
    sync(store_db, library)
    works = Works(store_db)
    seed, gone = works.ensure("SEED1111"), works.ensure("GONE2222")
    ReadersChoices(store_db).choose(seed, "Printed b0", gone)
    library.merge("KEEP1111", "GONE2222")

    sync(store_db, library)

    assert ReadersChoices(store_db).for_work(seed) == {"Printed b0": works.ensure("KEEP1111")}
    assert works.find("GONE2222") is None


def test_a_deleted_library_item_a_reader_chose_keeps_its_work(store_db: Conn) -> None:
    library = CountingLibrary([_item("SEED1111"), _item("GONE2222")])
    sync(store_db, library)
    works = Works(store_db)
    seed, gone = works.ensure("SEED1111"), works.ensure("GONE2222")
    ReadersChoices(store_db).choose(seed, "Printed b0", gone)
    library.remove("GONE2222")

    sync(store_db, library)

    assert ReadersChoices(store_db).for_work(seed) == {"Printed b0": gone}
    kept = works.get(gone)
    assert kept is not None and kept.zotero_key is None
