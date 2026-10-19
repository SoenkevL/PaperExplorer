"""Typed HTTP client for the API; the web app's only way to reach data."""

from typing import Any

import httpx
from pydantic import TypeAdapter

from paperexplorer_api.schemas import CitationDetail, Health, WorkSummary, WorkText

_WORK_SUMMARIES = TypeAdapter(list[WorkSummary])


class NotFoundError(LookupError):
    """The API has no such Work or Citation (HTTP 404)."""


class ApiClient:
    """Thin typed wrapper over the API's JSON endpoints."""

    def __init__(self, http: httpx.Client) -> None:
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
