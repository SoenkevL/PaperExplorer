"""The answering agent end to end: `FunctionModel` scripts the model, over real Postgres.

Each scripted response is a list of items streamed in order: `Think` (reasoning, which the
OpenAI-compatible model reads from Ollama's `reasoning` field), `Call` (a tool call), or text,
given as a string or as a function of the Conversation (to write a handle shown this turn).
"""

import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, replace

import httpx
import psycopg
import pytest
from opentelemetry.sdk.trace import ReadableSpan
from psycopg.rows import TupleRow
from pydantic import JsonValue
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaThinkingPart, DeltaToolCall, FunctionModel

from paperexplorer.asking import (
    PROMPT_VERSION,
    REQUEST_LIMIT,
    SYSTEM_PROMPT,
    AnsweredEvent,
    AnswerEvent,
    AskingContext,
    Conversation,
    DeltaEvent,
    ErrorEvent,
    StepEvent,
    ask,
)
from paperexplorer.config import Settings
from paperexplorer.domain import Work
from paperexplorer.embedding import Embedder, FakeEmbedder, OpenAICompatibleEmbedder
from paperexplorer.store import Chunks, StoredChunk
from paperexplorer.tracing import Tracing

from .conftest import DISCUSSION, RECORDING, in_memory_tracing

Conn = psycopg.Connection[TupleRow]
QUESTION = "How was the entropy computed?"
_SHOWN = re.compile(r"^\[#(\d+)\] .+ · p\. \d+\n(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Think:
    text: str


@dataclass(frozen=True)
class Call:
    tool: str
    args: dict[str, JsonValue]


type Item = Think | Call | str | Callable[[], str]


class Script:
    """A model that streams one scripted response per request and records each request."""

    def __init__(self, *responses: Sequence[Item], then: Sequence[Item] | None = None) -> None:
        self._responses = list(responses)
        self._then = then
        self.requests: list[tuple[list[ModelMessage], AgentInfo]] = []

    def model(self) -> FunctionModel:
        return FunctionModel(stream_function=self._stream, model_name="paperexplorer-chat")

    async def _stream(
        self, messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall] | dict[int, DeltaThinkingPart]]:
        self.requests.append((list(messages), info))
        response = self._responses.pop(0) if self._responses else self._then
        assert response is not None, "the model was asked more often than scripted"
        for i, item in enumerate(response):
            if isinstance(item, Think):
                yield {i: DeltaThinkingPart(content=item.text)}
            elif isinstance(item, Call):
                call_id = f"call-{len(self.requests)}-{i}"
                yield {i: DeltaToolCall(item.tool, json.dumps(item.args), tool_call_id=call_id)}
            else:
                yield item if isinstance(item, str) else item()


def context(
    conn: Conn, script: Script, *, tracing: Tracing | None = None, embedder: Embedder | None = None
) -> AskingContext:
    return AskingContext(
        conn=conn,
        embedder=embedder or FakeEmbedder(),
        model=script.model(),
        settings=Settings(_env_file=None),  # pyright: ignore[reportCallIssue]
        tracing=tracing or Tracing.off(),
    )


def turn(ctx: AskingContext, conversation: Conversation, question: str) -> list[AnswerEvent]:
    async def collect() -> list[AnswerEvent]:
        return [event async for event in ask(ctx, conversation, question)]

    return asyncio.run(collect())


def chunk(conn: Conn, work: Work, text: str) -> StoredChunk:
    return next(c for c in Chunks(conn).for_work(work.id) if c.chunk.text == text)


def tool_returns(messages: Sequence[ModelMessage], tool: str) -> list[str]:
    return [
        str(part.content)
        for m in messages
        if isinstance(m, ModelRequest)
        for part in m.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == tool
    ]


def handles_shown(output: str) -> dict[str, int]:
    return {text: int(n) for n, text in _SHOWN.findall(output)}


@pytest.fixture
def conversation(small_seed: Work) -> Conversation:
    return Conversation(work=small_seed)


def test_a_turn_streams_its_steps_and_draft_then_the_resolved_answer(
    store_db: Conn, small_seed: Work, conversation: Conversation
) -> None:
    recording = chunk(store_db, small_seed, RECORDING)

    def draft() -> str:
        return f"Entropy was computed from each EEG recording. [#{conversation.handle(recording)}]"

    script = Script(
        [Think("The methods section will say."), Call("read_section", {"heading": "§2"})],
        [
            Think("It does."),
            "Entropy was computed ",
            lambda: draft()[len("Entropy was computed ") :],
        ],
    )

    events = turn(context(store_db, script), conversation, QUESTION)

    assert [type(e) for e in events] == [
        StepEvent,
        StepEvent,
        DeltaEvent,
        DeltaEvent,
        AnsweredEvent,
    ]
    assert events[0] == StepEvent(tool="search", args={"query": QUESTION})
    assert events[1] == StepEvent(tool="read_section", args={"heading": "§2"})
    assert "".join(e.text for e in events if isinstance(e, DeltaEvent)) == draft()
    answered = events[-1]
    assert isinstance(answered, AnsweredEvent)
    assert answered.answer.text == "Entropy was computed from each EEG recording."
    [pointer] = answered.answer.sentences[0].pointers
    assert (pointer.chunk_id, pointer.support.value) == (recording.id, "close")
    assert conversation.questions == 1


def test_the_forced_search_is_in_the_history_as_the_models_own_tool_call(
    store_db: Conn, conversation: Conversation
) -> None:
    script = Script(["The Seed computed entropy from EEG."])

    turn(context(store_db, script), conversation, QUESTION)

    [(messages, info)] = script.requests
    question, call, result = messages
    assert isinstance(question, ModelRequest) and isinstance(question.parts[0], UserPromptPart)
    assert question.parts[0].content == QUESTION
    assert isinstance(call, ModelResponse)
    [search] = call.parts
    assert isinstance(search, ToolCallPart)
    assert (search.tool_name, search.args_as_dict()) == ("search", {"query": QUESTION})
    assert isinstance(result, ModelRequest)
    [returned] = result.parts
    assert isinstance(returned, ToolReturnPart) and returned.tool_call_id == search.tool_call_id
    assert RECORDING in str(returned.content)
    assert info.instructions is not None
    assert info.instructions.startswith(SYSTEM_PROMPT.strip())
    assert "§2 2 Methods · p. 2" in info.instructions
    assert [t.name for t in info.function_tools] == [
        "search",
        "read_section",
        "read_around",
        "reference",
        "citing_sentences",
    ]


def test_a_chunk_keeps_its_handle_across_questions(
    store_db: Conn, conversation: Conversation
) -> None:
    script = Script(["First Answer."], ["Second Answer."])
    ctx = context(store_db, script)

    turn(ctx, conversation, QUESTION)
    turn(ctx, conversation, "When did the EEG sessions take place?")

    first, second = (tool_returns(messages, "search")[-1] for messages, _ in script.requests)
    assert handles_shown(first) == handles_shown(second)
    assert len(handles_shown(second)) == 5
    history, _ = script.requests[1]
    assert any(
        isinstance(m, ModelResponse) and m.parts == [TextPart(content="First Answer.")]
        for m in history
    )
    assert conversation.questions == 2


def test_a_handle_the_conversation_does_not_know_is_dropped(
    store_db: Conn, conversation: Conversation
) -> None:
    script = Script(["The Seed does not say how caffeine affects sleep. [#42]"])

    events = turn(context(store_db, script), conversation, "Does caffeine affect sleep?")

    answered = events[-1]
    assert isinstance(answered, AnsweredEvent)
    assert answered.answer.text == "The Seed does not say how caffeine affects sleep."
    assert answered.answer.unknown_handles == [42]
    assert answered.answer.sentences[0].pointers == []


def test_reasoning_stays_out_of_the_draft_the_answer_and_the_conversation(
    store_db: Conn, conversation: Conversation
) -> None:
    script = Script(
        [Think("SECRET plan: search again."), Call("search", {"query": "EEG sessions"})],
        [Think("SECRET: enough."), "Sessions took place in the afternoon."],
    )

    events = turn(context(store_db, script), conversation, QUESTION)

    assert not any("SECRET" in e.model_dump_json() for e in events)
    assert not any(
        isinstance(part, ThinkingPart)
        for m in conversation.messages
        if isinstance(m, ModelResponse)
        for part in m.parts
    )
    assert "SECRET" not in repr(conversation.messages)


def test_the_last_request_offers_no_tools_so_the_model_answers(
    store_db: Conn, conversation: Conversation
) -> None:
    script = Script(
        *([Call("search", {"query": f"try {n}"})] for n in range(1, REQUEST_LIMIT)),
        ["Entropy was computed from each recording."],
    )

    events = turn(context(store_db, script), conversation, QUESTION)

    assert len(script.requests) == REQUEST_LIMIT
    *earlier, (_, last) = script.requests
    assert all(len(info.function_tools) == 5 for _, info in earlier)
    assert last.function_tools == []
    assert last.instructions is not None and "last request" in last.instructions
    assert all("last request" not in (info.instructions or "") for _, info in earlier)
    assert isinstance(events[-1], AnsweredEvent)
    assert sum(isinstance(e, StepEvent) for e in events) == REQUEST_LIMIT


def test_a_model_that_will_not_answer_ends_the_turn_with_an_error(
    store_db: Conn, conversation: Conversation
) -> None:
    script = Script(then=[Call("search", {"query": "again"})])

    events = turn(context(store_db, script), conversation, QUESTION)

    assert isinstance(events[-1], ErrorEvent)
    assert f"{REQUEST_LIMIT} requests" in events[-1].message
    assert not any(isinstance(e, AnsweredEvent) for e in events)
    assert (conversation.messages, conversation.questions) == ([], 0)


def test_every_tool_the_model_runs_reads_the_seed_and_counts_as_read(
    store_db: Conn, small_seed: Work, conversation: Conversation
) -> None:
    discussion = chunk(store_db, small_seed, DISCUSSION)
    handle = lambda: conversation.handle(discussion)  # noqa: E731
    script = Script(
        [Call("search", {"query": "sample size", "k": 2})],
        [Call("read_around", {"handle": 2})],
        [Call("reference", {"handle": 2, "marker": "[1]"})],
        [Call("citing_sentences", {"reference": "b0"})],
        [Call("read_section", {"heading": "3 Discussion"})],
        [lambda: f'The authors write that "The sample was small". [#{handle()}]'],
    )

    events = turn(context(store_db, script), conversation, "How large was the sample?")

    steps = [e.tool for e in events if isinstance(e, StepEvent)]
    assert steps == [
        "search",
        "search",
        "read_around",
        "reference",
        "citing_sentences",
        "read_section",
    ]
    messages, _ = script.requests[-1]
    assert "points at Reference" in tool_returns(messages, "reference")[0]
    assert "is cited 2 times" in tool_returns(messages, "citing_sentences")[0]
    answered = events[-1]
    assert isinstance(answered, AnsweredEvent)
    [quote] = answered.answer.quotes
    assert quote.exact and quote.block_id == discussion.chunk.block


def test_a_failing_model_ends_the_turn_with_an_error_and_keeps_the_conversation(
    store_db: Conn, conversation: Conversation
) -> None:
    def broken() -> str:
        raise RuntimeError("the server went away")

    events = turn(context(store_db, Script(["Entropy ", broken])), conversation, QUESTION)

    assert isinstance(events[-1], ErrorEvent)
    assert "the server went away" in events[-1].message
    assert (conversation.messages, conversation.questions) == ([], 0)


def _embedder(tracing: Tracing) -> OpenAICompatibleEmbedder:
    """The OpenAI-compatible adapter answering with `FakeEmbedder`'s Vectors, so it traces."""
    fake = FakeEmbedder()

    def answer(request: httpx.Request) -> httpx.Response:
        texts = json.loads(request.content)["input"]
        data = [{"index": i, "embedding": v} for i, v in enumerate(fake.embed(texts))]
        return httpx.Response(200, json={"data": data, "usage": {"prompt_tokens": 3}})

    http = httpx.Client(base_url="http://ollama/v1", transport=httpx.MockTransport(answer))
    return OpenAICompatibleEmbedder(http, fake.model, batch_size=32, tracing=tracing)


def test_one_question_is_one_trace_holding_every_model_tool_and_embedding_call(
    store_db: Conn, small_seed: Work, conversation: Conversation
) -> None:
    tracing, exporter, scores = in_memory_tracing()
    recording = chunk(store_db, small_seed, RECORDING)
    script = Script(
        [Think("The recording section says how."), Call("read_section", {"heading": "§3"})],
        [
            lambda: (
                f"Entropy was computed from each EEG recording. [#{conversation.handle(recording)}]"
            )
        ],
    )
    ctx = context(store_db, script, tracing=tracing, embedder=_embedder(tracing))

    events = turn(ctx, conversation, QUESTION)
    tracing.flush()

    assert isinstance(events[-1], AnsweredEvent)
    spans = exporter.get_finished_spans()
    by_name: dict[str, list[ReadableSpan]] = {}
    for span in spans:
        by_name.setdefault(span.name, []).append(span)
    [root] = by_name["question"]
    assert root.context is not None
    assert {s.context.trace_id for s in spans if s.context} == {root.context.trace_id}
    attributes = root.attributes or {}
    assert attributes["session.id"] == conversation.id
    assert attributes["langfuse.trace.tags"] == ("asking", "work:SEED0001")
    metadata = {
        key.removeprefix("langfuse.trace.metadata."): json.loads(str(value))
        for key, value in attributes.items()
        if key.startswith("langfuse.trace.metadata.")
    }
    assert metadata == {
        "work_id": str(small_seed.id),
        "zotero_key": "SEED0001",
        "position": "1",
        "prompt_version": PROMPT_VERSION,
        "model": "paperexplorer-chat",
        "num_ctx": "73728",
    }

    def parent(span: ReadableSpan) -> str:
        return next(
            s.name
            for s in spans
            if s.context and span.parent and s.context.span_id == span.parent.span_id
        )

    [forced] = by_name["forced search"]
    [resolution] = by_name["resolve answer"]
    assert parent(forced) == parent(resolution) == "question"
    assert sorted(parent(s) for s in by_name["embeddings fake-bag-of-words"]) == [
        "forced search",
        "resolve answer",
    ]
    chats = by_name["chat paperexplorer-chat"]
    assert len(chats) == 2
    assert {parent(c) for c in chats} == {"invoke_agent asking"}
    assert parent(by_name["invoke_agent asking"][0]) == "question"
    # The model's thinking is traced with its request, though never shown.
    thought = json.loads(str((chats[0].attributes or {})["gen_ai.output.messages"]))
    assert {"type": "thinking", "content": "The recording section says how."} in thought[0]["parts"]
    [read_section] = by_name["execute_tool read_section"]
    assert parent(read_section) == parent(by_name["chat paperexplorer-chat"][0])
    named = {str(s["name"]): s["value"] for s in scores}
    assert named["model_requests"] == 2
    assert named["tool_calls"] == 1
    assert named["pointers_close"] == 1
    assert named["pointer_coverage"] == 1.0
    assert float(str(named["latency_s"])) > 0
    assert {str(s["traceId"]) for s in scores} == {format(root.context.trace_id, "032x")}


@pytest.mark.parametrize(
    ("whole_seed", "tags"),
    [
        (False, ("asking", "work:SEED0001", "run:R1")),
        (True, ("asking", "work:SEED0001", "whole-seed", "run:R1")),
    ],
)
def test_the_answer_names_its_trace_which_carries_the_callers_tags(
    store_db: Conn, conversation: Conversation, whole_seed: bool, tags: tuple[str, ...]
) -> None:
    tracing, exporter, _ = in_memory_tracing()
    script = Script(["Entropy was computed from each EEG recording."])
    ctx = replace(
        context(store_db, script, tracing=tracing), tags=("run:R1",), whole_seed=whole_seed
    )

    events = turn(ctx, conversation, QUESTION)
    tracing.flush()

    [root] = [s for s in exporter.get_finished_spans() if s.name == "question"]
    assert root.context is not None
    answered = events[-1]
    assert isinstance(answered, AnsweredEvent)
    assert answered.trace_id == format(root.context.trace_id, "032x")
    assert (root.attributes or {})["langfuse.trace.tags"] == tags


def test_a_failed_turn_names_its_trace_too(store_db: Conn, conversation: Conversation) -> None:
    tracing, exporter, _ = in_memory_tracing()
    script = Script(then=[Call("search", {"query": "again"})])

    events = turn(context(store_db, script, tracing=tracing), conversation, QUESTION)
    tracing.flush()

    [root] = [s for s in exporter.get_finished_spans() if s.name == "question"]
    assert root.context is not None
    failed = events[-1]
    assert isinstance(failed, ErrorEvent)
    assert failed.trace_id == format(root.context.trace_id, "032x")


def test_without_tracing_the_answer_names_no_trace(
    store_db: Conn, conversation: Conversation
) -> None:
    events = turn(context(store_db, Script(["Fine."])), conversation, QUESTION)

    answered = events[-1]
    assert isinstance(answered, AnsweredEvent) and answered.trace_id is None


def test_the_whole_seed_mode_shows_every_chunk_with_its_handle_and_no_tools(
    store_db: Conn, small_seed: Work, conversation: Conversation
) -> None:
    discussion = chunk(store_db, small_seed, DISCUSSION)

    def draft() -> str:
        handle = conversation.handle(discussion)
        return f'The authors write that "The sample was small". [#{handle}]'

    script = Script([draft])
    ctx = replace(context(store_db, script), whole_seed=True)

    events = turn(ctx, conversation, "How large was the sample?")

    assert [type(e) for e in events] == [DeltaEvent, AnsweredEvent]
    [(messages, info)] = script.requests
    [question] = messages
    assert isinstance(question, ModelRequest)
    assert [p.content for p in question.parts if isinstance(p, UserPromptPart)] == [
        "How large was the sample?"
    ]
    assert info.function_tools == []
    assert info.instructions is not None
    assert info.instructions.startswith(SYSTEM_PROMPT.strip())
    assert "no tools" in info.instructions
    stored = Chunks(store_db).for_work(small_seed.id)
    assert handles_shown(info.instructions) == {
        c.chunk.text: n for n, c in enumerate(stored, start=1)
    }
    answered = events[-1]
    assert isinstance(answered, AnsweredEvent)
    [pointer] = answered.answer.sentences[0].pointers
    assert pointer.chunk_id == discussion.id
    [quote] = answered.answer.quotes
    assert quote.exact
    assert conversation.questions == 1


def test_the_live_mode_is_the_default(store_db: Conn) -> None:
    ctx = context(store_db, Script())

    assert (ctx.tags, ctx.whole_seed) == ((), False)
