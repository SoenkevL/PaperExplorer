"""The pipeline: Stages run one at a time per Work, re-runnable, with their status in the store."""

import logging

from paperexplorer.domain import WorkId
from paperexplorer.pipeline.stage import Stage, StageContext, StageError, StageResult
from paperexplorer.pipeline.stages import extract_citations, fetch, parse
from paperexplorer.store import StageStatuses

__all__ = ["STAGES", "Stage", "StageContext", "StageError", "StageResult", "run_stage"]

logger = logging.getLogger(__name__)

# The Seed's chain, in order; a Stage reads what the Stages before it stored.
STAGES: dict[str, Stage] = {
    "fetch": fetch,
    "parse": parse,
    "extract_citations": extract_citations,
}


def run_stage(ctx: StageContext, name: str, work: WorkId) -> StageResult:
    """Run one Stage for one Work and record its status.

    The Stage's writes are one transaction: a failure keeps none of them and is recorded with
    its error, then raised. Success makes every later Stage pending, since what they read changed.
    """
    if name not in STAGES:
        raise ValueError(f"No Stage {name!r}; the Stages are {', '.join(STAGES)}")
    names = list(STAGES)
    later = names[names.index(name) + 1 :]
    statuses = StageStatuses(ctx.conn)
    logger.info("Stage %s started for Work %d", name, work)
    with ctx.conn.transaction():
        statuses.start(work, name)
    try:
        with ctx.conn.transaction():
            result = STAGES[name](ctx, work)
            statuses.done(work, name, result.counts)
            statuses.clear(work, later)
    except Exception as e:
        with ctx.conn.transaction():
            statuses.fail(work, name, str(e) or type(e).__name__)
        logger.warning("Stage %s failed for Work %d: %s", name, work, e)
        raise
    logger.info("Stage %s finished for Work %d: %s", name, work, result.counts)
    return result
