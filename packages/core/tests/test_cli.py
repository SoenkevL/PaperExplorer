import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import psycopg
import pytest
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from typer.testing import CliRunner

from paperexplorer import asking, chunking, store
from paperexplorer.cli import StageName, app
from paperexplorer.config import Settings
from paperexplorer.embedding import FakeEmbedder, OpenAICompatibleEmbedder
from paperexplorer.parsing import Block, BlockKind, ParsedWork, Span, TextOrigin
from paperexplorer.pipeline import STAGES
from paperexplorer.tracing import Tracing


@pytest.fixture
def cli_env(scratch_database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PE_DATABASE_URL", scratch_database_url)
    # Never the real account from a local .env; these tests make no Zotero calls.
    monkeypatch.setenv("PE_ZOTERO_LIBRARY_ID", "0")
    monkeypatch.setenv("PE_ZOTERO_API_KEY", "unused")
    # Nor the real Langfuse: these tests send no traces.
    monkeypatch.setenv("PE_LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("PE_LANGFUSE_SECRET_KEY", "")


@pytest.mark.usefixtures("cli_env")
def test_migrate_brings_the_database_up_to_date() -> None:
    first = CliRunner().invoke(app, ["migrate"])
    second = CliRunner().invoke(app, ["migrate"])

    assert first.exit_code == 0, first.output
    assert "0002_works_text_citations.sql" in first.stdout
    assert second.exit_code == 0, second.output
    assert "up to date" in second.stdout


@pytest.mark.usefixtures("cli_env")
@pytest.mark.parametrize(
    ("stage", "first"), [("chunk", "parse"), ("embed", "chunk"), ("extract_citations", "parse")]
)
def test_run_says_what_is_missing_and_fails(stage: str, first: str) -> None:
    result = CliRunner().invoke(app, ["run", stage, "YAAC9XCX"])

    assert result.exit_code == 1
    assert f"run {first} first" in result.output


@pytest.mark.usefixtures("cli_env")
def test_run_rejects_an_unknown_stage() -> None:
    result = CliRunner().invoke(app, ["run", "summarise", "YAAC9XCX"])

    assert result.exit_code != 0


def test_run_takes_every_stage_of_the_chain() -> None:
    assert [s.value for s in StageName] == list(STAGES)


def test_trace_check_says_tracing_is_off_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PE_LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("PE_LANGFUSE_SECRET_KEY", "")

    result = CliRunner().invoke(app, ["trace-check"])

    assert result.exit_code == 1
    assert "Tracing is off" in result.output


MEASURED = "Sample entropy of the EEG was measured in every participant."
RESTED = "Participants rested quietly for ten minutes."


def _paragraph(text: str, heading_path: list[str], page: int) -> Block:
    return Block(
        kind=BlockKind.PARAGRAPH,
        heading_path=heading_path,
        page=page,
        boxes=[],
        text=text,
        sentences=[Span(start=0, end=len(text))],
    )


@pytest.fixture
def embedded_seed(
    cli_env: None, scratch_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Seed chunked and embedded by the fake, which the CLI then embeds Questions with."""
    monkeypatch.setenv("PE_LOG_LEVEL", "WARNING")  # stdout holds only what the command prints
    parsed = ParsedWork(
        text_origin=TextOrigin.PRINTED,
        blocks=[
            _paragraph(MEASURED, ["2 Methods", "2.3 EEG"], page=4),
            _paragraph(RESTED, ["3 Results"], page=5),
        ],
        references=[],
    )
    fake = FakeEmbedder("bge-m3")
    with psycopg.connect(scratch_database_url, autocommit=True) as conn:
        store.migrate(conn)
        work = store.Works(conn).ensure("YAAC9XCX")
        store.WorkTexts(conn).replace(work, parsed)
        chunks = store.Chunks(conn)
        chunks.replace(work, chunking.chunk(parsed))
        stored = chunks.for_work(work)
        vectors = fake.embed([s.chunk.text for s in stored])
        chunks.record_embeddings(
            work, fake.model, {s.id: v for s, v in zip(stored, vectors, strict=True)}
        )

    def from_settings(settings: Settings, tracing: Tracing) -> FakeEmbedder:
        return FakeEmbedder(settings.embedding_model)

    monkeypatch.setattr(OpenAICompatibleEmbedder, "from_settings", from_settings)


@pytest.mark.usefixtures("embedded_seed")
def test_search_lists_the_ranked_chunks_with_their_places_and_scores() -> None:
    result = CliRunner().invoke(app, ["search", "YAAC9XCX", "How was entropy measured?"])

    assert result.exit_code == 0, result.output
    header, first, text, second, *rest = result.stdout.splitlines()
    assert "2 Chunks" in header
    assert "bge-m3" in header
    assert first.split() == [
        "1", "rrf", "0.0328", "vector", "1", "0.471", "full", "text", "1", "0.061",
        "p4", "2", "Methods", ">", "2.3", "EEG",
    ]  # fmt: skip
    assert text.strip() == MEASURED
    assert second.split()[:10] == [
        "2", "rrf", "0.0161", "vector", "2", "0.000", "full", "text", "-", "-",
    ]  # fmt: skip
    assert "p5" in second
    assert "3 Results" in second
    assert rest == [f"     {RESTED}"]


@pytest.mark.usefixtures("embedded_seed")
def test_search_without_text_prints_no_words_of_the_chunks() -> None:
    result = CliRunner().invoke(app, ["search", "YAAC9XCX", "entropy", "-k", "1", "--no-text"])

    assert result.exit_code == 0, result.output
    assert len(result.stdout.splitlines()) == 2
    assert "1 Chunk for" in result.stdout
    assert "Sample" not in result.stdout


@pytest.mark.usefixtures("cli_env")
def test_search_says_when_the_work_is_unknown_and_fails() -> None:
    result = CliRunner().invoke(app, ["search", "NOSUCHKY", "entropy"])

    assert result.exit_code == 1
    assert "No Work NOSUCHKY" in result.output


def _scripted(*responses: str) -> Callable[[Settings], FunctionModel]:
    """A chat model that answers each request with the next of `responses`."""
    queue = list(responses)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield queue.pop(0)

    return lambda settings: FunctionModel(stream_function=stream, model_name=settings.chat_model)


@pytest.mark.usefixtures("embedded_seed")
def test_ask_streams_the_draft_then_lists_the_answers_pointers_and_quotes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = 'Sample entropy was measured "in every participant". [#1]'
    monkeypatch.setattr(asking, "chat_model", _scripted(draft))

    result = CliRunner().invoke(app, ["ask", "YAAC9XCX", "How was entropy measured?"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[:6] == [
        '→ search {"query": "How was entropy measured?"}',
        draft,
        "",
        "Answer",
        'Sample entropy was measured "in every participant".',
        "",
    ]
    assert lines[6] == "Pointers"
    assert lines[7].split() == ["s1", "close", "0.91", "p4", "2", "Methods", ">", "2.3", "EEG"]
    assert lines[8].strip() == MEASURED
    assert lines[9:11] == ["Quotes", '  "in every participant"  exact  p4']
    assert lines[11].startswith("summary: sentences 1, substantive 1, pointer coverage 1.00,")
    assert "close 1, weak 0, quotes exact 1, not exact 0, unknown handles 0" in lines[11]
    assert "model requests 1, tool calls 0, latency " in lines[11]


@pytest.mark.usefixtures("embedded_seed")
def test_ask_marks_a_quote_that_is_not_the_authors_words(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asking, "chat_model", _scripted('They "measured it twice". [#1]'))

    result = CliRunner().invoke(app, ["ask", "YAAC9XCX", "How was entropy measured?"])

    assert result.exit_code == 0, result.output
    assert '  "measured it twice"  not exact' in result.stdout.splitlines()


@pytest.mark.usefixtures("cli_env")
def test_ask_says_when_the_work_is_unknown_and_fails() -> None:
    result = CliRunner().invoke(app, ["ask", "NOSUCHKY", "Why?"])

    assert result.exit_code == 1
    assert "No Work NOSUCHKY" in result.output


EVAL_QUESTIONS = """
name: cli-questions
cases:
  - name: method-entropy
    inputs: How was entropy measured?
    metadata:
      kind: method
      places:
        - heading_path: ["2 Methods", "2.3 EEG"]
          page: 4
  - name: uncovered-caffeine
    inputs: How does caffeine affect sleep?
    metadata:
      kind: uncovered
"""


@pytest.fixture
def eval_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The Question set in `tmp_path`, which is also the data folder the runs go into."""
    monkeypatch.setenv("PE_DATA_DIR", str(tmp_path / "data"))
    questions = tmp_path / "questions.yaml"
    questions.write_text(EVAL_QUESTIONS)
    return tmp_path


@pytest.mark.usefixtures("embedded_seed")
def test_eval_asks_every_question_in_both_modes_and_writes_the_run(
    monkeypatch: pytest.MonkeyPatch, eval_files: Path
) -> None:
    measured = "Sample entropy was measured in every participant. [#1]"
    uncovered = "The Seed does not say how caffeine affects sleep."
    monkeypatch.setattr(asking, "chat_model", _scripted(measured, uncovered, measured, uncovered))

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "YAAC9XCX",
            "--questions",
            str(eval_files / "questions.yaml"),
            "--summary-dir",
            str(eval_files / "evals" / "runs"),
            "--run-id",
            "test-run",
        ],
    )

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "eval YAAC9XCX: 2 Questions, retrieval and baseline, run test-run"
    assert "hit@8 1.00 (≥ 0.80): met" in lines
    assert "Pointer coverage 1.00 (≥ 0.80): met" in lines
    assert "Quotes marked exact that are not 0 (0): met" in lines
    assert f"full report: {eval_files / 'data' / 'eval' / 'test-run'}" in lines
    run_dir = eval_files / "data" / "eval" / "test-run"
    assert (run_dir / "report.json").is_file() and (run_dir / "answers.md").is_file()
    assert (eval_files / "evals" / "runs" / "test-run.md").is_file()
    summary = json.loads((eval_files / "evals" / "runs" / "test-run.json").read_text())
    assert summary["run"]["langfuse_environment"] == "eval"
    assert summary["run"]["seed"] == "YAAC9XCX"
    assert list(summary["modes"]) == ["retrieval", "baseline"]


@pytest.mark.usefixtures("embedded_seed")
def test_eval_without_the_baseline_asks_only_as_the_reader_does(
    monkeypatch: pytest.MonkeyPatch, eval_files: Path
) -> None:
    monkeypatch.setattr(asking, "chat_model", _scripted("Measured. [#1]"))

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "YAAC9XCX",
            "--questions",
            str(eval_files / "questions.yaml"),
            "--summary-dir",
            str(eval_files / "evals" / "runs"),
            "--no-baseline",
            "--only",
            "method-entropy",
        ],
    )

    assert result.exit_code == 0, result.output
    assert ": 1 Question, retrieval, run " in result.stdout.splitlines()[0]


@pytest.mark.usefixtures("embedded_seed")
def test_eval_refuses_places_the_seed_does_not_have(eval_files: Path) -> None:
    questions = eval_files / "questions.yaml"
    questions.write_text(EVAL_QUESTIONS.replace("page: 4", "page: 9"))

    result = CliRunner().invoke(app, ["eval", "YAAC9XCX", "--questions", str(questions)])

    assert result.exit_code == 1
    assert "method-entropy: p9 2 Methods > 2.3 EEG" in result.output


@pytest.mark.usefixtures("cli_env")
def test_eval_says_when_the_work_is_unknown_and_fails(eval_files: Path) -> None:
    questions = str(eval_files / "questions.yaml")

    result = CliRunner().invoke(app, ["eval", "NOSUCHKY", "--questions", questions])

    assert result.exit_code == 1
    assert "No Work NOSUCHKY" in result.output
