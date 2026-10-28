import psycopg
import pytest
from typer.testing import CliRunner

from paperexplorer import chunking, store
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
