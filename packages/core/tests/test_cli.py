import pytest
from typer.testing import CliRunner

from paperexplorer.cli import StageName, app
from paperexplorer.pipeline import STAGES


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
