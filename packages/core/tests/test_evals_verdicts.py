"""A judge's verdicts on an eval run: read, held to the run's summary, posted as scores."""

from pathlib import Path

import pytest

from paperexplorer.evals import (
    EvalSummary,
    Mode,
    Verdicts,
    load_verdicts,
    post_verdicts,
    unjudged,
)

from .conftest import in_memory_tracing

ROOT = Path(__file__).parents[3]
RUNS = ROOT / "evals" / "runs"
VERDICTS = """\
run_id: 20261029T191529Z-94e980b
judge: Claude, stage 8b
grounded: Every claim is borne out by the Seed.
correct: It answers the Question rightly per the Seed.
verdicts:
  - question: method-lzc-vs-shannon
    mode: retrieval
    trace_id: 5360989acf8be6dfafd59ee97e83ff8e
    grounded: true
    correct: true
    note: Both differences, each pointed at.
    pointers:
      - {sentence: 0, similarity: 0.9207, apt: apt}
      - {sentence: 2, similarity: 0.7643, apt: partly}
  - question: method-study-selection
    mode: retrieval
    trace_id: 28d3ebb52f7953b5af83089e763d8834
    grounded: false
    correct: false
    note: Failed at the request limit.
  - question: method-lzc-vs-shannon
    mode: baseline
    trace_id: c424915d193fc9be73521dcfd0e2b217
    grounded: false
    correct: true
    note: Right difference; one wrong claim about compression.
"""


@pytest.fixture
def verdicts(tmp_path: Path) -> Verdicts:
    path = tmp_path / "run.verdicts.yaml"
    path.write_text(VERDICTS)
    return load_verdicts(path)


def test_verdicts_load_with_the_pointers_read_against_the_seed(verdicts: Verdicts) -> None:
    first = verdicts.verdicts[0]
    assert (first.question, first.mode, first.grounded, first.correct) == (
        "method-lzc-vs-shannon",
        Mode.RETRIEVAL,
        True,
        True,
    )
    assert [(p.sentence, p.apt) for p in first.pointers] == [(0, "apt"), (2, "partly")]


def test_the_share_judged_grounded_and_correct_counts_failed_turns_as_neither(
    verdicts: Verdicts,
) -> None:
    assert verdicts.share(Mode.RETRIEVAL) == 0.5
    assert verdicts.share(Mode.BASELINE) == 0.0


def test_each_verdict_becomes_two_scores_on_its_trace(verdicts: Verdicts) -> None:
    tracing, _, scores = in_memory_tracing()

    posted = post_verdicts(tracing, verdicts)
    tracing.flush()

    assert posted == 6
    assert sorted((s["traceId"], s["name"], s["value"]) for s in scores) == [
        ("28d3ebb52f7953b5af83089e763d8834", "judge.correct", 0),
        ("28d3ebb52f7953b5af83089e763d8834", "judge.grounded", 0),
        ("5360989acf8be6dfafd59ee97e83ff8e", "judge.correct", 1),
        ("5360989acf8be6dfafd59ee97e83ff8e", "judge.grounded", 1),
        ("c424915d193fc9be73521dcfd0e2b217", "judge.correct", 1),
        ("c424915d193fc9be73521dcfd0e2b217", "judge.grounded", 0),
    ]
    assert {s["comment"] for s in scores if s["traceId"] == "28d3ebb52f7953b5af83089e763d8834"} == {
        "Failed at the request limit."
    }


@pytest.mark.parametrize(
    "verdicts_file", sorted(RUNS.glob("*.verdicts.yaml")), ids=lambda p: p.name
)
def test_a_committed_run_has_one_verdict_per_turn_on_that_turns_trace(verdicts_file: Path) -> None:
    verdicts = load_verdicts(verdicts_file)
    summary_file = RUNS / f"{verdicts.run_id}.json"
    summary = EvalSummary.model_validate_json(summary_file.read_text())

    assert unjudged(verdicts, summary) == []


def test_turns_without_a_verdict_or_on_another_trace_are_named(verdicts: Verdicts) -> None:
    summary = EvalSummary.model_validate_json((RUNS / f"{verdicts.run_id}.json").read_text())

    missing = unjudged(verdicts, summary)

    assert "method-lzc-vs-shannon (retrieval)" not in missing
    assert "measurement-table-1-studies (retrieval)" in missing
    assert len(missing) == 2 * len(summary.questions) - 3


def test_verdicts_on_repeated_turns_are_keyed_by_repeat_and_shared_per_repeat(
    verdicts: Verdicts,
) -> None:
    again = verdicts.verdicts[0].model_copy(
        update={"repeat": 2, "trace_id": "0" * 32, "grounded": False}
    )
    repeated = verdicts.model_copy(update={"verdicts": [*verdicts.verdicts, again]})

    assert repeated.share(Mode.RETRIEVAL) == 1 / 3
    assert repeated.share(Mode.RETRIEVAL, repeat=1) == 0.5
    assert repeated.share(Mode.RETRIEVAL, repeat=2) == 0.0
