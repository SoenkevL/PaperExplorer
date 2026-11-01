"""Registry Checks, Readers' Choices and Duplicate Items over the API."""

import json
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow

from paperexplorer.config import Settings
from paperexplorer.domain import LibraryItem, WorkId
from paperexplorer.library import InMemoryLibrary
from paperexplorer.parsing import ParsedWork, Reference, TextOrigin
from paperexplorer.resolution import Registries
from paperexplorer.store import Works, WorkTexts
from paperexplorer_api.app import Checking
from paperexplorer_api.client import ApiClient, NotFoundError
from paperexplorer_api.schemas import (
    CheckFinishedEvent,
    Merge,
    ReadersChoice,
    ReferenceCheckedEvent,
)

from .conftest import AppFactory, Script

Conn = psycopg.Connection[TupleRow]
Json = dict[str, Any]

SEED = LibraryItem(key="SEED1111", title="The Seed", creators=["Seed, Sam"], year=2024)
RESNET = LibraryItem(
    key="RESN2016",
    title="Deep residual learning for image recognition",
    creators=["He, Kaiming"],
    year=2016,
    doi="10.5555/resnet",
)
ATTENTION = {
    "DOI": "10.5555/attention",
    "type": "journal-article",
    "title": ["Attention Is All You Need"],
    "author": [{"family": "Vaswani", "given": "A."}],
    "issued": {"date-parts": [[2017]]},
}
REFS = [
    Reference(id="b0", text="He (2016). Deep residual learning.", page=1, doi="10.5555/resnet",
              title=RESNET.title, surnames=["He"]),
    Reference(id="b1", text="Vaswani (2017). Attention.", page=1, doi="10.5555/attention",
              title="Attention Is All You Need", surnames=["Vaswani"]),
]  # fmt: skip


class Registry:
    """Crossref knows ATTENTION; OpenAlex knows nothing and reports `remaining_usd`."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.remaining_usd = "0.0900"

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "api.crossref.org":
            asked = request.url.params.get("filter", "")
            items = [ATTENTION] if "doi:10.5555/attention" in asked else []
            return httpx.Response(200, json={"message": {"items": items}})
        headers = {"x-ratelimit-remaining-usd": self.remaining_usd}
        if self.remaining_usd == "0":
            return httpx.Response(429, headers=headers)
        return httpx.Response(200, json={"results": []}, headers=headers)


@pytest.fixture
def registry() -> Registry:
    return Registry()


@pytest.fixture
def library() -> InMemoryLibrary:
    return InMemoryLibrary([SEED, RESNET], {})


@pytest.fixture
def checks(make_app: AppFactory, registry: Registry, library: InMemoryLibrary) -> TestClient:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    async def no_wait(_: float) -> None:
        return None

    checking = Checking(
        library=library,
        registries=lambda: Registries(
            settings, transport=httpx.MockTransport(registry.handle), sleep=no_wait
        ),
    )
    return TestClient(make_app(Script().model(), checking=checking))


@pytest.fixture
def seed(store_db: Conn) -> WorkId:
    work = Works(store_db).ensure(SEED.key)
    WorkTexts(store_db).replace(
        work, ParsedWork(text_origin=TextOrigin.PRINTED, blocks=[], references=REFS)
    )
    return work


def _events(body: str) -> list[Json]:
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]


def test_a_check_streams_each_references_resolution_then_the_counts(
    checks: TestClient, seed: WorkId
) -> None:
    response = checks.post(f"/works/{seed}/checks")

    assert response.status_code == 200
    events = _events(response.text)
    assert [(e["type"], e.get("resolution", {}).get("reference")) for e in events] == [
        ("reference", "b0"),
        ("reference", "b1"),
        ("finished", None),
    ]
    b0, b1 = events[0]["resolution"], events[1]["resolution"]
    assert (b0["level"], b0["layer"], b0["work"]["zotero_key"]) == (
        "certain",
        "library",
        "RESN2016",
    )
    assert (b1["level"], b1["layer"], b1["work"]["title"]) == (
        "certain", "crossref", "Attention Is All You Need",
    )  # fmt: skip
    assert events[-1]["counts"]["certain"] == 2


def test_a_check_stops_when_the_openalex_budget_is_spent_and_says_so(
    checks: TestClient, seed: WorkId, registry: Registry
) -> None:
    registry.remaining_usd = "0"

    events = _events(checks.post(f"/works/{seed}/checks").text)

    assert events[-1] == {
        "type": "budget_spent",
        "left": 1,
        "message": "OpenAlex budget spent, 1 References left, try after 00:00 UTC",
    }
    assert [e["resolution"]["reference"] for e in events if e["type"] == "reference"] == ["b0"]


def test_an_unparsed_work_cannot_be_checked(checks: TestClient, store_db: Conn) -> None:
    work = Works(store_db).ensure("NOTEXT11")

    assert checks.post(f"/works/{work}/checks").status_code == 404
    assert checks.get(f"/works/{work}/checks/cost").status_code == 404


def test_the_cost_of_a_check_comes_with_whats_left_of_the_openalex_day(
    checks: TestClient, seed: WorkId, registry: Registry
) -> None:
    registry.remaining_usd = "0.0420"

    cost = checks.get(f"/works/{seed}/checks/cost").json()

    # The Library has not been synced yet, so b0 is Unchecked too.
    assert cost == {
        "unchecked": 2, "crossref_batches": 1, "openalex_batches": 1, "title_searches": 2,
        "bibliographic_searches": 0, "openalex_usd": pytest.approx(0.0021), "budget_usd": 0.042,
    }  # fmt: skip


def test_one_reference_checked_again_asks_the_registries_past_the_cache(
    checks: TestClient, seed: WorkId, registry: Registry
) -> None:
    checks.post(f"/works/{seed}/checks")
    asked = len(registry.requests)

    cached = checks.post(f"/works/{seed}/references/b1/checks").json()
    again = checks.post(f"/works/{seed}/references/b1/checks", json={"refresh": True}).json()

    assert cached["resolution"]["layer"] == again["resolution"]["layer"] == "crossref"
    assert cached["stopped"] is None
    assert len(registry.requests) > asked
    assert checks.post(f"/works/{seed}/references/b9/checks").status_code == 404


def test_a_readers_choice_by_key_wins_and_clearing_it_resolves_again(
    checks: TestClient, seed: WorkId
) -> None:
    checks.post(f"/works/{seed}/checks")

    chosen = checks.put(f"/works/{seed}/references/b1/choice", json={"key": "RESN2016"}).json()
    cleared = checks.delete(f"/works/{seed}/references/b1/choice").json()

    assert (chosen["work"]["zotero_key"], chosen["layer"], chosen["chosen"]) == (
        "RESN2016", "reader", True,
    )  # fmt: skip
    assert (cleared["work"]["title"], cleared["layer"], cleared["chosen"]) == (
        "Attention Is All You Need", "crossref", False,
    )  # fmt: skip
    assert checks.delete(f"/works/{seed}/references/b1/choice").status_code == 404


def test_a_readers_choice_of_none_or_of_a_doi_no_work_holds(
    checks: TestClient, seed: WorkId, store_db: Conn
) -> None:
    none = checks.put(f"/works/{seed}/references/b0/choice", json={}).json()
    by_doi = checks.put(
        f"/works/{seed}/references/b1/choice", json={"key": "https://doi.org/10.5555/ELSEWHERE"}
    ).json()

    assert (none["state"], none["work"], none["chosen"]) == ("unresolved", None, True)
    assert (by_doi["work"]["doi"], by_doi["work"]["zotero_key"]) == ("10.5555/elsewhere", None)
    for body in ({"key": "not a key"}, {"work_id": 999999}):
        assert checks.put(f"/works/{seed}/references/b1/choice", json=body).status_code == 404
    assert (
        checks.put(f"/works/{seed}/references/b1/choice", json={"work_id": seed}).status_code == 422
    )


def test_the_candidates_for_a_choice_are_its_resolution_then_near_titles(
    checks: TestClient, seed: WorkId
) -> None:
    checks.post(f"/works/{seed}/checks")

    found = checks.get(f"/works/{seed}/references/b0/candidates").json()

    assert [w["zotero_key"] for w in found] == ["RESN2016"]


def test_duplicate_items_are_listed_let_stand_and_merged(
    checks: TestClient, library: InMemoryLibrary
) -> None:
    library.put(RESNET.model_copy(update={"key": "RESN2BIS"}))

    listed = checks.get("/duplicates").json()
    stood = checks.put("/duplicates/RESN2BIS/stand").json()
    merged = checks.post("/duplicates/merge", json={"keep": "RESN2BIS", "other": "RESN2016"})

    assert [(g["doi"], [i["zotero_key"] for i in g["items"]]) for g in listed] == [
        ("10.5555/resnet", ["RESN2016", "RESN2BIS"])
    ]
    assert [i["zotero_key"] for i in stood[0]["items"]] == ["RESN2BIS", "RESN2016"]
    assert merged.json() == {"moved": 0}
    assert checks.get("/duplicates").json() == []
    assert checks.put("/duplicates/SEED1111/stand").status_code == 404


def test_the_client_checks_chooses_and_merges_through_typed_calls(
    checks: TestClient, seed: WorkId, library: InMemoryLibrary
) -> None:
    client = ApiClient(checks)
    library.put(RESNET.model_copy(update={"key": "RESN2BIS"}))

    cost = client.check_cost(seed)
    events = list(client.check(seed))
    again = client.check_reference(seed, "b1", refresh=True)
    candidates = client.candidates(seed, "b0")
    chosen = client.choose(seed, "b1", ReadersChoice(key="RESN2016"))
    cleared = client.clear_choice(seed, "b1")
    [group] = client.duplicates()
    stood = client.let_stand("RESN2BIS")
    merged = client.merge(Merge(keep="RESN2BIS", other="RESN2016"))

    assert cost.unchecked == 2
    assert [type(e) for e in events] == [
        ReferenceCheckedEvent, ReferenceCheckedEvent, CheckFinishedEvent,
    ]  # fmt: skip
    assert again.resolution is not None and again.resolution.layer == "crossref"
    assert [w.zotero_key for w in candidates] == ["RESN2016", "RESN2BIS"]
    assert (chosen.chosen, cleared.chosen) == (True, False)
    assert [i.zotero_key for i in group.items] == ["RESN2016", "RESN2BIS"]
    assert stood[0].items[0].zotero_key == "RESN2BIS"
    assert merged.moved == 1
    with pytest.raises(NotFoundError):
        client.check_cost(999999)
