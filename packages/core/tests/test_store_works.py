from datetime import UTC, datetime

import psycopg
from psycopg.rows import TupleRow

from paperexplorer.domain import Attachment, LibraryItem, WorkId
from paperexplorer.store import Works

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


def _record(works: Works, item: LibraryItem) -> WorkId:
    work = works.ensure(item.key)
    works.record_library_item(work, item)
    return work


def _copy(key: str, *, pdf: bool, added: int) -> LibraryItem:
    return VILLIGER.model_copy(
        update={
            "key": key,
            "pdf": Attachment(key=f"ATT{key}", filename="v.pdf") if pdf else None,
            "added": datetime(added, 1, 1, tzinfo=UTC),
        }
    )


def test_two_library_items_with_one_doi_are_two_works_and_the_one_with_a_pdf_stands(
    store_db: Conn,
) -> None:
    works = Works(store_db)
    _record(works, _copy("OLDNOPDF", pdf=False, added=2020))
    with_pdf = _record(works, _copy("NEWWPDF1", pdf=True, added=2025))

    assert works.stand_in("10.1080/09540261.2024.2357669") == with_pdf


def test_when_both_or_neither_have_a_pdf_the_older_stands(store_db: Conn) -> None:
    works = Works(store_db)
    _record(works, _copy("NEWER111", pdf=True, added=2025))
    older = _record(works, _copy("OLDER222", pdf=True, added=2020))

    assert works.stand_in("10.1080/09540261.2024.2357669") == older


def test_the_readers_choice_of_duplicate_stands(store_db: Conn) -> None:
    works = Works(store_db)
    first = _record(works, _copy("WITHPDF1", pdf=True, added=2020))
    chosen = _record(works, _copy("CHOSEN22", pdf=False, added=2025))

    works.choose(chosen)

    assert works.stand_in("10.1080/09540261.2024.2357669") == chosen
    works.choose(first)
    assert works.stand_in("10.1080/09540261.2024.2357669") == first


def test_duplicates_lists_each_doi_two_items_hold_with_the_stand_in_first(store_db: Conn) -> None:
    works = Works(store_db)
    other = _record(works, _copy("OLDNOPDF", pdf=False, added=2020))
    stands = _record(works, _copy("NEWWPDF1", pdf=True, added=2025))
    _record(works, LibraryItem(key="SINGLE11", title="Alone", doi="10.1/alone"))

    assert [[w.id for w in d] for d in works.duplicates()] == [[stands, other]]


def test_a_doi_nobody_holds_has_no_stand_in(store_db: Conn) -> None:
    assert Works(store_db).stand_in("10.1/nobody") is None


def test_title_candidates_find_near_titles_and_titles_missing_their_subtitle(
    store_db: Conn,
) -> None:
    works = Works(store_db)
    full = _record(
        works,
        LibraryItem(
            key="ATTN2017",
            title="Attention Is All You Need: Transformers for Sequence Transduction",
        ),
    )
    _record(works, LibraryItem(key="RESN2016", title="Deep residual learning for images"))

    assert [w.id for w in works.title_candidates("Attention is all you need")] == [full]
    assert [w.id for w in works.title_candidates("Atention is all you need: transformers")] == [
        full
    ]
    assert works.title_candidates("Something nobody wrote about") == []


def test_works_are_listed_in_the_order_they_were_first_seen(store_db: Conn) -> None:
    works = Works(store_db)
    first, second = works.ensure("FIRST"), works.ensure("SECOND")

    assert [w.id for w in works.list()] == [first, second]


def test_a_library_doi_is_recorded_as_a_key_so_any_printed_form_finds_it(store_db: Conn) -> None:
    works = Works(store_db)
    url_form = _record(
        works, LibraryItem(key="URLFORM1", title="T", doi="https://doi.org/10.5555/ABC.")
    )
    _record(works, LibraryItem(key="BAREFORM", title="T", doi="10.5555/abc"))

    assert works.stand_in("10.5555/abc") == url_form
    assert [[w.zotero_key for w in d] for d in works.duplicates()] == [["URLFORM1", "BAREFORM"]]
