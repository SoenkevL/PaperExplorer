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
