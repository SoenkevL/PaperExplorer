"""Registry Checks: References looked up in Crossref and OpenAlex, only when the reader asks."""

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import psycopg
import pytest
from psycopg.rows import TupleRow

from paperexplorer.config import Settings
from paperexplorer.domain import (
    Layer,
    Level,
    LibraryItem,
    RegistrySource,
    Resolution,
    ResolutionState,
    WorkId,
)
from paperexplorer.parsing import ParsedWork, Reference, TextOrigin
from paperexplorer.resolution import (
    BudgetSpent,
    CheckCost,
    ReferenceChecked,
    Registries,
    RegistryCheckEvent,
    RegistryError,
    estimate,
    registry_check,
)
from paperexplorer.store import ReadersChoices, RegistryResponses, Resolutions, Works, WorkTexts

Conn = psycopg.Connection[TupleRow]
Json = dict[str, Any]


def crossref_item(
    doi: str, title: str, family: str, year: int, kind: str = "journal-article"
) -> Json:
    return {
        "DOI": doi,
        "type": kind,
        "title": [title],
        "author": [{"family": family, "given": "A."}],
        "issued": {"date-parts": [[year, 3]]},
    }


def openalex_work(doi: str | None, title: str, name: str, year: int, oa_id: str) -> Json:
    return {
        "id": f"https://openalex.org/{oa_id}",
        "doi": f"https://doi.org/{doi}" if doi else None,
        "title": title,
        "publication_year": year,
        "authorships": [{"author": {"display_name": name}}],
    }


RESNET = crossref_item(
    "10.1109/CVPR.2016.90", "Deep Residual Learning for Image Recognition", "He", 2016
)
RESNET_OA = openalex_work(
    "10.1109/cvpr.2016.90", "Deep Residual Learning for Image Recognition", "Kaiming He", 2016, "W1"
)


class Registry:
    """Crossref and OpenAlex as hand-written JSON, recording every request they are sent."""

    def __init__(self) -> None:
        self.crossref: dict[str, Json] = {}  # by lowercase DOI
        self.openalex: dict[str, Json] = {}
        self.titles: dict[str, list[Json]] = {}  # OpenAlex title.search, by the query sent
        self.bibliographic: list[Json] = []  # Crossref query.bibliographic's items, any query
        self.requests: list[httpx.Request] = []
        self.responses: list[Callable[[httpx.Request], httpx.Response | None]] = []
        self.remaining_usd = "0.0900"

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for respond in self.responses:
            if (found := respond(request)) is not None:
                return found
        params = request.url.params
        if request.url.host == "api.crossref.org":
            if "query.bibliographic" in params:
                return httpx.Response(200, json={"message": {"items": self.bibliographic}})
            dois = [d.removeprefix("doi:") for d in params["filter"].split(",")]
            items = [self.crossref[d.lower()] for d in dois if d.lower() in self.crossref]
            return httpx.Response(200, json={"message": {"items": items}})
        headers = {"x-ratelimit-remaining-usd": self.remaining_usd}
        if request.url.path != "/works":  # a singleton: free, and it reports the budget
            return httpx.Response(200, json={"id": request.url.path}, headers=headers)
        filter_ = params["filter"]
        if filter_.startswith("title.search:"):
            found = self.titles.get(filter_.removeprefix("title.search:"), [])
            return httpx.Response(200, json={"results": found}, headers=headers)
        # OpenAlex files APA's `10.1037//` form under the single slash, and answers it so.
        dois = [d.lower().replace("//", "/", 1) for d in filter_.removeprefix("doi:").split("|")]
        results = [self.openalex[d] for d in dois if d in self.openalex]
        return httpx.Response(200, json={"results": results}, headers=headers)

    def sent(self, host: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.host == host]


class Clock:
    """Time that passes only when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def registry() -> Registry:
    return Registry()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def registries(registry: Registry, clock: Clock) -> Registries:
    return Registries(
        Settings(_env_file=None),  # pyright: ignore[reportCallIssue]
        transport=httpx.MockTransport(registry.handle),
        clock=clock,
        sleep=clock.sleep,
    )


def _ref(key: str, **fields: object) -> Reference:
    return Reference.model_validate({"id": key, "text": f"Printed {key}", "page": 1} | fields)


def _seed(conn: Conn, refs: list[Reference]) -> WorkId:
    seed = Works(conn).ensure("SEED1111")
    WorkTexts(conn).replace(
        seed, ParsedWork(text_origin=TextOrigin.PRINTED, blocks=[], references=refs)
    )
    return seed


def _check(
    conn: Conn, seed: WorkId, refs: list[Reference], registries: Registries, **kw: bool
) -> list[RegistryCheckEvent]:
    async def collect() -> list[RegistryCheckEvent]:
        return [e async for e in registry_check(conn, seed, refs, registries, **kw)]

    return asyncio.run(collect())


def _checked(events: list[RegistryCheckEvent]) -> dict[str, Resolution]:
    return {e.resolution.reference: e.resolution for e in events if isinstance(e, ReferenceChecked)}


def test_a_printed_doi_crossref_knows_resolves_certain_to_a_new_work(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    registry.crossref["10.1109/cvpr.2016.90"] = RESNET
    registry.openalex["10.1109/cvpr.2016.90"] = RESNET_OA
    refs = [_ref("b0", doi="10.1109/CVPR.2016.90", title=RESNET["title"][0], surnames=["He"])]
    seed = _seed(store_db, refs)

    events = _check(store_db, seed, refs, registries)

    resolution = _checked(events)["b0"]
    assert (resolution.state, resolution.level, resolution.layer, resolution.evidence) == (
        ResolutionState.RESOLVED,
        Level.CERTAIN,
        Layer.CROSSREF,
        ["doi"],
    )
    assert resolution.work is not None
    work = Works(store_db).get(resolution.work)
    assert work is not None
    assert (work.doi, work.title, work.creators, work.year, work.zotero_key, work.synced_at) == (
        "10.1109/cvpr.2016.90",
        "Deep Residual Learning for Image Recognition",
        ["He, A."],
        2016,
        None,
        None,
    )
    assert Resolutions(store_db).for_work(seed) == [resolution]
    assert json.loads(json.dumps(events[-1].model_dump(mode="json")))["counts"]["certain"] == 1


def test_a_second_check_asks_no_registry_and_library_matches_ask_none(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    registry.crossref["10.1109/cvpr.2016.90"] = RESNET
    refs = [_ref("b0", doi="10.1109/CVPR.2016.90", title=RESNET["title"][0], surnames=["He"])]
    seed = _seed(store_db, refs)
    first = _checked(_check(store_db, seed, refs, registries))
    asked = len(registry.requests)

    again = _checked(_check(store_db, seed, refs, registries))

    assert asked == 2  # one batch to each registry
    assert len(registry.requests) == asked
    assert again == first


def test_a_doi_only_openalex_knows_resolves_from_openalex(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    arxiv = "10.48550/arxiv.1706.03762"
    registry.openalex[arxiv] = openalex_work(
        arxiv, "Attention Is All You Need", "Ashish Vaswani", 2017, "W2"
    )
    refs = [_ref("b0", doi="10.48550/arXiv.1706.03762", title="Attention is all you need")]
    seed = _seed(store_db, refs)

    resolution = _checked(_check(store_db, seed, refs, registries))["b0"]

    assert (resolution.level, resolution.layer) == (Level.CERTAIN, Layer.OPENALEX)
    assert resolution.work is not None
    work = Works(store_db).get(resolution.work)
    assert work is not None and work.creators == ["Vaswani, Ashish"]


def test_a_neighbours_doi_lets_a_title_search_win_at_low_and_keeps_the_dois_work(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    registry.crossref["10.1109/cvpr.2016.90"] = RESNET
    attention = openalex_work(None, "Attention Is All You Need", "Ashish Vaswani", 2017, "W2")
    registry.titles["attention is all you need"] = [attention]
    refs = [
        _ref("b0", doi="10.1109/cvpr.2016.90", title="Attention is all you need",
             surnames=["Vaswani"], year="2017"),
        _ref("b1", doi="10.1109/cvpr.2016.90", title="A title no registry holds"),
    ]  # fmt: skip
    seed = _seed(store_db, refs)

    checked = _checked(_check(store_db, seed, refs, registries))

    resnet = Works(store_db).standing_item("10.1109/cvpr.2016.90")
    assert resnet is not None
    won = checked["b0"]
    assert (won.level, won.layer, won.doi_conflict) == (Level.LOW, Layer.OPENALEX, resnet)
    assert "doi_conflict" in won.evidence
    assert won.work is not None and won.work != resnet
    assert (checked["b1"].state, checked["b1"].doi_conflict) == (
        ResolutionState.UNRESOLVED,
        resnet,
    )


def test_a_reference_without_a_title_is_found_by_its_printed_string_at_low(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    registry.bibliographic = [RESNET]
    text = "He A. Deep residual learning for image recognition. CVPR 2016."
    refs = [_ref("b0", text=text)]
    seed = _seed(store_db, refs)

    resolution = _checked(_check(store_db, seed, refs, registries))["b0"]

    assert (resolution.level, resolution.layer, resolution.evidence) == (
        Level.LOW,
        Layer.CROSSREF,
        ["bibliographic", "surname", "year"],
    )
    [query] = [
        r for r in registry.sent("api.crossref.org") if "query.bibliographic" in r.url.params
    ]
    assert query.url.params["query.bibliographic"] == text


def test_a_garbage_string_is_unresolved(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    registry.bibliographic = [RESNET]
    refs = [_ref("b0", text="pp. 12-19, ibid.")]
    seed = _seed(store_db, refs)

    events = _check(store_db, seed, refs, registries)

    assert _checked(events)["b0"].state is ResolutionState.UNRESOLVED
    assert Resolutions(store_db).for_work(seed)[0].state is ResolutionState.UNRESOLVED


def test_dois_go_a_hundred_to_a_batch_to_both_registries(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    refs = [_ref(f"b{i}", doi=f"10.5555/{i}") for i in range(150)]
    seed = _seed(store_db, refs)

    _check(store_db, seed, refs, registries)

    for host, sep in (("api.crossref.org", ","), ("api.openalex.org", "|")):
        batches = [r for r in registry.sent(host) if "doi:" in r.url.params.get("filter", "")]
        assert sorted(len(r.url.params["filter"].split(sep)) for r in batches) == [50, 100]


def test_crossref_list_queries_run_one_a_second_without_a_mailto(
    store_db: Conn, registry: Registry, registries: Registries, clock: Clock
) -> None:
    refs = [_ref(f"b{i}", text=f"Unknown work number {i}") for i in range(3)]
    seed = _seed(store_db, refs)

    _check(store_db, seed, refs, registries)

    assert len(registry.sent("api.crossref.org")) == 3
    assert clock.sleeps == [1.0, 1.0]


def test_a_429_waits_for_its_retry_after_and_tries_again(
    store_db: Conn, registry: Registry, registries: Registries, clock: Clock
) -> None:
    registry.crossref["10.1109/cvpr.2016.90"] = RESNET
    throttled: list[httpx.Request] = []

    def once(request: httpx.Request) -> httpx.Response | None:
        if request.url.host == "api.crossref.org" and not throttled:
            throttled.append(request)
            return httpx.Response(429, headers={"retry-after": "7"})
        return None

    registry.responses.append(once)
    refs = [_ref("b0", doi="10.1109/cvpr.2016.90", title=RESNET["title"][0])]
    seed = _seed(store_db, refs)

    resolution = _checked(_check(store_db, seed, refs, registries))["b0"]

    assert resolution.level is Level.CERTAIN
    assert 7.0 in clock.sleeps


def test_a_registry_failing_three_times_stops_the_check(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    registry.responses.append(
        lambda r: httpx.Response(503) if r.url.host == "api.openalex.org" else None
    )
    refs = [_ref("b0", doi="10.5555/x")]
    seed = _seed(store_db, refs)

    with pytest.raises(RegistryError, match="3 times"):
        _check(store_db, seed, refs, registries)
    assert len(registry.sent("api.openalex.org")) == 3


def test_a_spent_openalex_budget_stops_the_check_and_keeps_what_landed(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    registry.crossref["10.1109/cvpr.2016.90"] = RESNET
    registry.remaining_usd = "0.0000"  # the DOI batch spends the last of it
    refs = [
        _ref("b0", doi="10.1109/cvpr.2016.90", title=RESNET["title"][0]),
        _ref("b1", title="A title to search for"),
    ]
    seed = _seed(store_db, refs)

    events = _check(store_db, seed, refs, registries)

    assert isinstance(events[-1], BudgetSpent)
    assert events[-1].left == 1
    assert events[-1].message == "OpenAlex budget spent, 1 References left, try after 00:00 UTC"
    assert set(_checked(events)) == {"b0"}
    assert [r.reference for r in Resolutions(store_db).for_work(seed)] == ["b0"]


def test_refresh_asks_again_past_the_cache(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    refs = [_ref("b0", doi="10.1109/cvpr.2016.90", title=RESNET["title"][0], surnames=["He"])]
    seed = _seed(store_db, refs)
    assert _checked(_check(store_db, seed, refs, registries))["b0"].state is (
        ResolutionState.UNRESOLVED
    )
    registry.crossref["10.1109/cvpr.2016.90"] = RESNET  # registered since

    cached = _checked(_check(store_db, seed, refs, registries))["b0"]
    fresh = _checked(_check(store_db, seed, refs, registries, refresh=True))["b0"]

    assert cached.state is ResolutionState.UNRESOLVED
    assert fresh.level is Level.CERTAIN


def test_a_miss_is_asked_again_after_thirty_days(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    refs = [_ref("b0", doi="10.1109/cvpr.2016.90", title=RESNET["title"][0])]
    seed = _seed(store_db, refs)
    _check(store_db, seed, refs, registries)
    store_db.execute("UPDATE registry_responses SET fetched_at = now() - interval '31 days'")
    registry.crossref["10.1109/cvpr.2016.90"] = RESNET

    resolution = _checked(_check(store_db, seed, refs, registries))["b0"]

    assert resolution.level is Level.CERTAIN


def test_a_printed_preprint_doi_resolves_to_the_preprint_not_its_article(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    preprint = crossref_item(
        "10.21203/rs.3.rs-1763486/v1", "Entropy of the resting brain", "Lee", 2022,
        kind="posted-content",
    )  # fmt: skip
    preprint["relation"] = {"is-preprint-of": [{"id": "10.1007/s10548-023-00001-1"}]}
    registry.crossref["10.21203/rs.3.rs-1763486/v1"] = preprint
    refs = [_ref("b0", doi="10.21203/rs.3.rs-1763486/v1", title="Entropy of the resting brain")]
    seed = _seed(store_db, refs)

    resolution = _checked(_check(store_db, seed, refs, registries))["b0"]

    assert resolution.work is not None
    work = Works(store_db).get(resolution.work)
    assert work is not None and work.doi == "10.21203/rs.3.rs-1763486/v1"


def test_an_apa_double_slash_doi_finds_the_record_under_one_slash(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    registry.openalex["10.1037/0022-006x.69.4.597"] = openalex_work(
        "10.1037/0022-006x.69.4.597", "Brief treatment", "Ann Smith", 2001, "W3"
    )
    refs = [_ref("b0", doi="10.1037//0022-006X.69.4.597", title="Brief treatment")]
    seed = _seed(store_db, refs)

    resolution = _checked(_check(store_db, seed, refs, registries))["b0"]

    assert (resolution.level, resolution.layer) == (Level.CERTAIN, Layer.OPENALEX)


def test_a_reference_the_library_matches_is_never_sent_even_on_refresh(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    works = Works(store_db)
    held = works.ensure("RESN2016")
    works.record_library_item(
        held,
        LibraryItem(key="RESN2016", title=RESNET["title"][0], creators=["He, A."], year=2016,
                    doi="10.1109/cvpr.2016.90"),
    )  # fmt: skip
    refs = [_ref("b0", doi="10.1109/cvpr.2016.90", title=RESNET["title"][0], surnames=["He"])]
    seed = _seed(store_db, refs)

    resolution = _checked(_check(store_db, seed, refs, registries, refresh=True))["b0"]

    assert (resolution.work, resolution.layer) == (held, Layer.LIBRARY)
    assert registry.requests == []


def test_a_titled_reference_no_title_search_matches_is_not_sent_to_crossref(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    refs = [_ref("b0", title="A title no registry holds", surnames=["Nobody"])]
    seed = _seed(store_db, refs)

    resolution = _checked(_check(store_db, seed, refs, registries))["b0"]

    assert resolution.state is ResolutionState.UNRESOLVED
    assert registry.sent("api.crossref.org") == []


def test_a_registry_refusing_a_request_stops_the_check(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    registry.responses.append(
        lambda r: httpx.Response(400) if r.url.host == "api.crossref.org" else None
    )
    refs = [_ref("b0", doi="10.5555/x")]
    seed = _seed(store_db, refs)

    with pytest.raises(RegistryError, match="answered 400"):
        _check(store_db, seed, refs, registries)


def test_an_unregistered_doi_is_not_sent_and_its_title_is_searched(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    RegistryResponses(store_db).record("doi:handle", "10.5555/typo", {}, hit=False)
    refs = [_ref("b0", doi="10.5555/typo", title="Attention is all you need")]
    seed = _seed(store_db, refs)

    resolution = _checked(_check(store_db, seed, refs, registries))["b0"]

    assert [r.url.params["filter"] for r in registry.requests] == [
        "title.search:attention is all you need"
    ]
    assert "unregistered DOI 10.5555/typo" in resolution.evidence


def test_a_check_asks_nothing_for_a_readers_choice_even_on_refresh(
    store_db: Conn, registry: Registry, registries: Registries
) -> None:
    registry.crossref["10.1109/cvpr.2016.90"] = RESNET
    refs = [_ref("b0", doi="10.1109/CVPR.2016.90", title=RESNET["title"][0], surnames=["He"])]
    seed = _seed(store_db, refs)
    ReadersChoices(store_db).choose(seed, "Printed b0", None)

    events = _check(store_db, seed, refs, registries, refresh=True)

    assert registry.requests == []
    assert _checked(events)["b0"].layer is Layer.READER
    assert [r.state for r in Resolutions(store_db).for_work(seed)] == [ResolutionState.UNRESOLVED]


def test_the_cost_of_a_check_counts_the_calls_the_cache_does_not_answer(store_db: Conn) -> None:
    cache = RegistryResponses(store_db)
    for source, record in (
        (RegistrySource.CROSSREF_DOI, RESNET),
        (RegistrySource.OPENALEX_DOI, RESNET_OA),
    ):
        cache.record(source, "10.1109/cvpr.2016.90", record, hit=True)
    cache.record(RegistrySource.OPENALEX_TITLE, "a title searched before", [], hit=False)
    refs = [
        _ref("b0", doi="10.5555/zero", title="Zero"),
        _ref("b1", doi="10.5555/one"),  # no title: Crossref's bibliographic query
        _ref("b2", doi="10.1109/CVPR.2016.90", title=RESNET["title"][0], surnames=["He"]),
        _ref("b3", title="A title searched before"),  # Unresolved: nothing left to ask
        _ref("b4", doi="10.5555/four", title="Four"),  # the reader chose: never asked
    ]  # fmt: skip
    seed = _seed(store_db, refs)
    ReadersChoices(store_db).choose(seed, "Printed b4", None)

    cost = estimate(store_db, seed, refs)

    assert cost.model_copy(update={"openalex_usd": round(cost.openalex_usd, 6)}) == CheckCost(
        unchecked=2, crossref_batches=1, openalex_batches=1, title_searches=1,
        bibliographic_searches=1, openalex_usd=0.0011,
    )  # fmt: skip
    assert estimate(store_db, seed, refs[:1], refresh=True).title_searches == 1


def test_the_budget_left_today_comes_from_a_free_openalex_record(
    registry: Registry, registries: Registries
) -> None:
    registry.remaining_usd = "0.0420"

    async def ask() -> float | None:
        async with registries:
            return await registries.budget()

    assert asyncio.run(ask()) == pytest.approx(0.042)  # pyright: ignore[reportUnknownMemberType]
    [request] = registry.requests
    assert request.url.path.startswith("/works/W")
