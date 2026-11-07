"""The index page: finding Library Items and Ingesting them, over a mocked API."""

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi.testclient import TestClient

from paperexplorer_api.client import ApiClient
from paperexplorer_api.schemas import (
    BranchCount,
    CollectionCount,
    CollectionTree,
    LibraryPage,
    LibrarySynced,
    LibraryWork,
    StageStatus,
    WorkSummary,
)
from paperexplorer_web.app import create_app

type Handler = Callable[[httpx.Request], httpx.Response]

SEVEN = ["fetch", "parse", "chunk", "embed", "extract_citations", "verify_dois", "resolve"]
FIVE = SEVEN[:5]
WHEN = datetime(2026, 11, 4, tzinfo=UTC)


def _run(state: Any = "done", error: str | None = None) -> StageStatus:
    return StageStatus(state=state, counts={}, error=error, started_at=WHEN, finished_at=WHEN)


def _work(
    *,
    done: list[str] | None = None,
    failed: tuple[str, str] | None = None,
    run: Any = None,
    has_pdf: bool = True,
    work_id: int = 7,
) -> LibraryWork:
    runs = {s: _run() for s in done or []}
    if failed:
        runs[failed[0]] = _run("failed", failed[1])
    return LibraryWork(
        id=work_id,
        zotero_key="VILL2024",
        title="Personal experience",
        creators=["Villiger, Daniel"],
        year=2024,
        doi=None,
        text_origin="printed" if "parse" in (done or []) else None,
        stages={s: runs[s].state if s in runs else "pending" for s in SEVEN},
        has_pdf=has_pdf,
        run=run,
        stage_statuses=runs,
        collections=[],
        in_branch=True,
    )


NO_COLLECTIONS = CollectionTree(
    collections=[],
    library=BranchCount(works=1, ingested=0),
    unfiled=BranchCount(works=1, ingested=0),
)


class Api:
    """A mocked API: routes by method and path, and what it was sent."""

    def __init__(self, routes: dict[str, Handler | httpx.Response]) -> None:
        health = httpx.Response(200, json={"status": "ok", "version": "9.9"})
        self.routes: dict[str, Handler | httpx.Response] = {
            "GET /health": health,
            "GET /collections": _json(NO_COLLECTIONS),
            **routes,
        }
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        route = self.routes.get(f"{request.method} {request.url.path}")
        if route is None:
            return httpx.Response(404, json={"detail": "No such route"})
        return route(request) if callable(route) else route

    def web(self) -> TestClient:
        http = httpx.Client(base_url="http://api", transport=httpx.MockTransport(self))
        return TestClient(create_app(ApiClient(http)))


def _json(model: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=model.model_dump(mode="json"))


def _page(*works: LibraryWork, total: int | None = None) -> httpx.Response:
    total = len(works) if total is None else total
    return _json(LibraryPage(items=list(works), total=total, branch_total=total))


def _row(html: str, work_id: int = 7) -> str:
    found = re.search(rf'<li id="work-{work_id}".*?</li>', html, re.S)
    assert found, html
    return found.group(0)


def test_the_index_page_lists_the_library_and_syncs_as_it_opens() -> None:
    api = Api({"GET /library": _page(_work())})

    text = api.web().get("/").text

    assert "Personal experience" in _row(text)
    assert 'hx-post="/library/sync"' in text
    assert 'hx-trigger="load"' in text


def test_an_item_without_a_pdf_says_so_and_cannot_be_ingested() -> None:
    api = Api({"GET /library": _page(_work(has_pdf=False))})

    row = _row(api.web().get("/").text)

    assert "no PDF" in row
    assert "hx-post" not in row


def test_a_new_item_offers_ingest_and_shows_seven_pending_pips() -> None:
    api = Api({"GET /library": _page(_work())})

    row = _row(api.web().get("/").text)

    assert ">Ingest<" in row
    assert 'hx-post="/works/7/ingesting"' in row
    pips = re.findall(r'class="pip (\w+)" title="(\w+): \w+"', row)
    assert pips == [("pending", s) for s in SEVEN]
    assert row.index('class="pip-gap"') > row.index('title="extract_citations')
    assert row.index('class="pip-gap"') < row.index('title="verify_dois')
    strip = re.search(r'<span class="pips" role="group" aria-label="([^"]+)"', row)
    assert strip
    assert strip.group(1) == "Stages: " + ", ".join(f"{s} pending" for s in SEVEN)
    assert "every 2s" not in row


def test_each_pip_shows_its_stage_s_state_and_a_done_one_is_its_re_run() -> None:
    work = _work(done=["fetch", "parse"], failed=("chunk", "no text"))
    row = _row(Api({"GET /library": _page(work)}).web().get("/").text)

    fetch = re.search(r'<button type="button" class="pip done"[^>]*>', row)
    assert fetch
    assert 'aria-label="Re-run fetch"' in fetch.group(0)
    assert 'hx-confirm="Re-run fetch, parse' in fetch.group(0)
    assert '<span class="pip failed" title="chunk: failed">' in row
    assert "chunk: no text" in row


def test_a_running_stage_pulses_and_offers_no_re_run() -> None:
    work = _work(done=["fetch"], run="running")
    work.stages["parse"] = "running"
    row = _row(Api({"GET /library": _page(work)}).web().get("/").text)

    assert '<span class="pip running" title="parse: running">' in row
    assert '<span class="pip done" title="fetch: done">' in row
    assert '<button type="button" class="pip' not in row


def test_the_api_without_a_library_says_why_and_lists_what_it_has() -> None:
    summary = WorkSummary(
        id=1, zotero_key="OLD", title="Ingested before", creators=[], year=None, doi=None,
        text_origin=None, stages={},
    )  # fmt: skip
    api = Api(
        {
            "GET /works": httpx.Response(200, json=[summary.model_dump(mode="json")]),
            "GET /collections": httpx.Response(404, json={"detail": "Not Found"}),
        }
    )

    text = api.web().get("/").text

    assert "needs Zotero" in text
    assert "Ingested before" in text
    assert 'hx-post="/library/sync"' not in text


def test_searching_asks_the_api_and_answers_with_the_list_only() -> None:
    api = Api({"GET /library": _page(_work())})

    response = api.web().get("/library", params={"q": "smith 2019"})

    assert api.seen[-1].url.params["q"] == "smith 2019"
    assert "<html" not in response.text
    assert _row(response.text)


def test_more_results_load_after_the_ones_shown() -> None:
    api = Api({"GET /library": _page(_work(), total=51)})

    first = api.web().get("/library", params={"q": "x"}).text

    assert "Load more" in first
    assert "/library?q=x&amp;offset=1" in first

    last = Api({"GET /library": _page(_work())}).web().get("/library", params={"offset": 50}).text
    assert "Load more" not in last


def test_a_failed_search_says_why() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert "could not be read" in Api({"GET /library": refuse}).web().get("/library").text


def test_syncing_says_how_it_went_and_tells_the_page_to_read_the_list_again() -> None:
    api = Api({"POST /library/sync": _json(LibrarySynced(items=2, removed=0, stale=False))})

    response = api.web().post("/library/sync")

    assert response.headers["HX-Trigger"] == "library-synced"
    assert 'id="library-sync-status"' in response.text
    assert "could not be reached" not in response.text
    assert [r.url.path for r in api.seen] == ["/library/sync"]


def test_a_sync_that_could_not_reach_zotero_keeps_the_last_copy_and_says_so() -> None:
    api = Api({"POST /library/sync": _json(LibrarySynced(items=0, removed=0, stale=True))})

    response = api.web().post("/library/sync")

    assert "Zotero could not be reached" in response.text
    assert response.headers["HX-Trigger"] == "library-synced"


def test_a_failed_sync_says_why_and_does_not_reread_the_list() -> None:
    api = Api({"POST /library/sync": httpx.Response(500, text="boom")})

    response = api.web().post("/library/sync")

    assert "not synced" in response.text
    assert "HX-Trigger" not in response.headers


def test_the_list_reads_the_current_search_again_after_a_sync() -> None:
    text = Api({"GET /library": _page(_work())}).web().get("/library").text

    root = re.search(r'<div id="library-list"[^>]*>', text)
    assert root
    assert 'hx-trigger="library-synced from:body"' in root.group(0)
    assert 'hx-include="#library-search"' in root.group(0)
    assert 'hx-disinherit="*"' in root.group(0)


def test_opening_the_page_waits_for_no_sync() -> None:
    api = Api({"GET /library": _page(_work())})

    text = api.web().get("/").text

    assert "/duplicates" not in [r.url.path for r in api.seen]
    assert "/library/sync" not in [r.url.path for r in api.seen]
    section = re.search(r'<section id="duplicates"[^>]*>', text)
    assert section
    assert 'hx-get="/duplicates"' in section.group(0)
    assert 'hx-trigger="library-synced from:body"' in section.group(0)


def test_a_failed_load_more_stays_a_list_item_and_says_why() -> None:
    api = Api({"GET /library": httpx.Response(500, text="boom")})

    text = api.web().get("/library", params={"offset": 50}).text

    assert text.strip().startswith("<li")
    assert 'id="library-list"' not in text
    assert "could not be read" in text


def test_an_action_on_a_work_that_has_gone_says_so_instead_of_vanishing() -> None:
    api = Api(
        {
            "POST /works/7/ingesting": httpx.Response(404, json={"detail": "No Work 7"}),
            "GET /works/7/stages": httpx.Response(404, json={"detail": "No Work 7"}),
        }
    )

    row = _row(api.web().post("/works/7/ingesting", data={}).text)

    assert "No Work 7" in row
    assert "every 2s" not in row


def test_an_action_the_api_cannot_answer_says_why_and_does_not_poll() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    api = Api({"POST /works/7/ingesting": refuse, "GET /works/7/stages": refuse})

    row = _row(api.web().post("/works/7/ingesting", data={}).text)

    assert "refused" in row
    assert "every 2s" not in row


def test_starting_answers_with_a_row_that_polls_while_it_waits_and_can_be_cancelled() -> None:
    api = Api({"POST /works/7/ingesting": _json(_work(run="queued"), 202)})

    row = _row(api.web().post("/works/7/ingesting", data={}).text)

    assert json.loads(api.seen[-1].content) == {"from_stage": None}
    assert 'hx-get="/works/7/row"' in row
    assert 'hx-trigger="every 2s"' in row
    assert "queued" in row
    assert 'hx-delete="/works/7/stage-run"' in row
    assert ">Ingest<" not in row


def test_a_running_row_polls_and_cannot_be_cancelled_and_a_settled_one_stops() -> None:
    done = _work(done=FIVE)
    api = Api({"GET /works/7/stages": _json(_work(done=["fetch"], run="running"))})
    running = _row(api.web().get("/works/7/row").text)

    assert 'hx-trigger="every 2s"' in running
    assert "hx-delete" not in running
    assert "Re-run" not in running

    api = Api({"GET /works/7/stages": _json(done)})
    settled = _row(api.web().get("/works/7/row").text)

    assert "every 2s" not in settled


def test_a_poll_the_api_cannot_answer_keeps_polling_and_says_so() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    row = _row(Api({"GET /works/7/stages": refuse}).web().get("/works/7/row").text)

    assert 'hx-trigger="every 2s"' in row
    assert "API" in row


def test_a_failure_shows_its_reason_and_offers_resume() -> None:
    work = _work(done=["fetch", "parse", "chunk"], failed=("embed", "the Provider went away"))
    row = _row(Api({"GET /library": _page(work)}).web().get("/").text)

    assert "the Provider went away" in row
    assert ">Resume<" in row
    assert ">Ingest<" not in row


def test_rerunning_a_stage_asks_first_naming_what_will_run() -> None:
    api = Api({"GET /library": _page(_work(done=FIVE))})

    row = _row(api.web().get("/").text)

    parse = re.search(r"<button[^>]*Re-run parse[^>]*>", row)
    assert parse
    assert 'hx-confirm="Re-run parse, chunk, embed and extract_citations?' in row
    assert "Conversation" in parse.group(0)
    embed = re.search(r"<button[^>]*Re-run embed[^>]*>", row)
    assert embed
    assert "Conversation" not in embed.group(0)
    assert '"from_stage": "parse"' in parse.group(0).replace("&#34;", '"')


def test_a_rerun_sends_its_stage_to_the_api() -> None:
    api = Api({"POST /works/7/ingesting": _json(_work(run="queued"), 202)})

    api.web().post("/works/7/ingesting", data={"from_stage": "parse"})

    assert json.loads(api.seen[-1].content) == {"from_stage": "parse"}


def test_a_refusal_shows_in_the_row_with_the_work_as_it_stands() -> None:
    api = Api(
        {
            "POST /works/7/ingesting": httpx.Response(409, json={"detail": "already"}),
            "GET /works/7/stages": _json(_work(run="running")),
        }
    )

    row = _row(api.web().post("/works/7/ingesting", data={}).text)

    assert "already" in row
    assert 'hx-trigger="every 2s"' in row


def test_cancelling_answers_with_the_row_as_it_now_stands() -> None:
    api = Api(
        {
            "DELETE /works/7/stage-run": httpx.Response(204),
            "GET /works/7/stages": _json(_work()),
        }
    )

    row = _row(api.web().delete("/works/7/stage-run").text)

    assert ">Ingest<" in row


def test_an_ingested_work_offers_resolving_its_references_and_shows_how_that_went() -> None:
    row = _row(Api({"GET /library": _page(_work(done=FIVE))}).web().get("/").text)
    assert 'hx-post="/works/7/resolving"' in row
    assert ">Resolve References<" in row

    half = _row(Api({"GET /library": _page(_work(done=["fetch"]))}).web().get("/").text)
    assert "Resolve References" not in half

    done = _row(Api({"GET /library": _page(_work(done=SEVEN))}).web().get("/").text)
    assert "verify_dois" in done
    assert ">Resolve again<" in done


def test_resolving_is_sent_to_the_api() -> None:
    api = Api({"POST /works/7/resolving": _json(_work(done=FIVE, run="queued"), 202)})

    row = _row(api.web().post("/works/7/resolving").text)

    assert 'hx-trigger="every 2s"' in row


def test_a_parsed_work_links_to_its_page() -> None:
    row = _row(Api({"GET /library": _page(_work(done=["fetch", "parse"]))}).web().get("/").text)

    assert 'href="/works/7"' in row


# Collections, nested: Mind holds Perception, which holds Vision; Ethics stands alone.
TREE = CollectionTree(
    collections=[
        CollectionCount(key="ETHICS01", name="Ethics", parent_key=None, works=2, ingested=0),
        CollectionCount(key="MIND0001", name="Mind", parent_key=None, works=5, ingested=2),
        CollectionCount(
            key="PERC0001", name="Perception", parent_key="MIND0001", works=3, ingested=1
        ),
        CollectionCount(key="VISION01", name="Vision", parent_key="PERC0001", works=1, ingested=1),
    ],
    library=BranchCount(works=9, ingested=3),
    unfiled=BranchCount(works=2, ingested=0),
)


def _tree_api(**routes: Handler | httpx.Response) -> Api:
    return Api({"GET /collections": _json(TREE), "GET /library": _page(_work()), **routes})


def _treeitem(html: str, label: str) -> str:
    """The opening tag of the treeitem whose link reads `label`."""
    found = re.search(rf'<li role="treeitem"[^>]*>\s*(?:<[^>]*>\s*)*?<a [^>]*>{label}<', html)
    assert found, html
    tag = re.match(r"<li [^>]*>", found.group(0))
    assert tag
    return tag.group(0)


def test_the_first_visit_opens_the_collections_view_on_the_whole_library() -> None:
    api = _tree_api()

    response = api.web().get("/")

    assert 'role="tree"' in response.text
    whole = _treeitem(response.text, "Whole Library")
    assert 'aria-selected="true"' in whole
    assert 'aria-level="1"' in whole
    assert 'tabindex="0"' in whole
    assert "collection" not in api.seen[-1].url.params
    assert "unfiled" not in api.seen[-1].url.params
    assert response.cookies["pe.library_view"] == "collections"


def test_the_pane_lists_the_whole_library_the_roots_by_name_then_the_unfiled() -> None:
    text = _tree_api().web().get("/").text

    order = [
        m for m in re.findall(r'role="treeitem"[^>]*>\s*(?:<[^>]*>\s*)*?<a [^>]*>([^<]+)<', text)
    ]
    assert order == [
        "Whole Library",
        "Ethics",
        "Mind",
        "Perception",
        "Vision",
        "Not in a collection",
    ]
    mind = _treeitem(text, "Mind")
    assert 'aria-expanded="false"' in mind
    assert 'aria-selected="false"' in mind
    assert 'tabindex="-1"' in mind
    assert 'aria-level="2"' in _treeitem(text, "Perception")
    assert 'aria-level="3"' in _treeitem(text, "Vision")
    assert "aria-expanded" not in _treeitem(text, "Ethics")  # a leaf
    assert 'role="group"' in text


def test_a_selected_collection_renders_its_ancestors_open() -> None:
    api = _tree_api()

    response = api.web().get("/", params={"collection": "VISION01"})

    text = response.text
    assert 'aria-selected="true"' in _treeitem(text, "Vision")
    assert 'aria-expanded="true"' in _treeitem(text, "Mind")
    assert 'aria-expanded="true"' in _treeitem(text, "Perception")
    assert 'aria-selected="false"' in _treeitem(text, "Whole Library")
    assert api.seen[-1].url.params["collection"] == "VISION01"
    assert response.cookies["pe.collection"] == "VISION01"


def test_the_cookie_picks_the_collection_and_the_url_wins_over_it() -> None:
    api = _tree_api()
    web = api.web()
    web.cookies.set("pe.collection", "MIND0001")
    web.cookies.set("pe.deep", "1")

    web.get("/")
    assert api.seen[-1].url.params["collection"] == "MIND0001"
    assert api.seen[-1].url.params["deep"] == "true"

    web.get("/", params={"collection": "ETHICS01"})
    assert api.seen[-1].url.params["collection"] == "ETHICS01"
    assert "deep" not in api.seen[-1].url.params  # the URL's selection comes with its own box

    web.get("/", params={"unfiled": "1"})
    assert api.seen[-1].url.params["unfiled"] == "true"
    assert "collection" not in api.seen[-1].url.params

    web.cookies.set("pe.collection", "MIND0001")
    web.get("/", params={"collection": ""})  # Whole Library, named in the URL
    assert "collection" not in api.seen[-1].url.params


def test_a_cookie_naming_a_gone_collection_falls_back_to_the_whole_library() -> None:
    api = _tree_api()
    web = api.web()
    web.cookies.set("pe.collection", "GONE0001")

    response = web.get("/")

    assert 'aria-selected="true"' in _treeitem(response.text, "Whole Library")
    assert "collection" not in api.seen[-1].url.params
    assert response.cookies["pe.collection"] == "library"


def test_the_list_view_is_remembered_and_lists_the_whole_library() -> None:
    api = _tree_api()
    web = api.web()

    response = web.get("/", params={"view": "list"})

    assert response.cookies["pe.library_view"] == "list"
    assert 'role="tree"' not in response.text
    assert 'id="library-list"' in response.text
    assert _row(response.text)
    assert 'role="tree"' not in web.get("/").text
    assert 'role="tree"' in web.get("/", params={"collection": "MIND0001"}).text


def _works_side(html: str) -> str:
    found = re.search(r'<div id="library-works".*?<ul class="works">', html, re.S)
    assert found, html
    return found.group(0)


def test_the_works_side_shows_where_the_collection_sits_and_what_it_holds() -> None:
    text = _tree_api().web().get("/", params={"collection": "PERC0001"}).text

    side = _works_side(text)
    crumbs = re.search(r'<nav class="crumbs".*?</nav>', side, re.S)
    assert crumbs
    links = re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', crumbs.group(0))
    assert links == [("/?collection=", "Library"), ("/?collection=MIND0001", "Mind")]
    assert '<li aria-current="location">Perception</li>' in crumbs.group(0)
    assert '<meter min="0" max="3" value="1"' in side
    chip = re.search(r'<a class="chip"[^>]*>Vision <span class="chip-count">1</span>', side)
    assert chip
    assert 'hx-push-url="true"' in chip.group(0)
    assert "Show Works in subcollections" in side
    box = re.search(r'<input type="checkbox"[^>]*>', side)
    assert box
    assert "checked" not in box.group(0)
    assert 'hx-get="/?collection=PERC0001&amp;deep=1"' in box.group(0)


def test_the_box_turned_on_lists_the_branch_and_turns_off_again() -> None:
    api = _tree_api()

    response = api.web().get("/", params={"collection": "MIND0001", "deep": "1"})

    assert api.seen[-1].url.params["deep"] == "true"
    box = re.search(r'<input type="checkbox"[^>]*>', response.text)
    assert box
    assert " checked" in box.group(0)
    assert 'hx-get="/?collection=MIND0001"' in box.group(0)
    assert response.cookies["pe.deep"] == "1"


def test_a_collection_with_nothing_filed_directly_points_to_its_subcollections() -> None:
    api = _tree_api(**{"GET /library": _page()})

    text = api.web().get("/", params={"collection": "MIND0001"}).text

    assert "Nothing is filed directly in Mind" in text
    assert "show Works in subcollections" in text


def _searched(branch_total: int, total: int, offset: int = 0, n: int = 2) -> httpx.Response:
    works = [
        _work(work_id=offset + i + 1).model_copy(update={"in_branch": offset + i < branch_total})
        for i in range(n)
    ]
    return _json(LibraryPage(items=works, total=total, branch_total=branch_total))


def test_a_search_lists_the_branch_s_matches_first_then_the_rest_under_a_divider() -> None:
    api = _tree_api(**{"GET /library": _searched(branch_total=1, total=2)})

    text = api.web().get("/", params={"collection": "PERC0001", "q": "mind"}).text

    sent = api.seen[-1].url.params
    assert (sent["q"], sent["collection"], sent["deep"]) == ("mind", "PERC0001", "true")
    first, divider, second = (
        text.index('id="work-1"'),
        text.index("Elsewhere"),
        text.index('id="work-2"'),
    )
    assert first < divider < second
    assert "Elsewhere in the Library · 1" in text
    assert 'class="library-row in-branch"' in _row(text, 1)
    assert "in-branch" not in _row(text, 2)


def test_the_divider_falls_on_the_later_page_where_the_branch_ends() -> None:
    api = _tree_api(**{"GET /library": _searched(branch_total=51, total=53, offset=50)})

    text = (
        api.web().get("/library", params={"q": "mind", "offset": 50, "collection": "MIND0001"}).text
    )

    assert api.seen[-1].url.params["collection"] == "MIND0001"
    assert text.index('id="work-51"') < text.index("Elsewhere in the Library · 2")
    assert text.index("Elsewhere") < text.index('id="work-52"')

    first = _tree_api(**{"GET /library": _searched(branch_total=51, total=53, n=50)})
    page_one = first.web().get("/", params={"collection": "MIND0001", "q": "mind"}).text
    assert "Elsewhere" not in page_one
    assert "/library?q=mind&amp;offset=50&amp;collection=MIND0001" in page_one


def test_the_whole_library_and_the_list_view_draw_no_divider() -> None:
    api = _tree_api(**{"GET /library": _searched(branch_total=2, total=2)})

    assert "Elsewhere" not in api.web().get("/", params={"collection": "", "q": "x"}).text
    assert "Elsewhere" not in api.web().get("/", params={"view": "list", "q": "x"}).text


def test_while_searching_the_pane_marks_each_branch_s_matches_and_dims_the_rest() -> None:
    found = TREE.model_copy(
        update={
            "collections": [
                c.model_copy(update={"works": 1 if c.key in ("MIND0001", "PERC0001") else 0})
                for c in TREE.collections
            ],
            "library": BranchCount(works=1, ingested=0),
            "unfiled": BranchCount(works=0, ingested=0),
        }
    )

    def counts(request: httpx.Request) -> httpx.Response:
        return _json(found if request.url.params.get("q") else TREE)

    text = _tree_api(**{"GET /collections": counts}).web().get("/", params={"q": "mind"}).text

    assert 'class="no-match"' in _treeitem(text, "Ethics")
    assert "no-match" not in _treeitem(text, "Mind")
    assert re.search(
        r'>Mind</a><span class="tree-count"[^>]*>5</span>'
        r'<span class="tree-matches" title="1 match">1<',
        text,
    )
    assert 'class="no-match"' in _treeitem(text, "Not in a collection")


def test_a_row_names_its_other_collections_each_a_link_with_its_path() -> None:
    work = _work().model_copy(update={"collections": ["ETHICS01", "VISION01"]})
    api = _tree_api(**{"GET /library": _page(work)})

    beside = _row(api.web().get("/", params={"collection": "ETHICS01"}).text)
    whole = _row(api.web().get("/", params={"collection": ""}).text)
    listed = _row(api.web().get("/", params={"view": "list"}).text)

    assert re.search(
        r'<p class="filed">also in <a [^>]*title="Mind / Perception / Vision">Vision</a></p>',
        beside,
    )
    assert 'hx-get="/?collection=VISION01"' in beside
    assert re.search(r'<p class="filed">in <a [^>]*>Ethics</a>, <a [^>]*>Vision</a></p>', whole)
    assert re.search(
        r'<p class="filed">in <a href="/\?collection=ETHICS01" title="Ethics">Ethics</a>', listed
    )


def test_a_polled_row_names_its_collections_as_the_view_shows_them() -> None:
    work = _work(run="running").model_copy(update={"collections": ["ETHICS01", "VISION01"]})
    api = _tree_api(**{"GET /works/7/stages": _json(work)})
    web = api.web()
    web.cookies.set("pe.library_view", "collections")
    web.cookies.set("pe.collection", "ETHICS01")

    row = _row(web.get("/works/7/row", params={"tint": "1"}).text)

    assert re.search(r"also in <a [^>]*>Vision</a></p>", row)
    assert 'class="library-row in-branch"' in row
    assert 'hx-trigger="every 2s"' in row


def test_the_list_view_still_lists_when_the_collections_cannot_be_read() -> None:
    api = _tree_api(**{"GET /collections": httpx.Response(500, text="boom")})

    text = api.web().get("/", params={"view": "list"}).text

    assert "could not be read" not in text
    assert _row(text)


def test_the_collections_view_falls_back_to_the_list_when_its_tree_cannot_be_read() -> None:
    api = _tree_api(**{"GET /collections": httpx.Response(500, text="boom")})

    text = api.web().get("/").text

    assert 'role="tree"' not in text
    assert _row(text)


def test_load_more_on_the_whole_library_keeps_the_collections_view() -> None:
    api = _tree_api(**{"GET /library": _page(_work(), total=51)})

    text = api.web().get("/", params={"collection": ""}).text

    assert "/library?q=&amp;offset=1&amp;collection=" in text


def test_a_tinted_row_keeps_its_tint_through_a_cancel() -> None:
    api = _tree_api(
        **{"DELETE /works/7/stage-run": httpx.Response(204), "GET /works/7/stages": _json(_work())}
    )

    row = _row(api.web().delete("/works/7/stage-run", params={"tint": "1"}).text)

    assert 'class="library-row in-branch"' in row
