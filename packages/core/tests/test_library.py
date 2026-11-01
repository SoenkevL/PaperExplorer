"""The Library port: the same contract from InMemoryLibrary and from Zotero's Web API."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from paperexplorer.config import Settings
from paperexplorer.domain import Attachment, LibraryItem
from paperexplorer.library import (
    InMemoryLibrary,
    ItemNotFoundError,
    Library,
    LibraryUnavailableError,
    ZoteroLibrary,
)

ZOTERO = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "zotero"
ITEM = "QW3RT5YU"
PDF = Attachment(
    key="A7B8C9D2",
    filename="Villiger - 2024 - Personal psychedelic experience of psychedelic therapists.pdf",
)
TITLE = (
    "Personal psychedelic experience of psychedelic therapists during training: "
    "should it be required, optional, or prohibited?"
)
FILES_HOST = "files.example.org"


def _json(name: str) -> Any:
    return json.loads((ZOTERO / name).read_text())


class FakeZotero:
    """Zotero's Web API for one library: the Villiger item, its children, and its PDF file."""

    def __init__(self, pdf: bytes, children: list[Any] | None = None) -> None:
        self.pdf = pdf
        self.children = _json("villiger-children.json") if children is None else children
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == FILES_HOST:
            return httpx.Response(200, content=self.pdf)
        match request.url.path:
            case "/users/1234567/items/QW3RT5YU":
                return httpx.Response(200, json=_json("villiger-item.json"))
            case "/users/1234567/items/QW3RT5YU/children":
                return httpx.Response(200, json=self.children)
            case "/users/1234567/items/A7B8C9D2/file":
                return httpx.Response(302, headers={"Location": f"https://{FILES_HOST}/a?sig=1"})
            case _:
                return httpx.Response(404, text="Not found")

    def zotero_library(self, storage_dir: Path | None, cache_dir: Path) -> ZoteroLibrary:
        transport = httpx.MockTransport(self.handle)
        api = httpx.Client(
            base_url="https://api.zotero.org/users/1234567",
            headers={"Zotero-API-Key": "secret-key", "Zotero-API-Version": "3"},
            transport=transport,
        )
        return ZoteroLibrary(
            api, httpx.Client(transport=transport), storage_dir=storage_dir, cache_dir=cache_dir
        )


@pytest.fixture
def villiger_pdf(fixture_pdf: Callable[[str], Path]) -> Path:
    return fixture_pdf("villiger2024-tandf")


def _in_memory(pdf: Path, tmp_path: Path) -> Library:
    item = LibraryItem(
        key=ITEM, title=TITLE, creators=["Villiger, Daniel"], year=2024,
        doi="10.1080/09540261.2024.2357669", pdf=PDF,
    )  # fmt: skip
    return InMemoryLibrary([item], {PDF.key: pdf})


def _zotero_with_storage(pdf: Path, tmp_path: Path) -> Library:
    storage = tmp_path / "storage"
    (storage / PDF.key).mkdir(parents=True)
    (storage / PDF.key / PDF.filename).write_bytes(pdf.read_bytes())
    return FakeZotero(pdf.read_bytes()).zotero_library(storage, tmp_path / "cache")


ADAPTERS = pytest.mark.parametrize("make", [_in_memory, _zotero_with_storage])


@ADAPTERS
def test_an_item_gives_its_metadata_and_its_pdf(
    make: Callable[[Path, Path], Library], villiger_pdf: Path, tmp_path: Path
) -> None:
    item = make(villiger_pdf, tmp_path).item(ITEM)

    assert item.key == ITEM
    assert item.title == TITLE
    assert item.creators == ["Villiger, Daniel"]  # authors only
    assert item.year == 2024
    assert item.doi == "10.1080/09540261.2024.2357669"
    assert item.pdf == PDF


@ADAPTERS
def test_an_items_pdf_is_a_local_file(
    make: Callable[[Path, Path], Library], villiger_pdf: Path, tmp_path: Path
) -> None:
    library = make(villiger_pdf, tmp_path)

    path = library.pdf(PDF)

    assert path.read_bytes() == villiger_pdf.read_bytes()


@ADAPTERS
def test_an_unknown_item_is_not_found(
    make: Callable[[Path, Path], Library], villiger_pdf: Path, tmp_path: Path
) -> None:
    with pytest.raises(ItemNotFoundError, match="NOPE2345"):
        make(villiger_pdf, tmp_path).item("NOPE2345")


def test_zotero_is_asked_with_the_api_key(villiger_pdf: Path, tmp_path: Path) -> None:
    zotero = FakeZotero(villiger_pdf.read_bytes())

    zotero.zotero_library(None, tmp_path).item(ITEM)

    assert all(r.headers["Zotero-API-Key"] == "secret-key" for r in zotero.requests)
    assert all(r.headers["Zotero-API-Version"] == "3" for r in zotero.requests)


def test_a_pdf_not_in_storage_is_downloaded_once_and_the_key_stays_with_zotero(
    villiger_pdf: Path, tmp_path: Path
) -> None:
    zotero = FakeZotero(villiger_pdf.read_bytes())
    library = zotero.zotero_library(tmp_path / "empty-storage", tmp_path / "cache")

    first = library.pdf(PDF)
    second = library.pdf(PDF)

    assert first == second
    assert first.is_relative_to(tmp_path / "cache" / "pdfs")
    assert first.read_bytes() == villiger_pdf.read_bytes()
    downloads = [r for r in zotero.requests if r.url.host == FILES_HOST]
    assert len(downloads) == 1
    assert "Zotero-API-Key" not in downloads[0].headers


def test_an_item_without_a_stored_pdf_has_no_pdf(villiger_pdf: Path, tmp_path: Path) -> None:
    children = _json("villiger-children.json")
    children[2]["data"]["linkMode"] = "linked_file"  # on someone's disk, not in Zotero storage
    zotero = FakeZotero(villiger_pdf.read_bytes(), children=children)

    assert zotero.zotero_library(None, tmp_path).item(ITEM).pdf is None


def test_zotero_needs_a_library_id_and_an_api_key(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path, zotero_api_key=SecretStr("k"))  # pyright: ignore[reportCallIssue]

    with pytest.raises(ValueError, match="PE_ZOTERO_LIBRARY_ID"):
        ZoteroLibrary.from_settings(settings)


def _item(key: str, *, doi: str | None = None, pdf: Attachment | None = None) -> LibraryItem:
    return LibraryItem(key=key, title=f"Title of {key}", doi=doi, pdf=pdf)


def test_a_full_read_gives_every_item_and_the_librarys_version() -> None:
    library = InMemoryLibrary([_item("AAAA1111"), _item("BBBB2222")], {})

    changes = library.changes(None)

    assert changes.full
    assert [i.key for i in changes.items] == ["AAAA1111", "BBBB2222"]
    assert changes.deleted == []
    assert changes.version > 0


def test_changes_since_a_version_give_only_what_changed_and_what_went() -> None:
    library = InMemoryLibrary([_item("AAAA1111"), _item("BBBB2222"), _item("CCCC3333")], {})
    before = library.changes(None).version
    library.put(_item("BBBB2222", doi="10.1/b"))
    library.put(_item("DDDD4444"))
    library.remove("CCCC3333")

    changes = library.changes(before)

    assert not changes.full
    assert [(i.key, i.doi) for i in changes.items] == [("BBBB2222", "10.1/b"), ("DDDD4444", None)]
    assert changes.deleted == ["CCCC3333"]
    assert changes.version > before
    assert library.changes(changes.version).items == []


def test_a_merge_gives_the_kept_item_the_others_pdf_and_trashes_the_other() -> None:
    library = InMemoryLibrary([_item("KEEP1111", doi="10.1/x"), _item("GONE2222", pdf=PDF)], {})
    before = library.changes(None).version

    library.merge("KEEP1111", "GONE2222")

    assert library.item("KEEP1111").pdf == PDF
    assert library.item("KEEP1111").replaces == ["GONE2222"]
    with pytest.raises(ItemNotFoundError):
        library.item("GONE2222")
    changes = library.changes(before)
    assert [i.key for i in changes.items] == ["KEEP1111"]
    assert changes.deleted == ["GONE2222"]


def test_merging_an_unknown_item_changes_nothing() -> None:
    library = InMemoryLibrary([_item("KEEP1111")], {})
    before = library.changes(None).version

    with pytest.raises(ItemNotFoundError, match="NOPE2345"):
        library.merge("KEEP1111", "NOPE2345")

    assert library.changes(before).items == []


def _zitem(
    key: str,
    version: int,
    *,
    parent: str | None = None,
    item_type: str = "journalArticle",
    doi: str = "",
    added: str = "2026-01-01T00:00:00Z",
    **extra: Any,
) -> dict[str, Any]:
    """A Web API item as Zotero returns it, cut to the fields the adapter reads."""
    data: dict[str, Any] = {
        "key": key, "version": version, "itemType": item_type, "title": f"Title of {key}",
        "creators": [{"creatorType": "author", "firstName": "Ann", "lastName": "Lee"}],
        "DOI": doi, "dateAdded": added, "collections": [], "tags": [], "relations": {},
    } | extra  # fmt: skip
    if parent is not None:
        data["parentItem"] = parent
    return {"key": key, "version": version, "meta": {"parsedDate": "2020-05-01"}, "data": data}


def _zpdf(key: str, version: int, parent: str) -> dict[str, Any]:
    return _zitem(
        key, version, parent=parent, item_type="attachment",
        contentType="application/pdf", linkMode="imported_file", filename=f"{key}.pdf",
    )  # fmt: skip


class SyncingZotero:
    """Zotero's Web API for a library that changes: items, trash and deletions by version."""

    def __init__(self, version: int, items: list[dict[str, Any]]) -> None:
        self.version = version
        self.items = {i["key"]: i for i in items}
        self.trashed: set[str] = set()
        self.deleted: dict[str, int] = {}
        self.requests: list[httpx.Request] = []
        self.writes: list[tuple[str, dict[str, Any], str | None]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        since = int(request.url.params.get("since", "0"))
        headers = {"Last-Modified-Version": str(self.version)}
        path = request.url.path.removeprefix("/users/1234567")
        if request.method == "PATCH":
            return self._patch(path.removeprefix("/items/"), request)
        live = [i for k, i in self.items.items() if k not in self.trashed]
        match path.split("/")[1:]:
            case ["items"]:
                changed = [i for i in live if i["version"] > since]
                start, limit = int(request.url.params["start"]), int(request.url.params["limit"])
                headers["Total-Results"] = str(len(changed))
                return httpx.Response(200, json=changed[start : start + limit], headers=headers)
            case ["items", "trash"]:
                assert request.url.params["format"] == "keys"
                keys = [k for k in self.trashed if self.items[k]["version"] > since]
                return httpx.Response(200, text="\n".join(keys), headers=headers)
            case ["deleted"]:
                gone = [k for k, v in self.deleted.items() if v > since]
                return httpx.Response(200, json={"items": gone}, headers=headers)
            case ["items", key] if key in self.items:
                return httpx.Response(200, json=self.items[key], headers=headers)
            case ["items", key, "children"]:
                kids = [i for i in live if i["data"].get("parentItem") == key]
                return httpx.Response(200, json=kids, headers=headers)
            case _:
                return httpx.Response(404, text="Not found")

    def _patch(self, key: str, request: httpx.Request) -> httpx.Response:
        item = self.items[key]
        expected = request.headers.get("If-Unmodified-Since-Version")
        if expected != str(item["version"]):
            return httpx.Response(412, text="Precondition failed")
        body: dict[str, Any] = json.loads(request.content)
        self.writes.append((key, body, expected))
        self.version += 1
        item["version"] = item["data"]["version"] = self.version
        item["data"] |= body
        if body.get("deleted"):
            self.trashed.add(key)
        return httpx.Response(204, headers={"Last-Modified-Version": str(self.version)})

    def touch(self, key: str) -> None:
        self.version += 1
        self.items[key]["version"] = self.items[key]["data"]["version"] = self.version

    def library(self) -> ZoteroLibrary:
        transport = httpx.MockTransport(self.handle)
        api = httpx.Client(base_url="https://api.zotero.org/users/1234567", transport=transport)
        return ZoteroLibrary(
            api, httpx.Client(transport=transport), storage_dir=None, cache_dir=Path("unused")
        )


def test_zotero_reads_the_whole_library_in_pages_with_pdfs_from_the_same_read() -> None:
    items = [_zitem(f"ITEM{n:04d}", 10 + n) for n in range(150)]
    zotero = SyncingZotero(500, [*items, _zpdf("PDF00001", 300, parent="ITEM0007")])

    changes = zotero.library().changes(None)

    assert changes.full
    assert changes.version == 500
    assert len(changes.items) == 150
    seventh = next(i for i in changes.items if i.key == "ITEM0007")
    assert seventh.pdf == Attachment(key="PDF00001", filename="PDF00001.pdf")
    assert seventh.creators == ["Lee, Ann"]
    assert seventh.year == 2020
    assert seventh.added is not None and seventh.added.year == 2026
    assert [r.url.path.rsplit("/", 1)[-1] for r in zotero.requests] == ["items", "items"]


def test_zotero_changes_since_a_version_carry_each_items_pdf_and_what_went() -> None:
    zotero = SyncingZotero(
        100,
        [
            _zitem("EDIT1111", 50), _zpdf("PDFE1111", 51, parent="EDIT1111"),
            _zitem("NEWPDF22", 60), _zpdf("PDFN2222", 61, parent="NEWPDF22"),
            _zitem("TRASH333", 70), _zitem("SAME4444", 80),
        ],
    )  # fmt: skip
    zotero.touch("EDIT1111")  # its metadata edited
    zotero.touch("PDFN2222")  # only its PDF re-uploaded
    zotero.trashed.add("TRASH333")
    zotero.touch("TRASH333")
    zotero.version += 1
    zotero.deleted["GONE5555"] = zotero.version

    changes = zotero.library().changes(100)

    assert not changes.full
    assert changes.version == zotero.version
    assert {(i.key, i.pdf.key if i.pdf else None) for i in changes.items} == {
        ("EDIT1111", "PDFE1111"),
        ("NEWPDF22", "PDFN2222"),
    }
    assert sorted(changes.deleted) == ["GONE5555", "TRASH333"]


def test_a_zotero_merge_moves_collections_tags_and_children_and_trashes_the_other() -> None:
    zotero = SyncingZotero(
        100,
        [
            _zitem("KEEP1111", 10, doi="10.1/x", collections=["COLA"], tags=[{"tag": "a"}]),
            _zitem(
                "GONE2222", 20, doi="10.1/x", collections=["COLA", "COLB"],
                tags=[{"tag": "a"}, {"tag": "b"}],
                relations={"dc:relation": "http://zotero.org/users/1234567/items/REL00001"},
            ),
            _zpdf("PDFG2222", 21, parent="GONE2222"),
            _zitem("NOTE2222", 22, parent="GONE2222", item_type="note"),
        ],
    )  # fmt: skip
    library = zotero.library()

    library.merge("KEEP1111", "GONE2222")

    kept = zotero.writes[0]
    assert kept[0] == "KEEP1111" and kept[2] == "10"
    assert kept[1]["collections"] == ["COLA", "COLB"]
    assert kept[1]["tags"] == [{"tag": "a"}, {"tag": "b"}]
    assert kept[1]["relations"] == {
        "dc:relation": ["http://zotero.org/users/1234567/items/REL00001"],
        "dc:replaces": ["http://zotero.org/users/1234567/items/GONE2222"],
    }
    assert "DOI" not in kept[1]  # the kept item's fields win
    assert {(k, b["parentItem"]) for k, b, _ in zotero.writes[1:3]} == {
        ("PDFG2222", "KEEP1111"),
        ("NOTE2222", "KEEP1111"),
    }
    assert zotero.writes[3][:2] == ("GONE2222", {"deleted": 1})
    assert library.item("KEEP1111").pdf == Attachment(key="PDFG2222", filename="PDFG2222.pdf")
    assert library.item("KEEP1111").replaces == ["GONE2222"]
    assert library.changes(100).deleted == ["GONE2222"]


def test_a_zotero_merge_of_an_unknown_item_writes_nothing() -> None:
    zotero = SyncingZotero(100, [_zitem("KEEP1111", 10)])

    with pytest.raises(ItemNotFoundError, match="NOPE2345"):
        zotero.library().merge("KEEP1111", "NOPE2345")

    assert zotero.writes == []


@pytest.mark.parametrize(
    "reply",
    [
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("No route to host", request=r)),
        lambda r: httpx.Response(503, text="Service unavailable"),
        lambda r: httpx.Response(200, json=[]),  # no Last-Modified-Version
    ],
    ids=["unreachable", "5xx", "no-version"],
)
def test_zotero_that_cannot_say_what_changed_is_unavailable(
    reply: Callable[[httpx.Request], httpx.Response],
) -> None:
    api = httpx.Client(
        base_url="https://api.zotero.org/users/1234567", transport=httpx.MockTransport(reply)
    )
    library = ZoteroLibrary(api, httpx.Client(), storage_dir=None, cache_dir=Path("unused"))

    with pytest.raises(LibraryUnavailableError):
        library.changes(None)
