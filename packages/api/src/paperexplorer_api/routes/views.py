"""Core's values as the API's schemas, for every route group."""

from paperexplorer.domain import Layer, Resolution, Work, WorkId
from paperexplorer.store import Works
from paperexplorer_api import schemas
from paperexplorer_api.routes import Conn


def brief(work: Work) -> schemas.WorkBrief:
    """A Work as a Resolution names it."""
    return schemas.WorkBrief(
        id=work.id, zotero_key=work.zotero_key, title=work.title, creators=work.creators,
        year=work.year, doi=work.doi,
    )  # fmt: skip


def resolution_view(conn: Conn, r: Resolution) -> schemas.Resolution:
    """A Resolution with the Works it names."""
    works = Works(conn)

    def named(work: WorkId | None) -> schemas.WorkBrief | None:
        found = works.get(work) if work is not None else None
        return brief(found) if found else None

    return schemas.Resolution(
        reference=r.reference,
        state=r.state.value,
        work=named(r.work),
        level=r.level.value if r.level else None,
        confidence=r.confidence,
        evidence=r.evidence,
        layer=r.layer.value if r.layer else None,
        doi_conflict=named(r.doi_conflict),
        chosen=r.layer is Layer.READER,
    )
