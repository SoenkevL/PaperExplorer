"""An eval run: every Question asked in each mode, its full report and its summary without text.

The full report quotes the Seed and holds the Answers, so it stays under `data/` (the Seed's
licence is unknown); the summary holds metrics, places, similarities and trace ids only, and is
committed (design-asking.md, Eval).
"""

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel
from pydantic_evals import Dataset
from pydantic_evals.reporting import EvaluationReport, ReportCase

from paperexplorer.asking import PROMPT_VERSION, AskingContext
from paperexplorer.config import Settings
from paperexplorer.domain import Support, Work, WorkId
from paperexplorer.evals.models import Kind, Mode, Place, QuestionMeta, Turn
from paperexplorer.evals.questions import Questions
from paperexplorer.evals.turns import METRICS, AskingMetrics, ask_turn
from paperexplorer.store import WorkTexts

logger = logging.getLogger(__name__)

type Metrics = dict[str, bool | int | float]

# design-asking.md (Eval): the bar the live mode must meet.
HIT_AT_8_BAR = 0.8
POINTER_COVERAGE_BAR = 0.8
# Pointer cosines are counted in these buckets: below the first edge, then 0.05 wide up to 1.
_EDGES = [0.5 + 0.05 * i for i in range(10)]


class RunInfo(BaseModel):
    """What a run ran against: Seed, commit, model, prompt, embeddings, threshold, Questions."""

    run_id: str
    started_at: datetime
    git_sha: str | None
    seed: str
    work_id: WorkId
    model: str
    prompt_version: str
    num_ctx: int
    embedding_model: str
    support_threshold: float
    questions_file: str
    questions_sha: str
    langfuse_environment: str
    tracing_host: str | None


class QuestionRun(BaseModel):
    """One Question of a run: its turn in each mode and what each measured (the full report)."""

    name: str
    kind: Kind
    question: str
    places: list[Place]
    turns: dict[Mode, Turn]
    metrics: dict[Mode, Metrics]
    failures: dict[Mode, str] = {}


class EvalReport(BaseModel):
    """A whole run, Answers and the Seed's words included: it stays under `data/`."""

    run: RunInfo
    modes: list[Mode]
    questions: list[QuestionRun]


class BarCheck(BaseModel):
    """One line of the design's bar: the value measured, the bar, and whether it is met.

    `met` is None for what this run does not measure (the judged Answers, stage 8b).
    """

    name: str
    value: int | float | None
    bar: str
    met: bool | None


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
    """A turn without words: its trace, metrics, Pointers and tools."""

    trace_id: str | None
    conversation_id: str
    failed: bool
    metrics: Metrics
    pointers: list[PointerSummary]
    tools: list[str]
    first_delta_s: float | None


class QuestionSummary(BaseModel):
    """A Question with its expected places, the forced search's places and each mode's turn."""

    name: str
    kind: Kind
    question: str
    places: list[Place]
    search: list[Place] | None
    turns: dict[Mode, TurnSummary]


class ModeSummary(BaseModel):
    """One mode's aggregates. Coverage, hit@8 and places count the covered Questions only."""

    questions: int
    answered: int
    failed: int
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


class EvalSummary(BaseModel):
    """A run without the Seed's or the Answers' words: what is committed."""

    run: RunInfo
    bar: list[BarCheck]
    modes: dict[Mode, ModeSummary]
    questions: list[QuestionSummary]


@dataclass(frozen=True)
class Written:
    """Where a run was written: its folder under `data/`, and the summary files to commit."""

    run_dir: Path
    committed: list[Path]


def eval_settings() -> Settings:
    """The settings, with traces going to Langfuse's `eval` environment whatever `.env` says."""
    return Settings(langfuse_environment="eval")


def new_run_id(started: datetime, git_sha: str | None) -> str:
    """A run's id: its start in UTC, then the commit, e.g. `20261029T140512Z-57a4348`."""
    stamp = started.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{git_sha}" if git_sha else stamp


def file_sha(path: Path) -> str:
    """The first 12 hex digits of a file's SHA-256, as the prompt's version is taken."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def run_info(
    settings: Settings,
    work: Work,
    questions_file: Path,
    *,
    embedding_model: str,
    git_sha: str | None,
    tracing_host: str | None,
    started: datetime,
    run_id: str | None = None,
) -> RunInfo:
    """What a run starting now runs against."""
    return RunInfo(
        run_id=run_id or new_run_id(started, git_sha),
        started_at=started,
        git_sha=git_sha,
        seed=work.zotero_key or str(work.id),
        work_id=work.id,
        model=settings.chat_model,
        prompt_version=PROMPT_VERSION,
        num_ctx=settings.chat_num_ctx,
        embedding_model=embedding_model,
        support_threshold=settings.support_threshold,
        questions_file=questions_file.name,
        questions_sha=file_sha(questions_file),
        langfuse_environment=settings.langfuse_environment,
        tracing_host=tracing_host,
    )


async def run_eval(
    ctx: AskingContext,
    work: Work,
    questions: Questions,
    info: RunInfo,
    modes: Sequence[Mode] = (Mode.RETRIEVAL, Mode.BASELINE),
) -> EvalReport:
    """Ask every Question in each mode, one at a time, each in a fresh Conversation.

    Each turn is a `question` trace tagged `run:<run id>`; the baseline's are also tagged
    `whole-seed`. pydantic-evals runs the cases and measures them with `AskingMetrics`.
    """
    parsed = WorkTexts(ctx.conn).get(work.id)
    blocks = [b.text for b in parsed.blocks] if parsed else []
    dataset = Dataset[str, Turn, QuestionMeta](
        name=questions.name, cases=questions.cases, evaluators=[AskingMetrics()]
    )
    reports: dict[Mode, EvaluationReport[str, Turn, QuestionMeta]] = {}
    for mode in modes:
        asked = replace(
            ctx, tags=(*ctx.tags, f"run:{info.run_id}"), whole_seed=mode is Mode.BASELINE
        )
        logger.info("Asking %d Questions (%s)", len(dataset.cases), mode.value)

        async def task(question: str, asked: AskingContext = asked) -> Turn:
            return await ask_turn(asked, work, question, blocks)

        reports[mode] = await dataset.evaluate(
            task,
            name=mode.value,
            task_name=f"ask ({mode.value})",
            max_concurrency=1,
            progress=False,
        )
    return EvalReport(run=info, modes=list(modes), questions=_questions(dataset, reports))


def _questions(
    dataset: Dataset[str, Turn, QuestionMeta],
    reports: dict[Mode, EvaluationReport[str, Turn, QuestionMeta]],
) -> list[QuestionRun]:
    runs: list[QuestionRun] = []
    for case in dataset.cases:
        name = case.name or ""
        meta = case.metadata or QuestionMeta(kind=Kind.UNCOVERED)
        turns: dict[Mode, Turn] = {}
        measured: dict[Mode, Metrics] = {}
        failures: dict[Mode, str] = {}
        for mode, report in reports.items():
            done = next((c for c in report.cases if c.name == name), None)
            failed = next((f for f in report.failures if f.name == name), None)
            if done is not None:
                turns[mode] = done.output
                measured[mode] = _metrics(done)
                if done.evaluator_failures:
                    failures[mode] = done.evaluator_failures[0].error_message
            elif failed is not None:
                failures[mode] = failed.error_message
        runs.append(
            QuestionRun(
                name=name,
                kind=meta.kind,
                question=case.inputs,
                places=meta.places,
                turns=turns,
                metrics=measured,
                failures=failures,
            )
        )
    return runs


def _metrics(case: ReportCase[str, Turn, QuestionMeta]) -> Metrics:
    """The evaluator's assertions and scores, in the order `metrics` gives them."""
    found: Metrics = {name: r.value for name, r in case.scores.items()}
    found |= {name: r.value for name, r in case.assertions.items()}
    return {name: found[name] for name in METRICS if name in found}


def summarize(report: EvalReport) -> EvalSummary:
    """The run without the Seed's or the Answers' words: metrics, places, cosines, trace ids."""
    modes = {mode: _mode(report, mode) for mode in report.modes}
    live = modes.get(Mode.RETRIEVAL)
    return EvalSummary(
        run=report.run,
        bar=_bar(live),
        modes=modes,
        questions=[_question(q) for q in report.questions],
    )


def _bar(live: ModeSummary | None) -> list[BarCheck]:
    hit = live.hit_at_8 if live else None
    coverage = live.pointer_coverage if live else None
    unverified = live.quotes_exact_unverified if live else None
    return [
        BarCheck(
            name="hit@8",
            value=hit,
            bar=f"≥ {HIT_AT_8_BAR:.2f}",
            met=None if hit is None else hit >= HIT_AT_8_BAR,
        ),
        BarCheck(
            name="Pointer coverage",
            value=coverage,
            bar=f"≥ {POINTER_COVERAGE_BAR:.2f}",
            met=None if coverage is None else coverage >= POINTER_COVERAGE_BAR,
        ),
        BarCheck(
            name="Quotes marked exact that are not",
            value=unverified,
            bar="0",
            met=None if unverified is None else unverified == 0,
        ),
        BarCheck(name="Answers judged grounded and correct", value=None, bar="≥ 80%", met=None),
    ]


def _mode(report: EvalReport, mode: Mode) -> ModeSummary:
    asked = [(q, q.turns[mode]) for q in report.questions if mode in q.turns]
    answered = [(q, t) for q, t in asked if t.answer is not None]
    covered = [q for q in report.questions if q.kind is not Kind.UNCOVERED]
    uncovered = [q for q in report.questions if q.kind is Kind.UNCOVERED]
    hits = [
        bool(q.metrics[mode]["hit_at_8"]) for q in covered if "hit_at_8" in q.metrics.get(mode, {})
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
    return ModeSummary(
        questions=len(report.questions),
        answered=len(answered),
        failed=len(report.questions) - len(answered),
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
    retrieval = q.turns.get(Mode.RETRIEVAL)
    return QuestionSummary(
        name=q.name,
        kind=q.kind,
        question=q.question,
        places=q.places,
        search=[h.place for h in retrieval.search] if retrieval and retrieval.search else None,
        turns={
            mode: TurnSummary(
                trace_id=t.trace_id,
                conversation_id=t.conversation_id,
                failed=t.answer is None,
                metrics=q.metrics.get(mode, {}),
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
                tools=t.tools,
                first_delta_s=t.first_delta_s,
            )
            for mode, t in q.turns.items()
        },
    )


def write_run(
    report: EvalReport, summary: EvalSummary, data_dir: Path, summary_dir: Path
) -> Written:
    """Write the full report under `data_dir/<run id>/` and the summary also to `summary_dir`."""
    run_id = report.run.run_id
    run_dir = data_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_json = summary.model_dump_json(indent=1)
    summary_md = render_summary(summary)
    (run_dir / "report.json").write_text(report.model_dump_json(indent=1), encoding="utf-8")
    (run_dir / "answers.md").write_text(render_answers(report), encoding="utf-8")
    (run_dir / "summary.json").write_text(summary_json, encoding="utf-8")
    (run_dir / "summary.md").write_text(summary_md, encoding="utf-8")
    summary_dir.mkdir(parents=True, exist_ok=True)
    committed = [summary_dir / f"{run_id}.json", summary_dir / f"{run_id}.md"]
    committed[0].write_text(summary_json, encoding="utf-8")
    committed[1].write_text(summary_md, encoding="utf-8")
    return Written(run_dir=run_dir, committed=committed)


def render_summary(summary: EvalSummary) -> str:
    """The summary as Markdown: the bar, the modes side by side, cosines, and each Question."""
    run = summary.run
    covered = sum(q.kind is not Kind.UNCOVERED for q in summary.questions)
    modes = list(summary.modes)
    lines = [
        f"# Eval run {run.run_id}",
        "",
        f"Asking the Seed ({run.seed}) its eval Questions (`docs/design-asking.md`, Eval). No"
        " words of the Seed or of the Answers are here: the full report stays in"
        f" `data/eval/{run.run_id}/` on the machine that ran it.",
        "",
        f"- Started {run.started_at:%Y-%m-%d %H:%M} UTC at commit {run.git_sha or '-'};"
        f" traces in Langfuse's `{run.langfuse_environment}` environment"
        f" ({run.tracing_host or 'tracing off'}), tagged `run:{run.run_id}`.",
        f"- Model `{run.model}` (num_ctx {run.num_ctx}), prompt version `{run.prompt_version}`,"
        f" embeddings `{run.embedding_model}`, Support threshold {run.support_threshold:.2f}.",
        f"- Questions `{run.questions_file}` (sha256 `{run.questions_sha}`):"
        f" {len(summary.questions)}, {covered} covered and"
        f" {len(summary.questions) - covered} uncovered.",
        "",
        "## The bar (retrieval, the live mode)",
        "",
        "| Check | Value | Bar | Met |",
        "|---|---|---|---|",
    ]
    for check in summary.bar:
        met = {True: "met", False: "NOT met", None: "stage 8b"}[check.met]
        if check.met is None and check.value is None and check.name == "hit@8":
            met = "not run"
        lines.append(f"| {check.name} | {_number(check.value)} | {check.bar} | {met} |")
    lines += [
        "",
        "hit@8 and Pointer coverage count the covered Questions: coverage pools the substantive"
        " sentences of their Answers. Quotes marked exact that are not: exact Quotes whose words"
        " differ from the printed text at their place, folded as Quote matching folds them.",
        "",
        "## The modes",
        "",
        f"| | {' | '.join(m.value for m in modes)} |",
    ]
    lines.append(f"|---|{'---|' * len(modes)}")
    rows: list[tuple[str, list[str]]] = [
        ("Answered", [f"{s.answered} of {s.questions}" for s in summary.modes.values()]),
        ("hit@8 (covered)", [_number(s.hit_at_8) for s in summary.modes.values()]),
        (
            "Pointer coverage (covered)",
            [_number(s.pointer_coverage) for s in summary.modes.values()],
        ),
        (
            "Pointers close / weak",
            [f"{s.pointers_close} / {s.pointers_weak}" for s in summary.modes.values()],
        ),
        (
            "Pointers on an expected place (covered)",
            [_number(s.pointers_on_expected) for s in summary.modes.values()],
        ),
        (
            "Quotes exact / not exact / exact but not verified",
            [
                f"{s.quotes_exact} / {s.quotes_not_exact} / {s.quotes_exact_unverified}"
                for s in summary.modes.values()
            ],
        ),
        (
            "Uncovered Questions said so",
            [f"{s.uncovered_said} of {s.uncovered}" for s in summary.modes.values()],
        ),
        (
            "Model requests (mean)",
            [_number(s.model_requests_mean, 1) for s in summary.modes.values()],
        ),
        ("Tool calls (mean)", [_number(s.tool_calls_mean, 1) for s in summary.modes.values()]),
        (
            "Latency mean / max (s)",
            [
                f"{_number(s.latency_s_mean, 1)} / {_number(s.latency_s_max, 1)}"
                for s in summary.modes.values()
            ],
        ),
    ]
    lines += [f"| {label} | {' | '.join(values)} |" for label, values in rows]
    lines += [
        "",
        "## Pointer cosines",
        "",
        "| Mode | n | min | p10 | p25 | median | p75 | p90 | max |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for mode, s in summary.modes.items():
        q = s.similarity.quantiles
        cells = [_number(q.get(k)) for k in ("min", "p10", "p25", "median", "p75", "p90", "max")]
        lines.append(f"| {mode.value} | {s.similarity.count} | {' | '.join(cells)} |")
    for mode, s in summary.modes.items():
        lines += [
            "",
            f"{mode.value}, by bucket: Support, and where the Pointers landed",
            "",
            "| Cosine | close | weak | on an expected place | elsewhere | uncovered Question |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {b.low:.2f}-{b.high:.2f} | {b.close} | {b.weak} | {b.on_expected}"
            f" | {b.elsewhere} | {b.uncovered} |"
            for b in s.similarity.buckets
            if b.close or b.weak
        ]
    lines += ["", "## Questions"]
    for mode in modes:
        lines += [
            "",
            f"### {mode.value}",
            "",
            "| Question | Kind | hit@8 | Coverage | Close / weak | On expected | Quotes exact"
            " / not | Requests | Tools | Latency (s) | Says not covered | Trace |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for question in summary.questions:
            turn = question.turns.get(mode)
            if turn is None:
                lines.append(f"| {question.name} | {question.kind.value} | not asked |")
                continue
            m = turn.metrics
            said = "-"
            if question.kind is Kind.UNCOVERED and "says_not_covered" in m:
                said = "yes" if m["says_not_covered"] else "NO"
            lines.append(
                f"| {question.name} | {question.kind.value}"
                f" | {_flag(m.get('hit_at_8'))}"
                f" | {_number(m.get('pointer_coverage'))}"
                f" | {m.get('pointers_close', '-')} / {m.get('pointers_weak', '-')}"
                f" | {_number(m.get('pointers_on_expected'))}"
                f" | {m.get('quotes_exact', '-')} / {m.get('quotes_not_exact', '-')}"
                f" | {m.get('model_requests', '-')} | {m.get('tool_calls', '-')}"
                f" | {_number(m.get('latency_s'), 1)} | {said}"
                f" | {'failed · ' if turn.failed else ''}`{turn.trace_id or '-'}` |"
            )
    lines += ["", "### The Questions", ""]
    lines += [
        f"- `{q.name}` ({q.kind.value}): {q.question}"
        + (f" Expects {'; '.join(str(p) for p in q.places)}." if q.places else "")
        for q in summary.questions
    ]
    return "\n".join(lines) + "\n"


def render_answers(report: EvalReport) -> str:
    """Every Answer of the run with its Pointers' places and Quotes, for reading against the Seed.

    It quotes the Seed: it stays under `data/`.
    """
    lines = [
        f"# Answers of eval run {report.run.run_id}",
        "",
        "Not for git: this quotes the Seed, whose licence is unknown.",
    ]
    for q in report.questions:
        lines += ["", f"## {q.name} ({q.kind.value})", "", f"Question: {q.question}"]
        if q.places:
            lines.append(f"Expected: {'; '.join(str(p) for p in q.places)}")
        for mode in report.modes:
            turn = q.turns.get(mode)
            lines += ["", f"### {mode.value}"]
            if turn is None:
                lines.append(f"Not asked: {q.failures.get(mode, 'no turn')}")
                continue
            lines.append(
                f"trace `{turn.trace_id or '-'}` · requests {turn.model_requests or '-'}"
                f" · tools {', '.join(turn.tools) or '-'} · {turn.latency_s:.1f} s"
            )
            if turn.search is not None:
                found = ", ".join(
                    f"{h.rank}{'*' if h.place in q.places else ''} {h.place}" for h in turn.search
                )
                lines.append(f"search (* expected): {found}")
            lines.append("")
            if turn.answer is None:
                lines.append(f"Failed: {turn.error}")
                continue
            lines.append(turn.answer.text)
            lines += ["", "Pointers:" if turn.pointers else "Pointers: none"]
            for p in turn.pointers:
                expected = " (expected)" if p.place in q.places else ""
                lines.append(
                    f"- s{p.sentence + 1} {p.support.value} {p.similarity:.2f} {p.place}{expected}:"
                    f" {p.matched}"
                )
            lines.append("Quotes:" if turn.quotes else "Quotes: none")
            for quote in turn.quotes:
                state = "not exact"
                if quote.exact:
                    state = f"exact at {quote.place}, " + (
                        "verified" if quote.verified else "NOT VERIFIED"
                    )
                lines.append(f'- "{quote.words}" {state}')
            measured = q.metrics.get(mode, {})
            lines.append("Metrics: " + ", ".join(f"{k} {v}" for k, v in measured.items()))
    return "\n".join(lines) + "\n"


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _quantile(ordered: Sequence[float], at: float) -> float:
    """The `at` quantile of sorted values, interpolated linearly between ranks."""
    position = at * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _number(value: float | int | bool | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return _flag(value)
    if isinstance(value, int):
        return str(value)
    return f"{value:.{digits}f}"


def _flag(value: float | int | bool | None) -> str:
    if value is None:
        return "-"
    return "yes" if value else "no"
