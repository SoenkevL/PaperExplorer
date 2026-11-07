"""The Library in Zotero, read through its Web API; PDFs from local storage or downloaded."""

import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Self

import httpx

from paperexplorer.config import Settings
from paperexplorer.domain import (
    COLLECTION_DEPTH,
    Attachment,
    Collection,
    LibraryChanges,
    LibraryItem,
    NewItem,
)
from paperexplorer.library.port import (
    PAPEREXPLORER,
    SEED_TAG,
    ItemNotFoundError,
    LibraryUnavailableError,
    LibraryWriteError,
    OutsidePaperExplorerError,
)

logger = logging.getLogger(__name__)

API = "https://api.zotero.org"
# Attachments whose file Zotero stores; a linked file lives on someone's disk instead.
_STORED = frozenset({"imported_file", "imported_url"})
# Children of a Library Item, not Library Items themselves.
_CHILD_TYPES = frozenset({"attachment", "note", "annotation"})
_PAGE = 100  # the Web API's largest page
_GUARD_TTL = 60.0  # seconds the guard trusts what it read of a Collection
ADDED_BY = "Added by PaperExplorer"  # in `extra`, so the reader can find what PaperExplorer made
# The fields a new item fills, by item type; Zotero refuses a field its type lacks. Every type has
# `DOI`, `url` and `extra` (docs/research/2026-11-zotero-writes.md).
_PUBLICATION = {
    "journalArticle": "publicationTitle",
    "conferencePaper": "proceedingsTitle",
    "bookSection": "bookTitle",
    "preprint": "repository",
}
_VOLUME_ISSUE = frozenset({"journalArticle"})
_PAGES = frozenset({"journalArticle", "conferencePaper", "bookSection"})


class ZoteroLibrary:
    """Zotero's Web API for metadata; PDFs from `ZOTERO_STORAGE_DIR`, else downloaded once."""

    def __init__(
        self, api: httpx.Client, files: httpx.Client, *, storage_dir: Path | None, cache_dir: Path
    ) -> None:
        """`api` is based at the library (`…/users/<id>`) and sends the key; `files` does not."""
        self._api = api
        self._files = files
        self._storage = storage_dir
        self._cache = cache_dir / "pdfs"
        # What the guard last read of each Collection, and when: read again after `_GUARD_TTL`,
        # so a rename, move or trash in Zotero stops writes within a minute.
        self._parents: dict[str, tuple[float, str | None]] = {}
        self._roots: dict[str, float] = {}

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """The library named by the `PE_ZOTERO_*` settings."""
        if not settings.zotero_library_id or settings.zotero_api_key is None:
            raise ValueError("Zotero needs PE_ZOTERO_LIBRARY_ID and PE_ZOTERO_API_KEY")
        api = httpx.Client(
            base_url=f"{API}/{settings.zotero_library_type}s/{settings.zotero_library_id}",
            headers={
                "Zotero-API-Key": settings.zotero_api_key.get_secret_value(),
                "Zotero-API-Version": "3",
            },
            timeout=30.0,
        )
        files = httpx.Client(timeout=120.0, follow_redirects=True)
        return cls(
            api, files, storage_dir=settings.zotero_storage_dir, cache_dir=settings.cache_dir
        )

    def item(self, key: str) -> LibraryItem:
        """The Library Item with this key, with its stored PDF attachment if it has one."""
        return _library_item(self._raw(key), self._children(key))

    def changes(self, since: int | None) -> LibraryChanges:
        """Zotero's sync: items and Collections changed since a version, then those that went.

        A full read takes each item's PDF from the attachments in the same listing; after one,
        a changed item's children are read, and so is an attachment's parent that did not change.
        A trashed Collection counts as deleted, and a full read leaves out its subcollections too,
        which Zotero does not mark.
        """
        try:
            return self._changes(since)
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise LibraryUnavailableError(f"Zotero could not say what changed: {e!r}") from e

    def _changes(self, since: int | None) -> LibraryChanges:
        version, listed = self._listing("/items", since)
        _, listed_collections = self._listing("/collections", since)
        collections = [c for c in listed_collections if not c["data"].get("deleted")]
        trashed = [c["key"] for c in listed_collections if c["data"].get("deleted")]
        regular = {i["key"]: i for i in listed if _is_regular(i)}
        children: dict[str, list[dict[str, Any]]] = {}
        for i in listed:
            if parent := i["data"].get("parentItem"):
                children.setdefault(parent, []).append(i)
        if since is None:
            items = [_library_item(i, children.get(k, [])) for k, i in regular.items()]
            live = _outside_trash(listed_collections)
            return LibraryChanges(
                version=version,
                items=items,
                collections=[_collection(c) for c in collections if c["key"] in live],
                full=True,
            )
        for parent in children.keys() - regular.keys():
            response = self._api.get(f"/items/{parent}")
            if response.status_code == 404:  # the attachment's parent went too
                continue
            response.raise_for_status()
            if _is_regular(response.json()):
                regular[parent] = response.json()
        items: list[LibraryItem] = []
        for key, item in regular.items():
            items.append(_library_item(item, self._children(key)))
        trash = self._api.get("/items/trash", params={"since": since, "format": "keys"})
        trash.raise_for_status()
        deleted = self._api.get("/deleted", params={"since": since})
        deleted.raise_for_status()
        answer: dict[str, list[str]] = deleted.json()
        gone = [k for k in trash.text.split() if k] + answer.get("items", [])
        return LibraryChanges(
            version=version,
            items=items,
            deleted=gone,
            collections=[_collection(c) for c in collections],
            deleted_collections=trashed + answer.get("collections", []),
        )

    def merge(self, keep: str, other: str) -> None:
        """Zotero desktop's merge of `other` into `keep`, then `other` to the trash.

        `keep` takes `other`'s collections, tags, relations and children and a `dc:replaces`
        relation; its own fields win.
        """
        kept, gone = self._raw(keep), self._raw(other)
        children = self._children(other)
        k: dict[str, Any] = kept["data"]
        g: dict[str, Any] = gone["data"]
        relations = {p: _uris(v) for p, v in k.get("relations", {}).items()}
        for predicate, uris in g.get("relations", {}).items():
            relations[predicate] = _union(relations.get(predicate, []), _uris(uris))
        relations["dc:replaces"] = _union(relations.get("dc:replaces", []), [self._uri(other)])
        self._patch(
            kept,
            {
                "collections": _union(k.get("collections", []), g.get("collections", [])),
                "tags": _union(k.get("tags", []), g.get("tags", [])),
                "relations": relations,
            },
        )
        for child in children:
            self._patch(child, {"parentItem": keep})
        self._patch(gone, {"deleted": 1})
        logger.info("Merged Zotero item %s into %s; %s is in the trash", other, keep, other)

    def create_paperexplorer_collection(self, key: str) -> None:
        """Post the top-level PaperExplorer Collection with this key, once."""
        self._create("/collections", {"name": PAPEREXPLORER, "parentCollection": False}, key)
        self._parents[key] = (time.monotonic(), None)
        self._roots[key] = time.monotonic()

    def create_collection(self, key: str, name: str, *, within: str) -> None:
        """Post a Collection in the PaperExplorer Collection, once."""
        self._check_root(within)
        self._create("/collections", {"name": name, "parentCollection": within}, key)
        self._parents[key] = (time.monotonic(), within)

    def create_item(self, key: str, new: NewItem, *, collection: str, within: str) -> None:
        """Post a Library Item made as `new` has it, filed in `collection`, once."""
        self._check(collection, within)
        self._create("/items", _item_fields(new) | {"collections": [collection]}, key)

    def file(self, item: str, collection: str, *, within: str) -> None:
        """Patch the item's Collections to include `collection`."""
        self._check(collection, within)
        self._update(item, "collections", lambda held: _union(held, [collection]))

    def unfile(self, item: str, collection: str, *, within: str) -> None:
        """Patch the item's Collections to leave out `collection`."""
        self._check(collection, within)
        self._update(item, "collections", lambda held: [c for c in held if c != collection])

    def tag_seed(self, item: str) -> None:
        """Patch the item's tags to include `pe:seed`."""
        self._update(
            item,
            "tags",
            lambda held: (
                held if any(t.get("tag") == SEED_TAG for t in held) else [*held, {"tag": SEED_TAG}]
            ),
        )

    def untag_seed(self, item: str) -> None:
        """Patch the item's tags to leave out `pe:seed`."""
        self._update(item, "tags", lambda held: [t for t in held if t.get("tag") != SEED_TAG])

    def _check_root(self, within: str) -> None:
        """Raise unless `within` is a top-level Collection named PaperExplorer, not trashed."""
        if time.monotonic() - self._roots.get(within, -_GUARD_TTL) < _GUARD_TTL:
            return
        data = self._collection_data(within)
        if data is None or data["parentCollection"] or data["name"] != PAPEREXPLORER:
            self._roots.pop(within, None)
            raise OutsidePaperExplorerError(f"{within} is not the top-level {PAPEREXPLORER}")
        self._roots[within] = time.monotonic()

    def _check(self, collection: str, within: str) -> None:
        """Raise unless `collection` sits under the PaperExplorer Collection `within`."""
        self._check_root(within)
        key: str | None = collection
        for _ in range(COLLECTION_DEPTH):
            if key is None:
                break
            read = self._parents.get(key)
            if read is None or time.monotonic() - read[0] >= _GUARD_TTL:
                data = self._collection_data(key)
                if data is None:
                    self._parents.pop(key, None)
                    break
                read = (time.monotonic(), data["parentCollection"] or None)
                self._parents[key] = read
            key = read[1]
            if key == within:
                return
        raise OutsidePaperExplorerError(f"{collection} is not in the {PAPEREXPLORER} Collection")

    def _collection_data(self, key: str) -> dict[str, Any] | None:
        """A Collection's `data` as Zotero holds it, `None` if it is missing or trashed."""
        try:
            response = self._api.get(f"/collections/{key}")
        except httpx.HTTPError as e:
            raise LibraryWriteError(f"Zotero could not be reached: {e!r}") from e
        if response.status_code == 404:
            return None
        _raise_for(response)
        data: dict[str, Any] = response.json()["data"]
        return None if data.get("deleted") else data

    def _create(self, path: str, fields: dict[str, Any], key: str) -> None:
        """Post one object with a key made here at version 0; a key Zotero holds is done."""
        try:
            response = self._api.post(path, json=[{"key": key, "version": 0} | fields])
        except httpx.HTTPError as e:
            raise LibraryWriteError(f"Zotero could not be reached: {e!r}") from e
        _raise_for(response)
        failed: dict[str, Any] | None = response.json().get("failed", {}).get("0")
        if failed is not None and failed.get("code") != 412:  # 412: created already
            raise LibraryWriteError(f"Zotero refused {key}: {failed['code']} {failed['message']}")
        logger.info("Created Zotero object %s at %s", key, path)

    def _update(self, item: str, field: str, change: Callable[[list[Any]], list[Any]]) -> None:
        """Read the item, change one array field and patch it at that version, retried once."""
        for attempt in range(2):
            raw = self._raw_for_write(item)
            held: list[Any] = raw["data"].get(field, [])
            wanted = change(held)
            if wanted == held:
                return
            try:
                response = self._api.patch(
                    f"/items/{item}",
                    json={field: wanted},
                    headers={"If-Unmodified-Since-Version": str(raw["data"]["version"])},
                )
            except httpx.HTTPError as e:
                raise LibraryWriteError(f"Zotero could not be reached: {e!r}") from e
            if response.status_code == 412 and attempt == 0:
                continue  # edited since it was read: read it again
            _raise_for(response)
            return

    def _raw_for_write(self, key: str) -> dict[str, Any]:
        try:
            return self._raw(key)
        except httpx.HTTPError as e:
            raise LibraryWriteError(f"Zotero could not read {key}: {e!r}") from e

    def _children(self, key: str) -> list[dict[str, Any]]:
        response = self._api.get(f"/items/{key}/children")
        response.raise_for_status()
        return response.json()

    def _raw(self, key: str) -> dict[str, Any]:
        response = self._api.get(f"/items/{key}")
        if response.status_code == 404:
            raise ItemNotFoundError(f"Zotero has no item {key}")
        response.raise_for_status()
        return response.json()

    def _patch(self, item: dict[str, Any], fields: dict[str, Any]) -> None:
        """Write fields of one item, refused by Zotero if it changed since it was read."""
        response = self._api.patch(
            f"/items/{item['key']}",
            json=fields,
            headers={"If-Unmodified-Since-Version": str(item["version"])},
        )
        response.raise_for_status()

    def _uri(self, key: str) -> str:
        """An item's Zotero URI, as relations name it."""
        return f"http://zotero.org{self._api.base_url.path.rstrip('/')}/items/{key}"

    def _listing(self, path: str, since: int | None) -> tuple[int, list[dict[str, Any]]]:
        """Every item or Collection changed since a version, paged, and the first page's version.

        Those edited mid-read carry a later version, so the next sync reads them again.
        """
        params: dict[str, str | int] = {"format": "json", "limit": _PAGE}
        if since is not None:
            params["since"] = since
        listed: list[dict[str, Any]] = []
        version: int | None = None
        while True:
            response = self._api.get(path, params=params | {"start": len(listed)})
            response.raise_for_status()
            if version is None:
                version = int(response.headers["Last-Modified-Version"])
            page: list[dict[str, Any]] = response.json()
            listed += page
            if not page or len(listed) >= int(response.headers.get("Total-Results", 0)):
                return version, listed

    def pdf(self, attachment: Attachment) -> Path:
        """The PDF in Zotero storage if it is there, else a copy downloaded into the cache."""
        if self._storage is not None:
            stored = self._storage / attachment.key / attachment.filename
            if stored.is_file():
                return stored
        cached = self._cache / f"{attachment.key}.pdf"
        if cached.is_file():
            return cached
        logger.info("Downloading the PDF of attachment %s from Zotero", attachment.key)
        response = self._api.get(f"/items/{attachment.key}/file")
        if response.is_redirect:  # to Zotero's file host, which must not see the API key
            response = self._files.get(response.headers["Location"])
        response.raise_for_status()
        self._cache.mkdir(parents=True, exist_ok=True)
        partial = cached.with_suffix(".part")
        partial.write_bytes(response.content)
        partial.replace(cached)
        return cached


def _raise_for(response: httpx.Response) -> None:
    """Raise `LibraryWriteError` with Zotero's reason for a refused request."""
    if response.is_success:
        return
    reason = response.text.strip()[:200]
    raise LibraryWriteError(f"Zotero answered {response.status_code}: {reason}")


def _item_fields(new: NewItem) -> dict[str, Any]:
    """The Web API fields of a new item as `new` has it, only those its type has."""
    kind = new.item_type
    fields: dict[str, Any] = {"itemType": kind}
    if new.title:
        fields["title"] = new.title
    if new.creators:
        fields["creators"] = [_creator(c) for c in new.creators]
    if new.date:
        fields["date"] = new.date
    if new.doi:
        fields["DOI"] = new.doi
    if new.publication and kind in _PUBLICATION:
        fields[_PUBLICATION[kind]] = new.publication
    if kind in _VOLUME_ISSUE:
        fields |= {k: v for k, v in (("volume", new.volume), ("issue", new.issue)) if v}
    if new.pages and kind in _PAGES:
        fields["pages"] = new.pages
    if new.url:
        fields["url"] = new.url
    return fields | {"extra": ADDED_BY}


def _creator(name: str) -> dict[str, str]:
    """`Last, First` as Zotero's two fields, a name with no comma as its single field."""
    last, comma, first = name.partition(",")
    if not comma:
        return {"creatorType": "author", "name": name.strip()}
    return {"creatorType": "author", "lastName": last.strip(), "firstName": first.strip()}


def _is_regular(item: dict[str, Any]) -> bool:
    """Whether a Web API item is a Library Item rather than an attachment, note or annotation."""
    data: dict[str, Any] = item["data"]
    return data.get("itemType") not in _CHILD_TYPES and "parentItem" not in data


def _library_item(item: dict[str, Any], children: list[dict[str, Any]]) -> LibraryItem:
    """A Web API item and its children as a Library Item."""
    data: dict[str, Any] = item["data"]
    creators: list[dict[str, str]] = data.get("creators", [])
    authors = [c for c in creators if c.get("creatorType") == "author"] or creators
    parsed_date: str = item.get("meta", {}).get("parsedDate", "")
    return LibraryItem(
        key=data["key"],
        title=data.get("title") or None,
        creators=[_name(c) for c in authors],
        year=int(parsed_date[:4]) if parsed_date[:4].isdigit() else None,
        doi=data.get("DOI") or _extra_doi(data.get("extra", "")),
        pdf=_stored_pdf(children),
        added=data.get("dateAdded") or None,
        replaces=[
            u.rsplit("/", 1)[-1] for u in _uris(data.get("relations", {}).get("dc:replaces", []))
        ],
        collections=data.get("collections", []),
    )


# A `DOI: …` line in `extra`, where items of types that once had no DOI field keep theirs.
_EXTRA_DOI = re.compile(r"^\s*doi:\s*(10\.\S+)\s*$", re.IGNORECASE | re.MULTILINE)


def _extra_doi(extra: str) -> str | None:
    """The DOI on a `DOI:` line of an item's `extra`, if it has one."""
    found = _EXTRA_DOI.search(extra)
    return found.group(1) if found else None


def _collection(collection: dict[str, Any]) -> Collection:
    """A Web API Collection; Zotero's `parentCollection` is `false` for a root."""
    data: dict[str, Any] = collection["data"]
    return Collection(
        key=data["key"], name=data["name"], parent_key=data["parentCollection"] or None
    )


def _outside_trash(collections: list[dict[str, Any]]) -> set[str]:
    """The keys of the Collections neither trashed nor under a trashed one.

    Zotero marks only the Collection the reader trashed, not its subcollections.
    """
    parent = {c["key"]: _collection(c).parent_key for c in collections}
    trashed = {c["key"] for c in collections if c["data"].get("deleted")}

    def in_trash(key: str | None, depth: int = 0) -> bool:
        if key is None or depth > COLLECTION_DEPTH:
            return False
        return key in trashed or in_trash(parent.get(key), depth + 1)

    return {key for key in parent if not in_trash(key)}


def _uris(value: str | list[str]) -> list[str]:
    """A relation's objects: Zotero sends one as a string, several as a list."""
    return [value] if isinstance(value, str) else list(value)


def _union[T](ours: list[T], theirs: list[T]) -> list[T]:
    """Ours, then theirs not already among them, in order."""
    return ours + [t for t in theirs if t not in ours]


def _name(creator: dict[str, str]) -> str:
    """A creator as `Last, First`, or as Zotero's single-field name."""
    if "name" in creator:
        return creator["name"]
    return ", ".join(p for p in (creator.get("lastName"), creator.get("firstName")) if p)


def _stored_pdf(children: list[dict[str, Any]]) -> Attachment | None:
    """The first PDF attachment whose file Zotero stores."""
    for child in children:
        data: dict[str, Any] = child["data"]
        if data.get("itemType") != "attachment" or data.get("contentType") != "application/pdf":
            continue
        if data.get("linkMode") in _STORED:
            return Attachment(key=data["key"], filename=data["filename"])
        logger.warning("Attachment %s is a linked file; only stored PDFs are read", data["key"])
    return None
