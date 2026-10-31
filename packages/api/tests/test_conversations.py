"""The Conversation routes over real Postgres, a scripted chat model and the fake Embedder."""

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from starlette.types import Message

from paperexplorer.asking import Reading
from paperexplorer.domain import Vector
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.store import StoredChunk, Works
from paperexplorer_api import app as app_module
from paperexplorer_api.conversations import Conversations

from .conftest import AppFactory, Call, Script, Stored

Conn = psycopg.Connection[TupleRow]
QUESTION = "What did the training involve?"


def sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """The events of a `text/event-stream` body: each one's name and its JSON data."""
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        lines = [line for line in block.split("\n") if line and not line.startswith(":")]
        if not lines:
            continue
        fields = dict(line.split(": ", 1) for line in lines)
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def start(client: TestClient, work: int) -> str:
    response = client.post(f"/works/{work}/conversations")
    assert response.status_code == 201, response.text
    return response.json()["id"]


class Gate(FakeEmbedder):
    """The fake Embedder, blocking its thread (as a real one does) until the gate opens."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.open = threading.Event()

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        self.entered.set()
        assert self.open.wait(10), "the gate was never opened"
        return super().embed(texts)


def test_a_conversation_starts_about_an_embedded_work(
    make_app: AppFactory, villiger: Stored
) -> None:
    held = Conversations(limit=10, idle=timedelta(minutes=5))
    client = TestClient(make_app(Script().model(), conversations=held))

    response = client.post(f"/works/{villiger.work}/conversations")

    assert response.status_code == 201
    conversation = held.get(response.json()["id"])
    assert conversation is not None
    assert conversation.work.id == villiger.work
    assert response.json() == {"id": conversation.id}


def test_a_conversation_about_an_unknown_work_is_not_found(make_app: AppFactory) -> None:
    response = TestClient(make_app(Script().model())).post("/works/999999/conversations")

    assert response.status_code == 404


def test_a_work_without_chunks_cannot_start_a_conversation(
    make_app: AppFactory, store_db: Conn
) -> None:
    work = Works(store_db).ensure("NEW23456")

    response = TestClient(make_app(Script().model())).post(f"/works/{work}/conversations")

    assert response.status_code == 409
    assert "run chunk and embed" in response.json()["detail"]


def test_a_work_not_embedded_with_the_apis_embedder_cannot_start_a_conversation(
    make_app: AppFactory, villiger: Stored
) -> None:
    app = make_app(Script().model(), embedder=FakeEmbedder(model="another-model"))

    response = TestClient(app).post(f"/works/{villiger.work}/conversations")

    assert response.status_code == 409
    assert "another-model" in response.json()["detail"]
    assert "run embed" in response.json()["detail"]


def test_a_question_streams_its_steps_draft_and_answer_as_server_sent_events(
    make_app: AppFactory, villiger: Stored
) -> None:
    held = Conversations(limit=10, idle=timedelta(minutes=5))
    drafts: list[tuple[StoredChunk, str]] = []

    def draft() -> str:
        """Cite handle 1, shown this turn, by its first sentence, and quote that sentence."""
        if not drafts:
            conversation = held.get(conversation_id)
            assert conversation is not None
            cited = conversation.handles[1]
            sentence = cited.chunk.sentence_texts()[0]
            words = " ".join(sentence.split()[:4])
            drafts.append((cited, f'{sentence} [#1]\nThe authors write "{words}".'))
        return drafts[0][1]

    script = Script(
        [Call("read_around", {"handle": 1})],
        [lambda: draft()[:5], lambda: draft()[5:]],
    )
    client = TestClient(make_app(script.model(), conversations=held))
    conversation_id = start(client, villiger.work)

    response = client.post(f"/conversations/{conversation_id}/questions", json={"text": QUESTION})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse(response.text)
    assert [name for name, _ in events] == ["step", "step", "delta", "delta", "answer"]
    assert all(data["type"] == name for name, data in events)
    assert events[0][1] == {"type": "step", "tool": "search", "args": {"query": QUESTION}}
    assert events[1][1] == {"type": "step", "tool": "read_around", "args": {"handle": 1}}
    assert "".join(data["text"] for name, data in events if name == "delta") == draft()
    answer = events[-1][1]["answer"]
    [(cited, text)] = drafts
    sentence = cited.chunk.sentences[0]
    assert answer["text"] == text.replace(" [#1]", "")
    first, second = answer["sentences"]
    assert first == {
        "start": 0,
        "end": sentence.end - sentence.start,
        "pointers": [
            {
                "block_id": cited.chunk.block,
                "start": sentence.start,
                "end": sentence.end,
                "support": "close",
                "chunk_start": cited.chunk.span.start,
                "chunk_end": cited.chunk.span.end,
            }
        ],
    }
    assert second["pointers"] == []
    [quote] = answer["quotes"]
    block = villiger.parsed.blocks[quote["block_id"]].text
    assert set(quote) == {"start", "end", "exact", "block_id", "block_start", "block_end"}
    assert quote["exact"] is True
    assert (
        block[quote["block_start"] : quote["block_end"]]
        == answer["text"][quote["start"] : quote["end"]]
    )


def test_a_question_about_a_work_that_fits_streams_its_draft_and_answer_with_no_step(
    make_app: AppFactory, villiger: Stored
) -> None:
    script = Script(["The therapists took part in a training. [#1]"])
    client = TestClient(make_app(script.model(), reading=Reading.AUTO))
    conversation_id = start(client, villiger.work)

    response = client.post(f"/conversations/{conversation_id}/questions", json={"text": QUESTION})

    events = sse(response.text)
    # The whole Work is in context: no search runs, so the draft is the first event.
    assert [name for name, _ in events] == ["delta", "answer"]
    [sentence] = events[-1][1]["answer"]["sentences"]
    assert len(sentence["pointers"]) == 1


def test_the_next_question_continues_the_conversation(
    make_app: AppFactory, villiger: Stored
) -> None:
    held = Conversations(limit=10, idle=timedelta(minutes=5))
    script = Script(["It was a training."], ["It still was."])
    client = TestClient(make_app(script.model(), conversations=held))
    conversation_id = start(client, villiger.work)

    for question in (QUESTION, "And then?"):
        events = sse(
            client.post(f"/conversations/{conversation_id}/questions", json={"text": question}).text
        )
        assert events[-1][0] == "answer"

    conversation = held.get(conversation_id)
    assert conversation is not None
    assert conversation.questions == 2


def test_asking_an_unknown_conversation_is_not_found(make_app: AppFactory) -> None:
    response = TestClient(make_app(Script().model())).post(
        "/conversations/no-such-id/questions", json={"text": QUESTION}
    )

    assert response.status_code == 404


def test_an_evicted_conversation_is_not_found(make_app: AppFactory, villiger: Stored) -> None:
    held = Conversations(limit=1, idle=timedelta(minutes=5))
    client = TestClient(make_app(Script().model(), conversations=held))
    evicted = start(client, villiger.work)
    start(client, villiger.work)

    response = client.post(f"/conversations/{evicted}/questions", json={"text": QUESTION})

    assert response.status_code == 404
    assert "start a new one" in response.json()["detail"]


def test_an_empty_question_is_refused(make_app: AppFactory, villiger: Stored) -> None:
    client = TestClient(make_app(Script().model()))
    conversation_id = start(client, villiger.work)

    response = client.post(f"/conversations/{conversation_id}/questions", json={"text": "  "})

    assert response.status_code == 422


def test_a_failing_turn_ends_the_stream_with_an_error_event(
    make_app: AppFactory, villiger: Stored
) -> None:
    async def broken(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        raise RuntimeError("the chat model is down")
        yield ""  # pragma: no cover

    client = TestClient(make_app(FunctionModel(stream_function=broken, model_name="broken")))
    conversation_id = start(client, villiger.work)

    response = client.post(f"/conversations/{conversation_id}/questions", json={"text": QUESTION})

    assert response.status_code == 200
    events = sse(response.text)
    assert [name for name, _ in events] == ["step", "error"]
    assert "the chat model is down" in events[-1][1]["message"]


def test_a_second_question_while_one_is_answered_is_a_conflict(
    make_app: AppFactory, villiger: Stored
) -> None:
    gate = Gate()
    app = make_app(Script(["It was a training."], ["Again."]).model(), embedder=gate)
    with TestClient(app) as client, ThreadPoolExecutor(1) as pool:
        conversation_id = start(client, villiger.work)
        url = f"/conversations/{conversation_id}/questions"
        first = pool.submit(client.post, url, json={"text": QUESTION})
        assert gate.entered.wait(10)

        second = client.post(url, json={"text": "And then?"})
        gate.open.set()

        assert second.status_code == 409
        assert "still answering" in second.json()["detail"]
        assert sse(first.result(10).text)[-1][0] == "answer"
        # Once the turn has ended, the Conversation takes the next Question.
        again = client.post(url, json={"text": "And then?"})
        assert sse(again.text)[-1][0] == "answer"


def test_a_slow_turn_does_not_block_other_requests(make_app: AppFactory, villiger: Stored) -> None:
    gate = Gate()
    app = make_app(Script(["It was a training."]).model(), embedder=gate)
    with TestClient(app) as client, ThreadPoolExecutor(2) as pool:
        conversation_id = start(client, villiger.work)
        turn = pool.submit(
            client.post, f"/conversations/{conversation_id}/questions", json={"text": QUESTION}
        )
        assert gate.entered.wait(10)  # the turn now blocks its thread in the Embedder

        try:
            began = time.monotonic()
            health = pool.submit(client.get, "/health").result(timeout=5)
            waited = time.monotonic() - began
        finally:
            gate.open.set()

        assert health.status_code == 200
        assert waited < 2
        assert sse(turn.result(10).text)[-1][0] == "answer"


def test_a_client_that_disconnects_cancels_the_turn(make_app: AppFactory, villiger: Stored) -> None:
    held = Conversations(limit=10, idle=timedelta(minutes=5))
    streaming, cancelled = threading.Event(), threading.Event()
    requests: list[int] = []

    async def endless(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        requests.append(len(messages))
        if len(requests) > 1:
            yield "It was a training."
            return
        yield "The training "
        streaming.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        yield "never ends."  # pragma: no cover

    app = make_app(FunctionModel(stream_function=endless, model_name="endless"), conversations=held)
    with TestClient(app) as client:
        conversation_id = start(client, villiger.work)
        received: list[int] = []

        async def receive() -> Message:
            received.append(1)
            if len(received) == 1:
                return {"type": "http.request", "body": json.dumps({"text": QUESTION}).encode()}
            await asyncio.to_thread(streaming.wait, 10)
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            pass

        path = f"/conversations/{conversation_id}/questions"
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver"), (b"content-type", b"application/json")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "state": {},
        }
        asyncio.run(asyncio.wait_for(app(scope, receive, send), 10))

        assert cancelled.wait(10)
        conversation = held.get(conversation_id)
        assert conversation is not None
        assert conversation.questions == 0
        # The cancelled turn has let go of the Conversation.
        deadline = time.monotonic() + 5
        response = client.post(path, json={"text": QUESTION})
        while response.status_code == 409 and time.monotonic() < deadline:
            time.sleep(0.05)
            response = client.post(path, json={"text": QUESTION})
        assert response.status_code == 200
        assert sse(response.text)[-1][0] == "answer"


def test_the_app_from_the_environment_answers_questions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    monkeypatch.delenv("PE_LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.setattr(app_module, "configure_logging", lambda level, fmt: None)

    app = app_module.app_from_env()

    paths = app.openapi()["paths"]
    assert "/works/{work_id}/conversations" in paths
    assert "/conversations/{conversation_id}/questions" in paths
