"""The Conversation sidebar over a faked API whose JSON and events come from the API's schemas."""

import html
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator, Sequence

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
    ErrorEvent,
    Pointer,
    Quote,
    RedraftEvent,
    StepEvent,
    WorkText,
)
from paperexplorer_web.app import create_app, templates
from paperexplorer_web.relay import PendingQuestion, Views, relay

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
    # Each Question goes below the earlier ones and their Answers.
    assert re.search(r'hx-post="/works/7/questions" hx-target="#turns" hx-swap="beforeend"',
                     sidebar[1])  # fmt: skip
    assert re.search(r'<input type="hidden" id="conversation-id" name="conversation_id" value="">',
                     sidebar[1])  # fmt: skip
    legend = html.unescape(re.sub(r"<[^>]+>", "", sidebar[1]))
    for mark in ("close", "weak", "exact", "not exact"):
        assert mark in legend
    # The reader and its Citation panel stay as they were.
    assert 'id="b1"' in page
    assert 'hx-get="/works/7/panel?c=11"' in page
    assert 'id="panel"' in page


def test_below_64rem_the_sidebar_is_a_drawer_opened_by_a_button() -> None:
    page = FakeApi().client().get("/works/7").text
    css = FakeApi().client().get("/static/style.css").text

    toggle = re.search(r"<button[^>]*drawer-toggle[^>]*>", page)
    assert toggle, "no drawer toggle"
    assert 'aria-controls="conversation"' in toggle[0]
    assert 'aria-expanded="false"' in toggle[0]
    assert re.search(r"@media \(max-width: 64rem\)", css)
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


def test_the_reply_says_it_waits_until_the_first_draft_text_arrives() -> None:
    client = FakeApi().client()

    turn = _post(client)
    css = client.get("/static/style.css").text

    # A Seed read whole runs no search, so no step shows while the model reads and thinks.
    reply = re.search(r'<div class="reply"[^>]*>(.*?)</div>\s*</section>', turn, re.S)
    assert reply, "no reply"
    waiting = r'<div class="draft"[^>]*></div>\s*<p class="waiting">[^<]+</p>'
    assert re.search(waiting, reply[1]), reply[1]
    assert ".draft:not(:empty) + .waiting { display: none; }" in css


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


def test_a_redraft_clears_the_draft_and_says_pointers_are_being_added() -> None:
    api = FakeApi(
        [
            DeltaEvent(text="Entropy rose. It fell [#"),
            RedraftEvent(),
            DeltaEvent(text="Entropy rose. [#3] It fell."),
            ANSWERED,
        ]
    )
    client = api.client()
    turn = _post(client)
    question_id = _stream_url(turn).split("/")[2]
    css = client.get("/static/style.css").text

    events = _events(client.get(_stream_url(turn)).text)

    assert [name for name, _ in events] == ["delta", "redraft", "delta", "answer"]
    _, redraft, again, _ = (data for _, data in events)
    # Out of band: the draft emptied in place, so its stream goes on into it, and a line.
    assert f'<div id="draft-{question_id}" hx-swap-oob="innerHTML"></div>' in redraft
    assert re.search(rf'<p class="redraft" id="redraft-{question_id}" hx-swap-oob="true">', redraft)
    assert "Pointers" in _text(redraft)
    assert re.search(rf'<div class="draft" id="draft-{question_id}" sse-swap="delta"', turn)
    assert re.search(rf'<p class="redraft" id="redraft-{question_id}"></p>', turn)
    assert re.search(r'<div hidden sse-swap="conversation,redraft" hx-swap="none">', turn)
    # The first draft's open handle is dropped with it, not prefixed to the new draft.
    assert re.fullmatch(r'Entropy rose\. <span class="chip"[^>]*>#3</span> It fell\.', again)
    assert ".turn .redraft:empty { display: none; }" in css
    assert ".redraft:not(:empty) ~ .waiting { display: none; }" in css


def _words(element: ET.Element) -> str:
    """The element's text without its Pointer chips."""
    words = [element.text or ""]
    for child in element:
        if not child.get("class", "").startswith("pointer"):
            words.append(_words(child))
        words.append(child.tail or "")
    return "".join(words)


def _answer(data: str) -> ET.Element:
    body = ET.fromstring(data).find("div[@class='answer']")
    assert body is not None, "no Answer"
    return body


def test_the_answer_marks_pointers_by_support_and_quotes_by_exactness() -> None:
    # The emoji and the italic H are one code point each but two UTF-16 units; e + U+0301 is two
    # code points. Every offset counts code points.
    first = '\U0001f9e0 Entropy rose "after psilocybin" (Amigé).'
    second = 'It \U0001d43b fell "sharply".'
    text = f"{first}\n\n{second}"
    after = text.index("after")
    sharply = text.index("sharply")
    exact = Quote(start=after, end=after + 16, exact=True, block_id=1,
                  block_start=BLOCK.index("after psilocybin"), block_end=29)  # fmt: skip
    loose = Quote(start=sharply, end=sharply + 7, exact=False, block_id=None, block_start=None,
                  block_end=None)  # fmt: skip
    close = Pointer(block_id=1, start=0, end=44, support="close", chunk_start=0, chunk_end=67)
    weak = Pointer(block_id=1, start=45, end=67, support="weak", chunk_start=0, chunk_end=67)
    answer = Answer(
        text=text,
        sentences=[
            AnswerSentence(start=0, end=len(first), pointers=[close]),
            AnswerSentence(start=len(first) + 2, end=len(text), pointers=[weak]),
        ],
        quotes=[exact, loose],
    )

    [(name, data)] = _ask(FakeApi([AnsweredEvent(answer=answer)]).client())

    assert name == "answer"
    body = _answer(data)
    assert _words(body) == text
    sentences = [s for s in body.iter("span") if s.get("class") == "sentence"]
    assert [_words(s) for s in sentences] == [first, second]
    chips = [
        [(a.get("class"), a.get("href"), a.text) for a in s.iter("a") if a.get("data-chunk-start")]
        for s in sentences
    ]
    assert chips == [[("pointer close", "#b1", "1")], [("pointer weak", "#b1", "2")]]
    chip = next(a for a in body.iter("a") if a.get("class") == "pointer close")
    assert {k: chip.get(k) for k in ("data-block", "data-start", "data-end")} == {
        "data-block": "1", "data-start": "0", "data-end": "44"}  # fmt: skip
    assert (chip.get("data-chunk-start"), chip.get("data-chunk-end")) == ("0", "67")
    [quoted] = [a for a in body.iter("a") if a.get("class") == "quote exact"]
    assert quoted.text == "after psilocybin"
    assert BLOCK[13:29] == quoted.text
    assert (quoted.get("href"), quoted.get("data-start"), quoted.get("data-end")) == (
        "#b1", "13", "29")  # fmt: skip
    [not_exact] = [s for s in body.iter("span") if s.get("class") == "quote not-exact"]
    assert not_exact.text == "sharply"
    assert not_exact.get("data-block") is None


HOSTILE = '<script>alert("x")</script>'


def test_model_text_is_escaped_wherever_it_shows() -> None:
    text = '<b>Bold</b> "<i>it</i>" & <img src=x onerror=alert(1)>.'
    answer = Answer(
        text=text,
        sentences=[AnswerSentence(start=0, end=len(text), pointers=[PLACE])],
        quotes=[Quote(start=13, end=21, exact=False, block_id=None, block_start=None,
                      block_end=None)],
    )  # fmt: skip
    api = FakeApi(
        [
            StepEvent(tool="<search>", args={"query": HOSTILE}),
            DeltaEvent(text="<img src=x onerror=alert(1)> [#1]"),
            AnsweredEvent(answer=answer),
        ],
        [ErrorEvent(message=HOSTILE)],
    )
    client = api.client()
    turn = _post(client, HOSTILE)

    first = _events(client.get(_stream_url(turn)).text)
    second = _ask(client, "Again?", "conv-1")

    shown = [turn, *(data for _, data in first + second)]
    for fragment in shown:
        assert not re.search(r"<(script|img|b|i|search)\b", fragment), fragment
    assert "&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;" in turn
    assert _text(first[0][1]) == f"<search> {HOSTILE}"
    assert _text(first[1][1]) == "<img src=x onerror=alert(1)> #1"
    assert _words(_answer(first[2][1])) == text
    assert _text(second[0][1]) == HOSTILE


def test_an_error_ends_the_question_with_its_message() -> None:
    api = FakeApi(
        [
            StepEvent(tool="search", args={"query": "entropy"}),
            DeltaEvent(text="Entropy"),
            ErrorEvent(message="The model did not answer within 6 requests."),
        ]
    )

    events = _ask(api.client())

    assert [name for name, _ in events] == ["step", "delta", "error"]
    error = events[-1][1]
    assert error.startswith('<div class="reply failed-reply">')
    assert "The model did not answer within 6 requests." in _text(error)


def test_a_forgotten_conversation_is_replaced_and_the_sidebar_says_so() -> None:
    api = FakeApi(404, [ANSWERED])
    client = api.client()
    turn = _post(client, conversation_id="conv-old")
    question_id = _stream_url(turn).split("/")[2]

    events = _events(client.get(_stream_url(turn)).text)

    assert [name for name, _ in events] == ["conversation", "answer"]
    assert api.started == [7]
    assert api.asked == [("conv-old", QUESTION), ("conv-1", QUESTION)]
    notice = events[0][1]
    assert f'id="notice-{question_id}" hx-swap-oob="true"' in notice
    assert f'id="notice-{question_id}"' in turn
    assert "starts a new one" in _text(notice)
    assert re.search(r'<input type="hidden" id="conversation-id" name="conversation_id"'
                     r' value="conv-1" hx-swap-oob="true">', notice)  # fmt: skip


def test_a_work_not_embedded_is_not_asked_and_the_sidebar_says_so() -> None:
    api = FakeApi(start=409)

    turn = _post(api.client())

    assert "sse-connect" not in turn
    assert "Not asked" in turn
    assert "run chunk and embed" in turn
    assert api.asked == []


def test_a_forgotten_conversation_of_a_work_no_longer_embedded_asks_nothing_more() -> None:
    api = FakeApi(404, start=409)

    events = _ask(api.client(), conversation="conv-old")

    assert [name for name, _ in events] == ["error"]
    assert "Not asked" in _text(events[0][1])
    assert api.asked == [("conv-old", QUESTION)]


def test_a_question_while_the_conversation_still_answers_says_so() -> None:
    events = _ask(FakeApi(409).client(), conversation="conv-1")

    assert [name for name, _ in events] == ["error"]
    assert "still answering" in _text(events[0][1])


def test_a_questions_stream_is_given_once() -> None:
    api = FakeApi([ANSWERED], [ANSWERED])
    client = api.client()
    url = _stream_url(_post(client))

    client.get(url)
    again = _events(client.get(url).text)

    assert [name for name, _ in again] == ["error"]
    assert "ask it again" in _text(again[0][1])
    assert len(api.asked) == 1


def test_a_stream_that_ends_without_an_answer_says_so() -> None:
    events = _ask(FakeApi([DeltaEvent(text="Entropy")]).client())

    assert [name for name, _ in events] == ["delta", "error"]
    assert "without an Answer" in _text(events[-1][1])


def test_a_blank_question_asks_nothing() -> None:
    api = FakeApi()

    response = api.client().post("/works/7/questions", data={"text": "  ", "conversation_id": ""})

    assert response.status_code == 204
    assert api.started == []


def test_closing_the_relay_closes_the_apis_stream_and_so_cancels_the_turn() -> None:
    closed: list[bool] = []

    class Body(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            for event in (StepEvent(tool="search", args={}), DeltaEvent(text="Entropy")):
                yield f"event: {event.type}\ndata: {event.model_dump_json()}\n\n".encode()

        def close(self) -> None:
            closed.append(True)

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Body())

    api = ApiClient(httpx.Client(base_url="http://api", transport=httpx.MockTransport(respond)))
    question = PendingQuestion(work_id=7, conversation_id="conv-1", text=QUESTION)
    events = relay(api, "q1", question, Views(templates().env))

    assert next(events).startswith("event: step\n")
    events.close()

    assert closed == [True]
