"""A judge's verdicts on a run's Answers, read against the Seed (design-asking.md, Eval).

Committed beside the run's summary as `evals/runs/<run id>.verdicts.yaml`, in the judge's own
words: no words of the Seed or of the Answers. Each verdict is also two Langfuse scores on its
turn's trace, `judge.grounded` and `judge.correct`.
"""

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel

from paperexplorer.evals.models import Mode
from paperexplorer.evals.runs import EvalSummary
from paperexplorer.tracing import Tracing


class Aptness(StrEnum):
    """How well a Pointer's place bears out its sentence, as the judge read it."""

    APT = "apt"  # the place says what the sentence says
    PARTLY = "partly"  # it bears out part of the sentence, or a nearby sentence would do better
    NOT = "not"  # it does not bear the sentence out


class PointerReading(BaseModel):
    """The judge's reading of one Pointer: its sentence, its cosine, and how apt its place is."""

    sentence: int
    similarity: float
    apt: Aptness


class Verdict(BaseModel):
    """One turn judged: whether the Answer is grounded and correct, and why, in a short note."""

    question: str
    mode: Mode
    trace_id: str
    grounded: bool
    correct: bool
    note: str
    pointers: list[PointerReading] = []


class Verdicts(BaseModel):
    """Every turn of one run judged, with what the judge meant by grounded and correct."""

    run_id: str
    judge: str
    grounded: str
    correct: str
    verdicts: list[Verdict]

    def share(self, mode: Mode) -> float:
        """The share of the mode's turns judged both grounded and correct; a failed turn is not."""
        judged = [v for v in self.verdicts if v.mode is mode]
        return sum(v.grounded and v.correct for v in judged) / len(judged)


def load_verdicts(path: Path) -> Verdicts:
    """The verdicts in a YAML file."""
    return Verdicts.model_validate(yaml.safe_load(path.read_text("utf-8")))


def unjudged(verdicts: Verdicts, summary: EvalSummary) -> list[str]:
    """Each turn of the run with no verdict on its trace, as `<question> (<mode>)`."""
    judged = {(v.question, v.mode, v.trace_id) for v in verdicts.verdicts}
    return [
        f"{q.name} ({mode})"
        for q in summary.questions
        for mode, turn in q.turns.items()
        if (q.name, mode, turn.trace_id) not in judged
    ]


def post_verdicts(tracing: Tracing, verdicts: Verdicts) -> int:
    """Score each verdict's trace with `judge.grounded` and `judge.correct`; how many scores."""
    for v in verdicts.verdicts:
        tracing.score_trace(v.trace_id, "judge.grounded", v.grounded, comment=v.note)
        tracing.score_trace(v.trace_id, "judge.correct", v.correct, comment=v.note)
    return 2 * len(verdicts.verdicts)
