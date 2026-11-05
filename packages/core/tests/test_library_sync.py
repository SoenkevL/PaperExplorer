"""The Library sync: every Library Item a Work, kept current from the Library's changes."""

import logging

import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer.domain import (
    Collection,
    Level,
    LibraryChanges,
    LibraryItem,
    Resolution,
    ResolutionState,
)
from paperexplorer.library import InMemoryLibrary, LibraryUnavailableError, sync
from paperexplorer.parsing import ParsedWork, Reference, TextOrigin
from paperexplorer.store import (
    Collections,
    ReadersChoices,
    Resolutions,
    StageStatuses,
    Works,
    WorkTexts,
    migrate,
)

from .test_library import (  # pyright: ignore[reportPrivateUsage]
    SyncingZotero,
    _json,
    _zitem,
    _zpdf,
)

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


def _filed(key: str, *collections: str) -> LibraryItem:
    return LibraryItem(key=key, title=f"Title of {key}", collections=list(collections))


def _tree() -> CountingLibrary:
    """Psychedelics > Therapy > Training, and Methods; one item in two Collections."""
    library = CountingLibrary(
        [_filed("BOTH1111", "THERAPY2", "METHODS4"), _filed("DEEP2222", "TRAINING"),
         _filed("NONE3333")]
    )  # fmt: skip
    library.put_collection(Collection(key="TRAINING", name="Training", parent_key="THERAPY2"))
    library.put_collection(Collection(key="THERAPY2", name="Therapy", parent_key="PSYCHED1"))
    library.put_collection(Collection(key="PSYCHED1", name="Psychedelics"))  # parent listed last
    library.put_collection(Collection(key="METHODS4", name="Methods"))
    return library


def _filing(conn: Conn) -> dict[str, list[str]]:
    works, collections = Works(conn), Collections(conn)
    return {w.zotero_key: collections.of_work(w.id) for w in works.list() if w.zotero_key}


def test_a_sync_stores_every_collection_with_its_parent_and_each_works_collections(
    store_db: Conn,
) -> None:
    sync(store_db, _tree())

    assert Collections(store_db).list() == [
        Collection(key="METHODS4", name="Methods"),
        Collection(key="PSYCHED1", name="Psychedelics"),
        Collection(key="THERAPY2", name="Therapy", parent_key="PSYCHED1"),
        Collection(key="TRAINING", name="Training", parent_key="THERAPY2"),
    ]
    assert _filing(store_db) == {
        "BOTH1111": ["METHODS4", "THERAPY2"],
        "DEEP2222": ["TRAINING"],
        "NONE3333": [],
    }


def test_a_later_sync_records_collections_changed_and_refiles_changed_items(
    store_db: Conn,
) -> None:
    library = _tree()
    sync(store_db, library)
    library.put_collection(Collection(key="METHODS4", name="Methods and data"))
    library.put(_filed("BOTH1111", "TRAINING"))
    library.put(_filed("DEEP2222"))  # its last Collection taken away

    sync(store_db, library)

    assert Collection(key="METHODS4", name="Methods and data") in Collections(store_db).list()
    assert _filing(store_db) == {"BOTH1111": ["TRAINING"], "DEEP2222": [], "NONE3333": []}
    assert library.asked[-1] is not None


def test_a_collection_deleted_in_zotero_goes_with_its_subcollections_and_their_filing(
    store_db: Conn,
) -> None:
    library = _tree()
    sync(store_db, library)

    library.remove_collection("PSYCHED1")
    sync(store_db, library)

    assert [c.key for c in Collections(store_db).list()] == ["METHODS4"]
    assert _filing(store_db) == {"BOTH1111": ["METHODS4"], "DEEP2222": [], "NONE3333": []}
    assert [w.zotero_key for w in Works(store_db).list()] == ["BOTH1111", "DEEP2222", "NONE3333"]


def test_a_full_sync_drops_collections_it_does_not_list(store_db: Conn) -> None:
    library = _tree()
    sync(store_db, library)
    stale = InMemoryLibrary([_filed("BOTH1111", "METHODS4")], {})
    stale.put_collection(Collection(key="METHODS4", name="Methods"))

    sync(store_db, stale, full=True)

    assert [c.key for c in Collections(store_db).list()] == ["METHODS4"]
    assert _filing(store_db) == {"BOTH1111": ["METHODS4"]}


def test_a_parent_that_never_arrives_leaves_its_child_stored_and_warns(
    store_db: Conn, caplog: pytest.LogCaptureFixture
) -> None:
    library = CountingLibrary([_filed("AAAA1111", "ORPHAN11")])
    library.put_collection(Collection(key="ORPHAN11", name="Orphan", parent_key="MISSING1"))

    with caplog.at_level(logging.WARNING):
        sync(store_db, library)

    assert Collections(store_db).list() == [
        Collection(key="ORPHAN11", name="Orphan", parent_key="MISSING1")
    ]
    assert _filing(store_db) == {"AAAA1111": ["ORPHAN11"]}
    assert "MISSING1" in caplog.text


def test_an_item_naming_a_collection_the_store_lacks_is_filed_only_where_it_can_be(
    store_db: Conn,
) -> None:
    library = CountingLibrary([_filed("AAAA1111", "METHODS4", "UNKNOWN1")])
    library.put_collection(Collection(key="METHODS4", name="Methods"))

    sync(store_db, library)

    assert _filing(store_db) == {"AAAA1111": ["METHODS4"]}


def test_a_deleted_item_whose_work_stays_is_filed_nowhere(store_db: Conn) -> None:
    library = _tree()
    sync(store_db, library)
    seed = Works(store_db).ensure("DEEP2222")
    StageStatuses(store_db).start(seed, "fetch")
    library.remove("DEEP2222")

    sync(store_db, library)

    assert Collections(store_db).of_work(seed) == []


def test_a_collection_trashed_in_zotero_goes_with_the_subcollections_zotero_leaves_unmarked(
    store_db: Conn,
) -> None:
    zotero = SyncingZotero(10, [_zitem("DEEP2222", 5, collections=["TRAINING"])])
    zotero.put_collections(_json("collections.json")[:3])  # Psychedelics > Therapy > Training
    library = zotero.library()
    sync(store_db, library)

    zotero.trash_collection("THERAPY2")
    sync(store_db, library)

    assert [c.key for c in Collections(store_db).list()] == ["PSYCHED1"]
    assert _filing(store_db) == {"DEEP2222": []}


def test_a_collection_deleted_with_only_its_own_key_reported_takes_its_subcollections(
    store_db: Conn,
) -> None:
    zotero = SyncingZotero(10, [])
    zotero.put_collections(_json("collections.json")[:3])
    library = zotero.library()
    sync(store_db, library)

    for key in ("THERAPY2", "TRAINING"):  # Zotero's delete through the API logs only THERAPY2
        del zotero.collections[key]
    zotero.version += 1
    zotero.deleted_collections["THERAPY2"] = zotero.version
    sync(store_db, library)

    assert [c.key for c in Collections(store_db).list()] == ["PSYCHED1"]


def test_a_collection_restored_from_zoteros_trash_comes_back_with_its_branch_and_filing(
    store_db: Conn,
) -> None:
    zotero = SyncingZotero(10, [_zitem("DEEP2222", 5, collections=["TRAINING", "THERAPY2"])])
    zotero.put_collections(_json("collections.json")[:3])  # Psychedelics > Therapy > Training
    library = zotero.library()
    sync(store_db, library)
    zotero.trash_collection("PSYCHED1")
    sync(store_db, library)

    zotero.restore_collection("PSYCHED1")  # nothing under it changes version
    result = sync(store_db, library)

    assert [c.key for c in Collections(store_db).list()] == ["PSYCHED1", "THERAPY2", "TRAINING"]
    assert _filing(store_db) == {"DEEP2222": ["THERAPY2", "TRAINING"]}
    assert result.full  # a Collection the store did not hold: read it all once


def test_a_subcollection_edited_while_its_parent_is_in_the_trash_stays_gone(
    store_db: Conn,
) -> None:
    zotero = SyncingZotero(10, [])
    zotero.put_collections(_json("collections.json")[:3])
    library = zotero.library()
    sync(store_db, library)
    zotero.trash_collection("THERAPY2")
    sync(store_db, library)

    zotero.version += 1
    zotero.collections["TRAINING"]["version"] = zotero.version
    zotero.collections["TRAINING"]["data"]["name"] = "Training, renamed"
    sync(store_db, library)

    assert [c.key for c in Collections(store_db).list()] == ["PSYCHED1"]


def test_the_first_sync_after_the_collections_migration_reads_the_whole_library(
    store_db: Conn,
) -> None:
    library = CountingLibrary([_filed("AAAA1111", "METHODS4")])
    library.put_collection(Collection(key="METHODS4", name="Methods"))
    sync(store_db, library)
    store_db.execute("DROP TABLE work_collections, collections")  # as a store before 0009
    store_db.execute("DELETE FROM schema_migrations WHERE name = '0009_collections.sql'")

    migrate(store_db)
    result = sync(store_db, library)

    assert result.full and library.asked[-1] is None
    assert _filing(store_db) == {"AAAA1111": ["METHODS4"]}
