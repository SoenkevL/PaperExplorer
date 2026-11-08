"""The answering agent: one Question of a Conversation answered from the Seed (ADR 0008-0010).

`design-asking.md` (The agent) and `architecture.md` (Tracing) give the shape. A Seed that fits
`PE_WHOLE_SEED_MAX_TOKENS` is read whole: the system prompt and every Chunk in context, one
request, no tools; an Answer it gives without a handle is asked for them once more. A larger
one is searched: the Seed's map in context, a forced first search, five tools, at most
`REQUEST_LIMIT` model requests. A request that offers no tools must answer, and runs without
thinking unless `PE_CHAT_ANSWER_THINKING` says otherwise. The model's thinking is traced but
never shown; each Question is one `question` trace.
"""

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Container
from dataclasses import dataclass, replace

import psycopg
from psycopg.rows import TupleRow
from pydantic_ai import Agent, AgentRunResult, RunContext
from pydantic_ai.capabilities import AbstractCapability, Instrumentation, PrepareTools
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    AgentStreamEvent,
    FunctionToolCallEvent,
    ModelMessage,
    ModelRequest,
    PartDeltaEvent,
    PartStartEvent,
    SystemPromptPart,
    TextPart,
    TextPartDelta,
)
from pydantic_ai.models import Model, ModelRequestContext
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.models.openai import OpenAIChatModelSettings
from pydantic_ai.settings import ModelSettings, merge_model_settings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import UsageLimits

from paperexplorer.asking.answers import has_words, resolve_answer, wants_handles
from paperexplorer.asking.conversation import Conversation
from paperexplorer.asking.draft import DraftCut, answer_draft
from paperexplorer.asking.events import (
    AnsweredEvent,
    AnswerEvent,
    DeltaEvent,
    ErrorEvent,
    RedraftEvent,
    StepEvent,
)
from paperexplorer.asking.history import QuestionHistory
from paperexplorer.asking.prompts import (
    LAST_REQUEST,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    WHOLE_SEED,
)
from paperexplorer.asking.reading import Reading
from paperexplorer.asking.tools import SEED_TOOLS, SeedTools
from paperexplorer.config import Settings
from paperexplorer.domain import Answer
from paperexplorer.embedding import Embedder
from paperexplorer.inference import chat_context
from paperexplorer.store import ScoredChunk
from paperexplorer.tracing import Tracing

logger = logging.getLogger(__name__)

# At most this many model requests per Question; the last one offers no tools.
REQUEST_LIMIT = 6
NO_ANSWER = "The model gave no Answer."
# Probed on the tower (Ollama 0.40.3, 2026-10-31): `reasoning_effort: "none"` is what Ollama's
# OpenAI-compatible endpoint honours, so no `reasoning` comes back. pydantic-ai's unified
# `thinking=False` sends nothing to Ollama, and `think: false` in the body is ignored there.
_WITHOUT_THINKING = OpenAIChatModelSettings(openai_reasoning_effort="none")


@dataclass(frozen=True)
class AskingContext:
    """What answering works with: the store, the Embedder, the chat model, settings and tracing.

    `tags` go on every Question's trace beside its own (the eval's run id). `reading` is how a
    Question reads the Seed: `auto`, the whole Seed when it fits `PE_WHOLE_SEED_MAX_TOKENS` and
    retrieval otherwise; the eval sets one or the other.
    """

    conn: psycopg.Connection[TupleRow]
    embedder: Embedder
    model: Model
    settings: Settings
    tracing: Tracing
    tags: tuple[str, ...] = ()
    reading: Reading = Reading.AUTO


@dataclass
class _Tally:
    """What a turn has run so far: the model's requests and tool calls, and the forced search.

    The forced search is the server's call, not the model's: it is neither a request nor a tool.
    """

    model_requests: int = 0
    tool_calls: int = 0
    forced_search: list[ScoredChunk] | None = None


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
    try:
        await _answer_turn(ctx, conversation, question, emit)
    finally:
        emit(None)


async def _answer_turn(
    ctx: AskingContext,
    conversation: Conversation,
    question: str,
    emit: Callable[[AnswerEvent | None], None],
) -> None:
    started = time.monotonic()
    position = conversation.questions + 1
    try:
        tools = SeedTools(ctx.conn, ctx.embedder, conversation)
    except Exception as error:
        logger.exception(
            "Question %d of Conversation %s could not read the Seed", position, conversation.id
        )
        emit(ErrorEvent(message=f"The Seed could not be read: {error}"))
        return
    reading = ctx.reading.for_seed(tools.tokens(), ctx.settings.whole_seed_max_tokens)
    work = conversation.work
    key = work.zotero_key or str(work.id)
    metadata = {
        "work_id": str(work.id),
        "zotero_key": work.zotero_key or "",
        "position": str(position),
        "prompt_version": PROMPT_VERSION,
        "model": ctx.model.model_name,
        "num_ctx": str(chat_context(ctx.settings)),
        "temperature": str(ctx.settings.chat_temperature),
        "answer_thinking": str(ctx.settings.chat_answer_thinking).lower(),
        "reading": reading.value,
        "redrafted": "false",
    }
    whole = ["whole-seed"] if reading is Reading.WHOLE_SEED else []
    tags = ["asking", f"work:{key}", *whole, *ctx.tags]
    tally = _Tally()

    def failed(message: str, trace_id: str | None) -> ErrorEvent:
        return ErrorEvent(
            message=message,
            trace_id=trace_id,
            reading=reading,
            model_requests=tally.model_requests,
            tool_calls=tally.tool_calls,
            forced_search=tally.forced_search,
        )

    with ctx.tracing.trace(
        "question", tags=tags, session_id=conversation.id, metadata=metadata
    ) as trace_id:
        try:
            answer, redrafted = await _answer(
                ctx, tools, reading, conversation, question, tally, emit
            )
        except _NoAnswerError:
            logger.warning("The model's draft held no Answer once cut at its control tokens")
            emit(failed(NO_ANSWER, trace_id))
            return
        except UsageLimitExceeded:
            logger.warning("No Answer within %d model requests", REQUEST_LIMIT)
            emit(failed(f"The model did not answer within {REQUEST_LIMIT} requests.", trace_id))
            return
        except Exception as error:
            logger.exception("Question %d of Conversation %s failed", position, conversation.id)
            emit(failed(f"The model could not answer: {error}", trace_id))
            return
        if redrafted:
            ctx.tracing.add_metadata({"redrafted": "true"})
        _score(ctx.tracing, answer, tally, time.monotonic() - started)
    logger.info(
        "Answered Question %d of Conversation %s (%s%s) in %d requests, %.1f s",
        position,
        conversation.id,
        reading.value,
        ", redrafted" if redrafted else "",
        tally.model_requests,
        time.monotonic() - started,
    )
    emit(
        AnsweredEvent(
            answer=answer,
            trace_id=trace_id,
            reading=reading,
            redrafted=redrafted,
            model_requests=tally.model_requests,
            tool_calls=tally.tool_calls,
            forced_search=tally.forced_search,
        )
    )


async def _answer(
    ctx: AskingContext,
    tools: SeedTools,
    reading: Reading,
    conversation: Conversation,
    question: str,
    tally: _Tally,
    emit: Callable[[AnswerEvent | None], None],
) -> tuple[Answer, bool]:
    """Run the agent on the Question and resolve its draft into the Answer; whether redrafted.

    Reading the whole Seed, an Answer with sentences to point at the Seed and no handle at all
    is asked for its handles once more (`_redraft`); if that fails, the first Answer stands. An
    answered Question joins the Conversation here.
    """
    whole = reading is Reading.WHOLE_SEED
    if whole:
        instructions = f"{SYSTEM_PROMPT.strip()}\n\n{WHOLE_SEED}\n\n{tools.whole_seed()}"
        history = QuestionHistory.whole_seed(conversation, question)
    else:
        instructions = f"{SYSTEM_PROMPT.strip()}\n\n{tools.seed_map()}"
        found = _forced_search(ctx, tools, question, tally, emit)
        history = QuestionHistory.retrieval(conversation, question, found)
    agent = Agent(
        ctx.model,
        deps_type=SeedTools,
        name="asking",
        instructions=instructions,
        tools=[] if whole else SEED_TOOLS,
        model_settings=ModelSettings(temperature=ctx.settings.chat_temperature),
        capabilities=[
            PrepareTools(_no_tools_on_the_last_request),
            _NoteAfterReading(),
            _ThinkingOnlyWithTools(thinking=ctx.settings.chat_answer_thinking),
            Instrumentation(
                settings=InstrumentationSettings(tracer_provider=ctx.tracing.tracer_provider)
            ),
        ],
    )
    result = await _run(
        agent, tools, conversation.handles, history.request, REQUEST_LIMIT, tally, emit
    )
    draft = answer_draft(result.output)
    answer = _resolve(ctx, tools, conversation, draft)
    if not has_words(answer):
        raise _NoAnswerError
    history.answered(result.all_messages(), draft)
    redrafted = False
    if whole and wants_handles(answer):
        again = await _redraft(ctx, agent, tools, conversation, history, draft, tally, emit)
        if again is not None:
            answer, redrafted = again, True
    history.commit()
    return answer, redrafted


async def _run(
    agent: Agent[SeedTools, str],
    tools: SeedTools,
    handles: Container[int],
    history: list[ModelMessage],
    request_limit: int,
    tally: _Tally,
    emit: Callable[[AnswerEvent | None], None],
) -> AgentRunResult[str]:
    """Run the agent on `history`, streaming its steps and its draft as cut; its result.

    The draft is cut at the `handles` the Conversation knows when each delta comes, and every
    request's draft starts a cut of its own.
    """
    bare = not tools.numbered_references()
    async with agent.iter(
        None,
        message_history=history,
        deps=tools,
        usage_limits=UsageLimits(request_limit=request_limit),
    ) as run:
        async for node in run:
            if Agent.is_model_request_node(node):
                cut = DraftCut(handles, bare_handles=bare)
                async with node.stream(run.ctx) as stream:
                    tally.model_requests += 1  # sent: the request limit stops one before this
                    async for event in stream:
                        if (text := _draft(event)) and (pieces := cut.add(text)):
                            emit(DeltaEvent(pieces=pieces))
            elif Agent.is_call_tools_node(node):
                async with node.stream(run.ctx) as stream:
                    async for event in stream:
                        if isinstance(event, FunctionToolCallEvent):
                            tally.tool_calls += 1
                            args = event.part.args_as_dict()
                            emit(StepEvent(tool=event.part.tool_name, args=args))
    result = run.result
    assert result is not None, "the run ended without a result"
    return result


def _resolve(
    ctx: AskingContext, tools: SeedTools, conversation: Conversation, draft: str
) -> Answer:
    """The draft resolved into the Answer, in a `resolve answer` span of its own."""
    with ctx.tracing.span("resolve answer", input=draft) as span:
        answer = resolve_answer(
            draft,
            conversation.handles,
            tools.chunks_read(),
            ctx.embedder,
            ctx.settings.support_threshold,
            bare_handles=not tools.numbered_references(),
        )
        span.update(output=answer.counts().model_dump())
    return answer


async def _redraft(
    ctx: AskingContext,
    agent: Agent[SeedTools, str],
    tools: SeedTools,
    conversation: Conversation,
    history: QuestionHistory,
    first: str,
    tally: _Tally,
    emit: Callable[[AnswerEvent | None], None],
) -> Answer | None:
    """Ask once more for the Answer, word for word with its handles: the new Answer, kept.

    One request, without tools, after the `first` draft. None when it fails or leaves no words,
    and `history` keeps the first draft.
    """
    emit(RedraftEvent())
    request = history.redraft_request()
    with ctx.tracing.span("redraft", input=first) as span:
        try:
            result = await _run(agent, tools, conversation.handles, request, 1, tally, emit)
        except Exception as error:
            logger.warning("Asked again for its handles, the model failed", exc_info=True)
            span.update(output=f"failed: {error}")
            return None
        draft = answer_draft(result.output)
        span.update(output=draft)
    answer = _resolve(ctx, tools, conversation, draft)
    if not has_words(answer):
        logger.warning("Asked again for its handles, the model gave no Answer")
        return None
    history.redrafted(result.all_messages(), draft)
    return answer


class _NoAnswerError(Exception):
    """The draft holds no words once cut at its control tokens and without its handles."""


def _forced_search(
    ctx: AskingContext,
    tools: SeedTools,
    question: str,
    tally: _Tally,
    emit: Callable[[AnswerEvent | None], None],
) -> str:
    """Search the Seed for the Question, as a step of the turn: what the search shows."""
    emit(StepEvent(tool="search", args={"query": question}))
    with ctx.tracing.span("forced search", as_type="tool", input={"query": question}) as span:
        tally.forced_search, found = tools.ranked_search(question)
        span.update(output=found)
    return found


def _draft(event: AgentStreamEvent) -> str | None:
    """The draft text an event adds; thinking is never draft."""
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        return event.part.content
    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        return event.delta.content_delta
    return None


@dataclass
class _NoteAfterReading(AbstractCapability[SeedTools]):
    """Retrieval's last request, which offers no tools, ends with a note saying so.

    The note is a system prompt part at the end of the request, after all the model has read,
    which reaches Ollama as a user turn in `<system>` tags; it is for that request only and
    never joins the Conversation. Among the instructions, before the reading, the model ignored
    it (docs/eval-2026-10-29.md): in eval run 20261029T191529Z-94e980b it called a tool on all 8
    last requests; replayed with the note last, 23 of 24 were answered. Reading the whole Seed,
    the request's closing turn holds its note instead (`QUESTION_LABEL`).
    """

    async def before_model_request(
        self, ctx: RunContext[SeedTools], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        """Add the note to the last request's last message, the tools' results."""
        if ctx.run_step < REQUEST_LIMIT:
            return request_context
        note = LAST_REQUEST
        *earlier, last = request_context.messages
        if not isinstance(last, ModelRequest):
            return request_context
        # A new message, not the history's own: the note stays out of the Conversation.
        noted = replace(last, parts=[*last.parts, SystemPromptPart(note)])
        return replace(request_context, messages=[*earlier, noted])


@dataclass
class _ThinkingOnlyWithTools(AbstractCapability[SeedTools]):
    """A request that offers no tools must answer: it runs without thinking, unless `thinking`.

    Thinking off there also closes the way thinking leaked into Answers (chat-template tokens
    and monologue, eval of 2026-10-29); `DraftCut` stays as a guard.
    """

    thinking: bool

    async def before_model_request(
        self, ctx: RunContext[SeedTools], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        """Turn thinking off for this request if it offers no tools."""
        if self.thinking or request_context.model_request_parameters.function_tools:
            return request_context
        settings = merge_model_settings(request_context.model_settings, _WITHOUT_THINKING)
        return replace(request_context, model_settings=settings)


async def _no_tools_on_the_last_request(
    ctx: RunContext[SeedTools], tool_defs: list[ToolDefinition]
) -> list[ToolDefinition]:
    return [] if ctx.run_step >= REQUEST_LIMIT else tool_defs


def _score(tracing: Tracing, answer: Answer, tally: _Tally, latency_s: float) -> None:
    """Score the `question` trace with the Answer's counts, the turn's tally and its latency."""
    for name, value in answer.counts().model_dump().items():
        if value is not None:
            tracing.score(name, float(value))
    tracing.score("tool_calls", tally.tool_calls)
    tracing.score("model_requests", tally.model_requests)
    tracing.score("latency_s", round(latency_s, 3))
