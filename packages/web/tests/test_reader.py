"""The reader over a mocked API whose JSON comes from the API's own schemas."""

import hashlib
import html
import re

import httpx
from fastapi.testclient import TestClient

from paperexplorer_api.client import ApiClient
from paperexplorer_api.schemas import (
    Block,
    CitationDetail,
    CitationLink,
    CitationSpan,
    Reference,
    WorkSummary,
    WorkText,
)
from paperexplorer_web.app import create_app

STAGES = {"fetch": "done", "parse": "done", "extract_citations": "done"}
TEXT = "Therapists <may> need it (Smith, 2020; Jones & Lee, 2021) [1\u20133]."
WORK = WorkText(
    id=7,
    zotero_key="VILL2024",
    title="Personal psychedelic experience of psychedelic therapists",
    creators=["Villiger, Daniel"],
    year=2024,
    doi="10.1080/09540261.2024.2357669",
    text_origin="printed",
    stages=STAGES,  # pyright: ignore[reportArgumentType]
    blocks=[
        Block(position=0, kind="heading", heading_path=[], page=1, boxes=[],
              text="1 Introduction", ocr_confidence=None, citations=[]),
        Block(position=1, kind="paragraph", heading_path=["1 Introduction"], page=1, boxes=[],
              text=TEXT, ocr_confidence=None, citations=[
                  CitationSpan(id=11, start=25, end=38, confidence_level="certain"),
                  CitationSpan(id=12, start=39, end=57, confidence_level="low"),
                  CitationSpan(id=13, start=58, end=60, confidence_level="high"),
                  CitationSpan(id=14, start=60, end=61, confidence_level="high"),
                  CitationSpan(id=15, start=60, end=61, confidence_level="unlinked"),
                  CitationSpan(id=16, start=61, end=63, confidence_level="high"),
              ]),
        Block(position=2, kind="footnote", heading_path=["1 Introduction"], page=2, boxes=[],
              text="A footnote.", ocr_confidence=None, citations=[]),
    ],
)  # fmt: skip
SMITH = CitationDetail(
    id=11,
    work_id=7,
    block=1,
    text="(Smith, 2020;",
    citing_sentence="Therapists <may> need it.",
    text_origin="printed",
    link=CitationLink(confidence=1.0, evidence=["pdf_link", "grobid"], confidence_level="certain"),
    reference=Reference(
        key="b42",
        label=None,
        text="Smith, A. (2020). On the training of therapists. Journal 3, 1\u20139.",
        page=9,
        surnames=["Smith"],
        year="2020",
        title="On the training of therapists",
        doi="10.1000/xyz123",
    ),
)
UNLINKED = CitationDetail(
    id=15, work_id=7, block=1, text="\u2013", citing_sentence="Therapists <may> need it.",
    text_origin="printed", link=None, reference=None,
)  # fmt: skip


def _summary(work: WorkText) -> WorkSummary:
    return WorkSummary.model_validate(work.model_dump(exclude={"blocks"}))


def _reader(work: WorkText = WORK) -> TestClient:
    citations = {c.id: c for c in (SMITH, UNLINKED)}

    def api(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": "9.9"})
        if path == "/works":
            return httpx.Response(200, json=[_summary(work).model_dump(mode="json")])
        if path == f"/works/{work.id}":
            return httpx.Response(200, json=work.model_dump(mode="json"))
        if (m := re.fullmatch(r"/citations/(\d+)", path)) and int(m[1]) in citations:
            return httpx.Response(200, json=citations[int(m[1])].model_dump(mode="json"))
        return httpx.Response(404, json={"detail": f"No {path}"})

    client = ApiClient(httpx.Client(base_url="http://api", transport=httpx.MockTransport(api)))
    return TestClient(create_app(client))


def _block_html(page: str, position: int) -> str:
    found = re.search(rf'<(\w+)[^>]*id="b{position}"[^>]*>(.*?)</\1>', page, re.S)
    assert found, f"no block b{position}"
    return found[2]


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


def test_the_page_shows_the_works_snapshot_and_its_blocks_in_order() -> None:
    page = _reader().get("/works/7").text

    assert "Personal psychedelic experience of psychedelic therapists" in page
    assert "Villiger, Daniel" in page
    assert re.search(r'<h2[^>]*id="b0"', page)
    assert re.search(r'<p class="footnote[^"]*"[^>]*id="b2"', page)
    assert page.index('id="b0"') < page.index('id="b1"') < page.index('id="b2"')


def test_each_block_shows_exactly_the_authors_text() -> None:
    page = _reader().get("/works/7").text

    for block in WORK.blocks:
        assert _text(_block_html(page, block.position)) == block.text
    assert "&lt;may&gt;" in page


def test_every_citation_opens_the_panel_and_shows_its_confidence() -> None:
    page = _reader().get("/works/7").text

    links = re.findall(r'<a class="citation ([a-z]+)"[^>]*hx-get="([^"]+)"[^>]*>(.*?)</a>', page)
    assert [(level, html.unescape(text)) for level, _, text in links] == [
        ("certain", "(Smith, 2020;"),
        ("low", "Jones & Lee, 2021)"),
        ("high", "[1"),
        ("unlinked", "\u2013"),  # the dash stands for 14 and 15; the weaker shows
        ("high", "3]"),
    ]
    targets = [html.unescape(url) for _, url, _ in links]
    assert targets[0] == "/works/7/panel?c=11"
    assert targets[3] == "/works/7/panel?c=14&c=15"


def test_the_panel_shows_the_reference_as_printed_and_the_link() -> None:
    panel = _reader().get("/works/7/panel", params={"c": 11}).text

    assert html.escape(SMITH.reference.text if SMITH.reference else "") in panel
    assert "certain" in panel
    assert "the PDF's own link" in html.unescape(panel)
    assert 'href="https://doi.org/10.1000/xyz123"' in panel
    assert "Therapists &lt;may&gt; need it." in panel


def test_the_panel_says_when_no_evidence_ties_a_citation_to_a_reference() -> None:
    panel = _reader().get("/works/7/panel", params={"c": 15}).text

    assert "Unlinked Citation" in panel


def test_ocr_text_is_marked_as_possibly_misread() -> None:
    scan = WORK.model_copy(
        update={
            "text_origin": "ocr",
            "blocks": [b.model_copy(update={"ocr_confidence": 0.91}) for b in WORK.blocks],
        }
    )

    page = _reader(scan).get("/works/7").text

    assert "OCR" in page
    assert "may misread" in page
    assert re.search(r'<p class="paragraph ocr"[^>]*id="b1"', page)


def test_an_unknown_work_is_a_404_page() -> None:
    response = _reader().get("/works/999")

    assert response.status_code == 404
    assert "Not found" in response.text


def test_the_index_lists_the_works() -> None:
    page = _reader().get("/").text

    assert 'href="/works/7"' in page
    assert "Personal psychedelic experience" in page


def test_the_fonts_are_served_by_the_web_app_itself() -> None:
    reader = _reader()
    css = reader.get("/static/style.css").text

    faces = re.findall(r"@font-face\s*{[^}]*}", css)
    files = re.findall(r'url\("?/static/fonts/([\w.-]+\.woff2)"?\)', " ".join(faces))
    assert set(files) == {
        "inter-latin-wght-normal.woff2",
        "source-serif-4-latin-opsz-normal.woff2",
        "source-serif-4-latin-opsz-italic.woff2",
        "jetbrains-mono-latin-wght-normal.woff2",
    }
    for name in files:
        font = reader.get(f"/static/fonts/{name}")
        assert font.status_code == 200
        assert font.content[:4] == b"wOF2"
    assert "googleapis" not in css + reader.get("/works/7").text


def test_every_page_sets_its_stored_theme_before_the_stylesheet_and_offers_a_toggle() -> None:
    reader = _reader()

    for url in ("/", "/works/7", "/works/999"):
        page = reader.get(url).text
        head = page.split("</head>")[0]
        # The stored choice is applied before the stylesheet paints, so the page never flashes.
        assert head.index("pe.theme") < head.index("style.css"), url
        toggle = re.search(r'<button[^>]*class="theme-toggle"[^>]*>', page)
        assert toggle, url
        assert "aria-label=" in toggle[0]
        assert "panes.js" in head


def test_the_work_page_has_three_panes_and_a_toggle_for_each_side_pane() -> None:
    page = _reader().get("/works/7").text

    order = [m[1] for m in re.finditer(r'<(?:aside|main) id="([a-z-]+)"', page)]
    assert order == ["conversation", "work", "citation"]
    for pane in ("conversation", "citation"):
        toggle = re.search(rf'<button[^>]*class="pane-toggle"[^>]*data-pane="{pane}"[^>]*>', page)
        assert toggle, pane
        assert f'aria-controls="{pane}"' in toggle[0]
        assert 'aria-pressed="true"' in toggle[0]
    assert "Graph" not in page


def test_the_citation_pane_hints_until_a_citation_is_opened_and_is_a_sheet_below_80rem() -> None:
    reader = _reader()
    page = reader.get("/works/7").text
    css = reader.get("/static/style.css").text

    pane = re.search(r'<aside id="citation"[^>]*>(.*?)</aside>', page, re.S)
    assert pane, "no Citation pane"
    assert "Click a Citation" in pane[1]
    assert '<div id="panel" class="panel" aria-live="polite"></div>' in pane[1]
    handle = re.search(r'<button class="sheet-handle"[^>]*>', pane[1])
    assert handle and "aria-label=" in handle[0]
    assert re.search(r'<button class="sheet-toggle"[^>]*aria-controls="citation"', page)
    assert re.search(r"@media \(max-width: 80rem\)", css)
    # The panel's content no longer closes the pane by emptying it; the pane's own button does.
    assert "replaceChildren" not in reader.get("/works/7/panel", params={"c": 11}).text


def test_every_script_and_stylesheet_url_carries_its_contents_version() -> None:
    # A deploy that changes a file changes its URL, so no browser runs a cached old script
    # beside the new page (seen after the three-pane reader: the drawer opened and shut at once).
    client = _reader()
    page = client.get("/works/7").text

    urls = re.findall(r'(?:src|href)="(?:https?://[^/"]+)?(/static/[^"]+)"', page)
    names = {re.sub(r"\?.*", "", u).removeprefix("/static/") for u in urls}
    assert names == {"style.css", "htmx.min.js", "sse.min.js", "panes.js", "conversation.js"}
    for url in urls:
        assert re.search(r"\?v=[0-9a-f]{12}$", url), url
        assert client.get(url).status_code == 200
    script = next(u for u in urls if "/panes.js" in u)
    served = client.get(script).content
    assert script.endswith(hashlib.sha256(served).hexdigest()[:12])
