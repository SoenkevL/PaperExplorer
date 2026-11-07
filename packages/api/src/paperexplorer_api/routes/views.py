"""Core's values as the API's schemas, for every route group."""

import logging
from typing import Literal

import psycopg

from paperexplorer import catalog
from paperexplorer.domain import Layer, Resolution, Work, WorkId
from paperexplorer.library import Library
from paperexplorer.pipeline import STAGES
from paperexplorer.store import Explorations, StageState, StageStatuses, Works
from paperexplorer_api import schemas
from paperexplorer_api.routes import Conn

logger = logging.getLogger(__name__)


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


def exploration_view(
    conn: Conn, seed: WorkId, run: Literal["queued", "running"] | None
) -> schemas.Exploration | None:
    """The Seed's Exploration as it stands, or `None` if it has none under way."""
    state = catalog.state(conn, seed)
    if state is None:
        return None
    ran = StageStatuses(conn).for_work(seed)
    works = Works(conn)
    failed: list[schemas.FailedFiling] = []
    for filing in state.failed:
        work = works.get(filing.work)
        if work is not None:
            failed.append(schemas.FailedFiling(work=brief(work), error=filing.error))
    e = state.exploration
    return schemas.Exploration(
        seed=e.seed,
        collection_key=e.collection_key,
        name=e.name,
        created=e.created,
        filed=state.filed,
        failed=failed,
        removed_by_reader=state.removed_by_reader,
        not_filed=state.not_filed,
        awaiting_choice=state.awaiting_choice,
        unchecked=state.unchecked,
        stages_not_done=[s for s in STAGES if s not in ran or ran[s].state is not StageState.DONE],
        run=run,
    )


def refile(conn: Conn, library: Library, seed: WorkId) -> None:
    """File what changed for the Seed's Exploration, if it has one; the caller goes on regardless.

    A write that fails is recorded on its Filing; anything else is logged, short of the store.
    """
    if Explorations(conn).of_seed(seed) is None:
        return
    try:
        catalog.reconcile(conn, library, seed)
    except psycopg.Error:
        raise
    except Exception:
        logger.warning("The Exploration of Work %d was not reconciled", seed, exc_info=True)
