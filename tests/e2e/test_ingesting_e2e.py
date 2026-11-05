"""From an empty database to an answered Question, using only the interface: web → API → Stages."""

import html
import re
import time
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import TupleRow
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

from paperexplorer.config import Settings
from paperexplorer.domain import Attachment, LibraryItem
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.library import InMemoryLibrary
from paperexplorer.resolution import InMemoryDoiRegistry
from paperexplorer.store import migrate
from paperexplorer.tracing import Tracing
from paperexplorer.wiring import Wiring
from paperexplorer_api.app import Asking, Ingesting
from paperexplorer_api.app import create_app as create_api
from paperexplorer_api.client import ApiClient
from paperexplorer_api.conversations import Conversations
from paperexplorer_web.app import create_app as create_web

VILLIGER = LibraryItem(
    key="VILL2024",
    title="Personal psychedelic experience of psychedelic therapists during training",
    pdf=Attachment(key="ATTVILL1", filename="villiger.pdf"),
)


async def _answer(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
    yield "The therapists took part in a training. [#1]"


@pytest.fixture
def web(
    scratch_database_url: str, recorded_settings: Settings, fixture_pdf: Callable[[str], Path]
) -> Iterator[TestClient]:
    def connect() -> psycopg.Connection[TupleRow]:
        return psycopg.connect(scratch_database_url, autocommit=True)

    with connect() as conn:
        migrate(conn)
    embedder, tracing = FakeEmbedder(), Tracing.off()
    wiring = Wiring.from_settings(
        recorded_settings,
        tracing=tracing,
        embedder=embedder,
        model=FunctionModel(stream_function=_answer, model_name="paperexplorer-chat"),
    )
    api = create_api(
        connect,
        Asking(wiring.asking, Conversations.from_settings(recorded_settings)),
        ingesting=Ingesting(
            library=InMemoryLibrary([VILLIGER], {"ATTVILL1": fixture_pdf("villiger2024-tandf")}),
            settings=recorded_settings,
            embedder=embedder,
            tracing=tracing,
            doi_registry=InMemoryDoiRegistry(offline=True),
        ),
    )
    with TestClient(api) as client:
        yield TestClient(create_web(ApiClient(client)))


def _settled_row(web: TestClient, work_id: str) -> str:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        row = web.get(f"/works/{work_id}/row").text
        if "every 2s" not in row:
            return row
        time.sleep(0.05)
    pytest.fail("the row never settled")


def test_a_reader_finds_ingests_resolves_reads_and_asks_in_the_interface(web: TestClient) -> None:
    assert "VILL2024" not in web.get("/").text.replace("work-", "")

    web.post("/library/sync")
    listed = web.get("/library").text
    found = re.search(r'<li id="work-(\d+)"', listed)
    assert found
    work_id = found.group(1)
    assert (
        "Personal psychedelic experience" in web.get("/library", params={"q": "psychedelic"}).text
    )

    started = web.post(f"/works/{work_id}/ingesting", data={}).text
    assert "every 2s" in started

    ingested = _settled_row(web, work_id)
    assert ingested.count(": done") == 5
    assert ">Resolve References<" in ingested

    web.post(f"/works/{work_id}/resolving")
    resolved = _settled_row(web, work_id)
    assert ingested.count(": done") + 2 == resolved.count(": done")

    page = web.get(f"/works/{work_id}")
    assert page.status_code == 200
    assert 'class="citation' in page.text

    question = web.post(
        f"/works/{work_id}/questions", data={"text": "Who was trained?", "conversation_id": ""}
    ).text
    stream = re.search(r'sse-connect="([^"]+)"', question)
    assert stream
    answered = web.get(html.unescape(stream.group(1))).text
    assert "event: answer" in answered
    assert "The therapists took part in a training." in answered
