"""The Library's writes: only inside the PaperExplorer Collection, each create run once."""

from collections.abc import Callable

import pytest

from paperexplorer.domain import Collection, ItemDraft, LibraryItem
from paperexplorer.library import InMemoryLibrary, OutsidePaperExplorerError

ROOT, MINE, EXPL, ITEM = "ROOT2345", "MINE2345", "EXPL2345", "ITEM2345"


def _library() -> InMemoryLibrary:
    library = InMemoryLibrary([LibraryItem(key=ITEM, title="Held", collections=[MINE])], {})
    library.put_collection(Collection(key=MINE, name="My reading"))
    return library


def _explored() -> InMemoryLibrary:
    library = _library()
    library.create_paperexplorer_collection(ROOT)
    library.create_collection(EXPL, "Villiger 2024 - Personal experience", within=ROOT)
    return library


def test_the_paperexplorer_collection_is_one_top_level_collection_of_that_name() -> None:
    library = _library()

    library.create_paperexplorer_collection(ROOT)

    assert Collection(key=ROOT, name="PaperExplorer") in library.changes(None).collections


def test_an_exploration_collection_is_created_in_the_paperexplorer_collection() -> None:
    library = _explored()

    created = {c.key: c for c in library.changes(None).collections}[EXPL]
    assert created.parent_key == ROOT


def test_a_create_run_twice_with_one_key_makes_one_collection() -> None:
    library = _explored()

    library.create_collection(EXPL, "Villiger 2024 - Personal experience", within=ROOT)

    assert [c.key for c in library.changes(None).collections].count(EXPL) == 1


def test_filing_adds_one_collection_and_keeps_the_items_others() -> None:
    library = _explored()

    library.file(ITEM, EXPL, within=ROOT)

    assert library.item(ITEM).collections == [MINE, EXPL]
    library.unfile(ITEM, EXPL, within=ROOT)
    assert library.item(ITEM).collections == [MINE]


def test_a_new_item_is_created_filed_in_its_exploration_collection() -> None:
    library = _explored()
    draft = ItemDraft(item_type="journalArticle", title="A Source", doi="10.1/x", date="2020")

    library.create_item("NEWI2345", draft, collection=EXPL, within=ROOT)
    library.create_item("NEWI2345", draft, collection=EXPL, within=ROOT)

    created = library.item("NEWI2345")
    assert created.collections == [EXPL]
    assert created.doi == "10.1/x"
    assert library.drafts["NEWI2345"] == draft


def test_the_seed_tag_is_added_and_removed_and_no_other_tag_is_touched() -> None:
    library = _explored()
    library.tags[ITEM] = ["to read"]

    library.tag_seed(ITEM)
    library.tag_seed(ITEM)

    assert library.tags[ITEM] == ["to read", "pe:seed"]
    library.untag_seed(ITEM)
    assert library.tags[ITEM] == ["to read"]


@pytest.mark.parametrize(
    "write",
    [
        lambda lib: lib.file(ITEM, MINE, within=ROOT),
        lambda lib: lib.unfile(ITEM, MINE, within=ROOT),
        lambda lib: lib.file(ITEM, ROOT, within=ROOT),
        lambda lib: lib.file(ITEM, EXPL, within=MINE),
        lambda lib: lib.file(ITEM, EXPL, within=EXPL),
        lambda lib: lib.create_collection("SUBX2345", "Elsewhere", within=MINE),
        lambda lib: lib.create_item("NEWI2345", ItemDraft(), collection=MINE, within=ROOT),
    ],
    ids=[
        "file-in-own",
        "unfile-from-own",
        "file-in-root",
        "own-as-root",
        "nested-as-root",
        "collection-in-own",
        "item-in-own",
    ],
)
def test_a_write_outside_the_paperexplorer_collection_is_refused(
    write: Callable[[InMemoryLibrary], None],
) -> None:
    library = _explored()
    before = library.changes(None)

    with pytest.raises(OutsidePaperExplorerError):
        write(library)

    assert library.changes(None) == before
