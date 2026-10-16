import json
import logging
from pathlib import Path

import pytest
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
