"""A run's summary: what may be committed, without the Seed's or the Answers' words.

It holds metrics, places, similarities and trace ids only (design-asking.md, Eval). The live
mode may ask each Question several times, each a repeat of its own; the bar pools them.
"""

import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, field_validator

from paperexplorer.asking import Reading
from paperexplorer.domain import Support
from paperexplorer.evals.models import (
    EvalReport,
    Kind,
    Metrics,
    Mode,
    Place,
    QuestionRun,
    RunInfo,
    Turn,
)

# design-asking.md (Eval): the bar the live mode must meet.
HIT_AT_8_BAR = 0.8
POINTER_COVERAGE_BAR = 0.8
# Pointer cosines are counted in these buckets: below the first edge, then 0.05 wide up to 1.
_EDGES = [0.5 + 0.05 * i for i in range(10)]
# A tool's name is a word; the model once sent a call whose name was a drafted Answer
# (trace 448b0aa1), which would put its words in git.
_TOOL_NAME = re.compile(r"\w{1,40}", re.ASCII)
WITHHELD_TOOL = "(withheld: not shaped like a tool's name)"


class BarCheck(BaseModel):
    """One line of the design's bar: the value measured, the bar, and whether it is met.

    `met` is None for what this run does not measure (the judged Answers, stage 8b). `value`
    pools every turn of the mode; asked more than once, `per_repeat` gives each repeat's value
    and `spread` the largest less the smallest of them.
    """

    name: str
    value: int | float | None
    bar: str
    met: bool | None
    per_repeat: list[int | float | None] = []
    spread: float | None = None


class SimilarityBucket(BaseModel):
    """Pointers whose cosine lies in `[low, high)`, by Support and by where they landed."""

    low: float
    high: float
    close: int
    weak: int
    on_expected: int
    elsewhere: int
    uncovered: int


class Similarity(BaseModel):
    """The distribution of the Pointers' cosines, for setting the Support threshold."""

    count: int
    quantiles: dict[str, float]
    buckets: list[SimilarityBucket]


class PointerSummary(BaseModel):
    """A Pointer without words: sentence, Support, cosine, place, and if that place is expected."""

    sentence: int
    support: Support
    similarity: float
    place: Place
    on_expected: bool | None


class TurnSummary(BaseModel):
    """A turn without words: its repeat, trace, reading, metrics, Pointers and tools.

    A tool's name not shaped like one is withheld (`WITHHELD_TOOL`): it may hold model text.
    """

    repeat: int = 1  # runs before repeats asked each Question once
    trace_id: str | None
    conversation_id: str
    reading: Reading | None = None  # runs before ADR 0010 did not record it
    redrafted: bool = False
    failed: bool
    metrics: Metrics
    pointers: list[PointerSummary]
    tools: list[str]
    first_delta_s: float | None


class QuestionSummary(BaseModel):
    """A Question with its expected places, the forced search's places and each mode's turns."""

    name: str
    kind: Kind
    question: str
    places: list[Place]
    search: list[Place] | None
    turns: dict[Mode, list[TurnSummary]]

    @field_validator("turns", mode="before")
    @classmethod
    def _one_turn_is_a_list_of_one(cls, value: Any) -> Any:
        """Runs before repeats recorded one turn per mode, not a list."""
        if isinstance(value, dict):
            return {m: [t] if isinstance(t, dict) else t for m, t in value.items()}  # pyright: ignore[reportUnknownVariableType]
        return value


class RepeatSummary(BaseModel):
    """One repeat of a mode asked more than once: the values its bar is checked on."""

    repeat: int
    answered: int
    failed: int
    redrafted: int
    hit_at_8: float | None
    pointer_coverage: float | None
    quotes_exact_unverified: int


class ModeSummary(BaseModel):
    """One mode's aggregates over all its turns; coverage, hit@8 and places count covered ones.

    `repeats` is how many times each Question was asked in it, `by_repeat` each repeat's values
    when more than once. `readings` counts the mode's turns by how they read the Seed (none in
    runs before ADR 0010); `redrafted` the turns asked once more for their Answer's handles.
    """

    questions: int
    repeats: int = 1
    answered: int
    failed: int
    redrafted: int = 0
    readings: dict[Reading, int] = {}
    covered: int
    uncovered: int
    uncovered_said: int
    hit_at_8: float | None
    pointer_coverage: float | None
    pointers_close: int
    pointers_weak: int
    pointers_on_expected: float | None
    quotes_exact: int
    quotes_not_exact: int
    quotes_exact_unverified: int
    model_requests_mean: float | None
    tool_calls_mean: float | None
    latency_s_mean: float | None
    latency_s_max: float | None
    similarity: Similarity
    by_repeat: list[RepeatSummary] = []


class EvalSummary(BaseModel):
    """A run without the Seed's or the Answers' words: what is committed."""

    run: RunInfo
    bar: list[BarCheck]
    modes: dict[Mode, ModeSummary]
    questions: list[QuestionSummary]


def summarize(report: EvalReport) -> EvalSummary:
    """The run without the Seed's or the Answers' words: metrics, places, cosines, trace ids."""
    modes = {mode: _mode(report, mode) for mode in report.modes}
    return EvalSummary(
        run=report.run,
        bar=_bar(modes.get(Mode.LIVE), modes.get(Mode.RETRIEVAL)),
        modes=modes,
        questions=[_question(q) for q in report.questions],
    )


def _bar(live: ModeSummary | None, retrieval: ModeSummary | None) -> list[BarCheck]:
    """The bar, on the live mode's turns pooled; hit@8 is the forced search's, on the fallback.

    A live mode that read the Seed whole searched nothing, so hit@8 comes from the retrieval mode,
    asked once. Asked more than once, each live repeat's values stand beside the pooled ones.
    """
    searched = next((m for m in (live, retrieval) if m and m.hit_at_8 is not None), None)
    hit = searched.hit_at_8 if searched else None
    coverage = live.pointer_coverage if live else None
    unverified = live.quotes_exact_unverified if live else None
    repeats = live.by_repeat if live else []
    return [
        BarCheck(
            name="hit@8",
            value=hit,
            bar=f"≥ {HIT_AT_8_BAR:.2f}",
            met=None if hit is None else hit >= HIT_AT_8_BAR,
            **_per_repeat([r.hit_at_8 for r in searched.by_repeat] if searched else []),
        ),
        BarCheck(
            name="Pointer coverage",
            value=coverage,
            bar=f"≥ {POINTER_COVERAGE_BAR:.2f}",
            met=None if coverage is None else coverage >= POINTER_COVERAGE_BAR,
            **_per_repeat([r.pointer_coverage for r in repeats]),
        ),
        BarCheck(
            name="Quotes marked exact that are not",
            value=unverified,
            bar="0",
            met=None if unverified is None else unverified == 0,
            **_per_repeat([r.quotes_exact_unverified for r in repeats]),
        ),
        BarCheck(name="Answers judged grounded and correct", value=None, bar="≥ 80%", met=None),
    ]


def _per_repeat(values: Sequence[int | float | None]) -> dict[str, Any]:
    """Each repeat's value and their spread, the largest less the smallest; none for one pass."""
    known = [v for v in values if v is not None]
    spread = max(known) - min(known) if known else None
    return {"per_repeat": list(values), "spread": spread} if len(values) > 1 else {}


def _mode(report: EvalReport, mode: Mode) -> ModeSummary:
    """The mode's aggregates over all its turns, and each repeat's when asked more than once."""
    asked = [(q, t) for q in report.questions for t in q.turns.get(mode, [])]
    repeats = max((t.repeat for _, t in asked), default=1)
    summary = _aggregate(report, mode, asked, repeats)
    if repeats > 1:
        for repeat in range(1, repeats + 1):
            one = _aggregate(report, mode, [(q, t) for q, t in asked if t.repeat == repeat], 1)
            summary.by_repeat.append(
                RepeatSummary(
                    repeat=repeat,
                    answered=one.answered,
                    failed=one.failed,
                    redrafted=one.redrafted,
                    hit_at_8=one.hit_at_8,
                    pointer_coverage=one.pointer_coverage,
                    quotes_exact_unverified=one.quotes_exact_unverified,
                )
            )
    return summary


def _aggregate(
    report: EvalReport, mode: Mode, asked: Sequence[tuple[QuestionRun, Turn]], repeats: int
) -> ModeSummary:
    answered = [(q, t) for q, t in asked if t.answer is not None]
    covered = [q for q in report.questions if q.kind is not Kind.UNCOVERED]
    uncovered = [q for q in report.questions if q.kind is Kind.UNCOVERED]
    hits = [
        bool(m["hit_at_8"])
        for q, t in asked
        if q.kind is not Kind.UNCOVERED and "hit_at_8" in (m := q.measured(mode, t))
    ]
    substantive = [
        s
        for q, t in answered
        if q.kind is not Kind.UNCOVERED and t.answer is not None
        for s in t.answer.sentences
        if s.substantive
    ]
    expected_pointers = [
        p.place in q.places for q, t in answered if q.kind is not Kind.UNCOVERED for p in t.pointers
    ]
    pointers = [p for _, t in answered for p in t.pointers]
    quotes = [quote for _, t in answered for quote in t.quotes]
    requests = [t.model_requests for _, t in answered if t.model_requests is not None]
    latencies = [t.latency_s for _, t in asked]
    readings = Counter(t.reading for _, t in asked if t.reading is not None)
    return ModeSummary(
        questions=len(report.questions),
        repeats=repeats,
        answered=len(answered),
        failed=len(report.questions) * repeats - len(answered),
        redrafted=sum(t.redrafted for _, t in asked),
        readings={r: readings[r] for r in Reading if r in readings},
        covered=len(covered),
        uncovered=len(uncovered),
        uncovered_said=sum(t.says_not_covered for q, t in answered if q.kind is Kind.UNCOVERED),
        hit_at_8=_mean([float(h) for h in hits]),
        pointer_coverage=(
            sum(bool(s.pointers) for s in substantive) / len(substantive) if substantive else None
        ),
        pointers_close=sum(p.support is Support.CLOSE for p in pointers),
        pointers_weak=sum(p.support is Support.WEAK for p in pointers),
        pointers_on_expected=_mean([float(e) for e in expected_pointers]),
        quotes_exact=sum(q.exact for q in quotes),
        quotes_not_exact=sum(not q.exact for q in quotes),
        quotes_exact_unverified=sum(q.exact and q.verified is not True for q in quotes),
        model_requests_mean=_mean([float(r) for r in requests]),
        tool_calls_mean=_mean([float(t.tool_calls) for _, t in asked]),
        latency_s_mean=_mean(latencies),
        latency_s_max=max(latencies) if latencies else None,
        similarity=_similarity([(q, t) for q, t in answered]),
    )


def _similarity(answered: Sequence[tuple[QuestionRun, Turn]]) -> Similarity:
    cosines = sorted(p.similarity for _, t in answered for p in t.pointers)
    quantiles = (
        {
            name: round(_quantile(cosines, at), 4)
            for name, at in (
                ("min", 0.0),
                ("p10", 0.1),
                ("p25", 0.25),
                ("median", 0.5),
                ("p75", 0.75),
                ("p90", 0.9),
                ("max", 1.0),
            )
        }
        if cosines
        else {}
    )
    lows = [0.0, *_EDGES]
    highs = [*_EDGES, 1.0 + 1e-9]
    buckets: list[SimilarityBucket] = []
    for low, high in zip(lows, highs, strict=True):
        inside = [(q, p) for q, t in answered for p in t.pointers if low <= p.similarity < high]
        buckets.append(
            SimilarityBucket(
                low=round(low, 2),
                high=round(min(high, 1.0), 2),
                close=sum(p.support is Support.CLOSE for _, p in inside),
                weak=sum(p.support is Support.WEAK for _, p in inside),
                on_expected=sum(
                    q.kind is not Kind.UNCOVERED and p.place in q.places for q, p in inside
                ),
                elsewhere=sum(
                    q.kind is not Kind.UNCOVERED and p.place not in q.places for q, p in inside
                ),
                uncovered=sum(q.kind is Kind.UNCOVERED for q, _ in inside),
            )
        )
    return Similarity(count=len(cosines), quantiles=quantiles, buckets=buckets)


def _question(q: QuestionRun) -> QuestionSummary:
    found = next((t.search for ts in q.turns.values() for t in ts if t.search), None)
    return QuestionSummary(
        name=q.name,
        kind=q.kind,
        question=q.question,
        places=q.places,
        search=[h.place for h in found] if found else None,
        turns={mode: [_turn_summary(q, mode, t) for t in turns] for mode, turns in q.turns.items()},
    )


def _turn_summary(q: QuestionRun, mode: Mode, t: Turn) -> TurnSummary:
    return TurnSummary(
        repeat=t.repeat,
        trace_id=t.trace_id,
        conversation_id=t.conversation_id,
        reading=t.reading,
        redrafted=t.redrafted,
        failed=t.answer is None,
        metrics=q.measured(mode, t),
        pointers=[
            PointerSummary(
                sentence=p.sentence,
                support=p.support,
                similarity=round(p.similarity, 4),
                place=p.place,
                on_expected=None if q.kind is Kind.UNCOVERED else p.place in q.places,
            )
            for p in t.pointers
        ],
        tools=[n if _TOOL_NAME.fullmatch(n) else WITHHELD_TOOL for n in t.tools],
        first_delta_s=t.first_delta_s,
    )


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _quantile(ordered: Sequence[float], at: float) -> float:
    """The `at` quantile of sorted values, interpolated linearly between ranks."""
    position = at * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)
