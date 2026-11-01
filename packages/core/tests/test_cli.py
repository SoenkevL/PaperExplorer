import json
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel
from typer.testing import CliRunner

from paperexplorer import chunking, store
from paperexplorer.cli import StageName, app
from paperexplorer.config import Settings
from paperexplorer.domain import Attachment, LibraryItem
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.library import InMemoryLibrary, ZoteroLibrary
from paperexplorer.parsing import Block, BlockKind, ParsedWork, Reference, Span, TextOrigin
from paperexplorer.pipeline import STAGES
from paperexplorer.wiring import Wiring


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


def _wired(*responses: str) -> Callable[[Settings], Wiring]:
    """The CLI's wiring: the fake Embedder, and a chat model answering each request with the
    next of `responses`."""
    queue = list(responses)

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield queue.pop(0)

    return lambda settings: Wiring.from_settings(
        settings,
        embedder=FakeEmbedder(settings.embedding_model),
        model=FunctionModel(stream_function=stream, model_name=settings.chat_model),
    )


@pytest.mark.usefixtures("embedded_seed")
def test_search_lists_the_ranked_chunks_with_their_places_and_scores() -> None:
    result = CliRunner().invoke(
        app, ["search", "YAAC9XCX", "How was entropy measured?"], obj=_wired()
    )

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
    result = CliRunner().invoke(
        app, ["search", "YAAC9XCX", "entropy", "-k", "1", "--no-text"], obj=_wired()
    )

    assert result.exit_code == 0, result.output
    assert len(result.stdout.splitlines()) == 2
    assert "1 Chunk for" in result.stdout
    assert "Sample" not in result.stdout


@pytest.mark.usefixtures("cli_env")
def test_search_says_when_the_work_is_unknown_and_fails() -> None:
    result = CliRunner().invoke(app, ["search", "NOSUCHKY", "entropy"])

    assert result.exit_code == 1
    assert "No Work NOSUCHKY" in result.output


@pytest.mark.usefixtures("embedded_seed")
def test_ask_streams_the_draft_then_lists_the_answers_pointers_and_quotes() -> None:
    draft = 'Sample entropy was measured "in every participant". [#1]'

    result = CliRunner().invoke(
        app, ["ask", "YAAC9XCX", "How was entropy measured?"], obj=_wired(draft)
    )

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    # The Seed fits the budget, so it is read whole: no search, and the draft comes first.
    assert lines[:5] == [
        draft,
        "",
        "Answer",
        'Sample entropy was measured "in every participant".',
        "",
    ]
    assert lines[5] == "Pointers"
    assert lines[6].split() == ["s1", "close", "0.91", "p4", "2", "Methods", ">", "2.3", "EEG"]
    assert lines[7].strip() == MEASURED
    assert lines[8:10] == ["Quotes", '  "in every participant"  exact  p4']
    assert lines[10].startswith("summary: whole_seed, sentences 1, substantive 1,")
    assert "close 1, weak 0, quotes exact 1, not exact 0, unknown handles 0" in lines[10]
    assert "model requests 1, tool calls 0, latency " in lines[10]


@pytest.mark.usefixtures("embedded_seed")
def test_ask_says_when_the_model_writes_its_answer_again_with_handles() -> None:
    first = "Sample entropy was measured in every participant."

    result = CliRunner().invoke(
        app, ["ask", "YAAC9XCX", "How was entropy measured?"], obj=_wired(first, f"{first} [#1]")
    )

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[:3] == [
        first,
        "→ redraft: the draft gave no handle, so it is written again",
        f"{first} [#1]",
    ]
    assert lines[-1].startswith("summary: whole_seed, redrafted, sentences 1,")
    assert "pointer coverage 1.00" in lines[-1]
    assert "model requests 2, tool calls 0, latency " in lines[-1]


@pytest.mark.usefixtures("embedded_seed")
def test_ask_searches_a_seed_over_the_whole_seed_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PE_WHOLE_SEED_MAX_TOKENS", "1")

    result = CliRunner().invoke(
        app,
        ["ask", "YAAC9XCX", "How was entropy measured?"],
        obj=_wired("Sample entropy was measured. [#1]"),
    )

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == '→ search {"query": "How was entropy measured?"}'
    assert lines[-1].startswith("summary: retrieval, sentences 1,")
    assert "model requests 1, tool calls 0, latency " in lines[-1]


@pytest.mark.usefixtures("embedded_seed")
def test_ask_marks_a_quote_that_is_not_the_authors_words() -> None:
    result = CliRunner().invoke(
        app,
        ["ask", "YAAC9XCX", "How was entropy measured?"],
        obj=_wired('They "measured it twice". [#1]'),
    )

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
def test_eval_asks_every_question_live_and_by_retrieval_and_writes_the_run(
    eval_files: Path,
) -> None:
    measured = "Sample entropy was measured in every participant. [#1]"
    uncovered = "The Seed does not say how caffeine affects sleep."

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
        obj=_wired(measured, uncovered, measured, uncovered),
    )

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "eval YAAC9XCX: 2 Questions, live and retrieval, run test-run"
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
    assert list(summary["modes"]) == ["live", "retrieval"]


@pytest.mark.usefixtures("embedded_seed")
def test_eval_without_retrieval_asks_only_as_the_reader_does(
    eval_files: Path,
) -> None:
    result = CliRunner().invoke(
        app,
        [
            "eval",
            "YAAC9XCX",
            "--questions",
            str(eval_files / "questions.yaml"),
            "--summary-dir",
            str(eval_files / "evals" / "runs"),
            "--no-retrieval",
            "--only",
            "method-entropy",
        ],
        obj=_wired("Measured. [#1]"),
    )

    assert result.exit_code == 0, result.output
    assert ": 1 Question, live, run " in result.stdout.splitlines()[0]


@pytest.mark.usefixtures("embedded_seed")
def test_eval_repeats_each_question_live_as_often_as_asked_and_retrieval_once(
    eval_files: Path,
) -> None:
    measured = "Sample entropy was measured in every participant. [#1]"

    result = CliRunner().invoke(
        app,
        [
            "eval",
            "YAAC9XCX",
            "--questions",
            str(eval_files / "questions.yaml"),
            "--summary-dir",
            str(eval_files / "evals" / "runs"),
            "--only",
            "method-entropy",
            "--repeat",
            "2",
            "--run-id",
            "test-run",
        ],
        obj=_wired(measured, measured, measured),
    )

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "eval YAAC9XCX: 1 Question, live 2 times and retrieval, run test-run"
    summary = json.loads((eval_files / "evals" / "runs" / "test-run.json").read_text())
    assert summary["run"]["repeat"] == 2
    assert (summary["modes"]["live"]["repeats"], summary["modes"]["retrieval"]["repeats"]) == (2, 1)
    assert [t["repeat"] for t in summary["questions"][0]["turns"]["live"]] == [1, 2]


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


ATTENTION = LibraryItem(
    key="ATTN2017", title="Attention Is All You Need", creators=["Vaswani, Ashish"], year=2017,
    doi="10.5555/attention", added=datetime(2020, 1, 1, tzinfo=UTC),
)  # fmt: skip
ATTENTION_COPY = ATTENTION.model_copy(
    update={
        "key": "ATTNCOPY",
        "pdf": Attachment(key="ATTPDF11", filename="a.pdf"),
        "added": datetime(2025, 1, 1, tzinfo=UTC),
    }
)


@pytest.fixture
def library(cli_env: None, monkeypatch: pytest.MonkeyPatch) -> InMemoryLibrary:
    """The Library every command reads, in memory instead of Zotero."""
    monkeypatch.setenv("PE_LOG_LEVEL", "WARNING")
    held = InMemoryLibrary(
        [LibraryItem(key="SEED1111", title="The Seed"), ATTENTION, ATTENTION_COPY], {}
    )
    monkeypatch.setattr(ZoteroLibrary, "from_settings", lambda settings: held)
    return held


def _works(url: str) -> dict[str, str | None]:
    with psycopg.connect(url) as conn:
        return {w.zotero_key or f"#{w.id}": w.title for w in store.Works(conn).list()}


def test_sync_makes_every_library_item_a_work(
    library: InMemoryLibrary, scratch_database_url: str
) -> None:
    first = CliRunner().invoke(app, ["sync"])
    library.put(LibraryItem(key="NEWW2026", title="Added later"))
    second = CliRunner().invoke(app, ["sync"])

    assert first.exit_code == 0, first.output
    assert "3 Library Items" in first.stdout
    assert "4 Library Items" in second.stdout  # `sync` always reads the whole Library
    assert set(_works(scratch_database_url)) == {"SEED1111", "ATTN2017", "ATTNCOPY", "NEWW2026"}


def test_duplicates_lists_each_works_duplicate_items_standing_item_first(
    library: InMemoryLibrary,
) -> None:
    CliRunner().invoke(app, ["sync"])

    listed = CliRunner().invoke(app, ["duplicates"])
    CliRunner().invoke(app, ["duplicates", "--stand", "ATTN2017"])
    let = CliRunner().invoke(app, ["duplicates"])

    assert listed.exit_code == 0, listed.output
    doi, first, second = listed.stdout.splitlines()[:3]
    assert "10.5555/attention" in doi
    assert first.split()[:2] == ["*", "ATTNCOPY"]  # the one with a PDF stands
    assert second.split()[0] == "ATTN2017"
    assert let.stdout.splitlines()[1].split()[:2] == ["*", "ATTN2017"]


def _resolved_seed(url: str) -> None:
    refs = [
        Reference(id="b0", text="Vaswani 2017. Attention is all you need.", page=9,
                  title="Attention is all you need", surnames=["Vaswani"], year="2017"),
        Reference(id="b1", text="Nobody 1999. Unknown.", page=9, title="Unknown things"),
    ]  # fmt: skip
    with psycopg.connect(url, autocommit=True) as conn:
        store.migrate(conn)
        seed = store.Works(conn).ensure("SEED1111")
        store.WorkTexts(conn).replace(
            seed, ParsedWork(text_origin=TextOrigin.PRINTED, blocks=[], references=refs)
        )
    result = CliRunner().invoke(app, ["run", "resolve", "SEED1111"])
    assert result.exit_code == 0, result.output


def test_a_merge_asks_first_then_merges_in_the_library_and_moves_the_resolutions(
    library: InMemoryLibrary, scratch_database_url: str
) -> None:
    _resolved_seed(scratch_database_url)

    declined = CliRunner().invoke(
        app, ["duplicates", "--merge", "ATTN2017", "ATTNCOPY"], input="n\n"
    )
    assert declined.exit_code == 1
    assert library.item("ATTNCOPY").key == "ATTNCOPY"

    merged = CliRunner().invoke(app, ["duplicates", "--merge", "ATTN2017", "ATTNCOPY"], input="y\n")

    assert merged.exit_code == 0, merged.output
    assert library.item("ATTN2017").pdf == ATTENTION_COPY.pdf
    assert "ATTNCOPY" not in _works(scratch_database_url)
    listed = CliRunner().invoke(app, ["resolutions", "SEED1111"])
    assert "ATTN2017" in listed.stdout.splitlines()[1]


def test_resolutions_lists_each_reference_with_its_state_and_work(
    library: InMemoryLibrary, scratch_database_url: str
) -> None:
    _resolved_seed(scratch_database_url)

    result = CliRunner().invoke(app, ["resolutions", "SEED1111"])
    low = CliRunner().invoke(app, ["resolutions", "SEED1111", "--low"])

    assert result.exit_code == 0, result.output
    header, first, second, *_ = result.stdout.splitlines()
    assert "2 References" in header
    assert first.split()[:3] == ["b0", "high", "ATTNCOPY"]
    assert "Vaswani 2017" in first
    assert second.split()[:2] == ["b1", "unchecked"]
    assert "1 high" in result.stdout and "1 unchecked" in result.stdout
    assert low.stdout.splitlines()[1:] == []


def test_resolutions_of_an_unknown_work_fail(library: InMemoryLibrary) -> None:
    result = CliRunner().invoke(app, ["resolutions", "NOPE2345"])

    assert result.exit_code == 1
    assert "NOPE2345" in result.output
