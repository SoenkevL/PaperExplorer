"""The `DoiRegistry` over doi.org's Handle API: free, keyless, the record of every DOI."""

import asyncio
import logging
from collections.abc import Sequence
from typing import Self
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ValidationError

from paperexplorer.config import Settings
from paperexplorer.resolution.registration import DoiRecord, Registration

logger = logging.getLogger(__name__)

TRIES = 3


class _Value(BaseModel):
    type: str
    data: dict[str, object]


class _Answer(BaseModel):
    responseCode: int  # noqa: N815 (the Handle API's own name)
    values: list[_Value] = []


class DoiOrgRegistry:
    """`GET https://doi.org/api/handles/{doi}?type=URL`, `concurrency` at a time.

    responseCode 1 is registered (200, a Handle with no URL, too) and 100 unregistered;
    2, a 5xx after `TRIES` tries, or no connection leaves the DOI unverified.
    """

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        concurrency: int = 10,
        backoff: float = 0.5,
    ) -> None:
        """`transport` replaces the network in tests; `backoff` is the first retry's wait."""
        self._transport = transport
        self._concurrency = concurrency
        self._backoff = backoff

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """doi.org, as many at a time as `PE_DOI_ORG_CONCURRENCY` says."""
        return cls(concurrency=settings.doi_org_concurrency)

    def records(self, dois: Sequence[str]) -> dict[str, DoiRecord]:
        """One record per DOI asked, keyed as given."""
        return asyncio.run(self._records(dois))

    async def _records(self, dois: Sequence[str]) -> dict[str, DoiRecord]:
        limit = asyncio.Semaphore(self._concurrency)
        async with httpx.AsyncClient(
            base_url="https://doi.org", transport=self._transport, timeout=10.0
        ) as http:

            async def one(doi: str) -> DoiRecord:
                async with limit:
                    return await self._record(http, doi)

            found = await asyncio.gather(*(one(d) for d in dois))
        return dict(zip(dois, found, strict=True))

    async def _record(self, http: httpx.AsyncClient, doi: str) -> DoiRecord:
        unverified = DoiRecord(registration=Registration.UNVERIFIED)
        for attempt in range(TRIES):
            if attempt:
                await asyncio.sleep(self._backoff * 2 ** (attempt - 1))
            try:
                response = await http.get(
                    f"/api/handles/{quote(doi, safe='/')}", params={"type": "URL"}
                )
            except httpx.TransportError as e:
                logger.debug("doi.org unreachable for %s: %s", doi, e)
                return unverified
            if response.status_code >= 500:
                continue
            try:
                answer = _Answer.model_validate_json(response.content)
            except ValidationError:
                logger.debug("doi.org answered %s for %s", response.status_code, doi)
                return unverified
            if answer.responseCode == 100:
                return DoiRecord(registration=Registration.UNREGISTERED)
            if answer.responseCode in (1, 200):
                urls = [v.data.get("value") for v in answer.values if v.type == "URL"]
                url = next((u for u in urls if isinstance(u, str)), None)
                return DoiRecord(registration=Registration.REGISTERED, url=url)
            return unverified
        return unverified
