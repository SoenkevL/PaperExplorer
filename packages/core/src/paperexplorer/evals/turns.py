"""One Question asked of the Seed for the eval, and what that turn measures."""

import logging
import re
import time
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic_evals.evaluators import Evaluator, EvaluatorContext, EvaluatorOutput

from paperexplorer import asking
from paperexplorer.domain import Answer, ChunkId, Work
from paperexplorer.evals.models import (
    Hit,
    Mode,
    Place,
    PointerCheck,
    QuestionMeta,
    QuoteCheck,
    Turn,
)
from paperexplorer.store import ScoredChunk, StoredChunk

logger = logging.getLogger(__name__)

# hit@k: the forced search's best k Chunks, as many as it shows the model.
HIT_K = 8
# What `metrics` may measure, in the order it gives them.
METRICS = (
    "answered",
    "hit_at_8",
    "first_hit_rank",
    "pointer_coverage",
    "pointers_close",
    "pointers_weak",
    "pointers_on_expected",
    "places_pointed",
    "says_not_covered",
    "quotes_exact",
    "quotes_not_exact",
    "quotes_verified",
    "unknown_handles",
    "model_requests",
    "tool_calls",
    "latency_s",
)

# "The Seed does not say …", as the prompt asks the model to put it, and its near variants.
_SUBJECT = r"(?:seed|review|paper|article|text|work|authors?|it|they)"
_VERB = (
    r"(?:say|state|cover|address|discuss|mention|report|describe|give|provide|include|contain"
    r"|answer|specify|examine|investigate|tell|recommend|list|name|offer|consider|deal)"
)
_NOT_COVERED = re.compile(
    rf"\b{_SUBJECT}\s+(?:does|do|did)(?:\s+not|n't)\s+(?:\w+\s+)?{_VERB}\b"
    r"|\bnot\s+(?:covered|addressed|discussed|mentioned|stated|specified)\b"
    r"|\bno\s+(?:information|mention)\b"
    r"|\bsays?\s+nothing\b",
    re.IGNORECASE,
)
_HYPHEN_BREAK = re.compile(r"(?<=[^\W\d_])[-\u2010\u2011]\s*(?=[^\W\d_])")


def says_not_covered(text: str) -> bool:
    """Whether an Answer says the Seed does not cover the Question: a heuristic, for the eval."""
    return _NOT_COVERED.search(text.replace("\u2019", "'")) is not None


async def ask_turn(
    ctx: asking.AskingContext, work: Work, question: str, blocks: Sequence[str], repeat: int = 1
) -> Turn:
    """Ask `question` of the Seed in a fresh Conversation and record what the turn produced.

    The mode follows `ctx.reading`; the turn says how it read the Seed, and which `repeat` of
    the Question it is. `blocks` are the Seed's
    block texts, for the places Pointers and Quotes land on. A turn that read by retrieval has
    its forced search's Chunks from its last event.
    """
    mode = Mode.of(ctx.reading)
    conversation = asking.Conversation(work=work)
    answer: Answer | None = None
    error: str | None = None
    trace_id: str | None = None
    reading: asking.Reading | None = None
    steps: list[str] = []
    redrafted = False
    requests, tool_calls = 0, 0
    forced_search: list[ScoredChunk] | None = None
    first_delta: float | None = None
    started = time.monotonic()
    async for event in asking.ask(ctx, conversation, question):
        match event:
            case asking.StepEvent(tool=tool):
                steps.append(tool)
            case asking.DeltaEvent():
                if first_delta is None:
                    first_delta = time.monotonic() - started
            case asking.RedraftEvent():
                pass  # the Answer says whether the redraft stood
            case asking.AnsweredEvent():
                answer, trace_id, reading = event.answer, event.trace_id, event.reading
                redrafted = event.redrafted
                requests, tool_calls = event.model_requests, event.tool_calls
                forced_search = event.forced_search
            case asking.ErrorEvent():
                error, trace_id, reading = event.message, event.trace_id, event.reading
                requests, tool_calls = event.model_requests, event.tool_calls
                forced_search = event.forced_search
    latency = time.monotonic() - started
    search = None
    if forced_search is not None:
        search = [
            Hit(rank=i, chunk_id=r.id, place=Place.of(r.chunk), rrf=r.rrf)
            for i, r in enumerate(forced_search, start=1)
        ]
    forced = len(steps) - tool_calls  # the forced search's step, when it ran
    chunks = {c.id: c for c in conversation.handles.values()}
    logger.info(
        "Asked %r (%s, %s) in %.1f s: %s",
        question,
        mode.value,
        reading.value if reading else "-",
        latency,
        "answered" if answer else f"failed: {error}",
    )
    return Turn(
        mode=mode,
        repeat=repeat,
        reading=reading,
        question=question,
        conversation_id=conversation.id,
        trace_id=trace_id,
        answer=answer,
        error=error,
        search=search,
        pointers=_pointers(answer, chunks, blocks) if answer else [],
        quotes=_quotes(answer, list(chunks.values()), blocks) if answer else [],
        says_not_covered=answer is not None and says_not_covered(answer.text),
        redrafted=redrafted,
        model_requests=requests if answer else None,
        tool_calls=tool_calls,
        tools=steps[forced:],
        latency_s=round(latency, 3),
        first_delta_s=round(first_delta, 3) if first_delta is not None else None,
    )


def metrics(turn: Turn, meta: QuestionMeta) -> dict[str, bool | int | float]:
    """What a turn measures against its Question: booleans are checks, numbers are scores.

    hit@8, the Pointers on expected places and the places pointed at count for covered
    Questions only; whether the Answer says the Seed does not cover it, for uncovered ones.
    """
    if turn.answer is None:
        return {"answered": False, "tool_calls": turn.tool_calls, "latency_s": turn.latency_s}
    measured: dict[str, bool | int | float] = {"answered": True}
    places = set(meta.places)
    if meta.covered and turn.search is not None:
        ranks = [h.rank for h in turn.search[:HIT_K] if h.place in places]
        measured["hit_at_8"] = bool(ranks)
        if ranks:
            measured["first_hit_rank"] = min(ranks)
    counts = turn.answer.counts()
    if counts.pointer_coverage is not None:
        measured["pointer_coverage"] = counts.pointer_coverage
    measured["pointers_close"] = counts.pointers_close
    measured["pointers_weak"] = counts.pointers_weak
    if meta.covered:
        if turn.pointers:
            on = sum(p.place in places for p in turn.pointers)
            measured["pointers_on_expected"] = on / len(turn.pointers)
        measured["places_pointed"] = len(places & {p.place for p in turn.pointers}) / len(places)
    else:
        measured["says_not_covered"] = turn.says_not_covered
    exact = [q for q in turn.quotes if q.exact]
    measured["quotes_exact"] = len(exact)
    measured["quotes_not_exact"] = len(turn.quotes) - len(exact)
    if exact:
        measured["quotes_verified"] = all(q.verified for q in exact)
    measured["unknown_handles"] = counts.unknown_handles
    if turn.model_requests is not None:
        measured["model_requests"] = turn.model_requests
    measured["tool_calls"] = turn.tool_calls
    measured["latency_s"] = turn.latency_s
    return measured


@dataclass
class AskingMetrics(Evaluator[str, Turn, QuestionMeta]):
    """`metrics` for pydantic-evals: its checks become assertions, its numbers scores."""

    def evaluate(self, ctx: EvaluatorContext[str, Turn, QuestionMeta]) -> EvaluatorOutput:
        """Measure one case's turn against its Question."""
        if ctx.metadata is None:
            raise ValueError(f"Question {ctx.name!r} has no kind or places")
        return metrics(ctx.output, ctx.metadata)


def _pointers(
    answer: Answer, chunks: Mapping[ChunkId, StoredChunk], blocks: Sequence[str]
) -> list[PointerCheck]:
    return [
        PointerCheck(
            sentence=i,
            support=p.support,
            similarity=p.similarity,
            chunk_id=p.chunk_id,
            place=Place.of(chunks[p.chunk_id].chunk),
            matched=blocks[p.block_id][p.start : p.end],
        )
        for i, sentence in enumerate(answer.sentences)
        for p in sentence.pointers
    ]


def _quotes(
    answer: Answer, chunks: Sequence[StoredChunk], blocks: Sequence[str]
) -> list[QuoteCheck]:
    checks: list[QuoteCheck] = []
    for q in answer.quotes:
        words = answer.text[q.start : q.end]
        if q.block_id is None or q.block_start is None or q.block_end is None:
            checks.append(QuoteCheck(words=words, exact=False, place=None, verified=None))
            continue
        chunk = next(
            (
                c.chunk
                for c in chunks
                if c.chunk.block == q.block_id
                and c.chunk.span.start <= q.block_start < c.chunk.span.end
            ),
            None,
        )
        printed = blocks[q.block_id][q.block_start : q.block_end]
        checks.append(
            QuoteCheck(
                words=words,
                exact=True,
                place=Place.of(chunk) if chunk else None,
                verified=_comparable(words) == _comparable(printed),
            )
        )
    return checks


def _comparable(text: str) -> str:
    """`text` as Quote matching compares it: ligatures spelled out, line-break hyphens dropped."""
    text = text.replace("\u00ad", "")
    text = "".join(
        unicodedata.normalize("NFKC", c) if 0xFB00 <= ord(c) <= 0xFB06 else c for c in text
    )
    text = _HYPHEN_BREAK.sub("", text)
    return " ".join(text.split())
