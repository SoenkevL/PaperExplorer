"""The Conversation sidebar over a faked API whose JSON and events come from the API's schemas."""

import html
import json
import re
from collections.abc import Sequence

import httpx
from fastapi.testclient import TestClient

from paperexplorer_api.client import ApiClient
from paperexplorer_api.schemas import (
    AnswerEvent,
    Block,
    CitationSpan,
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
