import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow

from paperexplorer.store import Works
from paperexplorer_api.client import ApiClient, ConflictError, NotFoundError
from paperexplorer_api.schemas import (
    AnsweredEvent,
    DeltaEvent,
    ErrorEvent,
    HandlePiece,
    RedraftEvent,
    StepEvent,
    TextPiece,
)

from .conftest import AppFactory, Call, Script, Stored

QUESTION = "What did the training involve?"


def test_the_client_reads_works_and_citations_into_schemas(
    api: TestClient, villiger: Stored
) -> None:
    client = ApiClient(api)

    [summary] = client.works()
    work = client.work(summary.id)
    span = next(c for b in work.blocks for c in b.citations)
    citation = client.citation(span.id)

    assert summary.zotero_key == "VILL2024"
    assert len(work.blocks) == len(villiger.parsed.blocks)
    assert citation.id == span.id
    assert citation.work_id == work.id


def test_the_client_raises_not_found(api: TestClient) -> None:
    with pytest.raises(NotFoundError):
        ApiClient(api).work(999999)
    with pytest.raises(NotFoundError):
        ApiClient(api).citation(999999)


def test_the_client_starts_a_conversation_and_streams_its_typed_events(
    make_app: AppFactory, villiger: Stored
) -> None:
    script = Script([Call("read_around", {"handle": 1})], ["It was a training. [#1]"])
    client = ApiClient(TestClient(make_app(script.model())))

    conversation = client.start_conversation(villiger.work)
    events = list(client.ask(conversation.id, QUESTION))

    assert [type(e) for e in events] == [StepEvent, StepEvent, DeltaEvent, AnsweredEvent]
    assert events[0] == StepEvent(tool="search", args={"query": QUESTION})
    assert events[1] == StepEvent(tool="read_around", args={"handle": 1})
    assert events[2] == DeltaEvent(
        pieces=[TextPiece(text="It was a training. "), HandlePiece(handle=1)]
    )
    answered = events[-1]
    assert isinstance(answered, AnsweredEvent)
    assert answered.answer.text == "It was a training."
    [sentence] = answered.answer.sentences
    [pointer] = sentence.pointers
    assert pointer.chunk_start <= pointer.start < pointer.end <= pointer.chunk_end


def test_the_client_raises_not_found_for_an_unknown_work_or_conversation(
    api: TestClient,
) -> None:
    client = ApiClient(api)

    with pytest.raises(NotFoundError):
        client.start_conversation(999999)
    with pytest.raises(NotFoundError, match="start a new one"):
        list(client.ask("no-such-id", QUESTION))


def test_the_client_raises_conflict_for_a_work_not_yet_embedded(
    make_app: AppFactory, store_db: psycopg.Connection[TupleRow]
) -> None:
    work = Works(store_db).ensure("NEW23456")
    client = ApiClient(TestClient(make_app(Script().model())))

    with pytest.raises(ConflictError, match="run chunk and embed"):
        client.start_conversation(work)


class Canned:
    """A streamed response with a fixed body."""

    def __init__(self, body: str) -> None:
        self.status_code = 200
        self.headers = {"content-type": "text/event-stream"}
        self._body = body

    def iter_lines(self) -> Iterator[str]:
        yield from self._body.split("\n")

    def read(self) -> bytes:
        return self._body.encode()

    def json(self, **kwargs: Any) -> Any:
        return json.loads(self._body)

    def raise_for_status(self) -> object:
        return self


class CannedHttp:
    """An HTTP client whose every stream answers with one fixed body, noting when it closes."""

    def __init__(self, body: str) -> None:
        self.body = body
        self.closed = False

    def get(self, url: str) -> Canned:
        raise AssertionError("not used")

    def post(self, url: str, *, json: Any = None) -> Canned:
        raise AssertionError("not used")

    @contextmanager
    def stream(self, method: str, url: str, *, json: Any, timeout: float) -> Iterator[Canned]:
        try:
            yield Canned(self.body)
        finally:
            self.closed = True


def test_the_client_reads_server_sent_events_past_keep_alive_comments() -> None:
    http = CannedHttp(
        ": ping\n"
        "\n"
        "event: delta\n"
        'data: {"type": "delta",\n'
        'data:  "pieces": [{"text": "It was "}, {"handle": 3}]}\n'
        "\n"
        ": ping\n"
        "\n"
        "event: redraft\n"
        'data: {"type":"redraft"}\n'
        "\n"
        "event: error\n"
        'data: {"type":"error","message":"The model could not answer"}\n'
        "\n"
    )

    events = list(ApiClient(http).ask("c1", QUESTION))

    assert events == [
        DeltaEvent(pieces=[TextPiece(text="It was "), HandlePiece(handle=3)]),
        RedraftEvent(),
        ErrorEvent(message="The model could not answer"),
    ]


def test_closing_the_events_early_closes_the_stream() -> None:
    http = CannedHttp('event: delta\ndata: {"type":"delta","pieces":[{"text":"It"}]}\n\n' * 3)

    events = ApiClient(http).ask("c1", QUESTION)
    assert next(events) == DeltaEvent(pieces=[TextPiece(text="It")])
    events.close()

    assert http.closed
