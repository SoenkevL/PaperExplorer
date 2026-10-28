"""The Conversation sidebar over a faked API whose JSON and events come from the API's schemas."""

import html
import json
import re
from collections.abc import Sequence

import httpx
from fastapi.testclient import TestClient

from paperexplorer_api.client import ApiClient
from paperexplorer_api.schemas import (
    Answer,
    AnsweredEvent,
    AnswerEvent,
    AnswerSentence,
    Block,
    CitationSpan,
    DeltaEvent,
    Pointer,
    StepEvent,
    WorkText,
)
from paperexplorer_web.app import create_app

BLOCK = "Entropy rose after psilocybin (Smith, 2020). It fell after placebo."
WORK = WorkText(
    id=7,
    zotero_key="SEED0001",
    title="Entropy after psilocybin",
    creators=["Villiger, Daniel"],
    year=2024,
    doi=None,
    text_origin="printed",
    stages={"chunk": "done", "embed": "done"},
    blocks=[
        Block(position=0, kind="heading", heading_path=[], page=1, boxes=[],
              text="1 Introduction", ocr_confidence=None, citations=[]),
        Block(position=1, kind="paragraph", heading_path=["1 Introduction"], page=1, boxes=[],
              text=BLOCK, ocr_confidence=None,
              citations=[CitationSpan(id=11, start=30, end=43, confidence_level="certain")]),
    ],
)  # fmt: skip


class FakeApi:
    """The API's Work and Conversation routes, answering each Question with the next stream.

    A stream is a list of events, or the status (404, 409) the Question is answered with.
    """

    def __init__(self, *streams: Sequence[AnswerEvent] | int, start: int = 201) -> None:
        self.streams = list(streams)
        self.start_status = start
        self.started: list[int] = []
        self.asked: list[tuple[str, str]] = []

    def client(self) -> TestClient:
        http = httpx.Client(base_url="http://api", transport=httpx.MockTransport(self._respond))
        return TestClient(create_app(ApiClient(http)))

    def _respond(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/works/{WORK.id}":
            return httpx.Response(200, json=WORK.model_dump(mode="json"))
        if path == f"/works/{WORK.id}/conversations" and request.method == "POST":
            if self.start_status != 201:
                detail = f"Work {WORK.id} has no Chunks yet; run chunk and embed"
                return httpx.Response(self.start_status, json={"detail": detail})
            self.started.append(WORK.id)
            return httpx.Response(201, json={"id": f"conv-{len(self.started)}"})
        if m := re.fullmatch(r"/conversations/([^/]+)/questions", path):
            self.asked.append((m[1], json.loads(request.content)["text"]))
            stream = self.streams.pop(0)
            if isinstance(stream, int):
                return httpx.Response(stream, json={"detail": f"Conversation {m[1]}: {stream}"})
            body = "".join(f"event: {e.type}\ndata: {e.model_dump_json()}\n\n" for e in stream)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)
        return httpx.Response(404, json={"detail": f"No {path}"})


def test_the_work_page_has_the_conversation_sidebar_beside_the_reader() -> None:
    page = FakeApi().client().get("/works/7").text

    sidebar = re.search(r'<aside id="conversation"[^>]*>(.*?)</aside>', page, re.S)
    assert sidebar, "no Conversation sidebar"
    assert 'hx-post="/works/7/questions"' in sidebar[1]
    assert re.search(r'<input type="hidden" id="conversation-id" name="conversation_id" value="">',
                     sidebar[1])  # fmt: skip
    legend = html.unescape(re.sub(r"<[^>]+>", "", sidebar[1]))
    for mark in ("close", "weak", "exact", "not exact"):
        assert mark in legend
    # The reader and its Citation panel stay as they were.
    assert 'id="b1"' in page
    assert 'hx-get="/works/7/panel?c=11"' in page
    assert '<aside id="panel"' in page


def test_below_92rem_the_sidebar_is_a_drawer_opened_by_a_button() -> None:
    page = FakeApi().client().get("/works/7").text
    css = FakeApi().client().get("/static/style.css").text

    toggle = re.search(r"<button[^>]*drawer-toggle[^>]*>", page)
    assert toggle, "no drawer toggle"
    assert 'aria-controls="conversation"' in toggle[0]
    assert 'aria-expanded="false"' in toggle[0]
    assert re.search(r"@media \(min-width: 92rem\)", css)
    assert "conversation.js" in page
    assert "sse.min.js" in page


QUESTION = "How was <entropy> measured?"
PLACE = Pointer(block_id=1, start=45, end=67, support="close", chunk_start=0, chunk_end=67)
ANSWERED = AnsweredEvent(
    answer=Answer(
        text="Entropy rose. It fell.",
        sentences=[
            AnswerSentence(start=0, end=13, pointers=[]),
            AnswerSentence(start=14, end=22, pointers=[PLACE]),
        ],
        quotes=[],
    )
)


def _post(client: TestClient, text: str = QUESTION, conversation_id: str = "") -> str:
    data = {"text": text, "conversation_id": conversation_id}
    return client.post("/works/7/questions", data=data).raise_for_status().text


def _stream_url(turn: str) -> str:
    found = re.search(r'sse-connect="([^"]+)"', turn)
    assert found, "the Question does not stream"
    return html.unescape(found[1])


def _events(body: str) -> list[tuple[str, str]]:
    """Each server-sent event's name and data."""
    events: list[tuple[str, str]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        name, data = "", list[str]()
        for line in block.split("\n"):
            field, _, value = line.partition(":")
            if field == "event":
                name = value.removeprefix(" ")
            elif field == "data":
                data.append(value.removeprefix(" "))
        events.append((name, "\n".join(data)))
    return events


def _ask(client: TestClient, text: str = QUESTION, conversation: str = "") -> list[tuple[str, str]]:
    return _events(client.get(_stream_url(_post(client, text, conversation))).text)


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


def test_the_first_question_starts_the_conversation_and_streams_from_the_web_app() -> None:
    api = FakeApi()

    turn = _post(api.client())

    assert api.started == [7]
    assert "How was &lt;entropy&gt; measured?" in turn
    # The browser's EventSource GETs the web app; the Question waits there, not in the URL.
    assert re.fullmatch(r"/questions/[0-9a-f]{32}/stream", _stream_url(turn))
    assert re.search(r'<input type="hidden" id="conversation-id" name="conversation_id"'
                     r' value="conv-1" hx-swap-oob="true">', turn)  # fmt: skip
    assert api.asked == []


def test_a_later_question_is_asked_in_the_same_conversation() -> None:
    api = FakeApi([ANSWERED])

    events = _ask(api.client(), "And then?", "conv-1")

    assert api.started == []
    assert api.asked == [("conv-1", "And then?")]
    assert [name for name, _ in events] == ["answer"]


def test_each_event_is_relayed_as_html_for_its_place_in_the_question() -> None:
    api = FakeApi(
        [
            StepEvent(tool="search", args={"query": "entropy measured"}),
            DeltaEvent(text="Entropy rose. [#"),
            StepEvent(tool="read_around", args={"handle": 3, "n": 1}),
            DeltaEvent(text="3] It fell."),
            ANSWERED,
        ]
    )
    client = api.client()
    turn = _post(client)

    events = _events(client.get(_stream_url(turn)).text)

    assert [name for name, _ in events] == ["step", "delta", "step", "delta", "answer"]
    search, first, read, second, answer = (data for _, data in events)
    assert _text(search) == "search entropy measured"
    assert _text(read) == "read_around 3, 1"
    # The draft grows at the end of the draft; a handle waits until it is whole, then is a chip.
    assert re.search(r'sse-swap="delta" hx-swap="beforeend"', turn)
    assert first == "Entropy rose. "
    assert re.fullmatch(r'<span class="chip"[^>]*>#3</span> It fell\.', second)
    # The Answer replaces the whole reply, steps and draft, and so closes the EventSource.
    assert re.search(r'<div class="reply"[^>]*sse-swap="answer,error" hx-swap="outerHTML"', turn)
    assert answer.startswith('<div class="reply answered">')
    assert "Entropy rose. It fell." in _text(answer)
