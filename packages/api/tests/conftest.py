"""An API app over the rolled-back test database, with Works stored through core's repositories.

Its chat model is scripted (`Script`), its Embedder the fake one and its tracing off.
"""

import json
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Protocol

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow
from pydantic import JsonValue
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from paperexplorer.asking import AskingContext, Reading
from paperexplorer.chunking import chunk
from paperexplorer.citations import CitationGraph, extract
from paperexplorer.config import Settings
from paperexplorer.domain import Attachment, LibraryItem, WorkId
from paperexplorer.embedding import Embedder, FakeEmbedder
from paperexplorer.parsing import ParsedWork
from paperexplorer.store import Chunks, CitationGraphs, StageStatuses, Works, WorkTexts
from paperexplorer.tracing import Tracing
from paperexplorer_api.app import Asking, create_app
from paperexplorer_api.conversations import Conversations

Conn = psycopg.Connection[TupleRow]

VILLIGER = LibraryItem(
    key="VILL2024",
    title="Personal psychedelic experience of psychedelic therapists during training",
    creators=["Villiger, Daniel"],
    year=2024,
    doi="10.1080/09540261.2024.2357669",
    pdf=Attachment(key="ATTVILL1", filename="villiger.pdf"),
)


class Stored:
    """A Work as the pipeline leaves it: snapshot, text, embedded Chunks, Citations, Stages."""

    def __init__(self, conn: Conn, item: LibraryItem, parsed: ParsedWork) -> None:
        self.parsed = parsed
        self.graph: CitationGraph = extract(parsed)
        self.work: WorkId = Works(conn).ensure(item.key)
        Works(conn).record_library_item(self.work, item)
        WorkTexts(conn).replace(self.work, parsed)
        Chunks(conn).replace(self.work, chunk(parsed))
        stored = Chunks(conn).for_work(self.work)
        embedder = FakeEmbedder()
        vectors = embedder.embed([s.chunk.text for s in stored])
        Chunks(conn).record_embeddings(
            self.work, embedder.model, {s.id: v for s, v in zip(stored, vectors, strict=True)}
        )
        CitationGraphs(conn).replace(self.work, self.graph)
        for stage in ("fetch", "parse", "chunk", "embed", "extract_citations"):
            StageStatuses(conn).start(self.work, stage)
            StageStatuses(conn).done(self.work, stage, {})


@dataclass(frozen=True)
class Call:
    """A tool call the scripted model makes."""

    tool: str
    args: dict[str, JsonValue]


type Item = Call | str | Callable[[], str]


class Script:
    """A chat model that streams one scripted response per request.

    An item is a tool call, text, or a function giving text at stream time (to write a handle
    the turn has shown).
    """

    def __init__(self, *responses: Sequence[Item]) -> None:
        self._responses = list(responses)

    def model(self) -> FunctionModel:
        return FunctionModel(stream_function=self._stream, model_name="paperexplorer-chat")

    async def _stream(
        self, messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        assert self._responses, "the model was asked more often than scripted"
        for i, item in enumerate(self._responses.pop(0)):
            if isinstance(item, Call):
                yield {i: DeltaToolCall(item.tool, json.dumps(item.args), tool_call_id=f"c{i}")}
            else:
                yield item if isinstance(item, str) else item()


class AppFactory(Protocol):
    def __call__(
        self,
        model: Model,
        *,
        embedder: Embedder | None = None,
        conversations: Conversations | None = None,
        reading: Reading = Reading.RETRIEVAL,
    ) -> FastAPI: ...


@pytest.fixture
def make_app(store_db: Conn) -> AppFactory:
    """An app whose Questions the given chat model answers, over the test database.

    Its Questions search the Work, unless `reading` says otherwise: the fixture Works would fit
    whole, and most tests here are of the turn's steps.
    """

    def build(
        model: Model,
        *,
        embedder: Embedder | None = None,
        conversations: Conversations | None = None,
        reading: Reading = Reading.RETRIEVAL,
    ) -> FastAPI:
        settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
        fake = embedder or FakeEmbedder()
        return create_app(
            lambda: nullcontext(store_db),
            Asking(
                contexts=lambda conn: AskingContext(
                    conn=conn,
                    embedder=fake,
                    model=model,
                    settings=settings,
                    tracing=Tracing.off(),
                    reading=reading,
                ),
                conversations=conversations or Conversations.from_settings(settings),
            ),
        )

    return build


@pytest.fixture
def api(make_app: AppFactory) -> TestClient:
    return TestClient(make_app(Script().model()))


@pytest.fixture
def store_work(store_db: Conn) -> Callable[[LibraryItem, ParsedWork], Stored]:
    return lambda item, parsed: Stored(store_db, item, parsed)


@pytest.fixture
def villiger(
    store_work: Callable[[LibraryItem, ParsedWork], Stored],
    parse_fixture: Callable[[str], ParsedWork],
) -> Stored:
    return store_work(VILLIGER, parse_fixture("villiger2024-tandf"))
