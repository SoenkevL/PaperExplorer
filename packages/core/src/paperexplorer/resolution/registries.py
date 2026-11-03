"""Crossref and OpenAlex over HTTP, within their limits: one limiter per host and request type."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Collection
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import JsonValue

from paperexplorer.config import Settings
from paperexplorer.domain import normalize_doi

logger = logging.getLogger(__name__)

Json = dict[str, Any]
Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[None]]

CROSSREF = "https://api.crossref.org/works"
OPENALEX = "https://api.openalex.org/works"
BATCH = 100  # DOIs per batch call, to either registry
CANDIDATES = 5  # records per title or bibliographic search
# Crossref refuses a query past ~6,000 characters (431, then 414); a bibliography entry is far
# shorter, so anything longer is GROBID running entries together.
QUERY_CHARS = 1000
TRIES = 3
# An OpenAlex call's cost in US dollars of the day's budget (research/2026-10-doi-resolution.md).
OPENALEX_LIST_COST = 0.0001
OPENALEX_SEARCH_COST = 0.001
# Any record will do: a singleton costs nothing and still reports the day's budget.
OPENALEX_SINGLETON = f"{OPENALEX}/W2741809807"


class RegistryError(Exception):
    """A registry kept failing: three tries, each a 429 or a 5xx or no answer."""


class BudgetSpentError(Exception):
    """OpenAlex's daily budget is spent; it resets at 00:00 UTC."""


class _Limiter:
    """At most `rate` calls a second and `concurrency` at once; tightened, never loosened."""

    def __init__(self, rate: float, concurrency: int, clock: Clock, sleep: Sleep) -> None:
        self.rate, self.concurrency = rate, concurrency
        self._clock, self._sleep = clock, sleep
        self._next = 0.0
        self._running = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._free = asyncio.Condition()

    def _settle_on_loop(self) -> None:
        """Start afresh on a new event loop: a `Condition` serves one, and calls end with theirs."""
        loop = asyncio.get_running_loop()
        if loop is not self._loop:
            self._loop, self._running, self._free = loop, 0, asyncio.Condition()

    @asynccontextmanager
    async def slot(self):
        self._settle_on_loop()
        async with self._free:
            await self._free.wait_for(lambda: self._running < self.concurrency)
            self._running += 1
        try:
            now = self._clock()
            start, self._next = max(now, self._next), max(now, self._next) + 1 / self.rate
            if start > now:
                await self._sleep(start - now)
            yield
        finally:
            async with self._free:
                self._running -= 1
                self._free.notify_all()

    def hold(self, seconds: float) -> None:
        """Make the next call wait at least this long, as a 429's `Retry-After` asks."""
        self._next = max(self._next, self._clock() + seconds)

    def tighten(self, rate: float | None, concurrency: int | None) -> None:
        if rate is not None and 0 < rate < self.rate:
            logger.info("Registry limit tightened to %.2f/s", rate)
            self.rate = rate
        if concurrency is not None and 0 < concurrency < self.concurrency:
            self.concurrency = concurrency


class RegistryLimits:
    """One limiter per host and request type: the hosts count a client's calls, not its Checks."""

    def __init__(
        self, settings: Settings, *, clock: Clock = time.monotonic, sleep: Sleep = asyncio.sleep
    ) -> None:
        polite = settings.crossref_mailto is not None
        keyed = settings.openalex_api_key is not None
        self.crossref = _Limiter(3 if polite else 1, 3 if polite else 1, clock, sleep)
        self.crossref_single = _Limiter(10 if polite else 5, 3 if polite else 1, clock, sleep)
        self.openalex = _Limiter(25 if keyed else 8, 25 if keyed else 8, clock, sleep)


class Registries:
    """Crossref and OpenAlex as a Registry Check asks them; tests pass `transport` and a clock.

    Checks running side by side share a `RegistryLimits`, through `shared_registries`.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        limits: RegistryLimits | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._mailto = settings.crossref_mailto
        key = settings.openalex_api_key
        self._api_key = key.get_secret_value() if key else None
        self._transport = transport
        self._sleep = sleep
        limits = limits or RegistryLimits(settings, clock=clock, sleep=sleep)
        self._crossref = limits.crossref
        self._crossref_single = limits.crossref_single
        self._openalex = limits.openalex
        self._client: httpx.AsyncClient | None = None
        self.budget_usd: float | None = None  # what is left of OpenAlex's day, once a call says

    async def __aenter__(self) -> Registries:
        self._client = httpx.AsyncClient(transport=self._transport, timeout=30)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def budget(self) -> float | None:
        """What is left of OpenAlex's day in US dollars, asked of a free singleton record."""
        await self._get(self._openalex, OPENALEX_SINGLETON, self._keyed(), cost=0.0)
        return self.budget_usd

    async def crossref_dois(self, dois: Collection[str]) -> dict[str, JsonValue]:
        """Crossref's item for each of up to 100 normalized DOIs, `None` where it has none."""
        # A comma ends a filter's value, and Crossref has no way to quote one.
        listed = [d for d in dois if "," not in d]
        found = (
            await self._crossref_list({"filter": ",".join(f"doi:{d}" for d in listed)}, BATCH)
            if listed
            else []
        )
        for doi in dois:
            if "," in doi and (record := await self._crossref_record(doi)) is not None:
                found.append(record)
        return _by_doi(dois, found, "DOI")

    async def openalex_dois(self, dois: Collection[str]) -> dict[str, JsonValue]:
        """OpenAlex's Work for each of up to 100 normalized DOIs, `None` where it has none."""
        found = await self._openalex_list(
            {"filter": "doi:" + "|".join(_filter_value(d) for d in dois), "per-page": str(BATCH)},
            OPENALEX_LIST_COST,
        )
        return _by_doi(dois, found, "doi")

    async def openalex_title(self, title: str) -> list[JsonValue]:
        """OpenAlex's Works whose title matches the search, five at most."""
        return [
            *await self._openalex_list(
                {"filter": f"title.search:{title[:QUERY_CHARS]}", "per-page": str(CANDIDATES)},
                OPENALEX_SEARCH_COST,
            )
        ]

    async def crossref_bibliographic(self, text: str) -> list[JsonValue]:
        """Crossref's items for a printed Reference string, five at most."""
        query = {"query.bibliographic": text[:QUERY_CHARS]}
        return [*await self._crossref_list(query, CANDIDATES)]

    async def _crossref_list(self, params: dict[str, str], rows: int) -> list[Json]:
        params = params | {"rows": str(rows)} | ({"mailto": self._mailto} if self._mailto else {})
        response = await self._get(self._crossref, CROSSREF, params)
        self._follow_crossref_headers(response)
        items: list[Json] = response.json()["message"]["items"]
        return items

    async def _crossref_record(self, doi: str) -> Json | None:
        """Crossref's record of exactly this DOI, `None` when it answers that it has none."""
        url = f"{CROSSREF}/{quote(doi, safe='')}"
        params = {"mailto": self._mailto} if self._mailto else {}
        response = await self._get(self._crossref_single, url, params, absent_ok=True)
        if response.status_code == 404:
            return None
        record: Json = response.json()["message"]
        return record

    def _follow_crossref_headers(self, response: httpx.Response) -> None:
        limit, interval = (
            response.headers.get(h) for h in ("x-rate-limit-limit", "x-rate-limit-interval")
        )
        concurrency = response.headers.get("x-concurrency-limit")
        if limit and interval and interval.endswith("s") and interval[:-1].isdigit():
            self._crossref.tighten(float(limit) / max(int(interval[:-1]), 1), None)
        if concurrency and concurrency.isdigit():
            self._crossref.tighten(None, int(concurrency))

    async def _openalex_list(self, params: dict[str, str], cost: float) -> list[Json]:
        if self.budget_usd is not None and self.budget_usd < cost:
            raise BudgetSpentError
        response = await self._get(self._openalex, OPENALEX, params | self._keyed(), cost=cost)
        results: list[Json] = response.json()["results"]
        return results

    def _keyed(self) -> dict[str, str]:
        return {"api_key": self._api_key} if self._api_key else {}

    async def _get(
        self,
        limiter: _Limiter,
        url: str,
        params: dict[str, str],
        *,
        cost: float | None = None,
        absent_ok: bool = False,
    ) -> httpx.Response:
        """GET within `limiter`; `cost` is its share of OpenAlex's day; `absent_ok` allows 404."""
        assert self._client is not None, "use Registries as an async context manager"
        for attempt in range(TRIES):
            async with limiter.slot():
                try:
                    response = await self._client.get(url, params=params)
                except httpx.TransportError as e:
                    logger.warning("No answer from %s (try %d): %s", url, attempt + 1, e)
                    await self._sleep(2**attempt)
                    continue
            left = response.headers.get("x-ratelimit-remaining-usd")
            if response.status_code == 429:
                # Crossref's 429 names no wait, and a second is not enough to be let back in.
                wait = response.headers.get("retry-after", "")
                seconds = float(wait) if wait.replace(".", "", 1).isdigit() else 2**attempt
                logger.warning("%s asked to wait %ss (try %d)", url, seconds, attempt + 1)
                # Every call to this host waits, not only this one.
                limiter.hold(seconds)
                if (
                    cost is not None
                    and left
                    and float(left) <= cost
                    and await self._day_spent(cost)
                ):
                    raise BudgetSpentError
                continue
            if cost is not None and left:
                self.budget_usd = float(left)
            if response.status_code >= 500:
                logger.warning("%s answered %d (try %d)", url, response.status_code, attempt + 1)
                await self._sleep(2**attempt)
                continue
            if absent_ok and response.status_code == 404:
                return response
            if response.is_error:
                raise RegistryError(f"{url} answered {response.status_code}")
            return response
        raise RegistryError(f"{url} failed {TRIES} times")

    async def _day_spent(self, cost: float) -> bool:
        """Whether OpenAlex's day cannot pay `cost`, asked of a free singleton record.

        A 429 says `remaining-usd: 0` for a burst as well as for a spent day; only an answer
        that is not a 429 tells them apart. Nothing but 429s means the day is spent.
        """
        assert self._client is not None, "use Registries as an async context manager"
        for attempt in range(TRIES):
            async with self._openalex.slot():
                try:
                    response = await self._client.get(OPENALEX_SINGLETON, params=self._keyed())
                except httpx.TransportError as e:
                    logger.warning("No answer about OpenAlex's budget: %s", e)
                    return False
            if response.status_code != 429:
                left = response.headers.get("x-ratelimit-remaining-usd")
                if left is None:
                    return False
                self.budget_usd = float(left)
                return self.budget_usd < cost
            self._openalex.hold(float(2**attempt))
        return True


def shared_registries(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Clock = time.monotonic,
    sleep: Sleep = asyncio.sleep,
) -> Callable[[], Registries]:
    """Fresh `Registries` for each Check, all within one set of limits."""
    limits = RegistryLimits(settings, clock=clock, sleep=sleep)
    return lambda: Registries(settings, limits=limits, transport=transport, sleep=sleep)


def _filter_value(doi: str) -> str:
    """A DOI as an OpenAlex filter value: quoted when it holds what separates filters or values."""
    return f'"{doi}"' if "," in doi or "|" in doi else doi


def _by_doi(dois: Collection[str], records: list[Json], field: str) -> dict[str, JsonValue]:
    """Each asked DOI's record; APA's `//` form also finds a record under the single slash."""
    found: dict[str, Json] = {}
    for record in records:
        if doi := record.get(field):
            found[normalize_doi(doi)] = record
    return {d: found.get(d) or found.get(d.replace("//", "/", 1)) for d in dois}
