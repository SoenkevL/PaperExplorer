"""A run's summary and its Markdown, from hand-built reports: no Postgres, no model."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from paperexplorer.asking import Reading
from paperexplorer.domain import ChunkId, Support, WorkId
from paperexplorer.evals import (
    EvalReport,
    EvalSummary,
    Hit,
    Kind,
    Mode,
    PointerCheck,
    QuestionMeta,
    QuestionRun,
    QuoteCheck,
    RunInfo,
    Turn,
    load_verdicts,
    metrics,
    unjudged,
)
from paperexplorer.evals.rendering import render_answers, render_summary
from paperexplorer.evals.summaries import WITHHELD_TOOL, summarize

from .eval_turns import DISCUSSION_PLACE, RECORDING_PLACE, make_pointer, make_turn

RUNS = Path(__file__).resolve().parents[3] / "evals" / "runs"
COVERED = QuestionMeta(kind=Kind.METHOD, places=[RECORDING_PLACE])
UNCOVERED = QuestionMeta(kind=Kind.UNCOVERED)
SAYS_NOT_COVERED = "The Seed does not say how caffeine affects sleep."
# Every sentence of the covered Answer pointed: two close Pointers on the expected place, a
# weak one elsewhere.
POINTED = [
    make_pointer(0, RECORDING_PLACE, Support.CLOSE, 0.82),
    make_pointer(1, DISCUSSION_PLACE, Support.WEAK, 0.55),
    make_pointer(2, RECORDING_PLACE, Support.CLOSE, 0.91),
]
QUOTE = QuoteCheck(words="each EEG recording", exact=True, place=RECORDING_PLACE, verified=True)
SEARCH = [
    Hit(rank=1, chunk_id=ChunkId(7), place=DISCUSSION_PLACE, rrf=0.03),
    Hit(rank=2, chunk_id=ChunkId(8), place=RECORDING_PLACE, rrf=0.02),
]


def info(repeat: int = 1) -> RunInfo:
    return RunInfo(
        run_id="20261029T120000Z-abc1234",
        started_at=datetime(2026, 10, 29, 12, tzinfo=UTC),
        git_sha="abc1234",
        seed="SEED0001",
        work_id=WorkId(1),
        model="paperexplorer-chat",
        prompt_version="0123456789ab",
        num_ctx=73728,
        repeat=repeat,
        embedding_model="fake-bag-of-words",
        support_threshold=0.7,
        questions_file="small-seed-questions.yaml",
        questions_sha="fedcba987654",
        langfuse_environment="eval",
        tracing_host=None,
    )


def live(repeat: int = 1, pointers: list[PointerCheck] = POINTED, **changes: object) -> Turn:
    """A covered live turn that read the Seed whole, so searched nothing."""
    turn = make_turn(
        mode=Mode.LIVE, repeat=repeat, reading=Reading.WHOLE_SEED, pointers=pointers, quotes=[QUOTE]
    )
    return turn.model_copy(update=changes)


def by_retrieval(**changes: object) -> Turn:
    """A covered turn by retrieval, its forced search finding the expected place second."""
    turn = make_turn(search=SEARCH, pointers=POINTED, quotes=[QUOTE])
    return turn.model_copy(update=changes)


def said(mode: Mode, reading: Reading, repeat: int = 1) -> Turn:
    """An uncovered turn whose Answer says the Seed does not cover it."""
    return make_turn(mode=mode, repeat=repeat, reading=reading, text=SAYS_NOT_COVERED, search=[])


def question(name: str, meta: QuestionMeta, turns: list[Turn]) -> QuestionRun:
    """A Question of the report with its turns, each measured as the run measures it."""
    by_mode: dict[Mode, list[Turn]] = {}
    for t in turns:
        by_mode.setdefault(t.mode, []).append(t)
    return QuestionRun(
        name=name,
        kind=meta.kind,
        question=f"The {name} Question?",
        places=meta.places,
        turns=by_mode,
        metrics={m: {t.repeat: metrics(t, meta) for t in ts} for m, ts in by_mode.items()},
    )


def report(covered: list[Turn], uncovered: list[Turn], repeat: int = 1) -> EvalReport:
    modes = list(dict.fromkeys(t.mode for t in covered + uncovered))
    return EvalReport(
        run=info(repeat),
        modes=modes,
        questions=[
            question("method-entropy", COVERED, covered),
            question("uncovered-caffeine", UNCOVERED, uncovered),
        ],
    )


def both_modes() -> EvalReport:
    return report(
        [live(), by_retrieval()],
        [said(Mode.LIVE, Reading.WHOLE_SEED), said(Mode.RETRIEVAL, Reading.RETRIEVAL)],
    )


# The committed summaries


def test_the_newest_committed_summary_renders_exactly_as_committed() -> None:
    summary = EvalSummary.model_validate_json((RUNS / "20261031T192037Z-f349d1e.json").read_text())

    assert render_summary(summary) == (RUNS / "20261031T192037Z-f349d1e.md").read_text()


@pytest.mark.parametrize("run_id", ["20261029T191529Z-94e980b", "20261029T202214Z-2b41752"])
def test_the_committed_summaries_of_runs_before_adr_0010_still_read(run_id: str) -> None:
    summary = EvalSummary.model_validate_json((RUNS / f"{run_id}.json").read_text())

    assert set(summary.modes) == {Mode.RETRIEVAL, Mode.BASELINE}
    assert all(t.reading is None for q in summary.questions for ts in q.turns.values() for t in ts)
    assert all(m.readings == {} for m in summary.modes.values())


def test_the_committed_summary_and_verdicts_of_one_pass_read_as_repeat_1() -> None:
    summary = EvalSummary.model_validate_json((RUNS / "20261031T140751Z-a758bbc.json").read_text())
    verdicts = load_verdicts(RUNS / "20261031T140751Z-a758bbc.verdicts.yaml")

    turns = [t for q in summary.questions for ts in q.turns.values() for t in ts]
    assert len(turns) == 44
    assert {(t.repeat, t.redrafted) for t in turns} == {(1, False)}
    assert {m.repeats for m in summary.modes.values()} == {1}
    assert (summary.run.repeat, summary.run.temperature, summary.run.answer_thinking) == (
        1,
        None,
        None,
    )
    assert {v.repeat for v in verdicts.verdicts} == {1}
    assert unjudged(verdicts, summary) == []


# The bar and the modes


def test_the_bar_is_the_live_modes_with_hit_at_8_from_the_forced_search() -> None:
    summary = summarize(both_modes())

    bar = {b.name: (b.value, b.met) for b in summary.bar}
    assert bar == {
        "hit@8": (1.0, True),
        "Pointer coverage": (1.0, True),
        "Quotes marked exact that are not": (0, True),
        "Answers judged grounded and correct": (None, None),
    }
    one = summary.modes[Mode.LIVE]
    assert (one.answered, one.failed, one.covered, one.uncovered, one.uncovered_said) == (
        2,
        0,
        1,
        1,
        1,
    )
    assert one.hit_at_8 is None
    assert one.readings == {Reading.WHOLE_SEED: 2}
    assert (one.pointers_close, one.pointers_weak) == (2, 1)
    assert one.pointers_on_expected == pytest.approx(2 / 3)
    assert one.similarity.count == 3
    assert [(b.low, b.close, b.weak) for b in one.similarity.buckets if b.close or b.weak] == [
        (0.55, 0, 1),
        (0.8, 1, 0),
        (0.9, 1, 0),
    ]
    markdown = render_summary(summary)
    assert "| hit@8 | 1.00 | ≥ 0.80 | met |" in markdown
    assert "| Reading (turns) | whole_seed 2 | retrieval 2 |" in markdown
    assert "| Pointers close / weak | 2 / 1 | 2 / 1 |" in markdown
    assert (
        "| method-entropy | method | 1 | retrieval | no | yes | 1.00 | 2 / 1 | 0.67 | 1 / 0 | 2"
        " | 1 | 12.5 | - | `t1` |"
    ) in markdown
    assert "| uncovered-caffeine | uncovered | 1 | whole_seed | no | - |" in markdown


def test_without_the_retrieval_mode_a_seed_read_whole_runs_no_hit_at_8() -> None:
    summary = summarize(report([live()], [said(Mode.LIVE, Reading.WHOLE_SEED)]))

    bar = {b.name: (b.value, b.met) for b in summary.bar}
    assert bar["hit@8"] == (None, None)
    assert bar["Pointer coverage"] == (1.0, True)
    assert "| hit@8 | - | ≥ 0.80 | not run |" in render_summary(summary)


def test_a_failed_turn_counts_as_failed_and_its_row_says_so() -> None:
    failed = live(answer=None, error="The model gave no Answer.", pointers=[], quotes=[])

    summary = summarize(report([failed], [said(Mode.LIVE, Reading.WHOLE_SEED)]))

    assert (summary.modes[Mode.LIVE].answered, summary.modes[Mode.LIVE].failed) == (1, 1)
    assert summary.questions[0].turns[Mode.LIVE][0].failed is True
    assert "| failed · `t1` |" in render_summary(summary)


def test_a_redrafted_turn_is_counted_and_marked() -> None:
    summary = summarize(
        report([live(redrafted=True), by_retrieval()], [said(Mode.LIVE, Reading.WHOLE_SEED)])
    )

    assert (summary.modes[Mode.LIVE].redrafted, summary.modes[Mode.RETRIEVAL].redrafted) == (1, 0)
    assert summary.questions[0].turns[Mode.LIVE][0].redrafted is True
    assert "| Redrafted (turns) | 1 | 0 |" in render_summary(summary)


# Repeats


def test_asked_more_than_once_the_bar_pools_the_repeats_and_gives_each_with_the_spread() -> None:
    # The second live repeat points its first sentence only: a third of its sentences.
    covered = [live(1), live(2, pointers=POINTED[:1]), live(3), by_retrieval()]
    uncovered = [said(Mode.LIVE, Reading.WHOLE_SEED, r) for r in (1, 2, 3)]
    uncovered.append(said(Mode.RETRIEVAL, Reading.RETRIEVAL))

    summary = summarize(report(covered, uncovered, repeat=3))

    one = summary.modes[Mode.LIVE]
    assert (one.repeats, one.answered, one.failed) == (3, 6, 0)
    assert summary.modes[Mode.RETRIEVAL].repeats == 1
    assert [(r.repeat, r.pointer_coverage) for r in one.by_repeat] == [
        (1, 1.0),
        (2, pytest.approx(1 / 3)),
        (3, 1.0),
    ]
    coverage = next(b for b in summary.bar if b.name == "Pointer coverage")
    assert (coverage.value, coverage.met) == (pytest.approx(7 / 9), False)
    assert coverage.spread == pytest.approx(2 / 3)
    hit = next(b for b in summary.bar if b.name == "hit@8")
    assert (hit.value, hit.per_repeat) == (1.0, [])
    assert [t.repeat for t in summary.questions[0].turns[Mode.LIVE]] == [1, 2, 3]
    markdown = render_summary(summary)
    assert (
        "| Pointer coverage | 0.78 | ≥ 0.80 | NOT met | 1.00 / 0.33 / 1.00 (spread 0.67) |"
        in markdown
    )
    assert "| Answered | 6 of 6 | 2 of 2 |" in markdown
    assert "Live asked 3 times per Question" in markdown


# No words of the Seed or the Answers


def test_the_summary_holds_no_words_of_the_seed_or_the_answers_but_the_answers_do() -> None:
    full = both_modes()
    answer = full.questions[0].turns[Mode.LIVE][0].answer
    assert answer is not None

    summary = summarize(full)

    committed = summary.model_dump_json() + render_summary(summary)
    for words in (answer.text, SAYS_NOT_COVERED, "(the Chunk sentence)", QUOTE.words):
        assert words not in committed
    assert "The method-entropy Question?" in committed
    answers = render_answers(full)
    for words in (answer.text, SAYS_NOT_COVERED, "(the Chunk sentence)", QUOTE.words):
        assert words in answers
    assert "search (* expected): 1 p3 3 Discussion, 2* p2 2 Methods > 2.1 Recording" in answers


def test_a_tool_name_not_shaped_like_one_is_withheld() -> None:
    named = "thought\nAnswer: Entropy rose in each volunteer. [#2]\n<channel|>Then the reader"
    turn = by_retrieval(tools=["read_section", named], tool_calls=2)

    summary = summarize(report([turn], [said(Mode.RETRIEVAL, Reading.RETRIEVAL)]))

    assert summary.questions[0].turns[Mode.RETRIEVAL][0].tools == ["read_section", WITHHELD_TOOL]
    assert "Entropy rose" not in summary.model_dump_json()
