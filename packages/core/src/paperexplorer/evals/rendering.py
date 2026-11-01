"""A run as Markdown: its summary, to commit, and its Answers, which quote the Seed."""

from paperexplorer.evals.models import EvalReport, Kind, Mode, QuestionRun, Turn
from paperexplorer.evals.summaries import EvalSummary, QuestionSummary, TurnSummary


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
        f"- Model `{run.model}` (num_ctx {run.num_ctx}, temperature {_number(run.temperature)},"
        f" thinking on a request without tools {_on(run.answer_thinking)}), prompt version"
        f" `{run.prompt_version}`, embeddings `{run.embedding_model}`, Support threshold"
        f" {run.support_threshold:.2f}.",
        f"- Questions `{run.questions_file}` (sha256 `{run.questions_sha}`):"
        f" {len(summary.questions)}, {covered} covered and"
        f" {len(summary.questions) - covered} uncovered.",
    ]
    if run.repeat > 1:
        lines.append(
            f"- Live asked {run.repeat} times per Question, each in a fresh Conversation; the"
            " other modes once."
        )
    repeated = any(check.per_repeat for check in summary.bar)
    lines += [
        "",
        "## The bar (live, as the reader asks)",
        "",
        "| Check | Value | Bar | Met |" + (" Per repeat (spread) |" if repeated else ""),
        "|---|---|---|---|" + ("---|" if repeated else ""),
    ]
    for check in summary.bar:
        met = {True: "met", False: "NOT met", None: "to judge"}[check.met]
        if check.met is None and check.value is None and check.name == "hit@8":
            met = "not run"
        row = f"| {check.name} | {_number(check.value)} | {check.bar} | {met} |"
        if repeated:
            each = " / ".join(_number(v) for v in check.per_repeat) or "-"
            spread = f" (spread {_number(check.spread)})" if check.per_repeat else ""
            row += f" {each}{spread} |"
        lines.append(row)
    lines += [
        "",
        "hit@8 and Pointer coverage count the covered Questions: coverage pools the substantive"
        " sentences of their Answers. hit@8 is the forced search's, a check on the fallback: a"
        " live turn that read the Seed whole searched nothing, so it comes from the retrieval"
        " mode then. Quotes marked exact that are not: exact Quotes whose words differ from the"
        " printed text at their place, folded as Quote matching folds them. Asked more than"
        " once, a value pools every repeat's turns; the spread is the largest repeat's value"
        " less the smallest.",
        "",
        "## The modes",
        "",
        f"| | {' | '.join(m.value for m in modes)} |",
    ]
    lines.append(f"|---|{'---|' * len(modes)}")
    rows: list[tuple[str, list[str]]] = [
        (
            "Reading (turns)",
            [
                ", ".join(f"{r.value} {n}" for r, n in s.readings.items()) or "-"
                for s in summary.modes.values()
            ],
        ),
        ("Repeats", [str(s.repeats) for s in summary.modes.values()]),
        (
            "Answered",
            [f"{s.answered} of {s.questions * s.repeats}" for s in summary.modes.values()],
        ),
        ("Redrafted (turns)", [str(s.redrafted) for s in summary.modes.values()]),
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
            "| Question | Kind | Repeat | Reading | Redrafted | hit@8 | Coverage | Close / weak"
            " | On expected | Quotes exact / not | Requests | Tools | Latency (s)"
            " | Says not covered | Trace |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for question in summary.questions:
            turns = question.turns.get(mode, [])
            if not turns:
                lines.append(f"| {question.name} | {question.kind.value} | not asked |")
            for turn in turns:
                lines.append(_turn_row(question, turn))
    lines += ["", "### The Questions", ""]
    lines += [
        f"- `{q.name}` ({q.kind.value}): {q.question}"
        + (f" Expects {'; '.join(str(p) for p in q.places)}." if q.places else "")
        for q in summary.questions
    ]
    return "\n".join(lines) + "\n"


def _turn_row(question: QuestionSummary, turn: TurnSummary) -> str:
    """One turn's row in its mode's table of Questions."""
    m = turn.metrics
    said = "-"
    if question.kind is Kind.UNCOVERED and "says_not_covered" in m:
        said = "yes" if m["says_not_covered"] else "NO"
    return (
        f"| {question.name} | {question.kind.value} | {turn.repeat}"
        f" | {turn.reading.value if turn.reading else '-'} | {_flag(turn.redrafted)}"
        f" | {_flag(m.get('hit_at_8'))}"
        f" | {_number(m.get('pointer_coverage'))}"
        f" | {m.get('pointers_close', '-')} / {m.get('pointers_weak', '-')}"
        f" | {_number(m.get('pointers_on_expected'))}"
        f" | {m.get('quotes_exact', '-')} / {m.get('quotes_not_exact', '-')}"
        f" | {m.get('model_requests', '-')} | {m.get('tool_calls', '-')}"
        f" | {_number(m.get('latency_s'), 1)} | {said}"
        f" | {'failed · ' if turn.failed else ''}`{turn.trace_id or '-'}` |"
    )


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
            turns = q.turns.get(mode, [])
            for repeat, failure in q.failures.get(mode, {}).items():
                if all(t.repeat != repeat for t in turns):
                    lines += ["", f"### {mode.value} {repeat}", f"Not asked: {failure}"]
            if not turns and not q.failures.get(mode):
                lines += ["", f"### {mode.value}", "Not asked: no turn"]
            for turn in turns:
                lines += _answer_lines(q, mode, turn, repeated=len(turns) > 1)
    return "\n".join(lines) + "\n"


def _answer_lines(q: QuestionRun, mode: Mode, turn: Turn, *, repeated: bool) -> list[str]:
    """One turn's Answer with its Pointers' places, Quotes and metrics, under its heading."""
    lines = ["", f"### {mode.value} {turn.repeat}" if repeated else f"### {mode.value}"]
    lines.append(
        f"trace `{turn.trace_id or '-'}` · reading"
        f" {turn.reading.value if turn.reading else '-'}"
        f"{' · redrafted' if turn.redrafted else ''}"
        f" · requests {turn.model_requests or '-'}"
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
        return lines
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
            state = f"exact at {quote.place}, " + ("verified" if quote.verified else "NOT VERIFIED")
        lines.append(f'- "{quote.words}" {state}')
    measured = q.measured(mode, turn)
    lines.append("Metrics: " + ", ".join(f"{k} {v}" for k, v in measured.items()))
    return lines


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


def _on(value: bool | None) -> str:
    return "-" if value is None else "on" if value else "off"
