"""Typed application settings, read from the environment and `.env`."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["text", "json"]


class Settings(BaseSettings):
    """All runtime configuration; every field maps to a `PE_*` environment variable."""

    model_config = SettingsConfigDict(
        env_prefix="PE_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql://paperexplorer:paperexplorer@localhost:5432/paperexplorer"
    data_dir: Path = Path("data")

    embedding_base_url: str = "http://localhost:11434/v1"
    embedding_model: str = "bge-m3"
    embedding_api_key: SecretStr = SecretStr("ollama")

    zotero_library_id: str | None = None
    zotero_library_type: Literal["user", "group"] = "user"
    zotero_api_key: SecretStr | None = None
    zotero_storage_dir: Path | None = Field(
        default=None,
        description="Local Zotero `storage/` folder, read-only; else Web API download.",
    )

    log_level: LogLevel = "INFO"
    log_format: LogFormat = "text"

    @property
    def cache_dir(self) -> Path:
        """Disposable cache for downloaded PDFs and parser output."""
        return self.data_dir / "cache"
