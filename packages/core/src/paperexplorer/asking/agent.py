"""The answering agent: one Question of a Conversation answered from the Seed (ADR 0008, 0009).

`design-asking.md` (The agent) and `architecture.md` (Tracing) give the shape: the system prompt
and the Seed's map in context, a forced first search, five tools, at most `REQUEST_LIMIT` model
requests, the model's thinking traced but never shown, one `question` trace per Question.
"""

import asyncio
import hashlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from importlib import resources

import psycopg
from psycopg.rows import TupleRow
from pydantic_ai import Agent, RunContext, Tool
from pydantic_ai.capabilities import Instrumentation, PrepareTools
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    AgentStreamEvent,
    FunctionToolCallEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage, UsageLimits

from paperexplorer.asking.answers import resolve_answer
from paperexplorer.asking.conversation import Conversation
from paperexplorer.asking.events import (
    AnsweredEvent,
    AnswerEvent,
    DeltaEvent,
    ErrorEvent,
    StepEvent,
)
from paperexplorer.asking.tools import SeedTools
from paperexplorer.config import Settings
from paperexplorer.domain import Answer
from paperexplorer.embedding import Embedder
from paperexplorer.tracing import Tracing

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (resources.files("paperexplorer.asking") / "system-prompt.md").read_text("utf-8")
# The prompt's version is its content's hash, so an edit cannot forget to change it.
PROMPT_VERSION = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()[:12]
# At most this many model requests per Question; the last one offers no tools.
REQUEST_LIMIT = 6
LAST_REQUEST = (
    "This is your last request for this Question: no tools are left. Answer now from what you"
    " have read, and where it does not settle the Question, say what the Seed does not say."
)


@dataclass(frozen=True)
class AskingContext:
    """What answering works with: the store, the Embedder, the chat model, settings and tracing."""

    conn: psycopg.Connection[TupleRow]
    embedder: Embedder
    model: Model
    settings: Settings
    tracing: Tracing


def chat_model(settings: Settings) -> Model:
    """The chat model behind `PE_CHAT_BASE_URL`, Ollama's OpenAI-compatible endpoint by default.

    Ollama's profile reads the model's thinking from the `reasoning` field and sends tool
    schemas without `strict`; any OpenAI-compatible server that does the same fits.
    """
    provider = OllamaProvider(
        base_url=settings.chat_base_url, api_key=settings.chat_api_key.get_secret_value()
    )
    return OpenAIChatModel(settings.chat_model, provider=provider)


async def ask(
    ctx: AskingContext, conversation: Conversation, question: str
) -> AsyncIterator[AnswerEvent]:
    """Answer one Question of `conversation`: its steps and draft as they come, then the Answer.

    The turn ends with an `AnsweredEvent` or an `ErrorEvent`. Only an answered turn joins the
    Conversation. The turn runs in a task of its own, so its trace stays inside it; closing the
    stream early cancels it.
    """
    events: asyncio.Queue[AnswerEvent | None] = asyncio.Queue()
    task = asyncio.create_task(_turn(ctx, conversation, question, events.put_nowait))
    try:
        while (event := await events.get()) is not None:
            yield event
    finally:
        if not task.done():
            task.cancel()
            await asyncio.wait([task])


async def _turn(
    ctx: AskingContext,
    conversation: Conversation,
    question: str,
    emit: Callable[[AnswerEvent | None], None],
) -> None:
    started = time.monotonic()
    position = conversation.questions + 1
    work = conversation.work
    key = work.zotero_key or str(work.id)
    metadata = {
        "work_id": str(work.id),
        "zotero_key": work.zotero_key or "",
        "position": str(position),
        "prompt_version": PROMPT_VERSION,
        "model": ctx.model.model_name,
        "num_ctx": str(ctx.settings.chat_num_ctx),
    }
    try:
        with ctx.tracing.trace(
            "question",
            tags=["asking", f"work:{key}"],
            session_id=conversation.id,
            metadata=metadata,
        ):
            try:
                answer, messages, usage = await _answer(ctx, conversation, question, emit)
            except UsageLimitExceeded:
                logger.warning("No Answer within %d model requests", REQUEST_LIMIT)
                emit(
                    ErrorEvent(message=f"The model did not answer within {REQUEST_LIMIT} requests.")
                )
                return
            except Exception as error:
                logger.exception("Question %d of Conversation %s failed", position, conversation.id)
                emit(ErrorEvent(message=f"The model could not answer: {error}"))
                return
            conversation.messages = _without_thinking(messages)
            conversation.questions = position
            _score(ctx.tracing, answer, usage, time.monotonic() - started)
        logger.info(
            "Answered Question %d of Conversation %s in %d requests, %.1f s",
            position,
            conversation.id,
            usage.requests,
            time.monotonic() - started,
        )
        emit(AnsweredEvent(answer=answer))
    finally:
        emit(None)


async def _answer(
    ctx: AskingContext,
    conversation: Conversation,
    question: str,
    emit: Callable[[AnswerEvent | None], None],
) -> tuple[Answer, list[ModelMessage], RunUsage]:
    """Run the agent on the Question and resolve its draft into the Answer."""
    tools = SeedTools(ctx.conn, ctx.embedder, conversation)
    instructions = f"{SYSTEM_PROMPT.strip()}\n\n{tools.seed_map()}"
    emit(StepEvent(tool="search", args={"query": question}))
    with ctx.tracing.span("forced search", as_type="tool", input={"query": question}) as span:
        found = tools.search(question)
        span.update(output=found)
    call = ToolCallPart(
        "search", {"query": question}, tool_call_id=f"forced-{conversation.questions + 1}"
    )
    history: list[ModelMessage] = [
        *conversation.messages,
        ModelRequest(parts=[UserPromptPart(question)]),
        ModelResponse(parts=[call]),
        ModelRequest(parts=[ToolReturnPart("search", found, tool_call_id=call.tool_call_id)]),
    ]
    agent = Agent(
        ctx.model,
        deps_type=SeedTools,
        name="asking",
        instructions=[instructions, _last_request],
        tools=_TOOLS,
        capabilities=[
            PrepareTools(_no_tools_on_the_last_request),
            Instrumentation(
                settings=InstrumentationSettings(tracer_provider=ctx.tracing.tracer_provider)
            ),
        ],
    )
    async with agent.iter(
        None,
        message_history=history,
        deps=tools,
        usage_limits=UsageLimits(request_limit=REQUEST_LIMIT),
    ) as run:
        async for node in run:
            if Agent.is_model_request_node(node):
                async with node.stream(run.ctx) as stream:
                    async for event in stream:
                        if text := _draft(event):
                            emit(DeltaEvent(text=text))
            elif Agent.is_call_tools_node(node):
                async with node.stream(run.ctx) as stream:
                    async for event in stream:
                        if isinstance(event, FunctionToolCallEvent):
                            args = event.part.args_as_dict()
                            emit(StepEvent(tool=event.part.tool_name, args=args))
    result = run.result
    assert result is not None, "the run ended without a result"
    with ctx.tracing.span("resolve answer", input=result.output) as span:
        answer = resolve_answer(
            result.output,
            conversation.handles,
            tools.chunks_read(),
            ctx.embedder,
            ctx.settings.support_threshold,
        )
        span.update(output=answer.counts().model_dump())
    return answer, result.all_messages(), result.usage


def _draft(event: AgentStreamEvent) -> str | None:
    """The draft text an event adds; thinking is never draft."""
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        return event.part.content
    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        return event.delta.content_delta
    return None


def _last_request(ctx: RunContext[SeedTools]) -> str | None:
    return LAST_REQUEST if ctx.run_step >= REQUEST_LIMIT else None


async def _no_tools_on_the_last_request(
    ctx: RunContext[SeedTools], tool_defs: list[ToolDefinition]
) -> list[ToolDefinition]:
    return [] if ctx.run_step >= REQUEST_LIMIT else tool_defs


def _without_thinking(messages: list[ModelMessage]) -> list[ModelMessage]:
    """The messages with the model's thinking left out: it is traced, never kept or shown."""
    return [
        replace(m, parts=[p for p in m.parts if not isinstance(p, ThinkingPart)])
        if isinstance(m, ModelResponse)
        else m
        for m in messages
    ]


def _score(tracing: Tracing, answer: Answer, usage: RunUsage, latency_s: float) -> None:
    """Score the `question` trace with the Answer's counts, the run's usage and its latency."""
    for name, value in answer.counts().model_dump().items():
        if value is not None:
            tracing.score(name, float(value))
    tracing.score("tool_calls", usage.tool_calls)
    tracing.score("model_requests", usage.requests)
    tracing.score("latency_s", round(latency_s, 3))


async def _search(ctx: RunContext[SeedTools], query: str, k: int = 8) -> str:
    """Search the Seed's Chunks by words and meaning, best first.

    Args:
        ctx: The turn's tools.
        query: What to look for, in your words or the Seed's.
        k: How many Chunks to show, at most 20.
    """
    return ctx.deps.search(query, k)


async def _read_section(ctx: RunContext[SeedTools], heading: str, part: int = 1) -> str:
    """Read the Chunks under one heading of the map, in reading order, in parts of about 3k tokens.

    Args:
        ctx: The turn's tools.
        heading: The section's § number from the map, such as §4, or its heading.
        part: Which part to read; the output says when the section goes on.
    """
    return ctx.deps.read_section(heading, part)


async def _read_around(ctx: RunContext[SeedTools], handle: int | str, n: int = 1) -> str:
    """Read the Chunks just before and after one you have been shown, in reading order.

    Args:
        ctx: The turn's tools.
        handle: The Chunk's handle, such as 7 for [#7].
        n: How many Chunks on either side, at most 3.
    """
    return ctx.deps.read_around(handle, n)


async def _reference(ctx: RunContext[SeedTools], handle: int | str, marker: str) -> str:
    """Show the Reference, as printed, behind a Citation marker in a Chunk.

    Args:
        ctx: The turn's tools.
        handle: The handle of the Chunk that prints the marker, such as 7 for [#7].
        marker: The marker as the Chunk prints it, such as [12] or (Smith, 2019).
    """
    return ctx.deps.reference(handle, marker)


async def _citing_sentences(ctx: RunContext[SeedTools], reference: str) -> str:
    """Show every sentence of the Seed that cites one Reference.

    Args:
        ctx: The turn's tools.
        reference: The Reference's key as `reference` shows it (such as b12), its printed label,
            or words of its text.
    """
    return ctx.deps.citing_sentences(reference)


_TOOLS = [
    Tool(_search, name="search"),
    Tool(_read_section, name="read_section"),
    Tool(_read_around, name="read_around"),
    Tool(_reference, name="reference"),
    Tool(_citing_sentences, name="citing_sentences"),
]
