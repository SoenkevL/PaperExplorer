"""The answering agent end to end: `FunctionModel` scripts the model, over real Postgres.

Each scripted response is a list of items streamed in order: `Think` (reasoning, which the
OpenAI-compatible model reads from Ollama's `reasoning` field), `Call` (a tool call), or text,
given as a string or as a function of the Conversation (to write a handle shown this turn).
"""

import asyncio
import json
import re
import time
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
    SystemPromptPart,
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
    Reading,
    StepEvent,
    ask,
    chat_model,
)
from paperexplorer.asking.agent import LAST_REQUEST, NO_TOOLS, WHOLE_SEED
from paperexplorer.config import Settings
from paperexplorer.domain import Work
from paperexplorer.embedding import Embedder, FakeEmbedder, OpenAICompatibleEmbedder
from paperexplorer.store import Chunks, StoredChunk
from paperexplorer.tracing import Tracing

from .conftest import AUTHOR_YEAR_DISCUSSION, DISCUSSION, RECORDING, in_memory_tracing

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
# A response is its items, or a function of the request's messages that gives them.
type Response = Sequence[Item] | Callable[[list[ModelMessage]], Sequence[Item]]


class Script:
    """A model that streams one scripted response per request and records each request."""

    def __init__(self, *responses: Response, then: Response | None = None) -> None:
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
        if callable(response):
            response = response(list(messages))
        for i, item in enumerate(response):
            if isinstance(item, Think):
                yield {i: DeltaThinkingPart(content=item.text)}
            elif isinstance(item, Call):
                call_id = f"call-{len(self.requests)}-{i}"
                yield {i: DeltaToolCall(item.tool, json.dumps(item.args), tool_call_id=call_id)}
            else:
                yield item if isinstance(item, str) else item()


def context(
    conn: Conn,
    script: Script,
    *,
    tracing: Tracing | None = None,
    embedder: Embedder | None = None,
    reading: Reading = Reading.RETRIEVAL,
    settings: Settings | None = None,
) -> AskingContext:
    """The turn's context. The small Seed would fit whole; most tests here are of its tools."""
    return AskingContext(
        conn=conn,
        embedder=embedder or FakeEmbedder(),
        model=script.model(),
        settings=settings or Settings(_env_file=None),  # pyright: ignore[reportCallIssue]
        tracing=tracing or Tracing.off(),
        reading=reading,
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
    *earlier, (last_messages, last) = script.requests
    assert all(len(info.function_tools) == 5 for _, info in earlier)
    assert last.function_tools == []
    # The note that says so comes after everything the model read, not among the instructions.
    assert LAST_REQUEST in (trailing_note(last_messages) or "")
    assert all(trailing_note(messages) is None for messages, _ in earlier)
    assert all(LAST_REQUEST not in (info.instructions or "") for _, info in script.requests)
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


def user_prompts(messages: Sequence[ModelMessage]) -> list[str]:
    """Every user prompt in the messages, in order."""
    return [
        str(part.content)
        for m in messages
        if isinstance(m, ModelRequest)
        for part in m.parts
        if isinstance(part, UserPromptPart)
    ]


def trailing_note(messages: Sequence[ModelMessage]) -> str | None:
    """The words a request ends with after everything the model read, if they are a note."""
    last = messages[-1]
    if not isinstance(last, ModelRequest):
        return None
    part = last.parts[-1]
    if isinstance(part, SystemPromptPart | UserPromptPart) and isinstance(part.content, str):
        return part.content
    return None


def test_regression_eval_last_request_note_comes_after_the_reading(
    store_db: Conn, conversation: Conversation
) -> None:
    """Eval run 20261029T191529Z-94e980b, trace 0ba24102b1727f29db6f883add6e79a3.

    The model read for five requests. The sixth offered no tools and said so among the
    instructions, before everything it had read; the model called `search` anyway and the turn
    failed, as in all 8 retrieval turns that reached the limit. Replayed on the tower three times
    each, those 8 requests were answered 1 time in 24 as sent, 23 in 24 with the note last.
    """

    def recorded(messages: list[ModelMessage]) -> list[Item]:
        if "last request" in (trailing_note(messages) or ""):
            return ["Entropy was computed from each recording."]
        return [Call("search", {"query": "neuron compuron theoretical hypothesis method"})]

    script = Script(
        [Call("read_around", {"handle": 2, "n": 3})],
        [Call("read_section", {"heading": "§1"})],
        [Call("read_section", {"heading": "§2"})],
        [Call("read_around", {"handle": [1], "n": 3})],
        [Call("read_around", {"handle": 1, "n": 3})],
        recorded,
    )

    events = turn(context(store_db, script), conversation, QUESTION)

    assert len(script.requests) == REQUEST_LIMIT
    assert isinstance(events[-1], AnsweredEvent)
    (last_messages, last_info) = script.requests[-1]
    assert "last request" not in (last_info.instructions or "")
    assert all(trailing_note(messages) is None for messages, _ in script.requests[:-1])
    assert "last request" in (trailing_note(last_messages) or "")
    # The note is for that request only: the Conversation keeps the Question, not the note.
    assert user_prompts(conversation.messages) == [QUESTION]


def test_regression_eval_whole_seed_model_calls_a_tool_it_was_never_offered(
    store_db: Conn, conversation: Conversation
) -> None:
    """Eval run 20261029T191529Z-94e980b, trace d87f1f36136df123414ad8f4a41a7a2b (baseline).

    Offered no tools, the model called one named `Read` four times, and three more after the
    retry prompt, so the turn failed; two more baseline turns failed the same way. The note that
    there are no tools stood among the instructions, before the whole Seed; like the live
    agent's last request, every whole-Seed request now ends with it.
    """

    def recorded(messages: list[ModelMessage]) -> list[Item]:
        if "no tools" in (trailing_note(messages) or ""):
            return ["Entropy was computed from each EEG recording."]
        return [Call("Read", {"handle": f"[#{n}]"}) for n in (27, 48, 52, 53)]

    script = Script(then=recorded)
    ctx = context(store_db, script, reading=Reading.WHOLE_SEED)

    events = turn(ctx, conversation, QUESTION)

    assert isinstance(events[-1], AnsweredEvent)
    [(messages, info)] = script.requests
    assert info.function_tools == []
    assert "no tools" in (trailing_note(messages) or "")
    assert user_prompts(conversation.messages) == [QUESTION]


async def _stall(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """An OpenAI-compatible chat endpoint that streams the start of the model's thinking, then
    nothing more, as Ollama did."""
    head = await reader.readuntil(b"\r\n\r\n")
    length = re.search(rb"(?i)content-length: *(\d+)", head)
    await reader.readexactly(int(length.group(1)) if length else 0)
    chunk = {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "paperexplorer-chat",
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": "", "reasoning": "The Seed defines"},
                "finish_reason": None,
            }
        ],
    }
    event = f"data: {json.dumps(chunk)}\n\n".encode()
    writer.write(
        b"HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ntransfer-encoding: chunked\r\n\r\n"
        + f"{len(event):x}\r\n".encode()
        + event
        + b"\r\n"
    )
    await writer.drain()
    await asyncio.sleep(60)


def test_regression_eval_a_stalled_stream_ends_the_turn_at_the_chat_timeout(
    store_db: Conn, conversation: Conversation
) -> None:
    """Eval run 20261029T191529Z-94e980b, trace 356df10f4a9dce19597d1ddeddd037aa (baseline).

    The fourth request streamed the start of the model's thinking, then nothing; the turn failed
    620 s after it began, at the OpenAI client's default timeout of 600 s.
    """

    async def ask_stalled() -> list[AnswerEvent]:
        server = await asyncio.start_server(_stall, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        settings = Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            chat_base_url=f"http://127.0.0.1:{port}/v1",
            chat_timeout_s=0.5,
        )
        ctx = AskingContext(
            conn=store_db,
            embedder=FakeEmbedder(),
            model=chat_model(settings),
            settings=settings,
            tracing=Tracing.off(),
            reading=Reading.WHOLE_SEED,
        )

        async def collect() -> list[AnswerEvent]:
            return [event async for event in ask(ctx, conversation, QUESTION)]

        try:
            return await asyncio.wait_for(collect(), timeout=20)
        finally:
            server.abort_clients()
            server.close()
            await server.wait_closed()

    started = time.monotonic()
    events = asyncio.run(ask_stalled())

    assert isinstance(events[-1], ErrorEvent)
    assert "timed out" in events[-1].message
    assert time.monotonic() - started < 20
    assert conversation.questions == 0


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
        "reading": "retrieval",
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
    ("reading", "tags"),
    [
        (Reading.RETRIEVAL, ("asking", "work:SEED0001", "run:R1")),
        (Reading.WHOLE_SEED, ("asking", "work:SEED0001", "whole-seed", "run:R1")),
    ],
)
def test_the_answer_names_its_trace_which_carries_the_callers_tags(
    store_db: Conn, conversation: Conversation, reading: Reading, tags: tuple[str, ...]
) -> None:
    tracing, exporter, _ = in_memory_tracing()
    script = Script(["Entropy was computed from each EEG recording."])
    ctx = replace(context(store_db, script, tracing=tracing, reading=reading), tags=("run:R1",))

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
    ctx = context(store_db, script, reading=Reading.WHOLE_SEED)

    events = turn(ctx, conversation, "How large was the sample?")

    assert [type(e) for e in events] == [DeltaEvent, AnsweredEvent]
    [(messages, info)] = script.requests
    [question] = messages
    assert isinstance(question, ModelRequest)
    # The Question, then the note that there are no tools, last; the Conversation keeps neither
    # the note nor the Seed's text, only the Question and the Answer.
    assert user_prompts(messages)[0] == "How large was the sample?"
    assert NO_TOOLS in (trailing_note(messages) or "")
    assert user_prompts(conversation.messages) == ["How large was the sample?"]
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


def test_a_question_reads_the_whole_seed_when_it_fits_by_default(store_db: Conn) -> None:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    ctx = AskingContext(store_db, FakeEmbedder(), Script().model(), settings, Tracing.off())

    assert (ctx.tags, ctx.reading) == ((), Reading.AUTO)
    assert settings.whole_seed_max_tokens == 40000


def _metadata(span: ReadableSpan) -> dict[str, str]:
    return {
        key.removeprefix("langfuse.trace.metadata."): json.loads(str(value))
        for key, value in (span.attributes or {}).items()
        if key.startswith("langfuse.trace.metadata.")
    }


def test_auto_reads_a_seed_that_fits_whole_with_no_tools_and_no_search(
    store_db: Conn, conversation: Conversation
) -> None:
    tracing, exporter, _ = in_memory_tracing()
    script = Script(["Entropy was computed from each EEG recording."])
    ctx = context(store_db, script, tracing=tracing, reading=Reading.AUTO)

    events = turn(ctx, conversation, QUESTION)
    tracing.flush()

    # No forced search: the draft is the first thing the turn streams.
    assert [type(e) for e in events] == [DeltaEvent, AnsweredEvent]
    answered = events[-1]
    assert isinstance(answered, AnsweredEvent)
    assert answered.reading is Reading.WHOLE_SEED
    [(messages, info)] = script.requests
    assert info.function_tools == []
    assert info.instructions is not None
    assert info.instructions.startswith(f"{SYSTEM_PROMPT.strip()}\n\n{WHOLE_SEED}\n\n")
    assert NO_TOOLS in (trailing_note(messages) or "")
    [root] = [s for s in exporter.get_finished_spans() if s.name == "question"]
    tags = ("asking", "work:SEED0001", "whole-seed")
    assert (root.attributes or {})["langfuse.trace.tags"] == tags
    assert _metadata(root)["reading"] == "whole_seed"


@pytest.mark.parametrize(("spare", "reading"), [(0, Reading.WHOLE_SEED), (-1, Reading.RETRIEVAL)])
def test_auto_searches_a_seed_whose_chunks_hold_more_tokens_than_the_budget(
    store_db: Conn,
    small_seed: Work,
    conversation: Conversation,
    spare: int,
    reading: Reading,
) -> None:
    tokens = sum(c.chunk.tokens for c in Chunks(store_db).for_work(small_seed.id))
    settings = Settings(_env_file=None, whole_seed_max_tokens=tokens + spare)  # pyright: ignore[reportCallIssue]
    script = Script(["Entropy was computed from each EEG recording."])
    ctx = context(store_db, script, reading=Reading.AUTO, settings=settings)

    events = turn(ctx, conversation, QUESTION)

    answered = events[-1]
    assert isinstance(answered, AnsweredEvent)
    assert answered.reading is reading
    [(_, info)] = script.requests
    searched = reading is Reading.RETRIEVAL
    assert (events[0] == StepEvent(tool="search", args={"query": QUESTION})) is searched
    assert len(info.function_tools) == (5 if searched else 0)


@pytest.mark.parametrize("reading", [Reading.RETRIEVAL, Reading.WHOLE_SEED])
def test_a_reading_set_on_the_context_is_used_whatever_the_seeds_size(
    store_db: Conn, conversation: Conversation, reading: Reading
) -> None:
    settings = Settings(_env_file=None, whole_seed_max_tokens=1)  # pyright: ignore[reportCallIssue]
    script = Script(["Entropy was computed from each EEG recording."])

    events = turn(
        context(store_db, script, reading=reading, settings=settings), conversation, QUESTION
    )

    answered = events[-1]
    assert isinstance(answered, AnsweredEvent) and answered.reading is reading


def deltas(events: Sequence[AnswerEvent]) -> list[str]:
    return [e.text for e in events if isinstance(e, DeltaEvent)]


def last_response_text(conversation: Conversation) -> list[str]:
    """The text parts of the Conversation's last response: what later Questions see of it."""
    response = next(m for m in reversed(conversation.messages) if isinstance(m, ModelResponse))
    return [p.content for p in response.parts if isinstance(p, TextPart)]


def test_regression_eval_5e96992b6d5b49ce00d6d8a715afddf4_answer_ends_at_a_leaked_channel_token(
    store_db: Conn, conversation: Conversation
) -> None:
    """Eval run 20261029T202214Z-2b41752, trace 5e96992b6d5b49ce00d6d8a715afddf4 (baseline).

    After its Answer the model wrote gemma's `<channel|>`, then talked about the reader, then
    wrote more Answer-like lines, each run ending in `<channel|>` again. The reader saw it all.
    The Answer ends at the first control token, and so does the draft as it streams.
    """
    script = Script(
        [
            "The replication found the same rise in entropy. [#1]\n",
            "<channel|>The reader may want the numbers too.\n\n",
            "It did not report them. <channel|>The replication found it again.\n\n",
            "<channel|>",
        ]
    )

    events = turn(context(store_db, script, reading=Reading.WHOLE_SEED), conversation, QUESTION)

    answered = events[-1]
    assert isinstance(answered, AnsweredEvent)
    assert answered.answer.text == "The replication found the same rise in entropy."
    assert "".join(deltas(events)) == "The replication found the same rise in entropy. [#1]\n"
    # Later Questions see the Answer as cut, not what followed it.
    assert last_response_text(conversation) == [
        "The replication found the same rise in entropy. [#1]"
    ]


def test_regression_eval_c5b7289409b2182cea0d01e18bdb38f6_a_token_split_across_deltas_ends_it(
    store_db: Conn, conversation: Conversation
) -> None:
    """Eval run 20261029T202214Z-2b41752, trace c5b7289409b2182cea0d01e18bdb38f6 (baseline).

    The Answer was followed by seven lines, each opening with `<channel|>`. A token may arrive
    in pieces; none of it reaches the draft.
    """
    script = Script(
        [
            "Entropy was computed from each EEG recording. [#3]\n<cha",
            "nnel|>Then the reader asks about it again.\n",
            "<channel|>And once more.\n",
            "<channel|>ok",
        ]
    )

    events = turn(context(store_db, script, reading=Reading.WHOLE_SEED), conversation, QUESTION)

    answered = events[-1]
    assert isinstance(answered, AnsweredEvent)
    assert answered.answer.text == "Entropy was computed from each EEG recording."
    assert deltas(events) == ["Entropy was computed from each EEG recording. [#3]\n"]


def test_regression_eval_49f2a82d3fa6f54df59cc780a76aaa08_a_token_inside_a_delta_cuts_the_rest(
    store_db: Conn, conversation: Conversation
) -> None:
    """Eval run 20261029T191529Z-94e980b, trace 49f2a82d3fa6f54df59cc780a76aaa08 (retrieval).

    `<channel|>` stood right after a full stop, mid-delta, and the model's monologue followed.
    """
    script = Script(
        ["Two studies found no link with intensity.<channel|>So the reader also wants the dose."]
    )

    events = turn(context(store_db, script), conversation, QUESTION)

    answered = events[-1]
    assert isinstance(answered, AnsweredEvent)
    assert answered.answer.text == "Two studies found no link with intensity."
    assert deltas(events) == ["Two studies found no link with intensity."]


def test_regression_eval_fcb19996a0a8c2aca5129fb9fe75b646_a_token_alone_is_no_answer(
    store_db: Conn, conversation: Conversation
) -> None:
    """Eval run 20261029T191529Z-94e980b, trace fcb19996a0a8c2aca5129fb9fe75b646 (retrieval).

    The whole Answer was gemma's `<|tool_response>`, and the turn counted as answered. Cut at
    the token nothing is left: the turn fails and the Conversation stays as it was.
    """
    tracing, exporter, _ = in_memory_tracing()
    script = Script(["<|tool_response>"])

    events = turn(context(store_db, script, tracing=tracing), conversation, QUESTION)
    tracing.flush()

    assert [type(e) for e in events] == [StepEvent, ErrorEvent]
    failed = events[-1]
    assert isinstance(failed, ErrorEvent)
    assert failed.message == "The model gave no Answer."
    assert failed.reading is Reading.RETRIEVAL
    [root] = [s for s in exporter.get_finished_spans() if s.name == "question"]
    assert root.context is not None and failed.trace_id == format(root.context.trace_id, "032x")
    assert (conversation.messages, conversation.questions) == ([], 0)


def test_regression_eval_18ba564618a1ffce1b5033df85a5712e_an_echoed_system_block_is_left_out(
    store_db: Conn, conversation: Conversation
) -> None:
    """Eval run 20261029T191529Z-94e980b, trace 18ba564618a1ffce1b5033df85a5712e (baseline).

    The Answer opened by echoing a `<system>` note in its tags, and it ended on a lone `<`, the
    start of a token never finished. The block goes, the text around it stays.
    """
    script = Script(
        [
            "<system>Answer in plain",
            " text.</system>The recordings were made in the afternoon.",
            " They lasted an hour.<",
        ]
    )

    events = turn(context(store_db, script, reading=Reading.WHOLE_SEED), conversation, QUESTION)

    answered = events[-1]
    assert isinstance(answered, AnsweredEvent)
    expected = "The recordings were made in the afternoon. They lasted an hour."
    assert answered.answer.text == expected
    assert "".join(deltas(events)) == expected
    assert last_response_text(conversation) == [expected]


def test_angle_brackets_that_start_no_token_stay_in_the_answer(
    store_db: Conn, conversation: Conversation
) -> None:
    text = "Entropy fell when p < 0.05 and x<y held, a <b> tag aside."
    script = Script(["Entropy fell when p <", " 0.05 and x<", "y held, a <b", "> tag aside."])

    events = turn(context(store_db, script, reading=Reading.WHOLE_SEED), conversation, QUESTION)

    answered = events[-1]
    assert isinstance(answered, AnsweredEvent)
    assert answered.answer.text == text
    assert "".join(deltas(events)) == text


def test_regression_eval_35e1371558966e774f2a2d9466d2bb4f_the_question_closes_the_request(
    store_db: Conn, conversation: Conversation
) -> None:
    """Eval run 20261031T140751Z-a758bbc, trace 35e1371558966e774f2a2d9466d2bb4f (live).

    Reading the whole Seed, the request ended with the note that there are no tools, a user
    turn of its own after the Question. The model took the note for the reader's message and
    answered a Question nobody had asked; 7d24a397 did the same. The request now closes with
    one turn: the note, then the Question, last.
    """
    script = Script(["Entropy was computed from each EEG recording. [#3]"])

    events = turn(context(store_db, script, reading=Reading.WHOLE_SEED), conversation, QUESTION)

    assert isinstance(events[-1], AnsweredEvent)
    [(messages, _)] = script.requests
    last = messages[-1]
    assert isinstance(last, ModelRequest)
    [closing] = last.parts
    assert isinstance(closing, UserPromptPart) and isinstance(closing.content, str)
    assert closing.content.startswith(NO_TOOLS)
    assert closing.content.endswith(f"\n{QUESTION}")
    # The closing turn is for the request only: the Conversation keeps the bare Question.
    assert user_prompts(conversation.messages) == [QUESTION]


def test_regression_dc485d10c4cd2e5d03a5901b88429045_bare_numbers_are_handles_by_author_and_year(
    store_db: Conn, unnumbered_seed: Work
) -> None:
    """The live app on the tower, trace dc485d10c4cd2e5d03a5901b88429045, the whole Seed read.

    The model ended each sentence with a bare `[13]` or `[14]` instead of `[#13]`. The Seed
    cites by author and year, so it prints no such marker of its own, yet the numbers stayed
    in the text and the Answer got no Pointer.
    """
    conversation = Conversation(work=unnumbered_seed)
    script = Script(["Entropy was computed from each EEG recording [3]. The sample was small [5]."])

    events = turn(context(store_db, script, reading=Reading.WHOLE_SEED), conversation, QUESTION)

    answered = events[-1]
    assert isinstance(answered, AnsweredEvent)
    assert answered.answer.text == (
        "Entropy was computed from each EEG recording. The sample was small."
    )
    recording, discussion = (
        chunk(store_db, unnumbered_seed, text) for text in (RECORDING, AUTHOR_YEAR_DISCUSSION)
    )
    assert [[p.chunk_id for p in s.pointers] for s in answered.answer.sentences] == [
        [recording.id],
        [discussion.id],
    ]
    # The draft had its handles, so the model is not asked for them again.
    assert [e.type for e in events] == ["delta", "answer"]


def test_regression_eval_7f32813a3ef4cd52aca983d0e0ad660e_an_answer_is_asked_for_its_handles(
    store_db: Conn, conversation: Conversation
) -> None:
    """Eval run 20261031T140751Z-a758bbc, trace 7f32813a3ef4cd52aca983d0e0ad660e (live).

    Reading the whole Seed, the model answered in three sentences on the Seed and wrote no
    handle, so none of them got a Pointer. It is asked once more for its Answer, with handles.
    """
    first = "Entropy was computed from each EEG recording. The sample was small."
    second = "Entropy was computed from each EEG recording. [#3] The sample was small. [#5]"
    script = Script([first], [second])

    events = turn(context(store_db, script, reading=Reading.WHOLE_SEED), conversation, QUESTION)

    assert [e.type for e in events] == ["delta", "redraft", "delta", "answer"]
    assert deltas(events) == [first, second]
    answered = events[-1]
    assert isinstance(answered, AnsweredEvent)
    assert answered.answer.text == first
    assert [len(s.pointers) for s in answered.answer.sentences] == [1, 1]
    # The second request reads the first draft as the model's own response, then a note.
    _, (messages, info) = script.requests
    *_, response, note = messages
    assert isinstance(response, ModelResponse) and response.parts == [TextPart(content=first)]
    assert isinstance(note, ModelRequest)
    assert info.function_tools == []
    # The Conversation keeps the Question and the second draft, not the first or the note.
    assert user_prompts(conversation.messages) == [QUESTION]
    assert last_response_text(conversation) == [second]


def _broken() -> str:
    raise RuntimeError("the server went away")


@pytest.mark.parametrize("redraft", [[_broken], ["<|tool_response>"]], ids=["fails", "no-words"])
def test_regression_eval_70cf08933dac78bcf79873edbada5a1e_without_a_redraft_the_answer_stands(
    store_db: Conn, conversation: Conversation, redraft: list[Item]
) -> None:
    """Eval run 20261031T140751Z-a758bbc, trace 70cf08933dac78bcf79873edbada5a1e (live).

    Two sentences on the Seed, no handle. Asked again for them, a model that fails, or gives
    no words, leaves the first Answer standing.
    """
    first = "Entropy rose after the drug. Sessions took place in the afternoon."
    script = Script([first], redraft)

    events = turn(context(store_db, script, reading=Reading.WHOLE_SEED), conversation, QUESTION)

    assert [e.type for e in events][:2] == ["delta", "redraft"]
    answered = events[-1]
    assert isinstance(answered, AnsweredEvent)
    assert answered.answer.text == first
    assert all(s.pointers == [] for s in answered.answer.sentences)
    assert last_response_text(conversation) == [first]
    assert conversation.questions == 1


def test_regression_eval_35e1371558966e774f2a2d9466d2bb4f_a_not_covered_answer_is_not_redrafted(
    store_db: Conn, conversation: Conversation
) -> None:
    """Eval run 20261031T140751Z-a758bbc, trace 35e1371558966e774f2a2d9466d2bb4f (live).

    The Answer said what the Seed leaves out, and next which nearby topics it treats, as the
    prompt asks, and carried no handle: rightly so. It is not asked again.
    """
    script = Script(
        [
            "The Seed does not say how caffeine affects sleep or dreaming. It covers how brain"
            " entropy changed in ten volunteers after psilocybin."
        ]
    )

    events = turn(context(store_db, script, reading=Reading.WHOLE_SEED), conversation, QUESTION)

    assert [e.type for e in events] == ["delta", "answer"]
    assert len(script.requests) == 1
