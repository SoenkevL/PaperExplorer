"""Finding Library Items and Ingesting them over the API, on real connections that commit."""

import threading
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow

from paperexplorer.config import Settings
from paperexplorer.domain import Attachment, LibraryChanges, LibraryItem, Vector
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.library import InMemoryLibrary, LibraryUnavailableError
from paperexplorer.pipeline import INGESTING, RESOLVING
from paperexplorer.resolution import InMemoryDoiRegistry
from paperexplorer.store import StageStatuses, Works, migrate
from paperexplorer.tracing import Tracing
from paperexplorer.wiring import Wiring
from paperexplorer_api.app import Asking, Ingesting, create_app
from paperexplorer_api.client import ApiClient, ConflictError, NotFoundError
from paperexplorer_api.conversations import Conversations
from paperexplorer_api.schemas import INGESTING_STAGES, RESOLVING_STAGES, LibraryWork

from .conftest import Script

VILLIGER = LibraryItem(
    key="VILL2024",
    title="Personal psychedelic experience of psychedelic therapists during training",
    creators=["Villiger, Daniel"],
    year=2024,
    pdf=Attachment(key="ATTVILL1", filename="villiger.pdf"),
)
OTHER = LibraryItem(
    key="OTHR2024",
    title="The same PDF under another item",
    creators=["Other, Olive"],
    year=2023,
    pdf=Attachment(key="ATTVILL1", filename="villiger.pdf"),
)
NO_PDF = LibraryItem(key="NOPDF234", title="Only metadata")
FIVE = ["fetch", "parse", "chunk", "embed", "extract_citations"]


class GatedEmbedder(FakeEmbedder):
    """The fake Embedder, which can be held at the door or made to fail once."""

    def __init__(self) -> None:
        super().__init__()
        self.reached = threading.Event()
        self.door = threading.Event()
        self.door.set()
        self.fail_next = False

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        self.reached.set()
        assert self.door.wait(10), "the test never opened the door"
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("the Provider went away")
        return super().embed(texts)


class Harness:
    """An app over a scratch database, its typed client, and what the tests hold on to."""

    def __init__(self, app: FastAPI, library: InMemoryLibrary, embedder: GatedEmbedder) -> None:
        self.app = app
        self.client = ApiClient(TestClient(app))
        self.library = library
        self.embedder = embedder

    def synced(self) -> dict[str, LibraryWork]:
        self.client.sync_library()
        return {w.zotero_key: w for w in self.client.library().items if w.zotero_key}

    def settled(self, work_id: int) -> LibraryWork:
        """The Work once no run holds it."""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            work = self.client.work_stages(work_id)
            if work.run is None:
                return work
            time.sleep(0.02)
        pytest.fail(f"Work {work_id} still has a run")


@pytest.fixture
def connect(scratch_database_url: str) -> Callable[[], psycopg.Connection[TupleRow]]:
    with psycopg.connect(scratch_database_url, autocommit=True) as conn:
        migrate(conn)
    return lambda: psycopg.connect(scratch_database_url, autocommit=True)


@pytest.fixture
def library(fixture_pdf: Callable[[str], Path]) -> InMemoryLibrary:
    return InMemoryLibrary(
        [VILLIGER, OTHER, NO_PDF], {"ATTVILL1": fixture_pdf("villiger2024-tandf")}
    )


@pytest.fixture
def ingesting(
    recorded_settings: Settings, library: InMemoryLibrary
) -> Callable[[GatedEmbedder], Ingesting]:
    return lambda embedder: Ingesting(
        library=library,
        settings=recorded_settings,
        embedder=embedder,
        tracing=Tracing.off(),
        doi_registry=InMemoryDoiRegistry(offline=True),
    )


@pytest.fixture
def harness(
    connect: Callable[[], psycopg.Connection[TupleRow]],
    library: InMemoryLibrary,
    ingesting: Callable[[GatedEmbedder], Ingesting],
) -> Iterator[Harness]:
    embedder = GatedEmbedder()
    app = create_app(connect, ingesting=ingesting(embedder))
    with TestClient(app):
        yield Harness(app, library, embedder)
    embedder.door.set()


def test_the_contract_names_the_same_stages_as_the_pipeline() -> None:
    assert INGESTING_STAGES == INGESTING
    assert RESOLVING_STAGES == RESOLVING


def test_the_library_is_empty_until_it_syncs_then_lists_every_item(harness: Harness) -> None:
    assert harness.client.library().total == 0

    synced = harness.client.sync_library()

    assert (synced.items, synced.removed, synced.stale) == (3, 0, False)
    page = harness.client.library()
    assert page.total == 3
    by_key = {w.zotero_key: w for w in page.items}
    assert by_key["VILL2024"].has_pdf
    assert not by_key["NOPDF234"].has_pdf
    assert by_key["VILL2024"].stages["fetch"] == "pending"
    assert by_key["VILL2024"].run is None


def test_the_library_is_searched_and_paged(harness: Harness) -> None:
    harness.client.sync_library()

    assert [w.zotero_key for w in harness.client.library("olive").items] == ["OTHR2024"]
    page = harness.client.library(limit=1, offset=1)
    assert page.total == 3
    assert len(page.items) == 1


def test_a_library_that_cannot_be_reached_leaves_the_last_copy(
    connect: Callable[[], psycopg.Connection[TupleRow]],
    library: InMemoryLibrary,
    ingesting: Callable[[GatedEmbedder], Ingesting],
) -> None:
    class Unreachable(InMemoryLibrary):
        down = False

        def changes(self, since: int | None) -> LibraryChanges:
            if self.down:
                raise LibraryUnavailableError("offline")
            return library.changes(since)

    wired = ingesting(GatedEmbedder())
    flaky = Unreachable([], {})
    client = ApiClient(
        TestClient(
            create_app(
                connect,
                ingesting=Ingesting(
                    library=flaky,
                    settings=wired.settings,
                    embedder=wired.embedder,
                    tracing=wired.tracing,
                    doi_registry=wired.doi_registry,
                ),
            )
        )
    )
    client.sync_library()
    flaky.down = True

    assert client.sync_library().stale
    assert client.library().total == 3


def test_ingesting_runs_the_five_stages_and_the_work_can_be_read(harness: Harness) -> None:
    work = harness.synced()["VILL2024"]

    started = harness.client.ingest(work.id)

    assert started.run in {"queued", "running"}
    done = harness.settled(work.id)
    assert [done.stages[s] for s in FIVE] == ["done"] * 5
    assert done.stages["verify_dois"] == "pending"
    assert done.stage_runs["embed"].counts == {"chunks": 32, "embedded": 32}
    assert len(harness.client.work(work.id).blocks) == 33


def test_ingesting_goes_on_after_the_client_that_started_it_has_gone(harness: Harness) -> None:
    work = harness.synced()["VILL2024"]
    harness.embedder.door.clear()

    with TestClient(harness.app) as gone:
        assert ApiClient(gone).ingest(work.id).run in {"queued", "running"}

    harness.embedder.door.set()
    assert harness.settled(work.id).stages["extract_citations"] == "done"


def test_ingesting_is_refused_without_a_pdf_for_an_unknown_work_or_with_nothing_left(
    harness: Harness,
) -> None:
    items = harness.synced()

    with pytest.raises(ConflictError, match="no PDF"):
        harness.client.ingest(items["NOPDF234"].id)
    with pytest.raises(NotFoundError):
        harness.client.ingest(999_999)

    harness.client.ingest(items["VILL2024"].id)
    harness.settled(items["VILL2024"].id)
    with pytest.raises(ConflictError, match="Ingested"):
        harness.client.ingest(items["VILL2024"].id)


def test_a_stage_outside_the_chain_cannot_be_rerun(harness: Harness) -> None:
    work = harness.synced()["VILL2024"]

    with pytest.raises(Exception, match="422"):
        harness.client.ingest(work.id, from_stage="resolve")


def test_a_second_work_waits_and_may_be_cancelled_while_it_waits(harness: Harness) -> None:
    items = harness.synced()
    first, second = items["VILL2024"].id, items["OTHR2024"].id
    harness.embedder.door.clear()

    harness.client.ingest(first)
    assert harness.embedder.reached.wait(30)
    assert harness.client.ingest(second).run == "queued"

    with pytest.raises(ConflictError, match="already"):
        harness.client.ingest(first)
    with pytest.raises(ConflictError, match="already"):
        harness.client.ingest(second)
    with pytest.raises(ConflictError, match="running"):
        harness.client.cancel_ingesting(first)
    assert harness.client.work_stages(first).run == "running"

    harness.client.cancel_ingesting(second)

    assert harness.client.work_stages(second).run is None
    with pytest.raises(NotFoundError):
        harness.client.cancel_ingesting(second)
    harness.embedder.door.set()
    assert harness.settled(first).stages["extract_citations"] == "done"
    assert harness.client.work_stages(second).stages["fetch"] == "pending"


def test_queued_works_run_in_the_order_they_were_started(harness: Harness) -> None:
    items = harness.synced()
    first, second = items["VILL2024"].id, items["OTHR2024"].id
    harness.embedder.door.clear()
    harness.client.ingest(first)
    assert harness.embedder.reached.wait(30)
    harness.client.ingest(second)

    harness.embedder.door.set()

    assert harness.settled(second).stages["extract_citations"] == "done"
    assert harness.client.work_stages(first).stages["extract_citations"] == "done"


def test_a_failure_stops_the_chain_with_its_reason_and_ingesting_resumes_there(
    harness: Harness,
) -> None:
    work = harness.synced()["VILL2024"]
    harness.embedder.fail_next = True

    harness.client.ingest(work.id)
    failed = harness.settled(work.id)

    assert failed.stages["chunk"] == "done"
    assert failed.stages["embed"] == "failed"
    assert failed.stage_runs["embed"].error == "the Provider went away"
    assert failed.stages["extract_citations"] == "pending"
    chunked_at = failed.stage_runs["chunk"].finished_at

    harness.client.ingest(work.id)
    resumed = harness.settled(work.id)

    assert resumed.stage_runs["chunk"].finished_at == chunked_at
    assert resumed.stages["extract_citations"] == "done"


def test_rerunning_a_done_stage_continues_through_the_chain(harness: Harness) -> None:
    work = harness.synced()["VILL2024"]
    harness.client.ingest(work.id)
    fetched_at = harness.settled(work.id).stage_runs["fetch"].finished_at

    harness.client.ingest(work.id, from_stage="chunk")
    again = harness.settled(work.id)

    assert again.stage_runs["fetch"].finished_at == fetched_at
    assert [again.stages[s] for s in FIVE] == ["done"] * 5
    assert again.stage_runs["embed"].counts["embedded"] == 32


def test_resolving_needs_an_ingested_work_then_runs_the_last_two_stages(harness: Harness) -> None:
    work = harness.synced()["VILL2024"]

    with pytest.raises(ConflictError, match="Ingested"):
        harness.client.resolve_references(work.id)
    harness.client.ingest(work.id)
    harness.settled(work.id)

    assert harness.client.resolve_references(work.id).run in {"queued", "running"}
    resolved = harness.settled(work.id)

    assert resolved.stages["verify_dois"] == "done"
    assert resolved.stages["resolve"] == "done"


def test_changing_a_works_text_forgets_its_conversations_but_embedding_does_not(
    connect: Callable[[], psycopg.Connection[TupleRow]],
    library: InMemoryLibrary,
    ingesting: Callable[[GatedEmbedder], Ingesting],
    recorded_settings: Settings,
) -> None:
    wiring = Wiring.from_settings(
        recorded_settings,
        tracing=Tracing.off(),
        embedder=FakeEmbedder(),
        model=Script().model(),
    )
    conversations = Conversations.from_settings(recorded_settings)
    app = create_app(
        connect, Asking(wiring.asking, conversations), ingesting=ingesting(GatedEmbedder())
    )
    harness = Harness(app, library, GatedEmbedder())
    work = harness.synced()["VILL2024"]
    harness.client.ingest(work.id)
    harness.settled(work.id)
    conversation = harness.client.start_conversation(work.id)

    harness.client.ingest(work.id, from_stage="embed")
    harness.settled(work.id)
    assert conversations.get(conversation.id) is not None

    harness.client.ingest(work.id, from_stage="chunk")
    harness.settled(work.id)
    assert conversations.get(conversation.id) is None


def test_starting_up_marks_a_stage_left_running_as_interrupted(
    connect: Callable[[], psycopg.Connection[TupleRow]],
    ingesting: Callable[[GatedEmbedder], Ingesting],
) -> None:
    with connect() as conn:
        work = Works(conn).ensure("VILL2024")
        StageStatuses(conn).start(work, "parse")

    with TestClient(create_app(connect, ingesting=ingesting(GatedEmbedder()))) as client:
        stages = ApiClient(client).work_stages(work)

    assert stages.stages["parse"] == "failed"
    assert stages.stage_runs["parse"].error == "Interrupted by a restart"


def test_the_api_starts_before_the_database_is_migrated(
    scratch_database_url: str,
    ingesting: Callable[[GatedEmbedder], Ingesting],
) -> None:
    def unmigrated() -> psycopg.Connection[TupleRow]:
        return psycopg.connect(scratch_database_url, autocommit=True)

    with TestClient(create_app(unmigrated, ingesting=ingesting(GatedEmbedder()))) as client:
        assert client.get("/health").status_code == 200


def test_without_zoteros_keys_there_is_no_library_or_ingesting(
    connect: Callable[[], psycopg.Connection[TupleRow]],
) -> None:
    client = ApiClient(TestClient(create_app(connect)))

    with pytest.raises(NotFoundError):
        client.library()
    with pytest.raises(NotFoundError):
        client.ingest(1)
