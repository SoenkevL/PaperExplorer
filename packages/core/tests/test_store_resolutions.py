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


def _citing(conn: Conn, key: str, references: list[Resolution]) -> WorkId:
    work = _cited(conn, key)
    printed = [Reference(id=r.reference, text=f"Printed {r.reference}", page=1) for r in references]
    WorkTexts(conn).replace(
        work, ParsedWork(text_origin=TextOrigin.PRINTED, blocks=[], references=printed)
    )
    Resolutions(conn).replace(work, references)
    return work


def _naming(
    reference: str, work: WorkId, level: Level = Level.HIGH, layer: Layer = Layer.LIBRARY
) -> Resolution:
    return Resolution(
        reference=reference, state=ResolutionState.RESOLVED, work=work, level=level,
        confidence=0.9, evidence=["title"], layer=layer,
    )  # fmt: skip


def test_the_works_citing_a_work_are_those_whose_firm_resolutions_name_it(store_db: Conn) -> None:
    cited = _cited(store_db, "CITED111")
    other = _cited(store_db, "OTHER222")
    firm = _citing(store_db, "FIRM1111", [_naming("b0", cited), _naming("b1", other)])
    chosen = _citing(
        store_db, "CHOSEN11", [_naming("b0", cited, Level.LOW, Layer.READER)]
    )  # a Reader's Choice is firm, whatever level it carries
    _citing(store_db, "LOWLOW11", [_naming("b0", cited, Level.LOW)])
    _citing(store_db, "ELSEWHRE", [_naming("b0", other)])

    assert Resolutions(store_db).citing(cited) == {firm: ["b0"], chosen: ["b0"]}


def test_a_work_cited_by_two_of_a_works_references_has_both(store_db: Conn) -> None:
    cited = _cited(store_db, "CITED111")
    twice = _citing(store_db, "TWICE111", [_naming("b0", cited), _naming("b1", cited)])

    assert Resolutions(store_db).citing(cited) == {twice: ["b0", "b1"]}


def test_a_work_does_not_cite_itself_in_its_own_list(store_db: Conn) -> None:
    work = _cited(store_db, "SELF1111")
    WorkTexts(store_db).replace(
        work,
        ParsedWork(
            text_origin=TextOrigin.PRINTED,
            blocks=[],
            references=[Reference(id="b0", text="Printed", page=1)],
        ),
    )
    Resolutions(store_db).replace(work, [_naming("b0", work)])

    assert Resolutions(store_db).citing(work) == {}
