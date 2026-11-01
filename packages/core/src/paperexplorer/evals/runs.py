"""An eval run: every Question asked in each mode, folded into its full report, and written.

The full report and its summary are rendered (`summaries`, `rendering`) and written: the report
under `data/`, the summary also beside the Question set, to be committed (design-asking.md, Eval).
"""

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from pydantic_evals import Dataset
from pydantic_evals.reporting import EvaluationReport, ReportCase

from paperexplorer.asking import PROMPT_VERSION, AskingContext
from paperexplorer.config import Settings
from paperexplorer.domain import Work
from paperexplorer.evals.models import (
    EvalReport,
    Kind,
    Metrics,
    Mode,
    QuestionMeta,
    QuestionRun,
    RunInfo,
    Turn,
)
from paperexplorer.evals.questions import Questions
from paperexplorer.evals.rendering import render_answers, render_summary
from paperexplorer.evals.summaries import EvalSummary
from paperexplorer.evals.turns import METRICS, AskingMetrics, ask_turn
from paperexplorer.store import WorkTexts

logger = logging.getLogger(__name__)


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
    repeat: int = 1,
) -> RunInfo:
    """What a run starting now runs against, asking each Question live `repeat` times."""
    return RunInfo(
        run_id=run_id or new_run_id(started, git_sha),
        started_at=started,
        git_sha=git_sha,
        seed=work.zotero_key or str(work.id),
        work_id=work.id,
        model=settings.chat_model,
        prompt_version=PROMPT_VERSION,
        num_ctx=settings.chat_num_ctx,
        temperature=settings.chat_temperature,
        answer_thinking=settings.chat_answer_thinking,
        repeat=repeat,
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
    modes: Sequence[Mode] = (Mode.LIVE, Mode.RETRIEVAL),
) -> EvalReport:
    """Ask every Question in each mode, one at a time, each in a fresh Conversation.

    The live mode asks every Question `info.repeat` times, a pass of all of them per repeat;
    the others once. Each turn is a `question` trace tagged `run:<run id>`; those that read the
    whole Seed are also tagged `whole-seed`. pydantic-evals runs the cases and measures them
    with `AskingMetrics`.
    """
    parsed = WorkTexts(ctx.conn).get(work.id)
    blocks = [b.text for b in parsed.blocks] if parsed else []
    dataset = Dataset[str, Turn, QuestionMeta](
        name=questions.name, cases=questions.cases, evaluators=[AskingMetrics()]
    )
    reports: dict[tuple[Mode, int], EvaluationReport[str, Turn, QuestionMeta]] = {}
    for mode in modes:
        asked = replace(ctx, tags=(*ctx.tags, f"run:{info.run_id}"), reading=mode.reading)
        repeats = info.repeat if mode is Mode.LIVE else 1
        for repeat in range(1, repeats + 1):
            name = mode.value if repeats == 1 else f"{mode.value} {repeat}"
            logger.info("Asking %d Questions (%s)", len(dataset.cases), name)

            async def task(
                question: str, asked: AskingContext = asked, repeat: int = repeat
            ) -> Turn:
                return await ask_turn(asked, work, question, blocks, repeat)

            reports[mode, repeat] = await dataset.evaluate(
                task,
                name=name,
                task_name=f"ask ({name})",
                max_concurrency=1,
                progress=False,
            )
    return EvalReport(run=info, modes=list(modes), questions=_questions(dataset, reports))


def _questions(
    dataset: Dataset[str, Turn, QuestionMeta],
    reports: dict[tuple[Mode, int], EvaluationReport[str, Turn, QuestionMeta]],
) -> list[QuestionRun]:
    runs: list[QuestionRun] = []
    for case in dataset.cases:
        name = case.name or ""
        meta = case.metadata or QuestionMeta(kind=Kind.UNCOVERED)
        turns: dict[Mode, list[Turn]] = {}
        measured: dict[Mode, dict[int, Metrics]] = {}
        failures: dict[Mode, dict[int, str]] = {}
        for (mode, repeat), report in reports.items():
            done = next((c for c in report.cases if c.name == name), None)
            failed = next((f for f in report.failures if f.name == name), None)
            if done is not None:
                turns.setdefault(mode, []).append(done.output)
                measured.setdefault(mode, {})[repeat] = _metrics(done)
                if done.evaluator_failures:
                    failures.setdefault(mode, {})[repeat] = done.evaluator_failures[0].error_message
            elif failed is not None:
                failures.setdefault(mode, {})[repeat] = failed.error_message
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
