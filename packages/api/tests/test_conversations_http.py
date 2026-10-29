"""A Question's stream over real HTTP: uvicorn and httpx, which (unlike `TestClient`) do not
buffer the response, so events arrive while the turn still runs."""

import asyncio
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

import httpx
import uvicorn
from fastapi import FastAPI
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

from paperexplorer_api.client import ApiClient
from paperexplorer_api.schemas import AnsweredEvent, DeltaEvent, StepEvent

from .conftest import AppFactory, Stored

QUESTION = "What did the training involve?"


@contextmanager
def serving(app: FastAPI) -> Iterator[str]:
    """The app served by uvicorn on a free local port; its base URL."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(10)


class Waiting:
    """A chat model that streams a first delta, then waits for `go` before it finishes."""

    def __init__(self) -> None:
        self.go = threading.Event()
        self.cancelled = threading.Event()

    def model(self) -> FunctionModel:
        return FunctionModel(stream_function=self._stream, model_name="waiting")

    async def _stream(self, messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "The training "
        try:
            while not self.go.is_set():
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        yield "was long."


def test_events_arrive_while_the_turn_still_runs(make_app: AppFactory, villiger: Stored) -> None:
    model = Waiting()
    with serving(make_app(model.model())) as url, httpx.Client(base_url=url, timeout=10) as http:
        client = ApiClient(http)
        events = client.ask(client.start_conversation(villiger.work).id, QUESTION)

        assert isinstance(next(events), StepEvent)
        assert next(events) == DeltaEvent(text="The training ")
        model.go.set()
        rest = list(events)

    assert rest[0] == DeltaEvent(text="was long.")
    assert isinstance(rest[-1], AnsweredEvent)
    assert rest[-1].answer.text == "The training was long."


def test_closing_the_stream_cancels_the_turn(make_app: AppFactory, villiger: Stored) -> None:
    model = Waiting()
    with serving(make_app(model.model())) as url, httpx.Client(base_url=url, timeout=10) as http:
        client = ApiClient(http)
        events = client.ask(client.start_conversation(villiger.work).id, QUESTION)
        next(events)
        next(events)

        events.close()

        assert model.cancelled.wait(10)
