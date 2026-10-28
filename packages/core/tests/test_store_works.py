import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer.domain import Attachment, LibraryItem, WorkId
from paperexplorer.store import DuplicateWorkError, Works

Conn = psycopg.Connection[TupleRow]


def test_ensuring_a_library_item_twice_gives_the_same_work(store_db: Conn) -> None:
    works = Works(store_db)

    first = works.ensure("YAAC9XCX")

    assert works.ensure("YAAC9XCX") == first
    assert works.ensure("OTHERKEY") != first


VILLIGER = LibraryItem(
    key="VILL2024",
    title="Personal psychedelic experience of psychedelic therapists during training",
    creators=["Villiger, Daniel"],
    year=2024,
    doi="10.1080/09540261.2024.2357669",
    pdf=Attachment(key="ATTVILL1", filename="Villiger - 2024.pdf"),
)


def test_a_work_holds_its_library_items_metadata_snapshot(store_db: Conn) -> None:
    works = Works(store_db)
    work = works.ensure("VILL2024")

    works.record_library_item(work, VILLIGER)

    stored = works.get(work)
    assert stored is not None
    assert stored.id == work
    assert stored.zotero_key == "VILL2024"
    assert stored.title == VILLIGER.title
    assert stored.creators == ["Villiger, Daniel"]
    assert stored.year == 2024
    assert stored.doi == "10.1080/09540261.2024.2357669"
    assert stored.pdf == Attachment(key="ATTVILL1", filename="Villiger - 2024.pdf")
    assert stored.synced_at is not None
    assert stored.text_origin is None  # not parsed yet


def test_a_doi_is_stored_lowercased_since_dois_ignore_case(store_db: Conn) -> None:
    works = Works(store_db)
    work = works.ensure("WEGN2023")

    works.record_library_item(
        work, VILLIGER.model_copy(update={"key": "WEGN2023", "doi": "10.21203/RS.3.RS-2878411/V1"})
    )

    stored = works.get(work)
    assert stored is not None
    assert stored.doi == "10.21203/rs.3.rs-2878411/v1"


def test_an_unknown_work_is_none(store_db: Conn) -> None:
    assert Works(store_db).get(WorkId(-1)) is None
    assert Works(store_db).find("NOSUCHKY") is None


def test_a_work_is_found_by_its_zotero_key_without_creating_one(store_db: Conn) -> None:
    works = Works(store_db)
    work = works.ensure("VILL2024")

    found = works.find("VILL2024")

    assert found is not None
    assert found.id == work
    assert works.find("VILL2025") is None
    assert [w.id for w in works.list()] == [work]


def test_a_doi_belongs_to_one_work(store_db: Conn) -> None:
    works = Works(store_db)
    works.record_library_item(works.ensure("VILL2024"), VILLIGER)
    duplicate = works.ensure("VILLCOPY")

    with pytest.raises(DuplicateWorkError, match="VILL2024"):
        works.record_library_item(duplicate, VILLIGER.model_copy(update={"key": "VILLCOPY"}))


def test_works_are_listed_in_the_order_they_were_first_seen(store_db: Conn) -> None:
    works = Works(store_db)
    first, second = works.ensure("FIRST"), works.ensure("SECOND")

    assert [w.id for w in works.list()] == [first, second]
