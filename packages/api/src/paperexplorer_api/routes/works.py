"""Works: their snapshots and Stage status, and each one's text with its Citation spans."""

from collections import defaultdict

from fastapi import APIRouter, HTTPException

from paperexplorer.citations import confidence_level
from paperexplorer.domain import Work, WorkId
from paperexplorer.parsing import TextOrigin
from paperexplorer.pipeline import STAGES
from paperexplorer.store import CitationGraphs, Resolutions, StageStatuses, Works, WorkTexts
from paperexplorer_api import schemas
from paperexplorer_api.routes import Conn, Connections
from paperexplorer_api.routes.views import brief


def work_summary(work: Work, statuses: StageStatuses) -> schemas.WorkSummary:
    """The Work's snapshot and where each Stage stands."""
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


def _held_works(conn: Conn, work: WorkId) -> dict[str, Work]:
    """The Work each Reference's Library Match names, by Reference key, while it has its Item."""
    works = Works(conn)
    held: dict[str, Work] = {}
    for r in Resolutions(conn).for_work(work):
        found = works.get(r.work) if r.library_match and r.work is not None else None
        if found is not None and found.zotero_key is not None:
            held[r.reference] = found
    return held


def router(connections: Connections) -> APIRouter:
    """Routes under `/works`."""
    routes = APIRouter(prefix="/works", tags=["works"])

    @routes.get("")
    def list_works() -> list[schemas.WorkSummary]:  # pyright: ignore[reportUnusedFunction]
        with connections() as conn:
            statuses = StageStatuses(conn)
            return [work_summary(w, statuses) for w in Works(conn).with_stage_runs()]

    @routes.get("/{work_id}")
    def get_work(work_id: int) -> schemas.WorkText:  # pyright: ignore[reportUnusedFunction]
        with connections() as conn:
            work = Works(conn).get(WorkId(work_id))
            if work is None:
                raise HTTPException(404, f"No Work {work_id}")
            parsed = WorkTexts(conn).get(work.id)
            if parsed is None:
                raise HTTPException(404, f"Work {work_id} has no text yet; run parse")
            held = _held_works(conn, work.id)
            matches: dict[WorkId, schemas.LibraryMatch] = {}
            spans: defaultdict[int, list[schemas.CitationSpan]] = defaultdict(list)
            for stored in CitationGraphs(conn).for_work(work.id):
                c = stored.citation
                cited = held.get(c.link.reference_id) if c.link else None
                if cited is not None:
                    match = matches.setdefault(
                        cited.id, schemas.LibraryMatch(work=brief(cited), citations=[])
                    )
                    match.citations.append(stored.id)
                spans[c.block].append(
                    schemas.CitationSpan(
                        id=stored.id,
                        start=c.span.start,
                        end=c.span.end,
                        confidence_level=confidence_level(c.link).value,
                        library_match=cited is not None,
                    )
                )
            summary = work_summary(work, StageStatuses(conn))
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
        return schemas.WorkText(
            **summary.model_dump(), blocks=blocks, library_matches=list(matches.values())
        )

    @routes.get("/{work_id}/cited-by")
    def cited_by(work_id: int) -> list[schemas.CitingWork]:  # pyright: ignore[reportUnusedFunction]
        with connections() as conn:
            works = Works(conn)
            cited = works.get(WorkId(work_id))
            if cited is None:
                raise HTTPException(404, f"No Work {work_id}")
            citations = CitationGraphs(conn)
            found: list[tuple[Work, list[schemas.CitingSentence]]] = []
            for citer, references in Resolutions(conn).citing(cited.id).items():
                work = works.get(citer)
                if work is None or work.zotero_key is None:
                    continue
                sentences = [
                    schemas.CitingSentence(
                        id=s.id,
                        citing_sentence=s.citation.citing_sentence,
                        confidence_level=confidence_level(s.citation.link).value,
                    )
                    for s in citations.linked_to(citer, references)
                ]
                found.append((work, sentences))
        found.sort(key=lambda f: (f[0].year is None, -(f[0].year or 0), f[0].title or ""))
        return [
            schemas.CitingWork(
                work=brief(work), ocr=work.text_origin is TextOrigin.OCR, citations=sentences
            )
            for work, sentences in found
        ]

    return routes
