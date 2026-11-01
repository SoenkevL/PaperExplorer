"""Registry Checks, Readers' Choices and Duplicate Items in the web app, over a mocked API."""

import html
import json
import re
from collections.abc import Callable

import httpx
from fastapi.testclient import TestClient

from paperexplorer_api.client import ApiClient
from paperexplorer_api.schemas import (
    CitationDetail,
    DuplicateItem,
    DuplicateItems,
    Resolution,
    WorkBrief,
)
from paperexplorer_web.app import create_app

from .test_reader import SMITH, WORK, _summary, _text

type Handler = Callable[[httpx.Request], httpx.Response]

TRAINING = WorkBrief(
    id=21,
    zotero_key="SMIT2020",
    title="On the Training of Therapists: A Survey",
    creators=["Smith, Ann"],
    year=2020,
    doi="10.1000/xyz123",
)
NEIGHBOUR = WorkBrief(
    id=22, zotero_key=None, title="A neighbour's paper", creators=["Other, Bo"], year=2019,
    doi="10.1000/neighbour",
)  # fmt: skip
UNCHECKED = Resolution(
    reference="b42", state="unchecked", work=None, level=None, confidence=None, evidence=[],
    layer=None, doi_conflict=None, chosen=False,
)  # fmt: skip
RESOLVED = UNCHECKED.model_copy(
    update={
        "state": "resolved",
        "work": TRAINING,
        "level": "certain",
        "confidence": 1.0,
        "evidence": ["doi", "title"],
        "layer": "crossref",
    }
)
UNRESOLVED = UNCHECKED.model_copy(update={"state": "unresolved"})


def _json(model: object) -> httpx.Response:
    data = (
        [m.model_dump(mode="json") for m in model]  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType, reportAttributeAccessIssue]
        if isinstance(model, list)
        else model.model_dump(mode="json")  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    )
    return httpx.Response(200, json=data)


def _smith(resolution: Resolution | None) -> CitationDetail:
    assert SMITH.reference is not None
    reference = SMITH.reference.model_copy(update={"resolution": resolution})
    return SMITH.model_copy(update={"reference": reference})


def _web(
    routes: dict[tuple[str, str], httpx.Response | Handler] | None = None,
    smith: CitationDetail = SMITH,
) -> tuple[TestClient, list[httpx.Request]]:
    """The web app over an API that serves the reader's Work, Smith's Citation and `routes`."""
    sent: list[httpx.Request] = []
    served: dict[tuple[str, str], httpx.Response | Handler] = {
        ("GET", "/health"): httpx.Response(200, json={"status": "ok", "version": "9.9"}),
        ("GET", "/works"): _json([_summary(WORK)]),
        ("GET", f"/works/{WORK.id}"): _json(WORK),
        ("GET", "/citations/11"): _json(smith),
        **(routes or {}),
    }

    def api(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        found = served.get((request.method, request.url.path))
        if found is None:
            return httpx.Response(404, json={"detail": f"No {request.url.path}"})
        return found if isinstance(found, httpx.Response) else found(request)

    client = ApiClient(httpx.Client(base_url="http://api", transport=httpx.MockTransport(api)))
    return TestClient(create_app(client)), sent


def _panel(resolution: Resolution | None) -> str:
    web, _ = _web(smith=_smith(resolution))
    return web.get("/works/7/panel", params={"c": 11}).text


def _buttons(fragment: str) -> list[str]:
    return [_text(b).strip() for b in re.findall(r"<button[^>]*>.*?</button>", fragment, re.S)]


def test_the_panel_shows_the_work_a_reference_resolved_to_its_level_and_source() -> None:
    panel = _text(_panel(RESOLVED))

    assert "On the Training of Therapists: A Survey" in panel
    assert "Smith, Ann (2020)" in panel
    assert "certain" in panel
    assert "found in Crossref" in panel


def test_the_panel_offers_check_on_unchecked_and_check_again_on_unresolved_or_low() -> None:
    low = RESOLVED.model_copy(update={"level": "low"})
    chosen = RESOLVED.model_copy(update={"layer": "reader", "chosen": True})
    offered = {
        "unchecked": (UNCHECKED, "Check"),
        "unresolved": (UNRESOLVED, "Check again"),
        "low": (low, "Check again"),
        "certain": (RESOLVED, None),
        "chosen": (chosen, None),
    }
    for case, (resolution, check) in offered.items():
        buttons = [b for b in _buttons(_panel(resolution)) if b.startswith("Check")]
        assert buttons == ([check] if check else []), case
    assert "Unchecked Reference" in _text(_panel(UNCHECKED))
    assert "Unresolved Reference" in _text(_panel(UNRESOLVED))
    assert "Not resolved yet" in _text(_panel(None))


def test_check_asks_the_api_about_one_reference_and_shows_what_it_found() -> None:
    checked = {"resolution": RESOLVED.model_dump(mode="json"), "stopped": None}
    route = ("POST", "/works/7/references/b42/checks")
    web, sent = _web({route: httpx.Response(200, json=checked)})

    fragment = web.post("/works/7/references/b42/check", data={"c": "11", "refresh": "true"}).text

    assert json.loads(sent[-1].content) == {"refresh": True}
    assert fragment.lstrip().startswith('<div class="resolution" id="resolution-b42">')
    assert "found in Crossref" in _text(fragment)


def test_a_check_that_stopped_says_why_and_keeps_the_reference_as_it_was() -> None:
    stopped = {"resolution": None, "stopped": "OpenAlex budget spent, 1 References left"}
    route = ("POST", "/works/7/references/b42/checks")
    web, _ = _web({route: httpx.Response(200, json=stopped)}, smith=_smith(UNCHECKED))

    fragment = _text(web.post("/works/7/references/b42/check", data={"c": "11"}).text)

    assert "OpenAlex budget spent" in fragment
    assert "Unchecked Reference" in fragment


def test_a_check_the_api_cannot_serve_says_so() -> None:
    web, _ = _web(smith=_smith(UNCHECKED))  # no Zotero keys: the API serves no Check routes

    fragment = _text(web.post("/works/7/references/b42/check", data={"c": "11"}).text)

    assert "Not checked" in fragment
    assert "Unchecked Reference" in fragment


CHOICE = "/works/7/references/b42/choice"
CHOSEN = RESOLVED.model_copy(update={"layer": "reader", "chosen": True})


def test_choosing_lists_the_candidates_each_with_a_button() -> None:
    route = ("GET", "/works/7/references/b42/candidates")
    web, _ = _web({route: _json([TRAINING, NEIGHBOUR])})

    fragment = web.get("/works/7/references/b42/candidates", params={"c": 11}).text

    text = _text(fragment)
    assert text.index("On the Training of Therapists") < text.index("A neighbour's paper")
    assert _buttons(fragment) == ["Choose", "Choose", "Choose", "None of these"]
    assert 'name="named"' in fragment  # a Zotero key or a DOI of the reader's own


def test_the_reader_chooses_a_candidate_a_key_or_doi_or_none() -> None:
    chosen: list[object] = []

    def choose(request: httpx.Request) -> httpx.Response:
        chosen.append(json.loads(request.content))
        return _json(CHOSEN)

    web, _ = _web({("PUT", CHOICE): choose})

    for form in (
        {"pick": "work", "work_id": "21"},
        {"pick": "named", "named": " 10.1000/xyz123 "},
        {"pick": "none"},
    ):
        fragment = web.put(CHOICE, data={"c": "11", **form}).text
        assert "your choice" in _text(fragment)
    assert chosen == [
        {"work_id": 21, "key": None},
        {"work_id": None, "key": "10.1000/xyz123"},
        {"work_id": None, "key": None},
    ]


def test_a_choice_the_api_refuses_says_why_and_keeps_the_reference_as_it_was() -> None:
    refusals = {
        "unknown": httpx.Response(404, json={"detail": "No Work NOPE"}),
        "itself": httpx.Response(422, json={"detail": "A Work cannot cite itself"}),
    }
    for case, refusal in refusals.items():
        web, _ = _web({("PUT", CHOICE): refusal}, smith=_smith(UNRESOLVED))

        fragment = _text(web.put(CHOICE, data={"c": "11", "pick": "named", "named": "X"}).text)

        assert "Not chosen" in fragment, case
        assert str(refusal.json()["detail"]) in fragment, case
        assert "Unresolved Reference" in fragment, case


def test_a_choice_shows_and_can_be_cleared() -> None:
    web, sent = _web({("DELETE", CHOICE): _json(RESOLVED)}, smith=_smith(CHOSEN))
    panel = web.get("/works/7/panel", params={"c": 11}).text

    assert "Clear my choice" in _buttons(panel)
    fragment = web.delete(CHOICE, params={"c": 11}).text

    assert sent[-1].method == "DELETE"
    assert "found in Crossref" in _text(fragment)


def test_a_doi_conflict_shows_both_works_and_lets_the_reader_pick_either() -> None:
    conflict = RESOLVED.model_copy(update={"level": "low", "doi_conflict": NEIGHBOUR})

    panel = _panel(conflict)

    text = _text(panel)
    assert "DOI Conflict" in text
    assert "On the Training of Therapists" in text and "A neighbour's paper" in text
    picks = re.findall(r"<button[^>]*hx-put[^>]*>", panel)
    assert ['"work_id": 21' in p for p in picks] == [True, False]
    assert ['"work_id": 22' in p for p in picks] == [False, True]


COST = ("GET", "/works/7/checks/cost")


def _cost(unchecked: int = 12, budget: float | None = 0.99) -> httpx.Response:
    return httpx.Response(200, json={
        "unchecked": unchecked, "crossref_batches": 1, "openalex_batches": 1,
        "title_searches": 12, "bibliographic_searches": 3, "openalex_usd": 0.0121,
        "budget_usd": budget,
    })  # fmt: skip


def _stream(*events: dict[str, object]) -> httpx.Response:
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def test_the_work_page_loads_the_checks_cost_after_the_text() -> None:
    web, sent = _web({COST: _cost()})

    page = web.get("/works/7").text

    assert re.search(r'hx-get="/works/7/check"[^>]*hx-trigger="load"', page)
    assert not any(r.url.path == COST[1] for r in sent)  # OpenAlex is asked only once it loads


def test_the_check_button_counts_the_unchecked_references_and_shows_the_cost_first() -> None:
    web, _ = _web({COST: _cost()})

    box = web.get("/works/7/check").text

    assert _buttons(box) == ["Check 12 Unchecked References"]
    text = _text(box)
    assert "1 Crossref batch, 1 OpenAlex batch" in text
    assert "up to 12 title searches and 3 bibliographic searches" in text
    assert "$0.0121 of the $0.99 left of OpenAlex's day" in text


def test_the_cost_says_when_openalex_did_not_say_or_has_too_little_left() -> None:
    unknown, _ = _web({COST: _cost(budget=None)})
    short, _ = _web({COST: _cost(budget=0.005)})

    assert "OpenAlex did not say what is left of its day" in _text(
        unknown.get("/works/7/check").text
    )
    assert "may stop before it is done" in _text(short.get("/works/7/check").text)


def test_with_nothing_unchecked_or_no_checks_served_there_is_no_button() -> None:
    done, _ = _web({COST: _cost(unchecked=0)})
    unserved, _ = _web()

    assert _buttons(done.get("/works/7/check").text) == []
    assert "No Unchecked References" in _text(done.get("/works/7/check").text)
    assert _buttons(unserved.get("/works/7/check").text) == []
    assert "Zotero's keys" in _text(unserved.get("/works/7/check").text)


def test_a_started_check_streams_each_reference_then_how_it_ended() -> None:
    nowhere = UNRESOLVED.model_copy(update={"reference": "b43"})
    checks = _stream(
        {"type": "reference", "resolution": RESOLVED.model_dump(mode="json")},
        {"type": "reference", "resolution": nowhere.model_dump(mode="json")},
        {"type": "finished", "counts": {"references": 2, "certain": 1, "high": 0, "low": 0,
                                        "unchecked": 0, "unresolved": 1, "doi_conflicts": 0}},
    )  # fmt: skip
    web, sent = _web({("POST", "/works/7/checks"): checks})

    started = web.post("/works/7/checks").text
    stream = re.search(r'sse-connect="(/checks/[0-9a-f]+/stream)"', started)
    assert stream, started
    assert not any(r.url.path == "/works/7/checks" for r in sent)  # asked once the page streams
    events = web.get(stream[1]).text

    names = re.findall(r"^event: (\w+)$", events, re.M)
    assert names == ["reference", "reference", "finished"]
    text = _text(events)
    assert "On the Training of Therapists" in text and "certain" in text
    assert "b43" in text and "Unresolved" in text
    assert "2 References checked: 1 certain, 1 Unresolved" in text
    ending = events[events.index("event: finished") :]
    assert "b43" in _text(ending)  # the ending replaces the running list, so it lists them again
    assert json.loads(sent[-1].content) == {"refresh": False}
    assert "event: error" in web.get(stream[1]).text  # a reconnecting stream starts no Check


def test_a_check_that_stopped_says_why() -> None:
    endings = {
        "budget_spent": {"type": "budget_spent", "left": 4,
                         "message": "OpenAlex budget spent, 4 References left, try after 00:00"},
        "error": {"type": "error", "message": "Check stopped: Crossref kept failing"},
    }  # fmt: skip
    for name, ending in endings.items():
        web, _ = _web({("POST", "/works/7/checks"): _stream(ending)})
        stream = re.search(r'sse-connect="([^"]+)"', web.post("/works/7/checks").text)
        assert stream

        events = web.get(stream[1]).text

        assert re.findall(r"^event: (\w+)$", events, re.M) == [name], name
        assert str(ending["message"]) in _text(events), name


STANDING = DuplicateItem(**TRAINING.model_dump(), pdf=True, added="2024-01-02")
TWIN = DuplicateItem(
    **TRAINING.model_copy(update={"id": 23, "zotero_key": "TWIN2020"}).model_dump(),
    pdf=False,
    added="2025-03-04",
)
PAIR = DuplicateItems(doi="10.1000/xyz123", items=[STANDING, TWIN])


def _items(fragment: str) -> list[str]:
    return [_text(li) for li in re.findall(r"<li class=\"duplicate[^>]*>.*?</li>", fragment, re.S)]


def test_the_index_shows_duplicate_items_the_standing_item_first() -> None:
    web, _ = _web({("GET", "/duplicates"): _json([PAIR])})

    page = web.get("/").text

    assert "Duplicate Items" in page
    standing, twin = _items(page)
    assert "SMIT2020" in standing and "stands for 10.1000/xyz123" in standing
    assert "with its PDF" in standing and "no PDF" in twin
    assert "TWIN2020" in twin
    section = re.search(r'<section id="duplicates".*?</section>', page, re.S)
    assert section
    assert _buttons(section[0]) == ["Let stand", "Merge into SMIT2020"]


def test_without_duplicate_items_or_their_route_the_index_shows_none() -> None:
    none, _ = _web({("GET", "/duplicates"): _json([])})
    unserved, _ = _web()

    for web in (none, unserved):
        page = web.get("/")
        assert page.status_code == 200
        assert "Duplicate Items" not in page.text
        assert 'href="/works/7"' in page.text


def test_letting_an_item_stand_shows_the_items_as_they_now_stand() -> None:
    turned = PAIR.model_copy(update={"items": [TWIN, STANDING]})
    web, sent = _web({("PUT", "/duplicates/TWIN2020/stand"): _json([turned])})

    fragment = web.put("/duplicates/TWIN2020/stand").text

    assert sent[-1].url.path == "/duplicates/TWIN2020/stand"
    first, _ = _items(fragment)
    assert "TWIN2020" in first and "stands for" in first


def test_a_merge_asks_the_reader_first_then_merges_in_zotero() -> None:
    merged: list[object] = []

    def merge(request: httpx.Request) -> httpx.Response:
        merged.append(json.loads(request.content))
        return httpx.Response(200, json={"moved": 3})

    web, _ = _web({("POST", "/duplicates/merge"): merge, ("GET", "/duplicates"): _json([PAIR])})
    button = re.search(r"<button[^>]*hx-post=\"/duplicates/merge\"[^>]*>", web.get("/").text)
    assert button
    confirm = html.unescape(re.search(r'hx-confirm="([^"]*)"', button[0])[1])  # pyright: ignore[reportOptionalSubscript]
    assert "TWIN2020" in confirm and "SMIT2020" in confirm and "trash" in confirm

    after = web.post("/duplicates/merge", data={"keep": "SMIT2020", "other": "TWIN2020"}).text

    assert merged == [{"keep": "SMIT2020", "other": "TWIN2020"}]
    assert "Merged TWIN2020 into SMIT2020" in _text(after)
    assert "3 Resolutions moved" in _text(after)


def test_a_merge_the_api_refuses_says_why() -> None:
    refusal = httpx.Response(422, json={"detail": "SMIT2020 and TWIN2020 hold other DOIs"})
    web, _ = _web({("POST", "/duplicates/merge"): refusal, ("GET", "/duplicates"): _json([PAIR])})

    pair = {"keep": "SMIT2020", "other": "TWIN2020"}
    after = _text(web.post("/duplicates/merge", data=pair).text)

    assert "Not merged: SMIT2020 and TWIN2020 hold other DOIs" in after
    assert "TWIN2020" in after  # the items as they still stand
