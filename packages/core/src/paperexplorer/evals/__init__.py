"""The eval of asking the Seed (design-asking.md, Eval): its Questions, runs and summaries.

`run_eval` asks every Question of a pydantic-evals dataset in fresh Conversations, as the live
agent does and as the baseline with the whole Seed in context, and measures each turn: hit@8,
Pointer coverage and Support, Pointers on expected places, exact Quotes checked, requests,
tools and latency. `summarize` keeps what may be committed: no words of the Seed or the Answers.
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

__all__ = [
    "HIT_K",
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
    "QuestionMeta",
    "QuestionRun",
    "Questions",
    "QuoteCheck",
    "RunInfo",
    "Turn",
    "Written",
    "ask_turn",
    "eval_settings",
    "load_questions",
    "metrics",
    "new_run_id",
    "render_answers",
    "render_summary",
    "run_eval",
    "run_info",
    "says_not_covered",
    "summarize",
    "unknown_places",
    "write_run",
]
