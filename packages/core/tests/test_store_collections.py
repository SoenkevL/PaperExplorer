"""Browsing the Library by Collection: branch counts, filtering, and the branch-first search."""

import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer.domain import Collection, LibraryItem
from paperexplorer.store import Branch, BranchCount, Collections, StageStatuses, Works

Conn = psycopg.Connection[TupleRow]
FIVE = ["fetch", "parse", "chunk", "embed", "extract_citations"]


def _tree(conn: Conn, *collections: Collection) -> None:
    for c in collections:
        Collections(conn).record(c)


def _filed(conn: Conn, key: str, *collection_keys: str, title: str | None = None) -> None:
    works = Works(conn)
    work = works.ensure(key)
    works.record_library_item(work, LibraryItem(key=key, title=title or key))
    Collections(conn).file(work, collection_keys)


def _ingested(conn: Conn, key: str) -> None:
    work = Works(conn).ensure(key)
    statuses = StageStatuses(conn)
    for stage in FIVE:
        statuses.start(work, stage)
        statuses.done(work, stage, {})


# ROOT holds A and B; A holds A1.
TREE = (
    Collection(key="ROOT", name="Root"),
    Collection(key="A", name="A", parent_key="ROOT"),
    Collection(key="B", name="B", parent_key="ROOT"),
    Collection(key="A1", name="A1", parent_key="A"),
)


def test_a_work_in_two_sibling_collections_counts_once_in_their_parent(store_db: Conn) -> None:
    _tree(store_db, *TREE)
    _filed(store_db, "BOTH", "A", "B")
    _filed(store_db, "DEEP", "A1")
    _filed(store_db, "LOOSE")
    _ingested(store_db, "BOTH")

    counts = Collections(store_db).counts(done=FIVE)

    by_key = {c.key: (c.works, c.ingested) for c in counts.collections}
    assert by_key == {"ROOT": (2, 1), "A": (2, 1), "B": (1, 1), "A1": (1, 0)}
    assert [c.key for c in counts.collections] == ["A", "A1", "B", "ROOT"]  # by name
    assert counts.collections[0].parent_key == "ROOT"
    assert counts.library == BranchCount(works=3, ingested=1)
    assert counts.unfiled == BranchCount(works=1, ingested=0)


def test_a_search_counts_only_the_matching_works_in_each_branch(store_db: Conn) -> None:
    _tree(store_db, *TREE)
    _filed(store_db, "BOTH", "A", "B", title="Consciousness explained")
    _filed(store_db, "DEEP", "A1", title="Consciousness and time")
    _filed(store_db, "OTHER", "B", title="Perception")
    _filed(store_db, "LOOSE", title="Conscious machines")
    _ingested(store_db, "DEEP")

    counts = Collections(store_db).counts(done=FIVE, query="conscious")

    by_key = {c.key: (c.works, c.ingested) for c in counts.collections}
    assert by_key == {"ROOT": (2, 1), "A": (2, 1), "B": (1, 0), "A1": (1, 1)}
    assert counts.library == BranchCount(works=3, ingested=1)
    assert counts.unfiled == BranchCount(works=1, ingested=0)


def test_an_empty_collection_counts_nothing(store_db: Conn) -> None:
    _tree(store_db, Collection(key="EMPTY", name="Empty"))

    counts = Collections(store_db).counts(done=FIVE)

    assert [(c.key, c.works, c.ingested) for c in counts.collections] == [("EMPTY", 0, 0)]
    assert counts.library == BranchCount(works=0, ingested=0)


def test_a_cycle_cannot_come_from_zotero_but_the_counts_still_end(store_db: Conn) -> None:
    _tree(
        store_db,
        Collection(key="X", name="X", parent_key="Y"),
        Collection(key="Y", name="Y", parent_key="X"),
    )
    _filed(store_db, "IN_X", "X")

    counts = Collections(store_db).counts(done=FIVE)

    assert [(c.key, c.works) for c in counts.collections] == [("X", 1), ("Y", 1)]


def _keys(
    conn: Conn, branch: Branch | None, query: str = "", limit: int = 50, offset: int = 0
) -> list[tuple[str | None, bool]]:
    rows = Works(conn).library_items(query, limit, offset, done_first=FIVE, branch=branch)
    return [(r.work.zotero_key, r.in_branch) for r in rows]


def test_a_collection_lists_the_works_filed_directly_in_it(store_db: Conn) -> None:
    _tree(store_db, *TREE)
    _filed(store_db, "IN_A", "A")
    _filed(store_db, "IN_A1", "A1")
    _filed(store_db, "LOOSE")

    assert _keys(store_db, Branch(collection="A")) == [("IN_A", True)]
    assert Works(store_db).library_count("", Branch(collection="A")) == 1
    assert _keys(store_db, Branch(collection="ROOT")) == []


def test_deep_lists_the_whole_branch_each_work_once(store_db: Conn) -> None:
    _tree(store_db, *TREE)
    _filed(store_db, "BOTH", "A", "B")
    _filed(store_db, "IN_A1", "A1")
    _filed(store_db, "LOOSE")

    found = _keys(store_db, Branch(collection="ROOT", deep=True))

    assert sorted(found) == [("BOTH", True), ("IN_A1", True)]
    assert Works(store_db).library_count("", Branch(collection="ROOT", deep=True)) == 2
    assert sorted(_keys(store_db, Branch(collection="A", deep=True))) == [
        ("BOTH", True),
        ("IN_A1", True),
    ]


def test_unfiled_lists_the_library_items_in_no_collection(store_db: Conn) -> None:
    _tree(store_db, *TREE)
    _filed(store_db, "IN_A", "A")
    _filed(store_db, "LOOSE")
    store_db.execute("INSERT INTO works (title) VALUES ('Known from a registry only')")

    assert _keys(store_db, Branch(unfiled=True)) == [("LOOSE", True)]
    assert Works(store_db).library_count("", Branch(unfiled=True)) == 1


def test_a_search_covers_the_whole_library_with_the_branch_first_across_pages(
    store_db: Conn,
) -> None:
    _tree(store_db, *TREE)
    # Added in this order, so newest first is B2, B1 in the branch, then E3, E2, E1 elsewhere.
    for key, filed in [("E1", ()), ("B1", ("A",)), ("E2", ("B",)), ("B2", ("A",)), ("E3", ())]:
        _filed(store_db, key, *filed, title=f"entropy {key}")
    _filed(store_db, "NOMATCH", "A")
    added = "UPDATE works SET added_at = now() + make_interval(secs => id) WHERE zotero_key = %s"
    for key in ["E1", "B1", "E2", "B2", "E3", "NOMATCH"]:
        store_db.execute(added, (key,))
    branch = Branch(collection="A")

    first = _keys(store_db, branch, "entropy", limit=3)
    second = _keys(store_db, branch, "entropy", limit=3, offset=3)

    assert first == [("B2", True), ("B1", True), ("E3", False)]
    assert second == [("E2", False), ("E1", False)]
    assert Works(store_db).library_count("entropy") == 5
    assert Works(store_db).library_count("entropy", branch) == 2


def test_with_no_branch_every_row_is_in_it(store_db: Conn) -> None:
    _filed(store_db, "LOOSE")

    assert _keys(store_db, None) == [("LOOSE", True)]


def test_each_work_lists_the_collections_it_is_filed_in(store_db: Conn) -> None:
    _tree(store_db, *TREE)
    _filed(store_db, "BOTH", "B", "A")
    _filed(store_db, "LOOSE")
    works = Works(store_db)
    both, loose = works.ensure("BOTH"), works.ensure("LOOSE")

    assert Collections(store_db).of_works([both, loose]) == {both: ["A", "B"], loose: []}


def test_only_a_collection_goes_deep(store_db: Conn) -> None:
    with pytest.raises(ValueError, match="deep"):
        Branch(unfiled=True, deep=True)
    assert Branch.of(None, deep=True, unfiled=False) is None
    assert Collections(store_db).has("NOPE") is False
