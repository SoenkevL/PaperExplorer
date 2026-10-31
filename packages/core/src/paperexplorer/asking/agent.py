"""The answering agent: one Question of a Conversation answered from the Seed (ADR 0008-0010).

`design-asking.md` (The agent) and `architecture.md` (Tracing) give the shape. A Seed that fits
`PE_WHOLE_SEED_MAX_TOKENS` is read whole: the system prompt and every Chunk in context, one
request, no tools. A larger one is searched: the Seed's map in context, a forced first search,
five tools, at most `REQUEST_LIMIT` model requests. The model's thinking is traced but never
shown; each Question is one `question` trace.
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
from pydantic_ai.capabilities import AbstractCapability, Instrumentation, PrepareTools
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import (
    AgentStreamEvent,
    FunctionToolCallEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    SystemPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestContext
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage, UsageLimits

from paperexplorer.asking.answers import resolve_answer
from paperexplorer.asking.conversation import Conversation
from paperexplorer.asking.draft import DraftCut, answer_draft
from paperexplorer.asking.events import (
    AnsweredEvent,
    AnswerEvent,
    DeltaEvent,
    ErrorEvent,
    StepEvent,
)
from paperexplorer.asking.reading import Reading
from paperexplorer.asking.tools import SeedTools
from paperexplorer.config import Settings
from paperexplorer.domain import Answer
from paperexplorer.embedding import Embedder
from paperexplorer.tracing import Tracing

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (resources.files("paperexplorer.asking") / "system-prompt.md").read_text("utf-8")
# At most this many model requests per Question; the last one offers no tools.
REQUEST_LIMIT = 6
LAST_REQUEST = (
    "This is your last request for this Question: no tools are left. Answer now from what you"
    " have read, and where it does not settle the Question, say what the Seed does not say."
)
# A Seed that fits is read whole instead of searched (ADR 0010): the eval's baseline, unchanged.
WHOLE_SEED = (
    "For this Question you have no tools, and no search has run: the Seed's whole text is below,"
    " every Chunk under its handle. What is said above about searching and reading with tools"
    " does not apply; read the text below instead. Everything else above does."
)
NO_TOOLS = (
    "You have no tools for this Question: the Seed's whole text is in your instructions. Answer"
    " now from it, and where it does not settle the Question, say what the Seed does not say."
)
NO_ANSWER = "The model gave no Answer."
# The prompt's version hashes every text of ours the model reads, so an edit cannot forget it.
PROMPT_VERSION = hashlib.sha256(
    "\n\n".join([SYSTEM_PROMPT, LAST_REQUEST, WHOLE_SEED, NO_TOOLS]).encode()
).hexdigest()[:12]


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


def chat_model(settings: Settings) -> Model:
    """The chat model behind `PE_CHAT_BASE_URL`, Ollama's OpenAI-compatible endpoint by default.

    Ollama's profile reads the model's thinking from the `reasoning` field and sends tool
    schemas without `strict`; any OpenAI-compatible server that does the same fits.
    """
    provider = OllamaProvider(
        base_url=settings.chat_base_url, api_key=settings.chat_api_key.get_secret_value()
    )
    # A stream silent for `chat_timeout_s` fails the turn, rather than at the client's 600 s.
    return OpenAIChatModel(
        settings.chat_model,
        provider=provider,
        settings=ModelSettings(timeout=settings.chat_timeout_s),
    )


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
        logger.exception("Question %d of Conversation %s found no Seed", position, conversation.id)
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
        "num_ctx": str(ctx.settings.chat_num_ctx),
        "reading": reading.value,
    }
    whole = ["whole-seed"] if reading is Reading.WHOLE_SEED else []
    tags = ["asking", f"work:{key}", *whole, *ctx.tags]
    with ctx.tracing.trace(
        "question", tags=tags, session_id=conversation.id, metadata=metadata
    ) as trace_id:
        try:
            answer, messages, usage = await _answer(
                ctx, tools, reading, conversation, question, emit
            )
        except _NoAnswerError:
            logger.warning("The model's draft held no Answer once cut at its control tokens")
            emit(ErrorEvent(message=NO_ANSWER, trace_id=trace_id, reading=reading))
            return
        except UsageLimitExceeded:
            logger.warning("No Answer within %d model requests", REQUEST_LIMIT)
            emit(
                ErrorEvent(
                    message=f"The model did not answer within {REQUEST_LIMIT} requests.",
                    trace_id=trace_id,
                    reading=reading,
                )
            )
            return
        except Exception as error:
            logger.exception("Question %d of Conversation %s failed", position, conversation.id)
            emit(
                ErrorEvent(
                    message=f"The model could not answer: {error}",
                    trace_id=trace_id,
                    reading=reading,
                )
            )
            return
        conversation.messages = _without_thinking(messages)
        conversation.questions = position
        _score(ctx.tracing, answer, usage, time.monotonic() - started)
    logger.info(
        "Answered Question %d of Conversation %s (%s) in %d requests, %.1f s",
        position,
        conversation.id,
        reading.value,
        usage.requests,
        time.monotonic() - started,
    )
    emit(AnsweredEvent(answer=answer, trace_id=trace_id, reading=reading))


async def _answer(
    ctx: AskingContext,
    tools: SeedTools,
    reading: Reading,
    conversation: Conversation,
    question: str,
    emit: Callable[[AnswerEvent | None], None],
) -> tuple[Answer, list[ModelMessage], RunUsage]:
    """Run the agent on the Question and resolve its draft into the Answer."""
    whole = reading is Reading.WHOLE_SEED
    if whole:
        instructions = f"{SYSTEM_PROMPT.strip()}\n\n{WHOLE_SEED}\n\n{tools.whole_seed()}"
        history: list[ModelMessage] = [
            *conversation.messages,
            ModelRequest(parts=[UserPromptPart(question)]),
        ]
    else:
        instructions = f"{SYSTEM_PROMPT.strip()}\n\n{tools.seed_map()}"
        forced = _forced_search(ctx, tools, conversation, question, emit)
        history = [*conversation.messages, *forced]
    agent = Agent(
        ctx.model,
        deps_type=SeedTools,
        name="asking",
        instructions=instructions,
        tools=[] if whole else _TOOLS,
        capabilities=[
            PrepareTools(_no_tools_on_the_last_request),
            _NoteAfterReading(whole_seed=whole),
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
                cut = DraftCut()
                async with node.stream(run.ctx) as stream:
                    async for event in stream:
                        if (text := _draft(event)) and (shown := cut.add(text)):
                            emit(DeltaEvent(text=shown))
            elif Agent.is_call_tools_node(node):
                async with node.stream(run.ctx) as stream:
                    async for event in stream:
                        if isinstance(event, FunctionToolCallEvent):
                            args = event.part.args_as_dict()
                            emit(StepEvent(tool=event.part.tool_name, args=args))
    result = run.result
    assert result is not None, "the run ended without a result"
    draft = answer_draft(result.output)
    with ctx.tracing.span("resolve answer", input=draft) as span:
        answer = resolve_answer(
            draft,
            conversation.handles,
            tools.chunks_read(),
            ctx.embedder,
            ctx.settings.support_threshold,
        )
        span.update(output=answer.counts().model_dump())
    if not any(c.isalnum() for c in answer.text):
        raise _NoAnswerError
    return answer, _with_draft(result.all_messages(), draft), result.usage


class _NoAnswerError(Exception):
    """The draft holds no words once cut at its control tokens and without its handles."""


def _forced_search(
    ctx: AskingContext,
    tools: SeedTools,
    conversation: Conversation,
    question: str,
    emit: Callable[[AnswerEvent | None], None],
) -> list[ModelMessage]:
    """Search the Seed for the Question: the Question, then the search as the model's own call."""
    emit(StepEvent(tool="search", args={"query": question}))
    with ctx.tracing.span("forced search", as_type="tool", input={"query": question}) as span:
        found = tools.search(question)
        span.update(output=found)
    call = ToolCallPart(
        "search", {"query": question}, tool_call_id=f"forced-{conversation.questions + 1}"
    )
    return [
        ModelRequest(parts=[UserPromptPart(question)]),
        ModelResponse(parts=[call]),
        ModelRequest(parts=[ToolReturnPart("search", found, tool_call_id=call.tool_call_id)]),
    ]


def _draft(event: AgentStreamEvent) -> str | None:
    """The draft text an event adds; thinking is never draft."""
    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
        return event.part.content
    if isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
        return event.delta.content_delta
    return None


@dataclass
class _NoteAfterReading(AbstractCapability[SeedTools]):
    """A request that offers no tools ends with a note saying so, after all the model has read.

    The note is a system prompt part at the end of the request, which reaches Ollama as a user
    turn in `<system>` tags; it is for that request only and never joins the Conversation. Among
    the instructions, before the reading, the model ignored it (docs/eval-2026-10-29.md): in eval
    run 20261029T191529Z-94e980b it called a tool on all 8 last requests; replayed with the note
    last, 23 of 24 were answered.
    """

    whole_seed: bool

    async def before_model_request(
        self, ctx: RunContext[SeedTools], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        """Add the note to this request's last message, the Question or the tools' results."""
        if self.whole_seed:
            note = NO_TOOLS
        elif ctx.run_step >= REQUEST_LIMIT:
            note = LAST_REQUEST
        else:
            return request_context
        *earlier, last = request_context.messages
        if not isinstance(last, ModelRequest):
            return request_context
        # A new message, not the history's own: the note stays out of the Conversation.
        noted = replace(last, parts=[*last.parts, SystemPromptPart(note)])
        return replace(request_context, messages=[*earlier, noted])


async def _no_tools_on_the_last_request(
    ctx: RunContext[SeedTools], tool_defs: list[ToolDefinition]
) -> list[ToolDefinition]:
    return [] if ctx.run_step >= REQUEST_LIMIT else tool_defs


def _with_draft(messages: list[ModelMessage], draft: str) -> list[ModelMessage]:
    """The messages with the last response's text the Answer's draft, as cut, for later turns."""
    *earlier, last = messages
    if not isinstance(last, ModelResponse):
        return messages
    kept = [p for p in last.parts if not isinstance(p, TextPart)]
    return [*earlier, replace(last, parts=[*kept, TextPart(draft)])]


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
