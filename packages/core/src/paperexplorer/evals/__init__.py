"""The eval of asking the Seed (design-asking.md, Eval): its Questions, runs and summaries.

`run_eval` asks every Question of a pydantic-evals dataset in fresh Conversations, as the reader
asks it (live: the whole Seed when it fits) and by retrieval, forced, and measures each turn:
hit@8, Pointer coverage and Support, Pointers on expected places, exact Quotes checked,
requests, tools and latency. `summarize` keeps what may be committed: no words of the Seed or
the Answers.
A judge's `Verdicts` on a run are committed beside its summary and posted as Langfuse scores.
"""

from paperexplorer.evals.models import (
    Hit,
    Kind,
    Mode,
    Place,
    PointerCheck,
    QuestionMeta,
    QuoteCheck,
    Turn,
)
from paperexplorer.evals.questions import Questions, load_questions, unknown_places
from paperexplorer.evals.runs import (
    BarCheck,
    EvalReport,
    EvalSummary,
    ModeSummary,
    QuestionRun,
    RunInfo,
    Written,
    eval_settings,
    new_run_id,
    render_answers,
    render_summary,
    run_eval,
    run_info,
    summarize,
    write_run,
)
from paperexplorer.evals.turns import HIT_K, AskingMetrics, ask_turn, metrics, says_not_covered
from paperexplorer.evals.verdicts import (
    Aptness,
    PointerReading,
    Verdict,
    Verdicts,
    load_verdicts,
    post_verdicts,
    unjudged,
)

__all__ = [
    "HIT_K",
    "Aptness",
    "AskingMetrics",
    "BarCheck",
    "EvalReport",
    "EvalSummary",
    "Hit",
    "Kind",
    "Mode",
    "ModeSummary",
    "Place",
    "PointerCheck",
    "PointerReading",
    "QuestionMeta",
    "QuestionRun",
    "Questions",
    "QuoteCheck",
    "RunInfo",
    "Turn",
    "Verdict",
    "Verdicts",
    "Written",
    "ask_turn",
    "eval_settings",
    "load_questions",
    "load_verdicts",
    "metrics",
    "new_run_id",
    "post_verdicts",
    "render_answers",
    "render_summary",
    "run_eval",
    "run_info",
    "says_not_covered",
    "summarize",
    "unjudged",
    "unknown_places",
    "write_run",
]
