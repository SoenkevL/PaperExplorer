"""Jumps over a mocked API: the offer in the Citation pane, the frames, and where one lands."""

import re
from typing import Any

import httpx
from fastapi.testclient import TestClient

from paperexplorer_api.client import ApiClient
from paperexplorer_api.schemas import (
    Block,
    CitationDetail,
    CitationLink,
    CitationSpan,
    Evidence,
    Landing,
    LibraryMatch,
    Reference,
    Resolution,
    WorkBrief,
    WorkText,
)
from paperexplorer_web.app import create_app

SOURCE = WorkBrief(
    id=21,
    zotero_key="SMIT2020",
    title="On the training of therapists",
    creators=["Smith, Ann", "Lee, Bo"],
    year=2020,
    doi="10.1000/xyz",
    has_text=True,
    has_pdf=True,
)
UNREAD = SOURCE.model_copy(update={"has_text": False})
NO_PDF = SOURCE.model_copy(update={"has_text": False, "has_pdf": False})
SENTENCE = "Therapists <may> need it."


def _work(matches: list[LibraryMatch] | None = None) -> WorkText:
    return WorkText(
        id=7, zotero_key="VILL2024", title="The citing Work", creators=["Villiger, Daniel"],
        year=2024, doi=None, text_origin="printed", stages={},
        blocks=[
            Block(position=0, kind="paragraph", heading_path=[], page=1, boxes=[], text="See [1].",
                  ocr_confidence=None,
                  citations=[CitationSpan(id=11, start=4, end=7, confidence_level="certain",
                                          library_match=True)]),
        ],
        library_matches=matches or [],
    )  # fmt: skip


def _citation(work: WorkBrief | None, level: str = "certain") -> CitationDetail:
    resolution = Resolution(
        reference="b1",
        state="resolved",
        work=work,
        level=level,  # pyright: ignore[reportArgumentType]
        confidence=1.0,
        evidence=[],
        layer="library",
        doi_conflict=None,
        chosen=False,
        firm=level != "low",
    )
    return CitationDetail(
        id=11, work_id=7, block=0, text="[1]", citing_sentence=SENTENCE, text_origin="printed",
        link=CitationLink(confidence=1.0, evidence=["label"], confidence_level="certain"),
        reference=Reference(key="b1", label="1", text="Smith 2020", page=9, surnames=["Smith"],
                            year="2020", title="T", doi=None, resolution=resolution),
    )  # fmt: skip


def _landing(evidence: list[Evidence], reason: Any = None, work: WorkBrief = SOURCE) -> Landing:
    return Landing(
        citation=11, citing_sentence=SENTENCE, work=work, evidence=evidence, reason=reason
    )


EVIDENCE = [
    Evidence(rank=1, block=4, start=10, end=90, page=3, heading_path=["2 Methods"]),
    Evidence(rank=2, block=9, start=0, end=40, page=5, heading_path=["3 Results"]),
]


class FakeApi:
    """Answers what a test sets; everything else is a 404, as with no route."""

    def __init__(
        self,
        *,
        work: WorkText | None = None,
        citation: CitationDetail | None = None,
        landing: httpx.Response | None = None,
        stages: list[dict[str, Any]] | None = None,
        ingest: httpx.Response | None = None,
    ) -> None:
        self.work = work or _work()
        self.citation = citation
        self.landing = landing
        self.stages = stages or []
        self.ingest = ingest
        self.ingested: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/works/7":
            return httpx.Response(200, json=self.work.model_dump(mode="json"))
        if path == "/citations/11" and self.citation:
            return httpx.Response(200, json=self.citation.model_dump(mode="json"))
        if path == "/citations/11/retrieval-runs" and self.landing is not None:
            return self.landing
        if path == "/works/21/stages" and self.stages:
            return httpx.Response(200, json=self.stages.pop(0))
        if path == "/works/21/ingesting" and self.ingest is not None:
            self.ingested.append(path)
            return self.ingest
        return httpx.Response(404, json={"detail": f"No {path}"})

    def client(self) -> TestClient:
        http = httpx.Client(base_url="http://api", transport=httpx.MockTransport(self))
        return TestClient(create_app(ApiClient(http)))


def _stages(*, text: bool, pdf: bool = True, run: str | None = None) -> dict[str, Any]:
    return {
        "id": 21, "zotero_key": "SMIT2020", "title": SOURCE.title, "creators": SOURCE.creators,
        "year": 2020, "doi": SOURCE.doi, "text_origin": "printed" if text else None, "stages": {},
        "has_pdf": pdf, "run": run, "stage_statuses": {}, "collections": [], "in_branch": True,
    }  # fmt: skip


def _panel(citation: CitationDetail) -> str:
    return FakeApi(citation=citation).client().get("/works/7/panel", params={"c": 11}).text


def _jump_link(page: str) -> str:
    found = re.search(r'<a class="jump"[^>]*>', page)
    assert found, "no Jump"
    return found[0]


def test_a_firm_source_with_text_offers_the_jump_from_this_citation() -> None:
    link = _jump_link(_panel(_citation(SOURCE)))

    assert 'href="/works/21?citation=11"' in link
    assert 'data-work="21"' in link
    assert 'data-citation="11"' in link
    assert 'data-label="Smith 2020"' in link


def test_a_low_resolution_offers_no_jump() -> None:
    assert 'class="jump"' not in _panel(_citation(SOURCE, "low"))


def test_a_source_with_a_pdf_and_no_text_says_so_and_offers_ingest() -> None:
    panel = _panel(_citation(UNREAD))

    assert 'class="jump"' not in panel
    assert "not been read yet" in panel
    assert 'hx-post="/works/21/source"' in panel


def test_a_source_with_no_pdf_says_there_is_nothing_to_read() -> None:
    panel = _panel(_citation(NO_PDF))

    assert 'class="jump"' not in panel
    assert "no PDF" in panel
    assert "/source" not in panel
    assert "https://doi.org/10.1000/xyz" in panel


def _ingest(api: FakeApi) -> str:
    response = api.client().post("/works/21/source", data={"c": 11, "key": "b1"})
    assert response.status_code == 200
    return response.text


def test_ingesting_from_the_pane_starts_it_and_polls() -> None:
    api = FakeApi(
        ingest=httpx.Response(202, json=_stages(text=False, run="queued")),
    )

    fragment = _ingest(api)

    assert api.ingested == ["/works/21/ingesting"]
    assert 'hx-get="/works/21/source?c=11&amp;key=b1"' in fragment
    assert 'hx-trigger="every 2s"' in fragment


def test_a_refused_ingest_says_why_and_offers_it_again() -> None:
    api = FakeApi(
        ingest=httpx.Response(409, json={"detail": "A Stage Run is under way"}),
        stages=[_stages(text=False)],
    )

    fragment = _ingest(api)

    assert "A Stage Run is under way" in fragment
    assert 'hx-post="/works/21/source"' in fragment


def test_the_poll_shows_the_jump_once_the_source_is_parsed() -> None:
    client = FakeApi(stages=[_stages(text=True, run="running")]).client()

    fragment = client.get("/works/21/source", params={"c": 11, "key": "b1"}).text

    assert 'href="/works/21?citation=11"' in _jump_link(fragment)
    assert "every 2s" not in fragment


def test_the_poll_goes_on_while_the_source_is_being_read() -> None:
    client = FakeApi(stages=[_stages(text=False, run="running")]).client()

    fragment = client.get("/works/21/source", params={"c": 11, "key": "b1"}).text

    assert 'hx-trigger="every 2s"' in fragment
    assert "Ingesting" in fragment


def test_a_library_match_with_text_offers_the_jump_from_its_first_citation() -> None:
    matches = [
        LibraryMatch(work=SOURCE, citations=[11, 13]),
        LibraryMatch(work=UNREAD, citations=[14]),
    ]
    page = FakeApi(work=_work(matches)).client().get("/works/7/reader").text

    links = re.findall(r'<a class="jump"[^>]*>', page)
    assert len(links) == 1
    assert 'data-citation="11"' in links[0]
    assert 'href="/works/21?citation=11"' in links[0]


def test_the_work_address_is_a_shell_holding_the_work_in_a_frame() -> None:
    page = FakeApi().client().get("/works/7").text

    assert 'src="/works/7/reader"' in page
    assert re.search(r'<iframe[^>]*data-work="7"', page)
    assert 'data-label="Villiger 2024"' in page
    assert "trail.js" in page
    assert "<title>The citing Work" in page


def test_an_address_with_a_citation_opens_the_frame_on_it() -> None:
    page = FakeApi().client().get("/works/7", params={"citation": 11}).text

    assert 'src="/works/7/reader?citation=11"' in page


def test_an_unknown_work_is_not_found_at_either_address() -> None:
    client = FakeApi().client()

    assert client.get("/works/999").status_code == 404
    assert client.get("/works/999/reader").status_code == 404


def test_a_frames_links_open_at_the_top() -> None:
    page = FakeApi().client().get("/works/7/reader").text

    assert '<base target="_top">' in page.split("</head>")[0]


def test_the_reader_with_a_citation_asks_where_it_lands_once_shown() -> None:
    page = FakeApi().client().get("/works/7/reader", params={"citation": 11}).text

    arrival = re.search(r'<section id="arrival"[^>]*>', page)
    assert arrival
    assert 'hx-post="/works/7/arrival"' in arrival[0]
    assert 'hx-trigger="load"' in arrival[0]
    assert '"citation": 11' in arrival[0].replace("&#34;", '"')
    assert "Searching" in page


def test_the_reader_with_no_citation_shows_no_arrival() -> None:
    page = FakeApi().client().get("/works/7/reader").text

    assert re.search(r'<section id="arrival"[^>]*></section>', page)


def _arrival(landing: httpx.Response, work: int = 21) -> str:
    client = FakeApi(landing=landing).client()
    return client.post(f"/works/{work}/arrival", data={"citation": 11}).text


def _json(landing: Landing) -> httpx.Response:
    return httpx.Response(200, json=landing.model_dump(mode="json"))


def test_arrival_steps_through_the_evidence_and_says_it_is_a_candidate() -> None:
    strip = _arrival(_json(_landing(EVIDENCE)))

    assert "Therapists &lt;may&gt; need it." in strip
    assert "candidate" in strip
    assert 'data-block="4" data-start="10" data-end="90"' in strip
    assert 'data-block="9" data-start="0" data-end="40"' in strip
    assert "Top of Source" in strip


def test_arrival_with_no_evidence_stays_at_the_top_and_says_why() -> None:
    reasons = {
        "no_chunks": "not been cut into Chunks",
        "no_vectors": "not been embedded",
        "embedder_unreachable": "could not be reached",
        "no_match": "found nothing",
    }
    for reason, says in reasons.items():
        strip = _arrival(_json(_landing([], reason)))

        assert says in strip, reason
        assert "data-block" not in strip, reason
        assert "Therapists &lt;may&gt; need it." in strip, reason


def test_arrival_refused_by_the_api_stays_at_the_top_and_says_why() -> None:
    strip = _arrival(httpx.Response(409, json={"detail": "Citation 11 has no firm Source"}))

    assert "Citation 11 has no firm Source" in strip
    assert "data-block" not in strip


def test_arrival_for_a_source_the_citation_no_longer_names_shows_no_places() -> None:
    strip = _arrival(_json(_landing(EVIDENCE)), work=22)

    assert "data-block" not in strip
    assert "no longer" in strip
