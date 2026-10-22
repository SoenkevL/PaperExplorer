"""The Library in Zotero, read through its Web API; PDFs from local storage or downloaded."""

import logging
from pathlib import Path
from typing import Any, Self

import httpx

from paperexplorer.config import Settings
from paperexplorer.domain import Attachment, LibraryItem
from paperexplorer.library.port import ItemNotFoundError

logger = logging.getLogger(__name__)

API = "https://api.zotero.org"
# Attachments whose file Zotero stores; a linked file lives on someone's disk instead.
_STORED = frozenset({"imported_file", "imported_url"})


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
        response = self._api.get(f"/items/{key}")
        if response.status_code == 404:
            raise ItemNotFoundError(f"Zotero has no item {key}")
        response.raise_for_status()
        item: dict[str, Any] = response.json()
        data: dict[str, Any] = item["data"]
        children = self._api.get(f"/items/{key}/children")
        children.raise_for_status()
        creators: list[dict[str, str]] = data.get("creators", [])
        authors = [c for c in creators if c.get("creatorType") == "author"] or creators
        parsed_date: str = item.get("meta", {}).get("parsedDate", "")
        return LibraryItem(
            key=key,
            title=data.get("title") or None,
            creators=[_name(c) for c in authors],
            year=int(parsed_date[:4]) if parsed_date[:4].isdigit() else None,
            doi=data.get("DOI") or None,
            pdf=_stored_pdf(children.json()),
        )

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
