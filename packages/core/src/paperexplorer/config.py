"""Typed application settings, read from the environment and `.env`."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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
    embedding_batch_size: int = Field(default=32, ge=1, description="Texts per embedding request.")
    # Provisional until the eval sets it: bge-m3 puts a paraphrase of a sentence well above 0.7
    # and another sentence on the same topic below it; too low would call weak Support close.
    support_threshold: float = Field(
        default=0.7,
        ge=0,
        le=1,
        description="Cosine an Answer sentence needs with a Chunk sentence for close Support.",
    )

    zotero_library_id: str | None = None
    zotero_library_type: Literal["user", "group"] = "user"
    zotero_api_key: SecretStr | None = None
    zotero_storage_dir: Path | None = Field(
        default=None,
        description="Local Zotero `storage/` folder, read-only; else Web API download.",
    )

    grobid_url: str = "http://localhost:8070"
    tessdata_dir: Path | None = Field(
        default=None,
        description="Tesseract `tessdata` folder for OCR of scans; else LiteParse downloads it.",
    )

    log_level: LogLevel = "INFO"
    log_format: LogFormat = "text"

    # One key pair serves the tower and Langfuse Cloud: the tower's project is initialised with
    # the cloud project's keys, so the two differ only by URL. No keys means tracing is off.
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_base_url: str | None = Field(
        default=None, description="The tower's Langfuse; traces go here whenever it answers."
    )
    langfuse_fallback_url: str = "https://cloud.langfuse.com"
    langfuse_environment: str = "development"
    langfuse_release: str | None = Field(
        default=None, description="Release on every trace; else the git SHA, where git is."
    )
    langfuse_trace_cap_gb: float = Field(
        default=6.0, description="The tower prunes its oldest traces once they hold this on disk."
    )

    @field_validator(
        "zotero_storage_dir",
        "tessdata_dir",
        "langfuse_public_key",
        "langfuse_secret_key",
        "langfuse_base_url",
        "langfuse_release",
        mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        """`.env.example` leaves optional values empty; empty means unset (for folders, not cwd)."""
        return None if value == "" else value

    @property
    def cache_dir(self) -> Path:
        """Disposable cache for downloaded PDFs and parser output."""
        return self.data_dir / "cache"
