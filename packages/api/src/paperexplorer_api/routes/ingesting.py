"""The Library as the reader finds it, and Ingesting its Items."""

import logging
from dataclasses import dataclass
from typing import Annotated

import psycopg
from fastapi import APIRouter, HTTPException, Query, Response
from psycopg.rows import TupleRow

from paperexplorer.config import Settings
from paperexplorer.domain import Work, WorkId
from paperexplorer.embedding import Embedder
from paperexplorer.library import Library, sync
from paperexplorer.pipeline import INGESTING, RESOLVING, StageContext, remaining
from paperexplorer.resolution import DoiRegistry
from paperexplorer.store import StageState, StageStatuses, Works
from paperexplorer.tracing import Tracing
from paperexplorer_api import schemas
from paperexplorer_api.routes import Connections
from paperexplorer_api.routes.works import work_summary
from paperexplorer_api.stage_run_thread import StageRunThread

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Ingesting:
    """What the Library's routes and the Stages need."""

    library: Library
    settings: Settings
    embedder: Embedder
    tracing: Tracing
    doi_registry: DoiRegistry

    def context(self, conn: psycopg.Connection[TupleRow]) -> StageContext:
        """A Stage Run's `StageContext` around the store connection it opened."""
        return StageContext(
            conn=conn,
            settings=self.settings,
            library=self.library,
            embedder=self.embedder,
            tracing=self.tracing,
            doi_registry=self.doi_registry,
        )


def router(connections: Connections, ingesting: Ingesting, thread: StageRunThread) -> APIRouter:
    """Routes under `/library` and `/works/{id}`: its Stages and Stage Runs."""
    routes = APIRouter(tags=["ingesting"])

    def view(conn: psycopg.Connection[TupleRow], work: Work) -> schemas.LibraryWork:
        statuses = StageStatuses(conn)
        ran = statuses.for_work(work.id)
        return schemas.LibraryWork(
            **work_summary(work, statuses).model_dump(),
            has_pdf=work.pdf is not None,
            run=thread.run_of(work.id),
            stage_statuses={
                name: schemas.StageStatus(
                    state=s.state.value,
                    counts=s.counts,
                    error=s.error,
                    started_at=s.started_at,
                    finished_at=s.finished_at,
                )
                for name, s in ran.items()
            },
        )

    def found(conn: psycopg.Connection[TupleRow], work_id: int) -> Work:
        work = Works(conn).get(WorkId(work_id))
        if work is None:
            raise HTTPException(404, f"No Work {work_id}")
        return work

    def under_way(conn: psycopg.Connection[TupleRow], work: Work) -> bool:
        """Whether a Stage Run holds the Work, or the CLI is running a Stage of it."""
        ran = StageStatuses(conn).for_work(work.id).values()
        return thread.run_of(work.id) is not None or any(s.state is StageState.RUNNING for s in ran)

    def start(
        conn: psycopg.Connection[TupleRow], work: Work, stages: list[str]
    ) -> schemas.LibraryWork:
        if under_way(conn, work) or not thread.submit(work.id, stages):
            raise HTTPException(409, f"Work {work.id} already has a Stage Run")
        logger.info("Queued %s for Work %d", ", ".join(stages), work.id)
        return view(conn, work)

    @routes.get("/library")
    def library(  # pyright: ignore[reportUnusedFunction]
        q: str = "",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> schemas.LibraryPage:
        with connections() as conn:
            works = Works(conn)
            items = works.library_items(q, limit, offset, done_first=INGESTING)
            return schemas.LibraryPage(
                items=[view(conn, w) for w in items], total=works.library_count(q)
            )

    @routes.post("/library/sync")
    def sync_library() -> schemas.LibrarySynced:  # pyright: ignore[reportUnusedFunction]
        with connections() as conn, conn.transaction():
            result = sync(conn, ingesting.library)
        return schemas.LibrarySynced(items=result.items, removed=result.removed, stale=result.stale)

    @routes.get("/works/{work_id}/stages")
    def work_stages(work_id: int) -> schemas.LibraryWork:  # pyright: ignore[reportUnusedFunction]
        with connections() as conn:
            return view(conn, found(conn, work_id))

    @routes.post("/works/{work_id}/ingesting", status_code=202)
    def ingest(  # pyright: ignore[reportUnusedFunction]
        work_id: int, body: schemas.Ingest | None = None
    ) -> schemas.LibraryWork:
        from_stage = body.from_stage if body else None
        if from_stage is not None and from_stage not in INGESTING:
            raise HTTPException(422, f"{from_stage} is not one of {', '.join(INGESTING)}")
        with connections() as conn:
            work = found(conn, work_id)
            if work.pdf is None:
                raise HTTPException(409, f"{work.zotero_key} has no PDF stored in Zotero")
            if from_stage is not None:
                stages = list(INGESTING[INGESTING.index(from_stage) :])
            else:
                stages = remaining(StageStatuses(conn).for_work(work.id))
            if not stages:
                raise HTTPException(409, f"Work {work_id} is Ingested; re-run a Stage instead")
            return start(conn, work, stages)

    @routes.delete("/works/{work_id}/stage-run", status_code=204)
    def cancel(work_id: int) -> Response:  # pyright: ignore[reportUnusedFunction]
        if thread.cancel(WorkId(work_id)):
            return Response(status_code=204)
        if thread.run_of(WorkId(work_id)) == "running":
            raise HTTPException(
                409, f"Work {work_id} is running; a Stage Run under way cannot be cancelled"
            )
        raise HTTPException(404, f"Work {work_id} has no Stage Run waiting")

    @routes.post("/works/{work_id}/resolving", status_code=202)
    def resolving(work_id: int) -> schemas.LibraryWork:  # pyright: ignore[reportUnusedFunction]
        with connections() as conn:
            work = found(conn, work_id)
            if remaining(StageStatuses(conn).for_work(work.id)):
                raise HTTPException(409, f"Work {work_id} is not Ingested yet")
            return start(conn, work, list(RESOLVING))

    return routes
