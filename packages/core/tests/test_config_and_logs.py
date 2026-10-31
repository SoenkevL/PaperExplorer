import json
import logging
import re
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from paperexplorer import __version__
from paperexplorer.cli import app
from paperexplorer.config import Settings
from paperexplorer.logs import configure_logging


def test_settings_read_pe_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PE_EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setenv("PE_ZOTERO_STORAGE_DIR", "/zotero/storage")

    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    assert settings.embedding_model == "nomic-embed-text"
    assert settings.zotero_storage_dir == Path("/zotero/storage")


def test_zotero_storage_dir_is_optional(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PE_ZOTERO_STORAGE_DIR", raising=False)

    assert Settings(_env_file=None).zotero_storage_dir is None  # pyright: ignore[reportCallIssue]


def test_an_optional_folder_left_empty_in_env_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: `.env.example` ships `PE_ZOTERO_STORAGE_DIR=` and `PE_TESSDATA_DIR=` empty,
    # which read as Path("."), so the Seed was looked for in the current folder.
    monkeypatch.setenv("PE_ZOTERO_STORAGE_DIR", "")
    monkeypatch.setenv("PE_TESSDATA_DIR", "")

    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]

    assert settings.zotero_storage_dir is None
    assert settings.tessdata_dir is None


def test_json_logging_emits_one_parseable_line(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "json")

    logging.getLogger("paperexplorer.test").warning("low confidence for %s", "ref 12")

    record = json.loads(capsys.readouterr().out.strip())
    assert record["level"] == "WARNING"
    assert record["logger"] == "paperexplorer.test"
    assert record["message"] == "low confidence for ref 12"


def test_cli_prints_version() -> None:
    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_the_support_threshold_is_a_cosine_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PE_SUPPORT_THRESHOLD", "0.62")
    assert Settings(_env_file=None).support_threshold == 0.62  # pyright: ignore[reportCallIssue]

    monkeypatch.setenv("PE_SUPPORT_THRESHOLD", "1.5")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def test_the_whole_seed_budget_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PE_WHOLE_SEED_MAX_TOKENS", "20000")

    assert Settings(_env_file=None).whole_seed_max_tokens == 20000  # pyright: ignore[reportCallIssue]


@pytest.mark.parametrize("budget", [32768, 40000])
def test_the_whole_seed_budget_must_stay_below_the_chat_context(budget: int) -> None:
    with pytest.raises(ValidationError, match="PE_WHOLE_SEED_MAX_TOKENS"):
        Settings(_env_file=None, chat_num_ctx=32768, whole_seed_max_tokens=budget)  # pyright: ignore[reportCallIssue]


def test_the_chat_samples_at_0_5_and_answers_without_thinking_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert (settings.chat_temperature, settings.chat_answer_thinking) == (0.5, False)

    monkeypatch.setenv("PE_CHAT_TEMPERATURE", "0.2")
    monkeypatch.setenv("PE_CHAT_ANSWER_THINKING", "true")
    settings = Settings(_env_file=None)  # pyright: ignore[reportCallIssue]
    assert (settings.chat_temperature, settings.chat_answer_thinking) == (0.2, True)

    monkeypatch.setenv("PE_CHAT_TEMPERATURE", "-1")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def test_every_setting_is_mirrored_in_the_env_example() -> None:
    example = (Path(__file__).parents[3] / ".env.example").read_text("utf-8")

    missing = [
        f"PE_{name.upper()}"
        for name in Settings.model_fields
        if not re.search(rf"^PE_{name.upper()}=", example, re.MULTILINE)
    ]

    assert missing == []
