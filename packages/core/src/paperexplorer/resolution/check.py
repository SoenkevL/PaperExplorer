"""A Registry Check: References looked up in Crossref and OpenAlex, only when the reader asks.

The local pass runs first; each layer then asks the registries only for what neither the
Library nor the cache answers, keeps every response as it lands, and runs the local pass again.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Sequence
from itertools import batched
from math import ceil
from typing import Literal

import psycopg
from psycopg.rows import TupleRow
from pydantic import BaseModel, JsonValue

from paperexplorer.domain import Layer, RegistrySource, Resolution, ResolutionState, WorkId
from paperexplorer.parsing import Reference
from paperexplorer.resolution.layers import (
    bibliographic_key,
    bibliographic_query,
    doi_key,
    needs_bibliographic,
    title_key,
)
from paperexplorer.resolution.local import as_resolved, counts, resolve
from paperexplorer.resolution.registries import (
    BATCH,
    OPENALEX_LIST_COST,
    OPENALEX_SEARCH_COST,
    BudgetSpentError,
    Registries,
)
from paperexplorer.store import RegistryResponses, Resolutions

logger = logging.getLogger(__name__)


class ReferenceChecked(BaseModel):
    """One Reference's Resolution, saved as the Registry Check lands it."""

    kind: Literal["reference"] = "reference"
    resolution: Resolution


class RegistryCheckFinished(BaseModel):
    """The Registry Check went through every layer: its References by level and state."""

    kind: Literal["finished"] = "finished"
    counts: dict[str, int]


class BudgetSpent(BaseModel):
    """OpenAlex's day is spent: the Check stopped, every Resolution and response so far kept."""

    kind: Literal["budget_spent"] = "budget_spent"
    left: int  # References not yet checked; try after 00:00 UTC

    @property
    def message(self) -> str:
        """What the reader is told."""
        return f"OpenAlex budget spent, {self.left} References left, try after 00:00 UTC"


RegistryCheckEvent = ReferenceChecked | RegistryCheckFinished | BudgetSpent


class CheckCost(BaseModel):
    """What a Registry Check would ask, at most, before it starts.

    Title and bibliographic searches are an upper bound: a DOI hit settles a Reference before
    its title is searched. Only OpenAlex's calls cost, out of its daily budget.
    """

    unchecked: int  # Unchecked References, as the Work page's button counts them
    crossref_batches: int
    openalex_batches: int
    title_searches: int
    bibliographic_searches: int
    openalex_usd: float


def estimate(
    conn: psycopg.Connection[TupleRow],
    citing: WorkId,
    refs: Sequence[Reference],
    *,
    refresh: bool = False,
) -> CheckCost:
    """What a Registry Check of these References of `citing` would ask; no registry is called."""
    cache = RegistryResponses(conn)
    current = {r.reference: r for r in resolve(conn, citing, refs)}
    pending = [ref for ref, _ in as_resolved(conn, refs) if not _settled(current[ref.id], refresh)]

    def asked(source: RegistrySource, keys: Collection[str | None]) -> list[str]:
        return _unanswered(cache, source, keys, refresh=refresh)

    dois = [doi_key(r) for r in pending]
    titles = asked(RegistrySource.OPENALEX_TITLE, [title_key(r) for r in pending])
    # Layer 3 is for a low title match, and an unasked title search may yet bring one.
    printed = [
        bibliographic_key(r)
        for r in pending
        if needs_bibliographic(r, current[r.id]) or title_key(r) in titles
    ]
    crossref = ceil(len(asked(RegistrySource.CROSSREF_DOI, dois)) / BATCH)
    openalex = ceil(len(asked(RegistrySource.OPENALEX_DOI, dois)) / BATCH)
    return CheckCost(
        unchecked=sum(r.state is ResolutionState.UNCHECKED for r in current.values()),
        crossref_batches=crossref,
        openalex_batches=openalex,
        title_searches=len(titles),
        bibliographic_searches=len(asked(RegistrySource.CROSSREF_BIBLIOGRAPHIC, printed)),
        openalex_usd=openalex * OPENALEX_LIST_COST + len(titles) * OPENALEX_SEARCH_COST,
    )


def _settled(r: Resolution, refresh: bool) -> bool:
    """Whether the local pass settles a Reference before any registry is asked.

    On refresh only the Library and the reader do: what the cache resolved is asked again.
    """
    return r.firm and (not refresh or r.layer in (Layer.LIBRARY, Layer.READER))


def _unanswered(
    cache: RegistryResponses, source: RegistrySource, keys: Collection[str | None], *, refresh: bool
) -> list[str]:
    """The keys to ask: all of them on refresh, else those the cache does not answer."""
    wanted = sorted({k for k in keys if k is not None})
    answered: set[str] = set() if refresh else cache.fresh(source, wanted)
    return [k for k in wanted if k not in answered]


async def registry_check(
    conn: psycopg.Connection[TupleRow],
    citing: WorkId,
    refs: Sequence[Reference],
    registries: Registries,
    *,
    refresh: bool = False,
) -> AsyncIterator[RegistryCheckEvent]:
    """Check these References of `citing`, yielding each one's Resolution as it lands.

    The caller syncs the Library first. A Reference the Library matches at certain or high, or
    the reader chose for, is done without a registry call; `refresh` asks the registries again
    for the rest, past the cache. Rows are written on `conn` as they land: an autocommit
    connection keeps them when the Check stops partway, on a spent budget or a `RegistryError`.
    """
    cache, saved = RegistryResponses(conn), Resolutions(conn)
    pending = {ref.id: ref for ref, _ in as_resolved(conn, refs)}
    done: list[Resolution] = []

    def settle(final: Callable[[Resolution], bool]) -> list[ReferenceChecked]:
        """Run the local pass on what is pending; save and hand back what it settled."""
        landed: list[ReferenceChecked] = []
        for r in resolve(conn, citing, [ref for ref in refs if ref.id in pending]):
            if final(r):
                saved.save(citing, r)
                done.append(r)
                del pending[r.reference]
                landed.append(ReferenceChecked(resolution=r))
        return landed

    def unanswered(source: RegistrySource, keys: Collection[str | None]) -> list[str]:
        return _unanswered(cache, source, keys, refresh=refresh)

    async def ask_each(
        source: RegistrySource,
        keys: Collection[str | None],
        search: Callable[[str], Awaitable[JsonValue]],
    ) -> None:
        """One search per key the cache does not answer, each answer kept as it lands."""

        async def one(key: str) -> None:
            found = await search(key)
            cache.record(source, key, found or None, hit=bool(found))

        async with asyncio.TaskGroup() as group:  # one failing cancels the rest
            for key in unanswered(source, keys):
                group.create_task(one(key))

    async def ask_batches(
        source: RegistrySource,
        keys: Collection[str | None],
        lookup: Callable[[Collection[str]], Awaitable[dict[str, JsonValue]]],
    ) -> None:
        """DOIs the cache does not answer, a hundred to a call, each record kept as it lands."""

        async def one(batch: tuple[str, ...]) -> None:
            for doi, record in (await lookup(batch)).items():
                cache.record(source, doi, record, hit=record is not None)

        async with asyncio.TaskGroup() as group:
            for batch in batched(unanswered(source, keys), BATCH, strict=False):
                group.create_task(one(batch))

    async with registries:
        for event in settle(lambda r: _settled(r, refresh)):
            yield event
        try:
            dois = [doi_key(r) for r in pending.values()]
            async with asyncio.TaskGroup() as group:
                group.create_task(
                    ask_batches(RegistrySource.CROSSREF_DOI, dois, registries.crossref_dois)
                )
                group.create_task(
                    ask_batches(RegistrySource.OPENALEX_DOI, dois, registries.openalex_dois)
                )
            for event in settle(lambda r: r.firm):
                yield event
            titles = [title_key(r) for r in pending.values()]
            await ask_each(RegistrySource.OPENALEX_TITLE, titles, registries.openalex_title)
            for event in settle(lambda r: r.firm):
                yield event
            current = {r.reference: r for r in resolve(conn, citing, list(pending.values()))}
            printed = {
                bibliographic_key(ref): bibliographic_query(ref)
                for ref in pending.values()
                if needs_bibliographic(ref, current.get(ref.id))
            }
            await ask_each(
                RegistrySource.CROSSREF_BIBLIOGRAPHIC,
                list(printed),
                lambda key: registries.crossref_bibliographic(printed[key]),
            )
        except ExceptionGroup as group:
            if group.subgroup(BudgetSpentError) is None:
                raise _first(group) from group
            logger.warning("OpenAlex budget spent with %d References left", len(pending))
            for event in settle(lambda r: r.firm):
                yield event
            yield BudgetSpent(left=len(pending))
            return
        for event in settle(lambda _: True):
            yield event
    logger.info("Registry Check of Work %s: %s", citing, counts(done))
    yield RegistryCheckFinished(counts=counts(done))


def _first(group: BaseExceptionGroup[Exception]) -> Exception:
    """The first error a task group collected, out of any nesting."""
    first = group.exceptions[0]
    return _first(first) if isinstance(first, BaseExceptionGroup) else first
