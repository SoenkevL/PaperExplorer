"""Registry Checks of a Work or one Reference, Readers' Choices, and Duplicate Items."""

import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent

from paperexplorer import resolution
from paperexplorer.domain import RegistryRecord, Resolution, Work, WorkId, normalize_doi
from paperexplorer.library import Library, NotDuplicateItemsError, sync
from paperexplorer.library import merge as merge_items
from paperexplorer.parsing import Reference
from paperexplorer.store import ReadersChoices, Resolutions, Works, WorkTexts
from paperexplorer_api import schemas
from paperexplorer_api.routes import Conn, Connections
from paperexplorer_api.routes.views import brief, resolution_view

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Checking:
    """What Registry Checks and merges need: the Library, and fresh Registries for each Check."""

    library: Library
    registries: Callable[[], resolution.Registries]


def router(connections: Connections, checking: Checking) -> APIRouter:
    """Routes under `/works/{id}/checks`, `/works/{id}/references` and `/duplicates`."""
    routes = APIRouter(tags=["checks"])

    def references(work_id: int) -> tuple[WorkId, list[Reference]]:
        """The parsed Work's References; else 404 before anything streams."""
        with connections() as conn:
            parsed = WorkTexts(conn).get(WorkId(work_id))
        if parsed is None:
            raise HTTPException(404, f"No parsed Work {work_id}; run parse first")
        return WorkId(work_id), parsed.references

    def reference(work_id: int, key: str) -> tuple[WorkId, Reference]:
        """One Reference of the parsed Work, by its key; else 404."""
        work, refs = references(work_id)
        found = next((r for r in refs if r.id == key), None)
        if found is None:
            raise HTTPException(404, f"Work {work_id} prints no Reference {key}")
        return work, found

    def synced(conn: Conn) -> None:
        with conn.transaction():
            if sync(conn, checking.library).stale:
                logger.warning("Zotero could not be reached; checking against the last copy")

    @routes.get("/works/{work_id}/checks/cost")
    async def check_cost(
        found: Annotated[tuple[WorkId, list[Reference]], Depends(references)],
    ) -> schemas.CheckCost:  # pyright: ignore[reportUnusedFunction]
        work, refs = found
        with connections() as conn:
            cost = resolution.estimate(conn, work, refs)
        registries = checking.registries()
        try:
            async with registries:
                budget = await registries.budget()
        except resolution.BudgetSpentError:
            budget = 0.0
        except resolution.RegistryError:
            logger.warning("OpenAlex did not say what is left of its budget", exc_info=True)
            budget = None
        return schemas.CheckCost(**cost.model_dump(), budget_usd=budget)

    @routes.post("/works/{work_id}/checks", response_class=EventSourceResponse)
    async def check_work(  # pyright: ignore[reportUnusedFunction]
        found: Annotated[tuple[WorkId, list[Reference]], Depends(references)],
        check: schemas.Check | None = None,
    ) -> AsyncIterator[ServerSentEvent]:
        work, refs = found
        with connections() as conn:
            synced(conn)
            events = resolution.registry_check(
                conn, work, refs, checking.registries(), refresh=check.refresh if check else False
            )
            try:
                async for event in events:
                    sent = _event(conn, event)
                    yield ServerSentEvent(event=sent.type, raw_data=sent.model_dump_json())
            except resolution.RegistryError as e:
                sent = schemas.CheckErrorEvent(message=f"Check stopped: {e}; what landed is kept")
                yield ServerSentEvent(event=sent.type, raw_data=sent.model_dump_json())

    @routes.post("/works/{work_id}/references/{key}/checks")
    async def check_reference(  # pyright: ignore[reportUnusedFunction]
        found: Annotated[tuple[WorkId, Reference], Depends(reference)],
        check: schemas.Check | None = None,
    ) -> schemas.CheckedReference:
        work, ref = found
        with connections() as conn:
            synced(conn)
            checked: Resolution | None = None
            stopped: str | None = None
            try:
                async for event in resolution.registry_check(
                    conn,
                    work,
                    [ref],
                    checking.registries(),
                    refresh=check.refresh if check else False,
                ):
                    if isinstance(event, resolution.ReferenceChecked):
                        checked = event.resolution
                    elif isinstance(event, resolution.BudgetSpent):
                        stopped = event.message
            except resolution.RegistryError as e:
                stopped = f"Check stopped: {e}"
            view = resolution_view(conn, checked) if checked else None
        return schemas.CheckedReference(resolution=view, stopped=stopped)

    @routes.get("/works/{work_id}/references/{key}/candidates")
    def candidates(
        found: Annotated[tuple[WorkId, Reference], Depends(reference)],
    ) -> list[schemas.WorkBrief]:  # pyright: ignore[reportUnusedFunction]
        work, ref = found
        with connections() as conn:
            return [brief(w) for w in resolution.candidates(conn, work, ref)]

    @routes.put("/works/{work_id}/references/{key}/choice")
    def choose(
        found: Annotated[tuple[WorkId, Reference], Depends(reference)],
        choice: schemas.ReadersChoice,
    ) -> schemas.Resolution:  # pyright: ignore[reportUnusedFunction]
        work, ref = found
        with connections() as conn, conn.transaction():
            chosen = _chosen(conn, choice)
            if chosen == work:
                raise HTTPException(422, "A Work cannot cite itself")
            ReadersChoices(conn).choose(work, ref.text, chosen)
            return _resolve_again(conn, work, ref)

    @routes.delete("/works/{work_id}/references/{key}/choice")
    def clear_choice(
        found: Annotated[tuple[WorkId, Reference], Depends(reference)],
    ) -> schemas.Resolution:  # pyright: ignore[reportUnusedFunction]
        work, ref = found
        with connections() as conn, conn.transaction():
            if not ReadersChoices(conn).clear(work, ref.text):
                raise HTTPException(404, f"No Reader's Choice for Reference {ref.id}")
            return _resolve_again(conn, work, ref)

    @routes.get("/duplicates")
    def duplicates() -> list[schemas.DuplicateItems]:  # pyright: ignore[reportUnusedFunction]
        with connections() as conn:
            synced(conn)
            groups = Works(conn).duplicate_items()
        return [
            schemas.DuplicateItems(doi=g[0].doi or "", items=[_duplicate(w) for w in g])
            for g in groups
        ]

    def duplicate_item(conn: Conn, key: str) -> Work:
        found = Works(conn).duplicate_item(key)
        if found is None:
            raise HTTPException(
                404, f"{key} is not a Duplicate Item: no other Library Item holds its DOI"
            )
        return found

    @routes.put("/duplicates/{key}/stand")
    def let_stand(key: str) -> list[schemas.DuplicateItems]:  # pyright: ignore[reportUnusedFunction]
        with connections() as conn:
            synced(conn)
            with conn.transaction():
                Works(conn).let_stand(duplicate_item(conn, key).id)
        return duplicates()

    @routes.post("/duplicates/merge")
    def merge(pair: schemas.Merge) -> schemas.Merged:  # pyright: ignore[reportUnusedFunction]
        with connections() as conn:
            synced(conn)
            keep, other = duplicate_item(conn, pair.keep), duplicate_item(conn, pair.other)
            try:
                moved = merge_items(conn, checking.library, keep, other)
            except NotDuplicateItemsError as e:
                raise HTTPException(422, str(e)) from e
        return schemas.Merged(moved=moved)

    return routes


def _event(conn: Conn, event: resolution.RegistryCheckEvent) -> schemas.CheckEvent:
    match event:
        case resolution.ReferenceChecked(resolution=r):
            return schemas.ReferenceCheckedEvent(resolution=resolution_view(conn, r))
        case resolution.RegistryCheckFinished(counts=counts):
            return schemas.CheckFinishedEvent(counts=counts)
        case resolution.BudgetSpent(left=left):
            return schemas.BudgetSpentEvent(left=left, message=event.message)


def _chosen(conn: Conn, choice: schemas.ReadersChoice) -> WorkId | None:
    """The Work a choice names; a DOI no Work holds gets a Work of its own, as a Check's would."""
    works = Works(conn)
    if choice.work_id is not None:
        if works.get(WorkId(choice.work_id)) is None:
            raise HTTPException(404, f"No Work {choice.work_id}")
        return WorkId(choice.work_id)
    if choice.key is None:
        return None
    if (found := works.find(choice.key)) is not None:
        return found.id
    doi = normalize_doi(choice.key)
    if not doi.startswith("10.") or "/" not in doi:
        raise HTTPException(404, f"{choice.key} is neither a Library Item's key nor a DOI")
    return works.ensure_registry_work(RegistryRecord(doi=doi))


def _resolve_again(conn: Conn, work: WorkId, ref: Reference) -> schemas.Resolution:
    """The Reference resolved again locally, with its choice or without, and saved."""
    [again] = resolution.resolve(conn, work, [ref])
    Resolutions(conn).save(work, again)
    return resolution_view(conn, again)


def _duplicate(work: Work) -> schemas.DuplicateItem:
    added = work.added.date().isoformat() if work.added else None
    return schemas.DuplicateItem(**brief(work).model_dump(), pdf=work.pdf is not None, added=added)
