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
    )


class Api:
    """A mocked API: routes by method and path, and what it was sent."""

    def __init__(self, routes: dict[str, Handler | httpx.Response]) -> None:
        health = httpx.Response(200, json={"status": "ok", "version": "9.9"})
        self.routes: dict[str, Handler | httpx.Response] = {"GET /health": health, **routes}
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


def test_a_new_item_offers_ingest_and_only_the_five_stages() -> None:
    api = Api({"GET /library": _page(_work())})

    row = _row(api.web().get("/").text)

    assert ">Ingest<" in row
    assert 'hx-post="/works/7/ingesting"' in row
    for stage in FIVE:
        assert stage in row
    assert "verify_dois" not in row
    assert "every 2s" not in row


def test_the_api_without_a_library_says_why_and_lists_what_it_has() -> None:
    summary = WorkSummary(
        id=1, zotero_key="OLD", title="Ingested before", creators=[], year=None, doi=None,
        text_origin=None, stages={},
    )  # fmt: skip
    api = Api({"GET /works": httpx.Response(200, json=[summary.model_dump(mode="json")])})

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
