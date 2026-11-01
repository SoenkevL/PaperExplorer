"""Citations: one Citation with its Citation Link and the Reference it points at."""

from fastapi import APIRouter, HTTPException

from paperexplorer.citations import confidence_level
from paperexplorer.domain import CitationId
from paperexplorer.resolution import Registration, registration
from paperexplorer.store import CitationGraphs, Resolutions, Works
from paperexplorer_api import schemas
from paperexplorer_api.routes import Connections
from paperexplorer_api.routes.views import resolution_view


def router(connections: Connections) -> APIRouter:
    """Routes under `/citations`."""
    routes = APIRouter(prefix="/citations", tags=["citations"])

    @routes.get("/{citation_id}")
    def get_citation(citation_id: int) -> schemas.CitationDetail:  # pyright: ignore[reportUnusedFunction]
        with connections() as conn:
            detail = CitationGraphs(conn).get(CitationId(citation_id))
            work = Works(conn).get(detail.work) if detail else None
            ref = detail.reference if detail else None
            registered = registration(conn, ref.doi) if ref and ref.doi else None
            resolved = Resolutions(conn).get(detail.work, ref.id) if detail and ref else None
            resolved_view = resolution_view(conn, resolved) if resolved else None
        if detail is None or work is None or work.text_origin is None:
            raise HTTPException(404, f"No Citation {citation_id}")
        c, r = detail.citation, detail.reference
        return schemas.CitationDetail(
            id=detail.id,
            work_id=detail.work,
            block=c.block,
            text=c.text,
            citing_sentence=c.citing_sentence,
            text_origin=work.text_origin.value,
            link=schemas.CitationLink(
                confidence=c.link.confidence,
                evidence=[e.value for e in c.link.evidence],
                confidence_level=confidence_level(c.link).value,
            )
            if c.link
            else None,
            reference=schemas.Reference(
                key=r.id,
                label=r.label,
                text=r.text,
                page=r.page,
                surnames=r.surnames,
                year=r.year,
                title=r.title,
                doi=r.doi,
                doi_registration=None
                if registered in (None, Registration.UNVERIFIED)
                else registered.value,
                resolution=resolved_view,
            )
            if r
            else None,
        )

    return routes
