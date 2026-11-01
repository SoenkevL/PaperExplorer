import psycopg
from psycopg.rows import TupleRow

from paperexplorer.domain import Layer, Level, LibraryItem, Resolution, ResolutionState, WorkId
from paperexplorer.parsing import ParsedWork, Reference, TextOrigin
from paperexplorer.store import Resolutions, Works, WorkTexts

Conn = psycopg.Connection[TupleRow]


def _seed(conn: Conn, keys: list[str]) -> WorkId:
    seed = Works(conn).ensure("SEED1111")
    refs = [Reference(id=k, text=f"Printed {k}", page=1) for k in keys]
    WorkTexts(conn).replace(
        seed, ParsedWork(text_origin=TextOrigin.PRINTED, blocks=[], references=refs)
    )
    return seed


def _cited(conn: Conn, key: str) -> WorkId:
    works = Works(conn)
    work = works.ensure(key)
    works.record_library_item(work, LibraryItem(key=key, title=f"Title of {key}"))
    return work


def test_a_works_resolutions_come_back_in_reference_order(store_db: Conn) -> None:
    seed = _seed(store_db, ["b0", "b1", "b2"])
    cited, named = _cited(store_db, "CITED111"), _cited(store_db, "NAMED222")
    resolutions = [
        Resolution(reference="b2", state=ResolutionState.UNCHECKED, doi_conflict=named),
        Resolution(
            reference="b0", state=ResolutionState.RESOLVED, work=cited, level=Level.LOW,
            confidence=0.93, evidence=["title", "doi_conflict"], layer=Layer.LIBRARY,
            doi_conflict=named,
        ),
        Resolution(reference="b1", state=ResolutionState.UNCHECKED),
    ]  # fmt: skip

    Resolutions(store_db).replace(seed, resolutions)

    assert Resolutions(store_db).for_work(seed) == [resolutions[1], resolutions[2], resolutions[0]]


def test_resolving_again_replaces_the_earlier_resolutions(store_db: Conn) -> None:
    seed = _seed(store_db, ["b0"])
    store = Resolutions(store_db)
    store.replace(seed, [Resolution(reference="b0", state=ResolutionState.UNCHECKED)])
    cited = _cited(store_db, "CITED111")
    again = Resolution(
        reference="b0", state=ResolutionState.RESOLVED, work=cited, level=Level.CERTAIN,
        confidence=1.0, evidence=["doi"], layer=Layer.LIBRARY,
    )  # fmt: skip

    store.replace(seed, [again])

    assert store.for_work(seed) == [again]


def test_a_reparse_drops_the_works_resolutions(store_db: Conn) -> None:
    seed = _seed(store_db, ["b0"])
    Resolutions(store_db).replace(
        seed, [Resolution(reference="b0", state=ResolutionState.UNCHECKED)]
    )

    _seed(store_db, ["b0"])

    assert Resolutions(store_db).for_work(seed) == []


def test_a_work_a_resolution_names_stays_when_its_library_item_goes(store_db: Conn) -> None:
    seed = _seed(store_db, ["b0"])
    works = Works(store_db)
    cited = _cited(store_db, "CITED111")
    Resolutions(store_db).replace(
        seed,
        [
            Resolution(
                reference="b0", state=ResolutionState.RESOLVED, work=cited, level=Level.HIGH,
                confidence=1.0, evidence=["title", "year"], layer=Layer.LIBRARY,
            )
        ],
    )  # fmt: skip

    assert not works.remove_library_item("CITED111")

    kept = works.get(cited)
    assert kept is not None and kept.zotero_key is None
