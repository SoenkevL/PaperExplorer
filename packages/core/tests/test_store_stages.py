import psycopg
from psycopg.rows import TupleRow

from paperexplorer.store import StageState, StageStatuses, Works

Conn = psycopg.Connection[TupleRow]


def test_a_stage_runs_then_is_done_with_its_counts(store_db: Conn) -> None:
    work = Works(store_db).ensure("seed")
    statuses = StageStatuses(store_db)

    statuses.start(work, "parse")
    running = statuses.for_work(work)["parse"]
    statuses.done(work, "parse", {"blocks": 113})
    done = statuses.for_work(work)["parse"]

    assert running.state is StageState.RUNNING
    assert running.finished_at is None
    assert done.state is StageState.DONE
    assert done.counts == {"blocks": 113}
    assert done.finished_at is not None


def test_a_failed_stage_keeps_its_error_until_it_runs_again(store_db: Conn) -> None:
    work = Works(store_db).ensure("seed")
    statuses = StageStatuses(store_db)
    statuses.start(work, "fetch")
    statuses.fail(work, "fetch", "no PDF attached")
    failed = statuses.for_work(work)["fetch"]

    statuses.start(work, "fetch")

    assert failed.state is StageState.FAILED
    assert failed.error == "no PDF attached"
    again = statuses.for_work(work)["fetch"]
    assert again.state is StageState.RUNNING
    assert again.error is None


def test_a_cleared_stage_is_pending_again(store_db: Conn) -> None:
    work = Works(store_db).ensure("seed")
    statuses = StageStatuses(store_db)
    for stage in ("parse", "extract_citations"):
        statuses.start(work, stage)
        statuses.done(work, stage, {})

    statuses.clear(work, ["extract_citations"])

    assert list(statuses.for_work(work)) == ["parse"]
