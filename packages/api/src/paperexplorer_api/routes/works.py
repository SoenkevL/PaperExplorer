"""Works: their snapshots and Stage status, and each one's text with its Citation spans."""

from collections import defaultdict

from fastapi import APIRouter, HTTPException

from paperexplorer.citations import confidence_level
from paperexplorer.domain import Work, WorkId
from paperexplorer.pipeline import STAGES
from paperexplorer.store import CitationGraphs, StageStatuses, Works, WorkTexts
from paperexplorer_api import schemas
from paperexplorer_api.routes import Connections


def _summary(work: Work, statuses: StageStatuses) -> schemas.WorkSummary:
    ran = statuses.for_work(work.id)
    return schemas.WorkSummary(
        id=work.id,
        zotero_key=work.zotero_key,
        title=work.title,
        creators=work.creators,
        year=work.year,
        doi=work.doi,
        text_origin=work.text_origin.value if work.text_origin else None,
        stages={s: ran[s].state.value if s in ran else "pending" for s in STAGES},
    )


def router(connections: Connections) -> APIRouter:
    """Routes under `/works`."""
    routes = APIRouter(prefix="/works", tags=["works"])

    @routes.get("")
    def list_works() -> list[schemas.WorkSummary]:  # pyright: ignore[reportUnusedFunction]
        with connections() as conn:
            statuses = StageStatuses(conn)
            return [_summary(w, statuses) for w in Works(conn).with_stage_runs()]

    @routes.get("/{work_id}")
    def get_work(work_id: int) -> schemas.WorkText:  # pyright: ignore[reportUnusedFunction]
        with connections() as conn:
            work = Works(conn).get(WorkId(work_id))
            if work is None:
                raise HTTPException(404, f"No Work {work_id}")
            parsed = WorkTexts(conn).get(work.id)
            if parsed is None:
                raise HTTPException(404, f"Work {work_id} has no text yet; run parse")
            spans: defaultdict[int, list[schemas.CitationSpan]] = defaultdict(list)
            for stored in CitationGraphs(conn).for_work(work.id):
                c = stored.citation
                spans[c.block].append(
                    schemas.CitationSpan(
                        id=stored.id,
                        start=c.span.start,
                        end=c.span.end,
                        confidence_level=confidence_level(c.link).value,
                    )
                )
            summary = _summary(work, StageStatuses(conn))
        blocks = [
            schemas.Block(
                position=i,
                kind=b.kind.value,
                heading_path=b.heading_path,
                page=b.page,
                boxes=[schemas.Box.model_validate(box.model_dump()) for box in b.boxes],
                text=b.text,
                ocr_confidence=b.ocr_confidence,
                citations=spans[i],
            )
            for i, b in enumerate(parsed.blocks)
        ]
        return schemas.WorkText(**summary.model_dump(), blocks=blocks)

    return routes
