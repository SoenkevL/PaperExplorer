"""Ingesting: the five Stages run as a chain, resumed where they stopped."""

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer.config import Settings
from paperexplorer.domain import Attachment, LibraryChanges, LibraryItem
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.library import InMemoryLibrary, sync
from paperexplorer.pipeline import (
    INGESTING,
    RESOLVING,
    StageContext,
    StageError,
    remaining,
    run_chain,
)
from paperexplorer.resolution import InMemoryDoiRegistry
from paperexplorer.store import StageState, StageStatuses, Works, migrate
from paperexplorer.tracing import Tracing

Conn = psycopg.Connection[TupleRow]

VILLIGER = LibraryItem(
    key="VILL2024",
    title="Personal psychedelic experience of psychedelic therapists during training",
    creators=["Villiger, Daniel"],
    year=2024,
    doi="10.1080/09540261.2024.2357669",
    pdf=Attachment(key="ATTVILL1", filename="villiger.pdf"),
)
NO_PDF = LibraryItem(key="NOPDF234", title="Only metadata")
FIVE = ["fetch", "parse", "chunk", "embed", "extract_citations"]


@pytest.fixture
def ctx(
    store_db: Conn, recorded_settings: Settings, fixture_pdf: Callable[[str], Path]
) -> StageContext:
    library = InMemoryLibrary([VILLIGER, NO_PDF], {"ATTVILL1": fixture_pdf("villiger2024-tandf")})
    return StageContext(
        conn=store_db,
        settings=recorded_settings,
        library=library,
        embedder=FakeEmbedder(),
        tracing=Tracing.off(),
        doi_registry=InMemoryDoiRegistry(offline=True),
    )


def test_ingesting_is_the_five_stages_and_resolving_the_last_two() -> None:
    assert list(INGESTING) == FIVE
    assert list(RESOLVING) == ["verify_dois", "resolve"]


def test_a_new_work_has_all_five_stages_remaining(ctx: StageContext) -> None:
    work = Works(ctx.conn).ensure("VILL2024")

    assert remaining(StageStatuses(ctx.conn).for_work(work)) == FIVE


def test_a_chain_runs_its_stages_and_leaves_none_remaining(ctx: StageContext) -> None:
    work = Works(ctx.conn).ensure("VILL2024")

    run_chain(ctx, INGESTING, work)

    assert remaining(StageStatuses(ctx.conn).for_work(work)) == []


def test_a_chain_stops_at_the_first_failure_and_resumes_there(ctx: StageContext) -> None:
    work = Works(ctx.conn).ensure("NOPDF234")

    with pytest.raises(StageError, match="no PDF"):
        run_chain(ctx, INGESTING, work)

    statuses = StageStatuses(ctx.conn).for_work(work)
    assert list(statuses) == ["fetch"]
    assert statuses["fetch"].state is StageState.FAILED
    assert remaining(statuses) == FIVE


def test_a_chain_says_when_each_stage_has_committed_and_never_for_a_failed_one(
    ctx: StageContext,
) -> None:
    work = Works(ctx.conn).ensure("VILL2024")
    told: list[tuple[str, StageState]] = []

    run_chain(
        ctx,
        ["fetch", "parse"],
        work,
        after=lambda name: told.append((name, StageStatuses(ctx.conn).for_work(work)[name].state)),
    )

    assert told == [("fetch", StageState.DONE), ("parse", StageState.DONE)]

    told.clear()
    no_pdf = Works(ctx.conn).ensure("NOPDF234")
    with pytest.raises(StageError):
        run_chain(ctx, ["fetch"], no_pdf, after=lambda name: told.append((name, StageState.DONE)))
    assert told == []


def test_a_stage_left_running_for_one_work_can_be_recorded_failed(store_db: Conn) -> None:
    statuses = StageStatuses(store_db)
    work, other = Works(store_db).ensure("VILL2024"), Works(store_db).ensure("OTHER234")
    statuses.start(work, "embed")
    statuses.start(other, "embed")

    statuses.fail_running(work, "the connection was lost")

    assert statuses.for_work(work)["embed"].error == "the connection was lost"
    assert statuses.for_work(work)["embed"].state is StageState.FAILED
    assert statuses.for_work(other)["embed"].state is StageState.RUNNING


def test_a_stage_that_failed_on_a_rerun_resumes_from_there_though_later_ones_are_done(
    ctx: StageContext,
) -> None:
    work = Works(ctx.conn).ensure("VILL2024")
    run_chain(ctx, INGESTING, work)
    statuses = StageStatuses(ctx.conn)
    statuses.start(work, "parse")
    statuses.fail(work, "parse", "GROBID went away")

    assert remaining(statuses.for_work(work)) == ["parse", "chunk", "embed", "extract_citations"]


def test_a_restart_marks_what_was_running_as_failed_and_touches_the_rest(store_db: Conn) -> None:
    work = Works(store_db).ensure("VILL2024")
    statuses = StageStatuses(store_db)
    statuses.start(work, "fetch")
    statuses.done(work, "fetch", {"pdfs": 1})
    statuses.start(work, "parse")

    assert statuses.interrupt_running() == 1

    ran = statuses.for_work(work)
    assert ran["fetch"].state is StageState.DONE
    assert ran["parse"].state is StageState.FAILED
    assert ran["parse"].error == "Interrupted by a restart"
    assert ran["parse"].finished_at is not None


def _item(key: str, title: str, **fields: object) -> LibraryItem:
    return LibraryItem.model_validate({"key": key, "title": title} | fields)


def _record(conn: Conn, *items: LibraryItem) -> None:
    for item in items:
        Works(conn).record_library_item(Works(conn).ensure(item.key), item)


def _keys(conn: Conn, query: str = "", limit: int = 50, offset: int = 0) -> list[str | None]:
    found = Works(conn).library_items(query, limit, offset, done_first=FIVE)
    return [w.zotero_key for w in found]


def test_the_library_lists_ingested_works_first_then_newest_added(store_db: Conn) -> None:
    _record(
        store_db,
        _item("OLD", "Old", added=datetime(2020, 1, 1, tzinfo=UTC)),
        _item("NEW", "New", added=datetime(2024, 1, 1, tzinfo=UTC)),
        _item("DONE", "Done", added=datetime(2010, 1, 1, tzinfo=UTC)),
        _item("HALF", "Half", added=datetime(2011, 1, 1, tzinfo=UTC)),
    )
    statuses = StageStatuses(store_db)
    done = Works(store_db).ensure("DONE")
    for stage in FIVE:
        statuses.start(done, stage)
        statuses.done(done, stage, {})
    half = Works(store_db).ensure("HALF")
    statuses.start(half, "fetch")
    statuses.done(half, "fetch", {})

    assert _keys(store_db) == ["DONE", "NEW", "OLD", "HALF"]


def test_the_library_holds_only_library_items(store_db: Conn) -> None:
    _record(store_db, _item("ITEM", "An item"))
    store_db.execute("INSERT INTO works (title) VALUES ('Known from a registry only')")

    assert _keys(store_db) == ["ITEM"]
    assert Works(store_db).library_count("") == 1


@pytest.mark.parametrize(
    ("query", "found"),
    [
        ("entropy", ["ENT"]),
        ("ENTROPY", ["ENT"]),
        ("smith", ["ENT"]),
        ("2019", ["ENT"]),
        ("smith 2019", ["ENT"]),
        ("smith 2020", []),
        ("OTHR", ["OTHR"]),
        ("10.1000/abc", ["OTHR"]),
        ("100%", []),
        ("_", []),
    ],
)
def test_the_library_is_searched_by_title_creators_year_key_and_doi(
    store_db: Conn, query: str, found: list[str]
) -> None:
    _record(
        store_db,
        _item("ENT", "Brain entropy", creators=["Smith, Jane"], year=2019),
        _item("OTHR", "Something else", doi="10.1000/abc"),
    )

    assert _keys(store_db, query) == found
    assert Works(store_db).library_count(query) == len(found)


def test_the_library_is_read_in_pages(store_db: Conn) -> None:
    _record(
        store_db,
        *[_item(f"K{i}", f"T{i}", added=datetime(2020 + i, 1, 1, tzinfo=UTC)) for i in range(3)],
    )

    assert _keys(store_db, limit=2) == ["K2", "K1"]
    assert _keys(store_db, limit=2, offset=2) == ["K0"]
    assert Works(store_db).library_count("") == 3


class _WaitingLibrary(InMemoryLibrary):
    """Records when its changes are read, so a test can see whether two syncs overlapped."""

    def __init__(self) -> None:
        super().__init__([], {})
        self.read = threading.Event()

    def changes(self, since: int | None) -> LibraryChanges:
        self.read.set()
        return super().changes(since)


def test_a_second_sync_waits_for_the_first_to_commit(scratch_database_url: str) -> None:
    with psycopg.connect(scratch_database_url, autocommit=True) as first:
        migrate(first)
        second_library = _WaitingLibrary()

        def second_sync() -> None:
            with (
                psycopg.connect(scratch_database_url, autocommit=True) as conn,
                conn.transaction(),
            ):
                sync(conn, second_library)

        with first.transaction():
            sync(first, InMemoryLibrary([], {}))
            other = threading.Thread(target=second_sync)
            other.start()
            assert not second_library.read.wait(0.5)
        assert second_library.read.wait(5)
        other.join()
