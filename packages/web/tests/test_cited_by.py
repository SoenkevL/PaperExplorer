"""Cited by over a mocked API: the section on a Work's page and the address a Jump to it uses."""

import httpx
from fastapi.testclient import TestClient

from paperexplorer_api.client import ApiClient
from paperexplorer_api.schemas import (
    Block,
    CitingSentence,
    CitingWork,
    WorkBrief,
    WorkText,
)
from paperexplorer_web.app import create_app

_CITER = WorkBrief(
    id=30, zotero_key="KIMM2021", title="Kim's study", creators=["Kim, Jo"], year=2021, doi=None,
    has_text=True, has_pdf=True,
)  # fmt: skip
_WORK = WorkText(
    id=7, zotero_key="VILL2024", title="The cited Work", creators=[], year=2024, doi=None,
    text_origin="printed", stages={},
    blocks=[Block(position=0, kind="paragraph", heading_path=[], page=1, boxes=[], text="Text.",
                  ocr_confidence=None, citations=[])],
    library_matches=[],
)  # fmt: skip


def _sentence(id_: int, text: str, level: str = "certain") -> CitingSentence:
    return CitingSentence(id=id_, citing_sentence=text, confidence_level=level)  # pyright: ignore[reportArgumentType]


def _client(*citing: CitingWork, failing: bool = False) -> TestClient:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/works/7":
            return httpx.Response(200, json=_WORK.model_dump(mode="json"))
        if request.url.path == "/works/7/cited-by":
            if failing:
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(200, json=[c.model_dump(mode="json") for c in citing])
        return httpx.Response(404, json={"detail": "No such route"})

    http = httpx.Client(base_url="http://api", transport=httpx.MockTransport(answer))
    return TestClient(create_app(ApiClient(http)))


def _section(*citing: CitingWork, failing: bool = False) -> str:
    response = _client(*citing, failing=failing).get("/works/7/cited-by")
    assert response.status_code == 200
    return response.text


def test_the_work_page_reads_its_cited_by_when_loaded_and_when_shown_again() -> None:
    page = _client().get("/works/7/reader").text

    assert 'id="cited-by"' in page
    assert 'hx-get="/works/7/cited-by"' in page
    assert 'hx-trigger="load, trail:shown from:body"' in page


def test_nothing_is_shown_when_no_held_work_cites_this_one() -> None:
    assert _section().strip() == ""


def test_a_citing_work_folds_its_sentences_each_a_jump_to_its_citation() -> None:
    html = _section(
        CitingWork(work=_CITER, ocr=False, citations=[_sentence(5, "One."), _sentence(6, "Two.")])
    )

    assert "Kim&#39;s study" in html
    assert "(2021)" in html
    assert "2 Citations" in html
    assert 'href="/works/30?at=5"' in html
    assert 'href="/works/30?at=6"' in html
    assert 'data-work="30"' in html
    assert 'data-at="5"' in html
    assert 'data-label="Kim 2021"' in html
    assert "One." in html


def test_a_citing_work_with_no_linked_citation_says_so_and_jumps_to_its_top() -> None:
    html = _section(CitingWork(work=_CITER, ocr=False, citations=[]))

    assert "not linked" in html
    assert 'href="/works/30"' in html
    assert "data-at" not in html


def test_a_low_citation_link_is_marked() -> None:
    html = _section(CitingWork(work=_CITER, ocr=False, citations=[_sentence(5, "One.", "low")]))

    assert '<span class="level low">low</span>' in html


def test_only_a_low_link_is_marked() -> None:
    assert 'class="level' not in _section(
        CitingWork(work=_CITER, ocr=False, citations=[_sentence(5, "One.")])
    )


def test_sentences_of_ocr_text_say_they_may_misread_the_authors() -> None:
    html = _section(CitingWork(work=_CITER, ocr=True, citations=[_sentence(5, "One.")]))

    assert "OCR Text" in html


def test_a_sentence_with_two_citations_is_one_row_landing_on_the_first() -> None:
    html = _section(
        CitingWork(work=_CITER, ocr=False, citations=[_sentence(5, "Same."), _sentence(6, "Same.")])
    )

    assert "2 Citations" in html
    assert 'href="/works/30?at=5"' in html
    assert "at=6" not in html


def test_a_failed_read_says_so_in_place() -> None:
    assert "could not be read" in _section(failing=True)


def test_the_address_of_a_jump_to_a_citation_opens_the_frame_on_it() -> None:
    frame = _client().get("/works/7", params={"at": 5}).text

    assert 'src="/works/7/reader?at=5"' in frame
    assert 'data-at="5"' in frame


def test_the_reader_at_a_citation_lands_there_with_no_search() -> None:
    page = _client().get("/works/7/reader", params={"at": 5}).text

    assert 'data-at="5"' in page
    assert "hx-post" not in page.split('id="arrival"')[1].split(">")[0]
