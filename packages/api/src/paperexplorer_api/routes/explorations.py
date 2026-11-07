"""Explorations: a Seed's Exploration started or resumed, and where it stands."""

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from paperexplorer import catalog, resolution
from paperexplorer.domain import ResolutionState, WorkId
from paperexplorer.library import Library, LibraryWriteError, OutsidePaperExplorerError
from paperexplorer.pipeline import STAGES
from paperexplorer.store import Resolutions, StageState, StageStatuses, Works, WorkTexts
from paperexplorer_api import schemas
from paperexplorer_api.routes import Conn, Connections
from paperexplorer_api.routes.checks import Checking
from paperexplorer_api.routes.views import exploration_view, refile
from paperexplorer_api.stage_run_thread import StageRunThread, Then

logger = logging.getLogger(__name__)


def router(
    connections: Connections, library: Library, checking: Checking, thread: StageRunThread
) -> APIRouter:
    """Routes under `/works/{id}/exploration`."""
    routes = APIRouter(tags=["explorations"])

    def explored(conn: Conn, seed: WorkId) -> schemas.Exploration:
        view = exploration_view(conn, seed, thread.run_of(seed))
        if view is None:
            raise HTTPException(404, f"Work {seed} is the Seed of no Exploration")
        return view

    @routes.post("/works/{work_id}/exploration", status_code=202)
    def start(  # pyright: ignore[reportUnusedFunction]
        work_id: int, body: schemas.StartExploration | None = None
    ) -> schemas.Exploration:
        seed = WorkId(work_id)
        with connections() as conn:
            if Works(conn).get(seed) is None:
                raise HTTPException(404, f"No Work {work_id}")
            ran = StageStatuses(conn).for_work(seed)
            if thread.run_of(seed) is not None or any(
                s.state is StageState.RUNNING for s in ran.values()
            ):
                raise HTTPException(409, f"Work {work_id} has a Stage Run; start once it ends")
            try:
                catalog.start(conn, library, seed)
            except (catalog.NotASeedError, catalog.AmbiguousPaperExplorerError) as e:
                raise HTTPException(409, str(e)) from e
            except (LibraryWriteError, OutsidePaperExplorerError) as e:
                raise HTTPException(502, f"Zotero refused the PaperExplorer Collection: {e}") from e
            stages = [s for s in STAGES if s not in ran or ran[s].state is not StageState.DONE]
            check = body is not None and body.check
            if stages or check:
                then: Then | None = None
                if check:
                    then = lambda conn: _check_unchecked(conn, library, checking, seed)  # noqa: E731
                thread.submit(seed, stages, then=then)
                logger.info("Queued %s for the Exploration of Work %d", ", ".join(stages), seed)
            return explored(conn, seed)

    @routes.get("/works/{work_id}/exploration/proposal")
    def proposal(work_id: int) -> schemas.ExplorationProposal:  # pyright: ignore[reportUnusedFunction]
        with connections() as conn:
            work = Works(conn).get(WorkId(work_id))
        if work is None:
            raise HTTPException(404, f"No Work {work_id}")
        if work.pdf is None:
            raise HTTPException(409, f"Work {work_id} has no PDF; a Seed needs its text")
        return schemas.ExplorationProposal(name=catalog.exploration_name(work))

    @routes.get("/works/{work_id}/exploration")
    def exploration(work_id: int) -> schemas.Exploration:  # pyright: ignore[reportUnusedFunction]
        with connections() as conn:
            return explored(conn, WorkId(work_id))

    return routes


def _check_unchecked(conn: Conn, library: Library, checking: Checking, seed: WorkId) -> None:
    """A Registry Check of the Seed's Unchecked References, then their Filing.

    What a spent budget or a failed registry left unchecked stays Unchecked, as the state says.
    """
    parsed = WorkTexts(conn).get(seed)
    unchecked = {
        r.reference
        for r in Resolutions(conn).for_work(seed)
        if r.state is ResolutionState.UNCHECKED
    }
    refs = [r for r in parsed.references if r.id in unchecked] if parsed else []
    if refs:

        async def run() -> None:
            async for event in resolution.registry_check(conn, seed, refs, checking.registries()):
                if isinstance(event, resolution.BudgetSpent):
                    logger.warning("The Check of Work %d stopped: %s", seed, event.message)

        try:
            asyncio.run(run())
        except resolution.RegistryError as e:
            logger.warning("The Check of Work %d stopped: %s; what landed is kept", seed, e)
    refile(conn, library, seed)
