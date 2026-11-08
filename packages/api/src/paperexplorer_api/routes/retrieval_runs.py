"""Retrieval Runs: the Evidence a Jump from a Citation lands on in its Source."""

from fastapi import APIRouter, HTTPException

from paperexplorer import retrieval
from paperexplorer.domain import CitationId, Scope
from paperexplorer.store import CitationGraphs, Resolutions, Works
from paperexplorer_api import schemas
from paperexplorer_api.asking_thread import AskingContexts
from paperexplorer_api.routes import Connections
from paperexplorer_api.routes.views import brief


def router(connections: Connections, contexts: AskingContexts) -> APIRouter:
    """Routes under `/citations` that need the Embedder."""
    routes = APIRouter(prefix="/citations", tags=["citations"])

    @routes.post("/{citation_id}/retrieval-runs")
    def find_evidence(citation_id: int) -> schemas.Landing:  # pyright: ignore[reportUnusedFunction]
        with connections() as conn:
            detail = CitationGraphs(conn).get(CitationId(citation_id))
            if detail is None:
                raise HTTPException(404, f"No Citation {citation_id}")
            ref = detail.reference
            resolved = Resolutions(conn).get(detail.work, ref.id) if ref else None
            if resolved is None or not resolved.firm or resolved.work is None:
                raise HTTPException(409, f"Citation {citation_id} has no firm Source")
            source = Works(conn).get(resolved.work)
            if source is None or source.text_origin is None:
                raise HTTPException(409, f"The Source of Citation {citation_id} has no text yet")
            found = retrieval.find_evidence(
                conn, contexts(conn).embedder, detail, Scope.of(source.id)
            )
            work = brief(source)
        sentence = detail.citation.citing_sentence
        if isinstance(found, retrieval.NoEvidence):
            return schemas.Landing(
                citation=citation_id,
                citing_sentence=sentence,
                work=work,
                evidence=[],
                reason=found.value,
            )
        return schemas.Landing(
            citation=citation_id,
            citing_sentence=found.citing_sentence,
            work=work,
            evidence=[
                schemas.Evidence(
                    rank=rank,
                    block=e.chunk.block,
                    start=e.chunk.span.start,
                    end=e.chunk.span.end,
                    page=e.chunk.page,
                    heading_path=e.chunk.heading_path,
                )
                for rank, e in enumerate(found.evidence, start=1)
            ],
            reason=None,
        )

    return routes
