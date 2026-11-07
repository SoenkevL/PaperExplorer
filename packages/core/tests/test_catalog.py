"""Explorations in Zotero: started on a Seed, firm Sources filed, the reader's edits respected."""

import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer import catalog
from paperexplorer.catalog import drafts
from paperexplorer.domain import (
    Attachment,
    Collection,
    ItemDraft,
    Layer,
    Level,
    LibraryItem,
    RegistrySource,
    Resolution,
    ResolutionState,
    WorkId,
)
from paperexplorer.library import InMemoryLibrary, LibraryWriteError, sync
from paperexplorer.parsing import ParsedWork, Reference, TextOrigin
from paperexplorer.resolution.records import from_crossref
from paperexplorer.store import (
    Explorations,
    ReadersChoices,
    RegistryResponses,
    Resolutions,
    Works,
    WorkTexts,
)

Conn = psycopg.Connection[TupleRow]

MINE = Collection(key="MINE2345", name="My reading")
SEED = LibraryItem(
    key="SEED2345",
    title="Personal psychedelic experience of psychedelic therapists during training",
    creators=["Villiger, Daniel"],
    year=2024,
    pdf=Attachment(key="SEEDPDF2", filename="seed.pdf"),
    collections=[MINE.key],
)
HELD = LibraryItem(key="HELD2345", title="Held", doi="10.1/held", collections=[MINE.key])
OTHER = LibraryItem(key="OTHR2345", title="Other", doi="10.1/other")
CROSSREF = {
    "DOI": "10.1/found",
    "type": "journal-article",
    "title": ["Found by a Check"],
    "author": [{"family": "Lee", "given": "Ann"}],
    "issued": {"date-parts": [[2020, 3, 1]]},
    "container-title": ["Brain"],
    "volume": "3",
    "issue": "2",
    "page": "1-9",
}


@pytest.fixture
def library() -> InMemoryLibrary:
    library = InMemoryLibrary([SEED, HELD, OTHER], {})
    library.put_collection(MINE)
    return library


def _seed(conn: Conn, library: InMemoryLibrary, *refs: str) -> WorkId:
    with conn.transaction():
        sync(conn, library)
    found = Works(conn).find(SEED.key)
    assert found is not None
    references = [Reference(id=r, text=f"Printed {r}", page=1) for r in refs]
    WorkTexts(conn).replace(
        found.id, ParsedWork(text_origin=TextOrigin.PRINTED, blocks=[], references=references)
    )
    return found.id


def _work(conn: Conn, key: str) -> WorkId:
    found = Works(conn).find(key)
    assert found is not None
    return found.id


def _resolved(ref: str, work: WorkId, level: Level = Level.CERTAIN) -> Resolution:
    return Resolution(
        reference=ref, state=ResolutionState.RESOLVED, work=work, level=level,
        confidence=1.0, layer=Layer.LIBRARY,
    )  # fmt: skip


def _collection_of(library: InMemoryLibrary, seed: WorkId, conn: Conn) -> str:
    exploration = Explorations(conn).of_seed(seed)
    assert exploration is not None
    return exploration.collection_key


def _filed_in(library: InMemoryLibrary, key: str) -> list[str]:
    return [i.key for i in library.changes(None).items if key in i.collections]


def test_starting_files_the_seed_in_a_new_collection_in_the_paperexplorer_collection(
    store_db: Conn, library: InMemoryLibrary
) -> None:
    seed = _seed(store_db, library)

    exploration = catalog.start(store_db, library, seed)

    collections = {c.key: c for c in library.changes(None).collections}
    root = collections[exploration.collection_key].parent_key
    assert root is not None
    assert collections[root] == Collection(key=root, name="PaperExplorer")
    assert exploration.name == (
        "Villiger 2024 \N{EN DASH} Personal psychedelic experience of psychedelic"
        " therapists\N{HORIZONTAL ELLIPSIS}"
    )
    assert _filed_in(library, exploration.collection_key) == [SEED.key]
    assert library.item(SEED.key).collections == [MINE.key, exploration.collection_key]
    assert library.tags[SEED.key] == ["pe:seed"]


def test_a_paperexplorer_collection_there_already_is_adopted(
    store_db: Conn, library: InMemoryLibrary
) -> None:
    library.put_collection(Collection(key="ROOT2345", name="PaperExplorer"))
    seed = _seed(store_db, library)

    exploration = catalog.start(store_db, library, seed)

    collections = {c.key: c for c in library.changes(None).collections}
    assert collections[exploration.collection_key].parent_key == "ROOT2345"
    assert [c.name for c in collections.values()].count("PaperExplorer") == 1


def test_two_paperexplorer_collections_are_refused_until_the_reader_renames_one(
    store_db: Conn, library: InMemoryLibrary
) -> None:
    library.put_collection(Collection(key="ROOT2345", name="PaperExplorer"))
    library.put_collection(Collection(key="ROOT6789", name="PaperExplorer"))
    seed = _seed(store_db, library)

    with pytest.raises(catalog.AmbiguousPaperExplorerError, match="rename"):
        catalog.start(store_db, library, seed)


def test_starting_again_continues_the_seeds_exploration(
    store_db: Conn, library: InMemoryLibrary
) -> None:
    seed = _seed(store_db, library)
    first = catalog.start(store_db, library, seed)

    again = catalog.start(store_db, library, seed)

    assert again.id == first.id
    assert len([c for c in library.changes(None).collections if c.parent_key]) == 1


def test_a_work_without_a_pdf_cannot_be_a_seed(store_db: Conn, library: InMemoryLibrary) -> None:
    _seed(store_db, library)

    with pytest.raises(catalog.NotASeedError):
        catalog.start(store_db, library, _work(store_db, HELD.key))


def test_firm_sources_are_filed_and_a_low_one_waits_for_the_readers_choice(
    store_db: Conn, library: InMemoryLibrary
) -> None:
    seed = _seed(store_db, library, "b0", "b1")
    held, other = _work(store_db, HELD.key), _work(store_db, OTHER.key)
    Resolutions(store_db).replace(seed, [_resolved("b0", held), _resolved("b1", other, Level.LOW)])

    exploration = catalog.start(store_db, library, seed)

    assert sorted(_filed_in(library, exploration.collection_key)) == [HELD.key, SEED.key]
    assert library.item(HELD.key).collections == [MINE.key, exploration.collection_key]

    ReadersChoices(store_db).choose(seed, "Printed b1", other)
    Resolutions(store_db).save(
        seed, _resolved("b1", other).model_copy(update={"layer": Layer.READER})
    )
    result = catalog.reconcile(store_db, library, seed)

    assert result is not None and result.filed == 1
    assert OTHER.key in _filed_in(library, exploration.collection_key)


def test_a_source_found_by_a_check_gets_one_new_item_made_from_its_registry_record(
    store_db: Conn, library: InMemoryLibrary
) -> None:
    seed = _seed(store_db, library, "b0")
    RegistryResponses(store_db).record(
        RegistrySource.CROSSREF_DOI, "10.1/found", CROSSREF, hit=True
    )
    found = Works(store_db).ensure_registry_work(from_crossref(CROSSREF))
    Resolutions(store_db).replace(seed, [_resolved("b0", found)])

    exploration = catalog.start(store_db, library, seed)
    catalog.reconcile(store_db, library, seed)

    (created,) = [k for k in _filed_in(library, exploration.collection_key) if k != SEED.key]
    assert library.drafts[created] == ItemDraft(
        item_type="journalArticle",
        title="Found by a Check",
        creators=["Lee, Ann"],
        date="2020-03-01",
        doi="10.1/found",
        publication="Brain",
        volume="3",
        issue="2",
        pages="1-9",
        url="https://doi.org/10.1/found",
    )
    assert library.item(created).collections == [exploration.collection_key]
    assert len(library.drafts) == 1
    work = Works(store_db).get(found)
    assert work is not None and work.zotero_key == created


def test_a_source_whose_doi_the_library_holds_is_filed_not_created(
    store_db: Conn, library: InMemoryLibrary
) -> None:
    seed = _seed(store_db, library, "b0")
    registry_only = Works(store_db).ensure_registry_work(
        from_crossref({"DOI": "10.1/late", "title": ["Late"]})
    )
    Resolutions(store_db).replace(seed, [_resolved("b0", registry_only)])
    library.put(LibraryItem(key="LATE2345", title="Late", doi="10.1/late"))  # added since

    exploration = catalog.start(store_db, library, seed)

    assert "LATE2345" in _filed_in(library, exploration.collection_key)
    assert library.drafts == {}


def test_a_source_no_longer_firm_is_unfiled_and_what_the_reader_filed_stays(
    store_db: Conn, library: InMemoryLibrary
) -> None:
    seed = _seed(store_db, library, "b0")
    held = _work(store_db, HELD.key)
    Resolutions(store_db).replace(seed, [_resolved("b0", held)])
    exploration = catalog.start(store_db, library, seed)
    key = exploration.collection_key
    library.put(library.item(OTHER.key).model_copy(update={"collections": [key]}))  # by hand

    Resolutions(store_db).replace(seed, [_resolved("b0", held, Level.LOW)])
    result = catalog.reconcile(store_db, library, seed)

    assert result is not None and result.unfiled == 1
    assert sorted(_filed_in(library, key)) == [OTHER.key, SEED.key]
    assert library.item(HELD.key).collections == [MINE.key]


def test_a_source_the_reader_unfiled_in_zotero_stays_out_until_they_choose_it(
    store_db: Conn, library: InMemoryLibrary
) -> None:
    seed = _seed(store_db, library, "b0")
    held = _work(store_db, HELD.key)
    Resolutions(store_db).replace(seed, [_resolved("b0", held)])
    key = catalog.start(store_db, library, seed).collection_key
    library.put(library.item(HELD.key).model_copy(update={"collections": [MINE.key]}))

    catalog.reconcile(store_db, library, seed)
    assert HELD.key not in _filed_in(library, key)

    ReadersChoices(store_db).choose(seed, "Printed b0", held)
    catalog.reconcile(store_db, library, seed)
    assert HELD.key in _filed_in(library, key)


def test_deleting_the_exploration_collection_ends_it_and_takes_the_seed_tag_off(
    store_db: Conn, library: InMemoryLibrary
) -> None:
    seed = _seed(store_db, library)
    key = catalog.start(store_db, library, seed).collection_key

    library.remove_collection(key)
    with store_db.transaction():
        sync(store_db, library)
    assert catalog.reconcile(store_db, library, seed) is None

    assert Explorations(store_db).of_seed(seed) is None
    assert library.tags[SEED.key] == []
    assert catalog.start(store_db, library, seed).collection_key != key


class _FailingOnce(InMemoryLibrary):
    """A Library whose next file write is refused, as when Zotero cannot be reached."""

    fail = True

    def file(self, item: str, collection: str, *, within: str) -> None:
        if self.fail and item == HELD.key:
            self.fail = False
            raise LibraryWriteError("Zotero answered 503: down for maintenance")
        super().file(item, collection, within=within)


def test_a_failed_write_is_recorded_with_its_reason_and_done_by_the_next_run(
    store_db: Conn,
) -> None:
    library = _FailingOnce([SEED, HELD, OTHER], {})
    library.put_collection(MINE)
    seed = _seed(store_db, library, "b0")
    Resolutions(store_db).replace(seed, [_resolved("b0", _work(store_db, HELD.key))])

    exploration = catalog.start(store_db, library, seed)

    state = catalog.state(store_db, seed)
    assert state is not None
    assert [f.error for f in state.failed] == ["Zotero answered 503: down for maintenance"]
    result = catalog.reconcile(store_db, library, seed)
    assert result is not None and result.filed == 1
    assert HELD.key in _filed_in(library, exploration.collection_key)
    state = catalog.state(store_db, seed)
    assert state is not None and state.failed == [] and state.filed == 2


def test_a_collection_paperexplorer_created_does_not_make_the_next_sync_read_everything(
    store_db: Conn, library: InMemoryLibrary
) -> None:
    seed = _seed(store_db, library)
    catalog.start(store_db, library, seed)

    with store_db.transaction():
        assert not sync(store_db, library).full


def test_a_posted_content_record_drafts_a_preprint_on_its_server() -> None:
    record = {
        "DOI": "10.1101/2020.01.01.123",
        "type": "posted-content",
        "title": ["A preprint"],
        "author": [{"name": "The Consortium"}],
        "issued": {"date-parts": [[2020]]},
        "institution": [{"name": "bioRxiv"}],
    }

    assert drafts.from_crossref(record) == ItemDraft(
        item_type="preprint",
        title="A preprint",
        creators=["The Consortium"],
        date="2020",
        doi="10.1101/2020.01.01.123",
        publication="bioRxiv",
        url="https://doi.org/10.1101/2020.01.01.123",
    )
