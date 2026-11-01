"""The eval of asking the Seed: its Question set, what one turn measures, a run and its summary.

The model is scripted with `FunctionModel`, which answers each Question with a draft whose
`{name}` fields become the handles of the small Seed's Chunks as the turn shows them.
"""

import asyncio
import json
import re
from collections import Counter
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import TupleRow
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from paperexplorer.asking import AskingContext, Reading
from paperexplorer.asking.agent import REDRAFT
from paperexplorer.config import Settings
from paperexplorer.domain import ChunkId, Support, Work
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.evals import (
    EvalReport,
    Hit,
    Kind,
    Mode,
    Place,
    QuestionMeta,
    QuoteCheck,
    RunInfo,
    eval_settings,
    load_questions,
    metrics,
    new_run_id,
    run_eval,
    run_info,
    says_not_covered,
    summarize,
    unknown_places,
    write_run,
)
from paperexplorer.store import Chunks
from paperexplorer.tracing import Tracing

from .conftest import ABSTRACT, DISCUSSION, INTRODUCTION, RECORDING, SESSIONS, in_memory_tracing
from .eval_turns import DISCUSSION_PLACE, RECORDING_PLACE, make_pointer, make_turn

Conn = psycopg.Connection[TupleRow]
QUESTION_SET = Path(__file__).resolve().parents[3] / "evals" / "seed-questions.yaml"
COVERED = "How was the entropy computed?"
UNCOVERED = "How does caffeine affect sleep?"
QUESTIONS_YAML = f"""
name: small-seed-questions
cases:
  - name: method-entropy
    inputs: {COVERED}
    metadata:
      kind: method
      places:
        - heading_path: ["2 Methods", "2.1 Recording"]
          page: 2
  - name: uncovered-caffeine
    inputs: {UNCOVERED}
    metadata:
      kind: uncovered
"""
# The scripted Answers: one that points at two Chunks and quotes the authors, one that says the
# Seed does not cover the Question.
COVERED_DRAFT = (
    'Entropy was computed from "each EEG recording". {recording} The sample was small. {discussion}'
)
UNCOVERED_DRAFT = "The Seed does not say how caffeine affects sleep."
CHUNK_NAMES = {"recording": RECORDING, "discussion": DISCUSSION}
_SHOWN = re.compile(r"^\[#(\d+)\] .+ · p\. \d+\n(.+)$", re.MULTILINE)


def scripted(drafts: Mapping[str, str]) -> FunctionModel:
    """A model answering each Question with its draft, `{name}` replaced by a Chunk's handle."""

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        requests = [m for m in messages if isinstance(m, ModelRequest)]
        # Reading the whole Seed, the Question is the last line of the request's closing turn.
        question = next(
            str(p.content).rsplit("\n", 1)[-1]
            for m in requests
            for p in m.parts
            if isinstance(p, UserPromptPart)
        )
        shown = "\n".join(
            [info.instructions or ""]
            + [str(p.content) for m in requests for p in m.parts if isinstance(p, ToolReturnPart)]
        )
        handles = {text: n for n, text in _SHOWN.findall(shown)}
        yield drafts[question].format(
            **{name: f"[#{handles[text]}]" for name, text in CHUNK_NAMES.items()}
        )

    return FunctionModel(stream_function=stream, model_name="paperexplorer-chat")


def context(conn: Conn, tracing: Tracing | None = None) -> AskingContext:
    return AskingContext(
        conn=conn,
        embedder=FakeEmbedder(),
        model=scripted({COVERED: COVERED_DRAFT, UNCOVERED: UNCOVERED_DRAFT}),
        settings=Settings(_env_file=None),  # pyright: ignore[reportCallIssue]
        tracing=tracing or Tracing.off(),
    )


def info(work: Work) -> RunInfo:
    return RunInfo(
        run_id="20261029T120000Z-abc1234",
        started_at=datetime(2026, 10, 29, 12, tzinfo=UTC),
        git_sha="abc1234",
        seed="SEED0001",
        work_id=work.id,
        model="paperexplorer-chat",
        prompt_version="0123456789ab",
        num_ctx=73728,
        embedding_model="fake-bag-of-words",
        support_threshold=0.7,
        questions_file="small-seed-questions.yaml",
        questions_sha="fedcba987654",
        langfuse_environment="eval",
        tracing_host=None,
    )


@pytest.fixture
def questions_file(tmp_path: Path) -> Path:
    path = tmp_path / "small-seed-questions.yaml"
    path.write_text(QUESTIONS_YAML)
    return path


def run(conn: Conn, work: Work, questions_file: Path, tracing: Tracing | None = None) -> EvalReport:
    questions = load_questions(questions_file)
    return asyncio.run(run_eval(context(conn, tracing), work, questions, info(work)))


# The Question set


def test_the_seed_question_set_has_about_twenty_questions_of_every_kind() -> None:
    questions = load_questions(QUESTION_SET)

    kinds = Counter(c.metadata.kind for c in questions.cases if c.metadata)
    assert 18 <= len(questions.cases) <= 25
    assert set(kinds) == set(Kind)
    assert kinds[Kind.UNCOVERED] >= 4
    for case in questions.cases:
        assert case.metadata is not None and case.name is not None
        assert case.name.startswith(case.metadata.kind.value)
        assert bool(case.metadata.places) == (case.metadata.kind is not Kind.UNCOVERED)
    assert not any("caffeine" in c.inputs.casefold() for c in questions.cases)


@pytest.mark.parametrize(
    ("kind", "places"),
    [(Kind.UNCOVERED, [RECORDING_PLACE]), (Kind.METHOD, [])],
)
def test_only_a_covered_question_lists_places(kind: Kind, places: list[Place]) -> None:
    with pytest.raises(ValidationError):
        QuestionMeta(kind=kind, places=places)


def test_unknown_places_are_those_where_no_chunk_of_the_seed_sits(
    store_db: Conn, small_seed: Work, tmp_path: Path
) -> None:
    path = tmp_path / "questions.yaml"
    path.write_text(
        QUESTIONS_YAML.replace(
            '["2 Methods", "2.1 Recording"]\n          page: 2', '["2 Methods"]\n          page: 7'
        )
    )

    unknown = unknown_places(load_questions(path), Chunks(store_db).for_work(small_seed.id))

    assert unknown == [("method-entropy", Place(heading_path=("2 Methods",), page=7))]
    assert unknown_places(load_questions(QUESTION_SET), []) != []


# What one turn measures


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("The Seed does not say how psilocybin is regulated in the European Union.", True),
        ("The Seed doesn\u2019t mention heart rate or blood pressure.", True),
        ("The review does not directly address dosing. It covers entropy.", True),
        ("This is not covered by the Seed.", True),
        ("The Seed gives no information on software.", True),
        ("Entropy was computed from each EEG recording.", False),
        ("Entropy did not increase under methamphetamine (Murray et al., 2024).", False),
        ("Murray et al. did not report a correlation with intensity.", False),
    ],
)
def test_an_answer_says_the_seed_does_not_cover_a_question(text: str, said: bool) -> None:
    assert says_not_covered(text) is said


def test_a_covered_turn_measures_hit_at_8_pointers_on_expected_places_and_quotes() -> None:
    elsewhere = Place(heading_path=("1 Introduction",), page=1)
    meta = QuestionMeta(kind=Kind.METHOD, places=[RECORDING_PLACE, DISCUSSION_PLACE])
    turn = make_turn(
        search=[
            Hit(rank=1, chunk_id=ChunkId(7), place=elsewhere, rrf=0.03),
            Hit(rank=2, chunk_id=ChunkId(8), place=RECORDING_PLACE, rrf=0.02),
        ],
        pointers=[
            make_pointer(0, RECORDING_PLACE, Support.CLOSE, 0.82),
            make_pointer(1, elsewhere, Support.WEAK, 0.55),
        ],
        quotes=[
            QuoteCheck(words="w", exact=True, place=RECORDING_PLACE, verified=True),
            QuoteCheck(words="x", exact=False, place=None, verified=None),
        ],
    )

    measured = metrics(turn, meta)

    assert measured == {
        "answered": True,
        "hit_at_8": True,
        "first_hit_rank": 2,
        "pointer_coverage": pytest.approx(2 / 3),
        "pointers_close": 1,
        "pointers_weak": 1,
        "pointers_on_expected": 0.5,
        "places_pointed": 0.5,
        "quotes_exact": 1,
        "quotes_not_exact": 1,
        "quotes_verified": True,
        "unknown_handles": 0,
        "model_requests": 2,
        "tool_calls": 1,
        "latency_s": 12.5,
    }


def test_a_search_that_misses_every_expected_place_is_no_hit() -> None:
    meta = QuestionMeta(kind=Kind.METHOD, places=[RECORDING_PLACE])
    turn = make_turn(search=[Hit(rank=1, chunk_id=ChunkId(9), place=DISCUSSION_PLACE, rrf=0.03)])

    measured = metrics(turn, meta)

    assert measured["hit_at_8"] is False
    assert "first_hit_rank" not in measured


def test_an_uncovered_turn_measures_whether_it_says_so_and_no_places() -> None:
    meta = QuestionMeta(kind=Kind.UNCOVERED)
    turn = make_turn(text="The Seed does not say how caffeine affects sleep.", search=[])

    measured = metrics(turn, meta)

    assert measured["says_not_covered"] is True
    assert not {"hit_at_8", "pointers_on_expected", "places_pointed"} & set(measured)


def test_a_turn_that_read_the_whole_seed_has_no_search_so_no_hit_at_8() -> None:
    meta = QuestionMeta(kind=Kind.METHOD, places=[RECORDING_PLACE])

    measured = metrics(make_turn(mode=Mode.LIVE, reading=Reading.WHOLE_SEED, search=None), meta)

    assert "hit_at_8" not in measured


def test_a_failed_turn_measures_only_that_it_failed_and_its_latency() -> None:
    meta = QuestionMeta(kind=Kind.METHOD, places=[RECORDING_PLACE])

    measured = metrics(make_turn(error="The model did not answer within 6 requests."), meta)

    assert measured == {"answered": False, "tool_calls": 1, "latency_s": 12.5}


# A run and its summary


def test_a_run_asks_every_question_live_and_by_retrieval_each_in_a_fresh_conversation(
    store_db: Conn, small_seed: Work, questions_file: Path
) -> None:
    report = run(store_db, small_seed, questions_file)

    assert report.modes == [Mode.LIVE, Mode.RETRIEVAL]
    assert [q.name for q in report.questions] == ["method-entropy", "uncovered-caffeine"]
    covered, uncovered = report.questions
    [live], [retrieval] = covered.turns[Mode.LIVE], covered.turns[Mode.RETRIEVAL]
    # The small Seed fits the budget, so the live mode reads it whole and searches nothing.
    assert (live.reading, retrieval.reading) == (Reading.WHOLE_SEED, Reading.RETRIEVAL)
    assert retrieval.search is not None and len(retrieval.search) == 5
    assert live.search is None
    for turn in (live, retrieval):
        assert turn.answer is not None
        assert (
            turn.answer.text
            == 'Entropy was computed from "each EEG recording". The sample was small.'
        )
        assert [(p.sentence, p.place, p.support) for p in turn.pointers] == [
            (0, RECORDING_PLACE, Support.CLOSE),
            (1, DISCUSSION_PLACE, Support.CLOSE),
        ]
        assert turn.pointers[0].matched == RECORDING
        assert [(q.words, q.exact, q.place, q.verified) for q in turn.quotes] == [
            ("each EEG recording", True, RECORDING_PLACE, True)
        ]
        assert (turn.model_requests, turn.tool_calls, turn.tools) == (1, 0, [])
    assert covered.metrics[Mode.RETRIEVAL][1]["hit_at_8"] is True
    assert covered.metrics[Mode.RETRIEVAL][1]["pointers_on_expected"] == 0.5
    assert "hit_at_8" not in covered.metrics[Mode.LIVE][1]
    assert uncovered.metrics[Mode.LIVE][1]["says_not_covered"] is True
    conversations = [
        t.conversation_id for q in report.questions for ts in q.turns.values() for t in ts
    ]
    assert len(set(conversations)) == 4


def test_each_turn_is_a_question_trace_tagged_with_the_run_and_named_in_the_report(
    store_db: Conn, small_seed: Work, questions_file: Path
) -> None:
    tracing, exporter, _ = in_memory_tracing()

    report = run(store_db, small_seed, questions_file, tracing)
    tracing.flush()

    roots = [s for s in exporter.get_finished_spans() if s.name == "question"]
    tags = {
        format(s.context.trace_id, "032x"): (s.attributes or {})["langfuse.trace.tags"]
        for s in roots
        if s.context
    }
    run_tag = "run:20261029T120000Z-abc1234"
    turns = [t for q in report.questions for ts in q.turns.values() for t in ts]
    assert {t.trace_id for t in turns} == set(tags)
    for t in turns:
        assert t.trace_id is not None
        expected = ("asking", "work:SEED0001", run_tag)
        if t.reading is Reading.WHOLE_SEED:
            expected = ("asking", "work:SEED0001", "whole-seed", run_tag)
        assert tags[t.trace_id] == expected
    assert {t.mode for t in turns if t.reading is Reading.WHOLE_SEED} == {Mode.LIVE}


def test_the_summary_carries_metrics_and_trace_ids_but_no_seed_or_answer_text(
    store_db: Conn, small_seed: Work, questions_file: Path, tmp_path: Path
) -> None:
    report = run(store_db, small_seed, questions_file)
    summary = summarize(report)

    written = write_run(report, summary, tmp_path / "data" / "eval", tmp_path / "evals" / "runs")

    run_dir = tmp_path / "data" / "eval" / "20261029T120000Z-abc1234"
    assert sorted(p.name for p in run_dir.iterdir()) == [
        "answers.md",
        "report.json",
        "summary.json",
        "summary.md",
    ]
    committed = sorted(p.relative_to(tmp_path) for p in written.committed)
    assert committed == [
        Path("evals/runs/20261029T120000Z-abc1234.json"),
        Path("evals/runs/20261029T120000Z-abc1234.md"),
    ]
    full = (run_dir / "report.json").read_text() + (run_dir / "answers.md").read_text()
    assert "each EEG recording" in full and RECORDING in full
    seed_text = [ABSTRACT, INTRODUCTION, RECORDING, SESSIONS, DISCUSSION]
    answer_text = [
        "Entropy was computed",
        "each EEG recording",
        "The sample was small",
        "caffeine affects",
    ]
    for path in written.committed:
        text = path.read_text()
        for words in seed_text + answer_text:
            assert words not in text, f"{words!r} is in {path.name}"
    loaded = json.loads(written.committed[0].read_text())
    assert loaded["run"]["run_id"] == "20261029T120000Z-abc1234"
    [covered, uncovered] = loaded["questions"]
    assert covered["question"] == COVERED and uncovered["question"] == UNCOVERED
    assert covered["turns"]["retrieval"][0]["trace_id"] is None
    assert covered["turns"]["live"][0]["reading"] == "whole_seed"
    assert covered["turns"]["retrieval"][0]["reading"] == "retrieval"
    assert [p["similarity"] for p in covered["turns"]["retrieval"][0]["pointers"]] == [
        pytest.approx(p.similarity, abs=1e-4)
        for p in report.questions[0].turns[Mode.RETRIEVAL][0].pointers
    ]


def test_regression_eval_fcb19996a0a8c2aca5129fb9fe75b646_a_turn_with_no_answer_has_failed(
    store_db: Conn, small_seed: Work, questions_file: Path
) -> None:
    """Eval run 20261029T191529Z-94e980b, trace fcb19996a0a8c2aca5129fb9fe75b646 (retrieval).

    An Answer that was only gemma's `<|tool_response>` counted as answered. It is no Answer.
    """
    model = scripted({COVERED: "<|tool_response>", UNCOVERED: UNCOVERED_DRAFT})
    ctx = replace(context(store_db), model=model)
    questions = load_questions(questions_file)

    report = asyncio.run(run_eval(ctx, small_seed, questions, info(small_seed), [Mode.LIVE]))

    covered = report.questions[0]
    [turn] = covered.turns[Mode.LIVE]
    assert (turn.answer, turn.error) == (None, "The model gave no Answer.")
    assert covered.metrics[Mode.LIVE][1]["answered"] is False
    assert summarize(report).modes[Mode.LIVE].failed == 1


def test_a_run_id_is_its_start_in_utc_and_the_commit() -> None:
    started = datetime(2026, 10, 29, 14, 5, 12, tzinfo=UTC)

    assert new_run_id(started, "57a4348") == "20261029T140512Z-57a4348"
    assert new_run_id(started, None) == "20261029T140512Z"


def test_an_eval_traces_to_the_eval_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PE_LANGFUSE_ENVIRONMENT", "development")

    assert eval_settings().langfuse_environment == "eval"


def test_regression_eval_448b0aa1b3b3490eb24f1a310c12392f_a_tool_name_with_answer_text_is_withheld(
    store_db: Conn, small_seed: Work, questions_file: Path, tmp_path: Path
) -> None:
    """Eval run 20261031T140751Z-a758bbc, trace 448b0aa1b3b3490eb24f1a310c12392f (retrieval).

    The model called a tool whose name was a drafted Answer, a control token and its monologue
    included, and the summary to commit recorded the name as the turn's tool: words of an
    Answer in git. A name not shaped like a tool's is withheld.
    """
    named = "thought\nAnswer: Entropy rose in each volunteer. [#2]\n<channel|>Then the reader"
    asked = 0

    async def stream(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        nonlocal asked
        asked += 1
        if asked == 1:
            yield {0: DeltaToolCall(named, "{}", tool_call_id="call-1")}
        else:
            yield "Entropy was computed from each EEG recording."

    ctx = replace(
        context(store_db),
        model=FunctionModel(stream_function=stream, model_name="paperexplorer-chat"),
    )
    questions = load_questions(questions_file)
    questions.cases = questions.cases[:1]

    report = asyncio.run(run_eval(ctx, small_seed, questions, info(small_seed), [Mode.RETRIEVAL]))
    written = write_run(report, summarize(report), tmp_path / "data", tmp_path / "runs")

    for path in written.committed:
        text = path.read_text()
        assert "Entropy rose" not in text and "channel" not in text, path.name
    assert "(withheld" in written.committed[0].read_text()


# Repeats


def test_a_run_asks_each_question_live_as_often_as_it_repeats_and_by_retrieval_once(
    store_db: Conn, small_seed: Work, questions_file: Path
) -> None:
    questions = load_questions(questions_file)
    repeated = info(small_seed).model_copy(update={"repeat": 3})

    report = asyncio.run(run_eval(context(store_db), small_seed, questions, repeated))

    covered, _ = report.questions
    assert [t.repeat for t in covered.turns[Mode.LIVE]] == [1, 2, 3]
    assert [t.repeat for t in covered.turns[Mode.RETRIEVAL]] == [1]
    assert sorted(covered.metrics[Mode.LIVE]) == [1, 2, 3]
    conversations = [
        t.conversation_id for q in report.questions for ts in q.turns.values() for t in ts
    ]
    assert len(set(conversations)) == 2 * (3 + 1)


def test_a_turn_records_that_it_was_redrafted_and_the_request_it_took(
    store_db: Conn, small_seed: Work, questions_file: Path
) -> None:
    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        last = messages[-1]
        asked_again = isinstance(last, ModelRequest) and any(
            isinstance(p, UserPromptPart) and str(p.content) == REDRAFT for p in last.parts
        )
        recording = "Entropy was computed from each EEG recording."
        yield f"{recording} [#3]" if asked_again else recording

    ctx = replace(
        context(store_db),
        model=FunctionModel(stream_function=stream, model_name="paperexplorer-chat"),
    )
    questions = load_questions(questions_file)
    questions.cases = questions.cases[:1]

    report = asyncio.run(run_eval(ctx, small_seed, questions, info(small_seed), [Mode.LIVE]))

    [turn] = report.questions[0].turns[Mode.LIVE]
    assert (turn.redrafted, turn.model_requests) == (True, 2)
    assert turn.answer is not None and turn.pointers


def test_a_run_records_the_chat_settings_and_how_often_live_repeats(
    small_seed: Work, questions_file: Path
) -> None:
    settings = Settings(_env_file=None, chat_temperature=0.3)  # pyright: ignore[reportCallIssue]

    recorded = run_info(
        settings,
        small_seed,
        questions_file,
        embedding_model="fake-bag-of-words",
        git_sha="abc1234",
        tracing_host=None,
        started=datetime(2026, 10, 31, 12, tzinfo=UTC),
        repeat=3,
    )

    assert (recorded.temperature, recorded.answer_thinking, recorded.repeat) == (0.3, False, 3)
