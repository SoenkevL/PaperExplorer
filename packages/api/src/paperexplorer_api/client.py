"""Typed HTTP client for the API; the web app's only way to reach data."""

import httpx

from paperexplorer_api.schemas import Health


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
        response = self._http.get("/health")
        response.raise_for_status()
        return Health.model_validate(response.json())
