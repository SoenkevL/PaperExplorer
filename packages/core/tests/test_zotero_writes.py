"""ZoteroLibrary's writes over the Web API, as docs/research/2026-11-zotero-writes.md has them."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from paperexplorer.domain import NewItem
from paperexplorer.library import LibraryWriteError, OutsidePaperExplorerError, ZoteroLibrary

ROOT, MINE, EXPL, ITEM = "ROOT2345", "MINE2345", "EXPL2345", "ITEM2345"
PREFIX = "/users/1234567"


class WritingZotero:
    """One library on the Web API: its Collections and items, writes checked as the server does."""

    def __init__(self) -> None:
        self.version = 10
        self.collections: dict[str, dict[str, Any]] = {
            MINE: {"key": MINE, "version": 1, "name": "My reading", "parentCollection": False},
            ROOT: {"key": ROOT, "version": 2, "name": "PaperExplorer", "parentCollection": False},
            EXPL: {"key": EXPL, "version": 3, "name": "Villiger 2024", "parentCollection": ROOT},
        }
        self.items: dict[str, dict[str, Any]] = {
            ITEM: {
                "key": ITEM,
                "version": 5,
                "itemType": "journalArticle",
                "title": "Held",
                "collections": [MINE],
                "tags": [{"tag": "to read"}],
            }
        }
        self.requests: list[httpx.Request] = []
        self.stale_patches = 0  # the next PATCHes refused 412, as after an edit elsewhere

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix(PREFIX)
        kind, _, key = path.strip("/").partition("/")
        store = self.collections if kind == "collections" else self.items
        if request.method == "GET":
            if key not in store:
                return httpx.Response(404, text="Not found")
            return httpx.Response(200, json={"key": key, "version": 0, "data": store[key]})
        if request.method == "POST":
            return self._create(store, json.loads(request.content))
        if request.method == "PATCH":
            return self._patch(key, request)
        return httpx.Response(405)

    def _create(self, store: dict[str, dict[str, Any]], objects: list[Any]) -> httpx.Response:
        report: dict[str, dict[str, Any]] = {"successful": {}, "unchanged": {}, "failed": {}}
        for i, obj in enumerate(objects):
            key = obj["key"]
            if obj.get("version") != 0:
                report["failed"][str(i)] = {"key": key, "code": 428, "message": "no version"}
            elif key in store:
                report["failed"][str(i)] = {"key": key, "code": 412, "message": "exists"}
            elif obj.get("title") == "Too long":
                report["failed"][str(i)] = {"key": key, "code": 413, "message": "title too long"}
            else:
                self.version += 1
                store[key] = obj | {"version": self.version}
                report["successful"][str(i)] = {"key": key, "version": self.version}
        return httpx.Response(
            200, json=report, headers={"Last-Modified-Version": str(self.version)}
        )

    def _patch(self, key: str, request: httpx.Request) -> httpx.Response:
        if key not in self.items:
            return httpx.Response(404)
        if "If-Unmodified-Since-Version" not in request.headers:
            return httpx.Response(428)
        if self.stale_patches:
            self.stale_patches -= 1
            self.version += 1
            self.items[key]["version"] = self.version
            return httpx.Response(412)
        if int(request.headers["If-Unmodified-Since-Version"]) != self.items[key]["version"]:
            return httpx.Response(412)
        self.version += 1
        self.items[key] |= json.loads(request.content) | {"version": self.version}
        return httpx.Response(204, headers={"Last-Modified-Version": str(self.version)})

    def library(self) -> ZoteroLibrary:
        transport = httpx.MockTransport(self.handle)
        api = httpx.Client(base_url=f"https://api.zotero.org{PREFIX}", transport=transport)
        return ZoteroLibrary(
            api, httpx.Client(transport=transport), storage_dir=None, cache_dir=Path("unused")
        )

    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method in {"POST", "PATCH"}]


def test_the_paperexplorer_collection_is_posted_top_level_with_its_key_at_version_0() -> None:
    zotero = WritingZotero()
    del zotero.collections[ROOT], zotero.collections[EXPL]

    zotero.library().create_paperexplorer_collection("NEWR2345")

    assert json.loads(zotero.writes()[0].content) == [
        {"key": "NEWR2345", "version": 0, "name": "PaperExplorer", "parentCollection": False}
    ]


def test_an_exploration_collection_is_posted_in_the_paperexplorer_collection() -> None:
    zotero = WritingZotero()

    zotero.library().create_collection("NEWC2345", "Lee 2020 - A title", within=ROOT)

    assert zotero.collections["NEWC2345"]["parentCollection"] == ROOT
    assert zotero.collections["NEWC2345"]["name"] == "Lee 2020 - A title"


def test_a_create_whose_key_exists_already_counts_as_done() -> None:
    zotero = WritingZotero()

    zotero.library().create_collection(EXPL, "Villiger 2024", within=ROOT)

    assert len(zotero.writes()) == 1


def test_a_create_zotero_refuses_raises_with_its_reason() -> None:
    zotero = WritingZotero()

    with pytest.raises(LibraryWriteError, match=r"413 title too long"):
        zotero.library().create_item(
            "NEWI2345", NewItem(title="Too long"), collection=EXPL, within=ROOT
        )


def test_a_new_item_is_posted_with_its_fields_filed_in_its_collection() -> None:
    zotero = WritingZotero()
    draft = NewItem(
        item_type="journalArticle",
        title="Brain entropy",
        creators=["Lee, Ann", "Consortium"],
        date="2020-03-01",
        doi="10.1/abc",
        publication="Brain",
        volume="3",
        issue="2",
        pages="1-9",
        url="https://doi.org/10.1/abc",
    )

    zotero.library().create_item("NEWI2345", draft, collection=EXPL, within=ROOT)

    assert json.loads(zotero.writes()[0].content) == [
        {
            "key": "NEWI2345",
            "version": 0,
            "itemType": "journalArticle",
            "title": "Brain entropy",
            "creators": [
                {"creatorType": "author", "lastName": "Lee", "firstName": "Ann"},
                {"creatorType": "author", "name": "Consortium"},
            ],
            "date": "2020-03-01",
            "DOI": "10.1/abc",
            "publicationTitle": "Brain",
            "volume": "3",
            "issue": "2",
            "pages": "1-9",
            "url": "https://doi.org/10.1/abc",
            "extra": "Added by PaperExplorer",
            "collections": [EXPL],
        }
    ]


def test_a_preprint_names_its_server_as_its_repository_and_sends_no_journal_fields() -> None:
    zotero = WritingZotero()
    draft = NewItem(item_type="preprint", title="P", publication="bioRxiv", volume="1")

    zotero.library().create_item("NEWI2345", draft, collection=EXPL, within=ROOT)

    sent = json.loads(zotero.writes()[0].content)[0]
    assert sent["repository"] == "bioRxiv"
    assert "publicationTitle" not in sent and "volume" not in sent


def test_filing_patches_the_whole_collections_array_at_the_items_version() -> None:
    zotero = WritingZotero()

    zotero.library().file(ITEM, EXPL, within=ROOT)

    patch = zotero.writes()[0]
    assert json.loads(patch.content) == {"collections": [MINE, EXPL]}
    assert patch.headers["If-Unmodified-Since-Version"] == "5"
    assert zotero.items[ITEM]["collections"] == [MINE, EXPL]


def test_filing_refused_as_stale_is_read_again_and_tried_once_more() -> None:
    zotero = WritingZotero()
    zotero.stale_patches = 1

    zotero.library().file(ITEM, EXPL, within=ROOT)

    assert zotero.items[ITEM]["collections"] == [MINE, EXPL]
    assert len(zotero.writes()) == 2


def test_filing_twice_stale_raises() -> None:
    zotero = WritingZotero()
    zotero.stale_patches = 2

    with pytest.raises(LibraryWriteError, match="412"):
        zotero.library().file(ITEM, EXPL, within=ROOT)


def test_unfiling_keeps_the_items_other_collections() -> None:
    zotero = WritingZotero()
    zotero.items[ITEM]["collections"] = [MINE, EXPL]

    zotero.library().unfile(ITEM, EXPL, within=ROOT)

    assert zotero.items[ITEM]["collections"] == [MINE]


def test_an_item_filed_already_is_not_written() -> None:
    zotero = WritingZotero()
    zotero.items[ITEM]["collections"] = [MINE, EXPL]

    zotero.library().file(ITEM, EXPL, within=ROOT)

    assert zotero.writes() == []


def test_the_seed_tag_is_added_and_removed_keeping_the_other_tags() -> None:
    zotero = WritingZotero()
    library = zotero.library()

    library.tag_seed(ITEM)
    assert zotero.items[ITEM]["tags"] == [{"tag": "to read"}, {"tag": "pe:seed"}]
    library.untag_seed(ITEM)
    assert zotero.items[ITEM]["tags"] == [{"tag": "to read"}]


@pytest.mark.parametrize(
    ("collection", "within"),
    [(MINE, ROOT), (ROOT, ROOT), (EXPL, MINE), (EXPL, EXPL), ("GONE2345", ROOT)],
    ids=["own", "root-itself", "own-as-root", "nested-as-root", "unknown"],
)
def test_a_write_outside_the_paperexplorer_collection_sends_nothing(
    collection: str, within: str
) -> None:
    zotero = WritingZotero()
    library = zotero.library()

    with pytest.raises(OutsidePaperExplorerError):
        library.file(ITEM, collection, within=within)
    with pytest.raises(OutsidePaperExplorerError):
        library.create_item("NEWI2345", NewItem(), collection=collection, within=within)

    assert zotero.writes() == []


def test_a_trashed_paperexplorer_collection_takes_no_writes() -> None:
    zotero = WritingZotero()
    zotero.collections[ROOT]["deleted"] = True

    with pytest.raises(OutsidePaperExplorerError):
        zotero.library().create_collection("NEWC2345", "x", within=ROOT)

    assert zotero.writes() == []


def test_a_seed_tag_there_already_in_any_type_is_not_added_again() -> None:
    zotero = WritingZotero()
    zotero.items[ITEM]["tags"] = [{"tag": "pe:seed", "type": 1}]

    zotero.library().tag_seed(ITEM)

    assert zotero.writes() == []
