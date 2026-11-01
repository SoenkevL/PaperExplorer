"""A Registry Check: References looked up in Crossref and OpenAlex, only when the reader asks.

The local pass runs first; each layer then asks the registries only for what neither the
Library nor the cache answers, keeps every response as it lands, and runs the local pass again.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from itertools import batched
from typing import Any, Literal

import psycopg
from psycopg.rows import TupleRow
from pydantic import BaseModel

from paperexplorer.domain import Level, RegistrySource, Resolution, ResolutionState, WorkId
from paperexplorer.parsing import Reference
from paperexplorer.resolution.local import (
    bibliographic_key,
    counts,
    doi_key,
    resolve,
    title_key,
)
from paperexplorer.resolution.registries import BATCH, BudgetSpentError, Registries
from paperexplorer.store import RegistryResponses, Resolutions

logger = logging.getLogger(__name__)


class ReferenceChecked(BaseModel):
    """One Reference's Resolution, saved as the Check lands it."""

    kind: Literal["reference"] = "reference"
    resolution: Resolution


class CheckFinished(BaseModel):
    """The Check went through every layer: its References by level and state."""

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


CheckEvent = ReferenceChecked | CheckFinished | BudgetSpent


async def check(
    conn: psycopg.Connection[TupleRow],
    citing: WorkId,
    refs: Sequence[Reference],
    registries: Registries,
    *,
    refresh: bool = False,
) -> AsyncIterator[CheckEvent]:
    """Check these References of `citing`, yielding each one's Resolution as it lands.

    `refresh` asks the registries again for all of them, past the cache, and lets even a
    Reference the Library matched go through the layers. Rows are written on `conn` as they
    land: an autocommit connection keeps them when the Check stops partway.
    """
    cache, saved = RegistryResponses(conn), Resolutions(conn)
    pending = {ref.id: ref for ref in refs}
    done: list[Resolution] = []

    def settle(final: Callable[[Resolution], bool]) -> list[ReferenceChecked]:
        landed: list[ReferenceChecked] = []
        for r in resolve(conn, citing, list(pending.values())):
            if final(r):
                saved.save(citing, r)
                done.append(r)
                del pending[r.reference]
                landed.append(ReferenceChecked(resolution=r))
        return landed

    def certain_or_high(r: Resolution) -> bool:
        return r.state is ResolutionState.RESOLVED and r.level is not Level.LOW

    async def fill(
        source: RegistrySource,
        keys: Iterable[str | None],
        ask: Callable[[str], Awaitable[Any]],
    ) -> None:
        """Ask for each key the cache does not answer (all on refresh), keeping each answer."""
        wanted = sorted({k for k in keys if k is not None})
        cached = {} if refresh else cache.get(source, wanted)
        missing = [k for k in wanted if k not in cached]

        async def one(key: str) -> None:
            found = await ask(key)
            cache.put(source, key, found or None)

        await asyncio.gather(*(one(k) for k in missing))

    async def fill_dois(source: RegistrySource, dois: Iterable[str | None]) -> None:
        wanted = sorted({d for d in dois if d is not None})
        cached = {} if refresh else cache.get(source, wanted)
        ask = (
            registries.crossref_dois
            if source is RegistrySource.CROSSREF_DOI
            else (registries.openalex_dois)
        )

        async def batch(dois: tuple[str, ...]) -> None:
            for doi, record in (await ask(dois)).items():
                cache.put(source, doi, record)

        await asyncio.gather(
            *(
                batch(b)
                for b in batched([d for d in wanted if d not in cached], BATCH, strict=False)
            )
        )

    async with registries:
        if not refresh:
            for event in settle(certain_or_high):
                yield event
        try:
            dois = [doi_key(r) for r in pending.values()]
            await asyncio.gather(
                fill_dois(RegistrySource.CROSSREF_DOI, dois),
                fill_dois(RegistrySource.OPENALEX_DOI, dois),
            )
            for event in settle(certain_or_high):
                yield event
            await fill(
                RegistrySource.OPENALEX_TITLE,
                (title_key(r) for r in pending.values()),
                registries.openalex_title,
            )
            for event in settle(certain_or_high):
                yield event
            await fill(
                RegistrySource.CROSSREF_BIBLIOGRAPHIC,
                (bibliographic_key(r) for r in pending.values()),
                lambda key: registries.crossref_bibliographic(_printed(pending, key)),
            )
        except BudgetSpentError:
            left = len(pending)
            logger.warning("OpenAlex budget spent with %d References left", left)
            for event in settle(certain_or_high):
                yield event
            yield BudgetSpent(left=len(pending))
            return
        for event in settle(lambda _: True):
            yield event
    logger.info("Registry Check of Work %s: %s", citing, counts(done))
    yield CheckFinished(counts=counts(done))


def _printed(pending: dict[str, Reference], key: str) -> str:
    """The printed string a bibliographic key was made from, as Crossref is sent it."""
    return next(" ".join(r.text.split()) for r in pending.values() if bibliographic_key(r) == key)
