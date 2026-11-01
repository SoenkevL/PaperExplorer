"""Typed application settings, read from the environment and `.env`."""

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
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
    # Set from the eval of 2026-10-29 (docs/eval-2026-10-29.md): of 168 Pointers at or above 0.7,
    # 2 sat at a place that does not bear their sentence out (both of one invented sentence); of
    # 20 below it, 7. 0.75 would make 37 apt or partly apt ones weak to catch those 2.
    support_threshold: float = Field(
        default=0.7,
        ge=0,
        le=1,
        description="Cosine an Answer sentence needs with a Chunk sentence for close Support.",
    )

    # The answering agent's chat model, behind any OpenAI-compatible endpoint (Ollama by default).
    chat_base_url: str = "http://localhost:11434/v1"
    chat_model: str = Field(
        default="paperexplorer-chat", description="Ollama alias from scripts/setup-tower.sh."
    )
    chat_api_key: SecretStr = SecretStr("ollama")
    chat_num_ctx: int = Field(
        default=73728,
        ge=1,
        description="The chat model's context in tokens, as its Modelfile sets it; on every trace.",
    )
    # A Seed this small is read whole, in context, rather than searched (ADR 0010). Counted in
    # bge-m3 tokens, the sum of its Chunks'; the rest of the chat context is left for gemma
    # counting more tokens than bge-m3, the system prompt, the Conversation, the model's thinking
    # and its Answer. The Seed of the eval of 2026-10-29 is about 18k.
    whole_seed_max_tokens: int = Field(
        default=40000,
        ge=0,
        description="A Seed of at most this many tokens is answered from whole, else searched.",
    )
    # Ollama sends nothing while it loads the model and reads the prompt (about 30 s for the
    # whole Seed, cold); a stream silent much longer has stalled (eval 2026-10-29).
    chat_timeout_s: float = Field(
        default=120,
        gt=0,
        description="Seconds a chat request may wait for its stream's next bytes.",
    )
    # Sent with every chat request, so each trace records it; left to Ollama, it was unset and
    # one pass of the eval could not tell a change from noise (docs/eval-2026-10-31.md).
    chat_temperature: float = Field(
        default=0.5, ge=0, le=2, description="The chat model's sampling temperature."
    )
    # A request that offers no tools must answer; thinking there is how chat-template tokens and
    # monologue reached Answers (eval 2026-10-29). gemma-4 thinks only on or off.
    chat_answer_thinking: bool = Field(
        default=False,
        description="Whether the model thinks before answering on a request that offers no tools.",
    )
    # The API holds Conversations in memory (a restart clears them) and evicts the least recently
    # used beyond this many, and any left unused for this long.
    conversations_max: int = Field(
        default=50, ge=1, description="Conversations the API holds in memory at most."
    )
    conversation_idle_minutes: float = Field(
        default=120, gt=0, description="The API forgets a Conversation unused for this long."
    )

    zotero_library_id: str | None = None
    zotero_library_type: Literal["user", "group"] = "user"
    zotero_api_key: SecretStr | None = None
    zotero_storage_dir: Path | None = Field(
        default=None,
        description="Local Zotero `storage/` folder, read-only; else Web API download.",
    )

    # The registries a Registry Check asks (design-resolution.md). Without these the limiters
    # run at the keyless and public rates; filling them changes no code.
    crossref_mailto: str | None = Field(
        default=None, description="An email for Crossref's polite pool (3 list queries a second)."
    )
    openalex_api_key: SecretStr | None = Field(
        default=None, description="OpenAlex API key: 1,000 title searches a day instead of 100."
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
        "crossref_mailto",
        "openalex_api_key",
        mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, value: object) -> object:
        """`.env.example` leaves optional values empty; empty means unset (for folders, not cwd)."""
        return None if value == "" else value

    @model_validator(mode="after")
    def _whole_seed_fits_the_context(self) -> Self:
        if self.whole_seed_max_tokens >= self.chat_num_ctx:
            raise ValueError(
                f"PE_WHOLE_SEED_MAX_TOKENS ({self.whole_seed_max_tokens}) must stay below"
                f" PE_CHAT_NUM_CTX ({self.chat_num_ctx}): the whole Seed would leave no room for"
                " the prompt, the Conversation and the Answer"
            )
        return self

    @property
    def cache_dir(self) -> Path:
        """Disposable cache for downloaded PDFs and parser output."""
        return self.data_dir / "cache"
