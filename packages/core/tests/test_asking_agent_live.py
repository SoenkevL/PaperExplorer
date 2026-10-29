"""The answering agent with the real chat model and bge-m3, on the tower.

It asks the CC BY Villiger fixture, since the Seed's text may not be committed (its licence is
unknown); `scripts/tower-check.sh` asks the Seed itself and logs only counts.
"""

import asyncio
import json
import uuid
from collections.abc import Callable

import httpx
import psycopg
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from psycopg.rows import TupleRow

from paperexplorer import chunking, citations
from paperexplorer.asking import (
    AnsweredEvent,
    AnswerEvent,
    AskingContext,
    Conversation,
    DeltaEvent,
    StepEvent,
    ask,
    chat_model,
)
from paperexplorer.config import Settings
from paperexplorer.embedding import OpenAICompatibleEmbedder
from paperexplorer.parsing import ParsedWork
from paperexplorer.store import Chunks, CitationGraphs, Works, WorkTexts
from paperexplorer.tracing import configure_tracing

QUESTION = "How many ongoing clinical studies of psilocybin were there in February 2024?"


@pytest.mark.tower
def test_the_chat_model_answers_from_what_it_read_and_its_thinking_stays_in_the_trace(
    store_db: psycopg.Connection[TupleRow], parse_fixture: Callable[[str], ParsedWork]
) -> None:
    settings = Settings()
    exporter = InMemorySpanExporter()
    tracing = configure_tracing(
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            langfuse_public_key=f"pk-lf-test-{uuid.uuid4()}",
            langfuse_secret_key="sk-lf-test",
            langfuse_base_url="http://langfuse.test",
        ),
        http=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={}))),
        span_exporter=exporter,
    )
    embedder = OpenAICompatibleEmbedder.from_settings(settings, tracing)
    parsed = parse_fixture("villiger2024-tandf")
    works, chunks = Works(store_db), Chunks(store_db)
    work = works.ensure("VILLIGER")
    WorkTexts(store_db).replace(work, parsed)
    chunks.replace(work, chunking.chunk(parsed))
    stored = chunks.for_work(work)
    vectors = embedder.embed([s.chunk.text for s in stored])
    chunks.record_embeddings(
        work, embedder.model, dict(zip((s.id for s in stored), vectors, strict=True))
    )
    CitationGraphs(store_db).replace(work, citations.extract(parsed))
    found = works.get(work)
    assert found is not None
    conversation = Conversation(work=found)
    ctx = AskingContext(
        conn=store_db,
        embedder=embedder,
        model=chat_model(settings),
        settings=settings,
        tracing=tracing,
    )

    async def collect() -> list[AnswerEvent]:
        return [event async for event in ask(ctx, conversation, QUESTION)]

    events = asyncio.run(collect())
    tracing.flush()

    answered = events[-1]
    assert isinstance(answered, AnsweredEvent), answered
    assert events[0] == StepEvent(tool="search", args={"query": QUESTION})
    answer = answered.answer
    assert "84" in answer.text, answer.text
    counts = answer.counts()
    assert counts.pointers_close + counts.pointers_weak >= 1, answer
    assert counts.unknown_handles == 0, answer
    draft = "".join(e.text for e in events if isinstance(e, DeltaEvent))
    thinking = [
        part["content"]
        for span in exporter.get_finished_spans()
        if span.name == f"chat {settings.chat_model}"
        for message in json.loads(str((span.attributes or {}).get("gen_ai.output.messages", "[]")))
        for part in message.get("parts", [])
        if part.get("type") == "thinking" and part.get("content")
    ]
    assert thinking, "Ollama returned no reasoning for the chat model"
    for thought in thinking:
        assert thought not in draft and thought not in answer.text
