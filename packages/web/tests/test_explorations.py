"""Explorations in the web app: started from a row or the Work page, and where they stand."""

import json
import re

import httpx

from paperexplorer_api.schemas import (
    BranchCount,
    CheckCost,
    CollectionCount,
    CollectionTree,
    Exploration,
    ExplorationProposal,
    FailedFiling,
    WorkBrief,
)

from .test_library import SEVEN, Api, _json, _page, _row, _work

NAME = "Villiger 2024 \N{EN DASH} Personal experience"
COST = CheckCost(
    unchecked=169, crossref_batches=4, openalex_batches=2, title_searches=30,
    bibliographic_searches=10, openalex_usd=0.03, budget_usd=0.9,
)  # fmt: skip


def _exploration(**changes: object) -> Exploration:
    explored = Exploration(
        seed=7, collection_key="EXPL2345", name=NAME, created=True, filed=17, failed=[],
        removed_by_reader=0, not_filed=0, awaiting_choice=2, unchecked=169, stages_not_done=[],
        run=None,
    )  # fmt: skip
    return explored.model_copy(update=changes)


def test_a_work_with_a_pdf_offers_to_start_its_exploration() -> None:
    api = Api({"GET /library": _page(_work())})

    row = _row(api.web().get("/").text)

    assert ">Start Exploration<" in row
    assert 'hx-get="/works/7/exploration/start"' in row


def test_a_work_without_a_pdf_offers_no_exploration() -> None:
    api = Api({"GET /library": _page(_work(has_pdf=False))})

    assert "Exploration" not in _row(api.web().get("/").text)


def test_the_start_form_names_the_collection_and_offers_the_check_ticked_with_its_cost() -> None:
    api = Api({
        "GET /works/7/exploration/proposal": _json(ExplorationProposal(name=NAME)),
        "GET /works/7/checks/cost": _json(COST),
    })  # fmt: skip

    form = api.web().get("/works/7/exploration/start").text

    assert NAME in form
    assert 'hx-post="/works/7/exploration"' in form
    box = re.search(r'<input type="checkbox" name="check"[^>]*>', form)
    assert box and "checked" in box.group(0)
    assert "check 169 Unchecked References" in form
    assert "$0.03" in form


def test_an_unparsed_works_start_form_says_the_checks_cost_comes_once_parsed() -> None:
    api = Api({"GET /works/7/exploration/proposal": _json(ExplorationProposal(name=NAME))})

    form = api.web().get("/works/7/exploration/start").text

    assert "once its References are parsed" in form
    box = re.search(r'<input type="checkbox" name="check"[^>]*>', form)
    assert box and "checked" in box.group(0)


def test_starting_sends_the_check_as_ticked_and_shows_the_row_with_its_exploration() -> None:
    sent: list[object] = []

    def start(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return _json(_exploration(run="queued"), 202)

    running = _work(run="queued").model_copy(update={"exploration": _exploration(run="queued")})
    api = Api({"POST /works/7/exploration": start, "GET /works/7/stages": _json(running)})
    web = api.web()

    ticked = _row(web.post("/works/7/exploration", data={"check": "on"}).text)
    web.post("/works/7/exploration", data={})

    assert sent == [{"check": True}, {"check": False}]
    assert NAME in ticked
    assert "Start Exploration" not in ticked


def test_a_refused_start_says_why_on_the_row() -> None:
    refused = httpx.Response(409, json={"detail": "Work 7 has no Library Item with a PDF"})
    api = Api({"POST /works/7/exploration": refused, "GET /works/7/stages": _json(_work())})

    row = _row(api.web().post("/works/7/exploration", data={"check": "on"}).text)

    assert "Not started: Work 7 has no Library Item with a PDF" in row


def test_a_partial_exploration_names_what_is_missing_beside_resume() -> None:
    cited = WorkBrief(
        id=9,
        zotero_key=None,
        title="A Source",
        creators=[],
        year=None,
        doi=None,
        has_text=False,
        has_pdf=False,
    )
    partial = _exploration(
        stages_not_done=["embed"], failed=[FailedFiling(work=cited, error="413 too long")]
    )
    seed = _work(done=[s for s in SEVEN if s != "embed"]).model_copy(
        update={"exploration": partial}
    )
    api = Api({"GET /library": _page(seed)})

    row = _row(api.web().get("/").text)

    assert "embed not done" in row
    assert "1 Filing failed" in row
    assert re.search(r'hx-post="/works/7/exploration"[^>]*>Resume Exploration<', row)
    assert ">Resume<" not in row  # Resume Exploration runs those Stages too


def test_a_finished_exploration_shows_its_counts_and_offers_no_resume() -> None:
    seed = _work(done=SEVEN).model_copy(update={"exploration": _exploration()})
    api = Api({"GET /library": _page(seed)})

    row = _row(api.web().get("/").text)

    assert "17 filed" in row
    assert "2 awaiting your choice" in row
    assert "169 Unchecked" in row
    assert "Resume Exploration" not in row
    assert "Start Exploration" not in row


def test_the_work_page_shows_its_exploration_and_polls_while_it_runs() -> None:
    api = Api({"GET /works/7/exploration": _json(_exploration(run="running"))})

    section = api.web().get("/works/7/exploration").text

    assert NAME in section
    assert 'hx-get="/works/7/exploration" hx-trigger="every 2s"' in section


def test_the_work_page_of_a_work_with_no_exploration_offers_the_start_dialog() -> None:
    api = Api({})

    section = api.web().get("/works/7/exploration").text

    assert 'hx-get="/works/7/exploration/start?place=page"' in section
    assert ">Start Exploration<" in section


def test_starting_from_the_work_page_answers_with_its_section() -> None:
    api = Api({
        "POST /works/7/exploration": _json(_exploration(run="queued"), 202),
        "GET /works/7/exploration": _json(_exploration(run="queued")),
    })  # fmt: skip

    section = api.web().post("/works/7/exploration", data={"place": "page"}).text

    assert NAME in section
    assert '<li id="work-7"' not in section


def test_the_tree_marks_each_exploration_collection() -> None:
    tree = CollectionTree(
        collections=[
            CollectionCount(key="ROOT2345", name="PaperExplorer", parent_key=None, works=17,
                            ingested=1),
            CollectionCount(key="EXPL2345", name=NAME, parent_key="ROOT2345", works=17,
                            ingested=1, exploration=True),
        ],
        library=BranchCount(works=20, ingested=1),
        unfiled=BranchCount(works=2, ingested=0),
    )  # fmt: skip
    api = Api({"GET /collections": _json(tree), "GET /library": _page(_work())})

    text = api.web().get("/").text

    explored = re.search(r'<li role="treeitem"[^>]*data-key="EXPL2345"[^>]*>.*?</li>', text, re.S)
    root = re.search(r'<li role="treeitem"[^>]*data-key="ROOT2345"[^>]*>.*?<a ', text, re.S)
    assert explored and 'class="tree-mark"' in explored.group(0)
    assert root and "tree-mark" not in root.group(0)


def test_a_cost_the_api_could_not_give_leaves_the_check_unticked_and_says_so() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    api = Api({
        "GET /works/7/exploration/proposal": _json(ExplorationProposal(name=NAME)),
        "GET /works/7/checks/cost": down,
    })  # fmt: skip

    form = api.web().get("/works/7/exploration/start").text

    assert "once its References are parsed" not in form
    assert "cost could not be read" in form
    box = re.search(r'<input type="checkbox" name="check"[^>]*>', form)
    assert box and "checked" not in box.group(0)
