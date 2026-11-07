"""The pipeline: Stages run one at a time per Work, re-runnable, with their status in the store."""

import logging
from collections.abc import Callable, Iterable, Mapping

from paperexplorer.domain import WorkId
from paperexplorer.pipeline.stage import Stage, StageContext, StageError, StageResult
from paperexplorer.pipeline.stages import (
    chunk,
    embed,
    extract_citations,
    fetch,
    parse,
    resolve,
    verify_dois,
)
from paperexplorer.store import StageState, StageStatus, StageStatuses

__all__ = [
    "INGESTING",
    "RESOLVING",
    "STAGES",
    "Stage",
    "StageContext",
    "StageError",
    "StageResult",
    "downstream",
    "remaining",
    "run_chain",
    "run_stage",
]

logger = logging.getLogger(__name__)

# Every Stage, in the order its pips show and a Stage Run takes them.
STAGES: dict[str, Stage] = {
    "fetch": fetch,
    "parse": parse,
    "chunk": chunk,
    "embed": embed,
    "extract_citations": extract_citations,
    "verify_dois": verify_dois,
    "resolve": resolve,
}

# What each Stage reads from the store: after `parse` the Stages branch, so a failure in one
# branch (a rate-limited `embed`) leaves the others to run.
READS: dict[str, tuple[str, ...]] = {
    "fetch": (),
    "parse": ("fetch",),
    "chunk": ("parse",),
    "embed": ("chunk",),
    "extract_citations": ("parse",),
    "verify_dois": ("parse",),
    "resolve": ("parse", "verify_dois"),
}

# The Stages that make a Work Ingested, and the two that resolve its References afterwards.
INGESTING: tuple[str, ...] = ("fetch", "parse", "chunk", "embed", "extract_citations")
RESOLVING: tuple[str, ...] = ("verify_dois", "resolve")


def downstream(name: str) -> list[str]:
    """The Stages that read what `name` stores, directly or through another, in Stage order."""
    found = {name}
    for later in STAGES:
        if any(read in found for read in READS[later]):
            found.add(later)
    return [s for s in STAGES if s in found and s != name]


def run_stage(ctx: StageContext, name: str, work: WorkId) -> StageResult:
    """Run one Stage for one Work and record its status.

    The Stage's writes are one transaction: a failure keeps none of them and is recorded with
    its error, then raised. Success makes its downstream Stages pending, since what they read
    changed.
    """
    if name not in STAGES:
        raise ValueError(f"No Stage {name!r}; the Stages are {', '.join(STAGES)}")
    later = downstream(name)
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


def run_chain(
    ctx: StageContext,
    names: Iterable[str],
    work: WorkId,
    *,
    after: Callable[[str], None] | None = None,
) -> None:
    """Run the Stages in order, calling `after` as each commits.

    A failure skips the Stages downstream of it; the others still run, then the first failure
    is raised.
    """
    failed: Exception | None = None
    skipped: set[str] = set()
    for name in names:
        if name in skipped:
            continue
        try:
            run_stage(ctx, name, work)
        except Exception as e:
            failed = failed or e
            skipped.update(downstream(name))
            continue
        if after is not None:
            after(name)
    if failed is not None:
        raise failed


def remaining(ran: Mapping[str, StageStatus]) -> list[str]:
    """The Ingesting Stages not done, with those downstream of them: all need to run."""
    found: set[str] = set()
    for name in INGESTING:
        if name not in ran or ran[name].state is not StageState.DONE:
            found.update([name, *downstream(name)])
    return [s for s in INGESTING if s in found]
