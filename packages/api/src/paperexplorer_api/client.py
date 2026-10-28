"""Typed HTTP client for the API; the web app's only way to reach data."""

from collections.abc import Mapping
from typing import Any, Protocol

import httpx
from pydantic import TypeAdapter

from paperexplorer_api.schemas import CitationDetail, Health, WorkSummary, WorkText

_WORK_SUMMARIES = TypeAdapter(list[WorkSummary])


class _Response(Protocol):
    @property
    def status_code(self) -> int: ...
    @property
    def headers(self) -> Mapping[str, str]: ...
    def json(self, **kwargs: Any) -> Any: ...
    def raise_for_status(self) -> object: ...


class HttpClient(Protocol):
    """What `ApiClient` needs of an HTTP client: an `httpx.Client`, or Starlette's `TestClient`.

    Starlette's `TestClient` is an `httpx2.Client` once httpx2 is installed (pydantic-ai brings
    it), so the client is typed by what it does rather than by its class.
    """

    def get(self, url: str) -> _Response:
        """GET `url`, relative to the client's base URL."""
        ...


class NotFoundError(LookupError):
    """The API has no such Work or Citation (HTTP 404)."""


class ApiClient:
    """Thin typed wrapper over the API's JSON endpoints."""

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @classmethod
    def from_url(cls, base_url: str, *, timeout: float = 10.0) -> ApiClient:
        """Client for an API at `base_url`."""
        return cls(httpx.Client(base_url=base_url, timeout=timeout))

    def health(self) -> Health:
        """Fetch the API's health."""
        return Health.model_validate(self._get("/health"))

    def works(self) -> list[WorkSummary]:
        """Every Work, with its Stage status."""
        return _WORK_SUMMARIES.validate_python(self._get("/works"))

    def work(self, work_id: int) -> WorkText:
        """One Work with its text and Citation spans; `NotFoundError` if it has no text."""
        return WorkText.model_validate(self._get(f"/works/{work_id}"))

    def citation(self, citation_id: int) -> CitationDetail:
        """One Citation with its Citation Link and Reference."""
        return CitationDetail.model_validate(self._get(f"/citations/{citation_id}"))

    def _get(self, path: str) -> Any:
        response = self._http.get(path)
        if response.status_code == 404:
            is_json = response.headers.get("content-type", "").startswith("application/json")
            raise NotFoundError(response.json().get("detail", path) if is_json else path)
        response.raise_for_status()
        return response.json()
