"""Explorations over the API: started, resumed, read, and filed as their Resolutions change."""

from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow

from paperexplorer.config import Settings
from paperexplorer.domain import LibraryItem
from paperexplorer.library import SEED_TAG, InMemoryLibrary
from paperexplorer.resolution import InMemoryDoiRegistry, Registries
from paperexplorer.store import migrate
from paperexplorer.tracing import Tracing
from paperexplorer_api.app import Checking, Ingesting, create_app
from paperexplorer_api.client import ConflictError, NotFoundError
from paperexplorer_api.schemas import ReadersChoice

from .test_ingesting import NO_PDF, VILLIGER, GatedEmbedder, Harness

# Villiger cites this one by its DOI, so once resolved it is a Library Match.
CITED = LibraryItem(
    key="CITD2022",
    title="Personal psychedelic use is common among a sample of psychedelic therapists:"
    " Implications for research and practice",
    creators=["Aday, Jacob S."],
    year=2023,
    doi="10.1089/psymed.2022.0004",
)
# Crossref knows this one, which the Library does not hold.
ANDERSEN = {
    "DOI": "10.1111/acps.13249",
    "type": "journal-article",
    "title": [
        "Therapeutic effects of classic serotonergic psychedelics: A systematic review of"
        " modern-era clinical studies"
    ],
    "author": [{"family": "Andersen", "given": "Kristoffer A. A."}],
    "issued": {"date-parts": [[2021]]},
    "container-title": ["Acta Psychiatrica Scandinavica"],
}
Conn = psycopg.Connection[TupleRow]


class Registry:
    """Crossref and OpenAlex as a Check meets them: Crossref knows ANDERSEN, OpenAlex nothing."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "api.crossref.org":
            asked = request.url.params.get("filter", "")
            items = [ANDERSEN] if f"doi:{ANDERSEN['DOI']}" in asked else []
            return httpx.Response(200, json={"message": {"items": items}})
        headers = {"x-ratelimit-remaining-usd": "1.0"}
        return httpx.Response(200, json={"results": []}, headers=headers)


@pytest.fixture
def connect(scratch_database_url: str) -> Callable[[], Conn]:
    with psycopg.connect(scratch_database_url, autocommit=True) as conn:
        migrate(conn)
    return lambda: psycopg.connect(scratch_database_url, autocommit=True)


@pytest.fixture
def library(fixture_pdf: Callable[[str], Path]) -> InMemoryLibrary:
    return InMemoryLibrary(
        [VILLIGER, CITED, NO_PDF], {"ATTVILL1": fixture_pdf("villiger2024-tandf")}
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
def registry() -> Registry:
    return Registry()


@pytest.fixture
def harness(
    connect: Callable[[], Conn],
    library: InMemoryLibrary,
    registry: Registry,
    ingesting: Callable[[GatedEmbedder], Ingesting],
) -> Iterator[Harness]:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    async def no_wait(_: float) -> None:
        return None

    checking = Checking(
        library=library,
        registries=lambda: Registries(
            settings, transport=httpx.MockTransport(registry.handle), sleep=no_wait
        ),
    )
    embedder = GatedEmbedder()
    app = create_app(connect, checking=checking, ingesting=ingesting(embedder))
    with TestClient(app):
        yield Harness(app, library, embedder)
    embedder.door.set()


def _exploration_collection(library: InMemoryLibrary) -> tuple[str, str]:
    """The PaperExplorer Collection's key and the one Exploration Collection's in it."""
    collections = library.changes(None).collections
    [root] = [c for c in collections if c.name == "PaperExplorer" and c.parent_key is None]
    [inside] = [c for c in collections if c.parent_key == root.key]
    return root.key, inside.key


def test_starting_an_exploration_files_the_seed_then_its_sources_once_resolved(
    harness: Harness,
) -> None:
    seed = harness.synced()["VILL2024"]

    started = harness.client.start_exploration(seed.id)

    _, collection = _exploration_collection(harness.library)
    assert started.collection_key == collection
    assert started.name.startswith("Villiger 2024 \N{EN DASH} Personal psychedelic")
    assert started.created
    assert collection in harness.library.item("VILL2024").collections
    assert SEED_TAG in harness.library.tags["VILL2024"]
    harness.settled(seed.id)
    explored = harness.client.exploration(seed.id)
    assert explored.stages_not_done == []
    assert explored.filed == 2
    assert collection in harness.library.item("CITD2022").collections


def test_a_ticked_check_runs_after_resolve_and_what_it_finds_is_filed_as_a_new_item(
    harness: Harness, registry: Registry
) -> None:
    seed = harness.synced()["VILL2024"]

    harness.client.start_exploration(seed.id, check=True)
    harness.settled(seed.id)

    explored = harness.client.exploration(seed.id)
    assert registry.requests
    assert explored.unchecked < 79
    [(key, new)] = harness.library.created.items()
    assert new.doi == "10.1111/acps.13249"
    assert explored.collection_key in harness.library.item(key).collections
    assert explored.filed == 3


def test_without_the_check_no_registry_is_asked(harness: Harness, registry: Registry) -> None:
    seed = harness.synced()["VILL2024"]

    harness.client.start_exploration(seed.id)
    harness.settled(seed.id)

    assert registry.requests == []
    assert harness.client.exploration(seed.id).unchecked == 79


def _explored(harness: Harness) -> int:
    """Villiger's Exploration started, its Stages run, without a Check."""
    seed = harness.synced()["VILL2024"].id
    harness.client.start_exploration(seed)
    harness.settled(seed)
    return seed


def test_a_readers_choice_files_the_work_chosen_and_clearing_it_takes_it_back(
    harness: Harness,
) -> None:
    seed = _explored(harness)
    collection = harness.client.exploration(seed).collection_key

    harness.client.choose(seed, "b5", ReadersChoice(key="NOPDF234"))
    assert collection in harness.library.item("NOPDF234").collections

    harness.client.clear_choice(seed, "b5")
    assert collection not in harness.library.item("NOPDF234").collections


def test_checking_one_reference_files_what_it_found(harness: Harness) -> None:
    seed = _explored(harness)

    harness.client.check_reference(seed, "b1")

    [(key, _)] = harness.library.created.items()
    collection = harness.client.exploration(seed).collection_key
    assert collection in harness.library.item(key).collections


def test_a_failed_embed_leaves_the_sources_filed_and_resuming_runs_only_what_is_missing(
    harness: Harness,
) -> None:
    seed = harness.synced()["VILL2024"].id
    harness.embedder.fail_next = True

    harness.client.start_exploration(seed)
    harness.settled(seed)

    partial = harness.client.exploration(seed)
    assert partial.stages_not_done == ["embed"]
    assert partial.filed == 2
    harness.client.start_exploration(seed)
    harness.settled(seed)
    resumed = harness.client.exploration(seed)
    assert resumed.stages_not_done == []
    assert resumed.collection_key == partial.collection_key


def test_a_work_without_a_pdf_cannot_be_a_seed_and_writes_nothing(harness: Harness) -> None:
    no_pdf = harness.synced()["NOPDF234"].id

    with pytest.raises(ConflictError, match="PDF"):
        harness.client.start_exploration(no_pdf)

    assert harness.library.changes(None).collections == []


def test_a_work_with_no_exploration_has_none_to_show(harness: Harness) -> None:
    seed = harness.synced()["VILL2024"].id

    with pytest.raises(NotFoundError):
        harness.client.exploration(seed)
    with pytest.raises(NotFoundError):
        harness.client.start_exploration(9999)


def test_an_exploration_waits_for_a_stage_run_under_way(harness: Harness) -> None:
    seed = harness.synced()["VILL2024"].id
    harness.embedder.door.clear()
    harness.client.ingest(seed)
    assert harness.embedder.reached.wait(10)

    with pytest.raises(ConflictError, match="Stage Run"):
        harness.client.start_exploration(seed)

    harness.embedder.door.set()
    harness.settled(seed)


def test_the_seeds_row_carries_its_exploration_and_the_tree_marks_its_collection(
    harness: Harness,
) -> None:
    seed = _explored(harness)

    row = harness.client.work_stages(seed)
    tree = harness.client.collections()

    assert row.exploration is not None
    assert row.exploration.collection_key in row.collections
    marked = {c.key: c.exploration for c in tree.collections}
    assert marked[row.exploration.collection_key]
    assert [k for k, m in marked.items() if m] == [row.exploration.collection_key]


def test_the_start_dialog_learns_the_collections_name_before_anything_is_written(
    harness: Harness,
) -> None:
    works = harness.synced()

    proposal = harness.client.exploration_proposal(works["VILL2024"].id)

    assert proposal.name.startswith("Villiger 2024 \N{EN DASH} Personal psychedelic")
    assert harness.library.changes(None).collections == []
    with pytest.raises(ConflictError, match="PDF"):
        harness.client.exploration_proposal(works["NOPDF234"].id)


def test_a_check_that_breaks_leaves_the_stages_done_and_the_sources_filed(
    harness: Harness, registry: Registry
) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("a registry answered nonsense")

    registry.handle = broken  # type: ignore[method-assign]
    seed = harness.synced()["VILL2024"].id

    harness.client.start_exploration(seed, check=True)
    row = harness.settled(seed)

    assert all(s.state == "done" for s in row.stage_statuses.values())
    explored = harness.client.exploration(seed)
    assert explored.filed == 2
    assert explored.stages_not_done == []
