"""The Provider: who runs the chat and embedding models. A new one is one entry in the table."""

import httpx
import httpx2
from pydantic import BaseModel, ConfigDict
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.settings import ModelSettings

from paperexplorer.config import Settings
from paperexplorer.embedding import Embedder, OpenAICompatibleEmbedder
from paperexplorer.tracing import Tracing

# The first request after a while waits for the server to load the model.
_EMBEDDING_TIMEOUT_S = 120.0


class ProviderError(ValueError):
    """`PE_PROVIDER` names no Provider, or the settings do not fit the one it names."""


class Provider(BaseModel):
    """What differs from one OpenAI-compatible Provider to another."""

    model_config = ConfigDict(frozen=True)

    base_url: str
    chat_model: str
    chat_context: int
    embedding_model: str
    # The message field the Provider's thinking arrives in, and must be sent back in.
    thinking_field: str
    # What goes in place of a key for a Provider that needs none; else `PE_PROVIDER_API_KEY`.
    no_key_placeholder: str | None = None


def _providers(settings: Settings) -> dict[str, Provider]:
    return {
        "ollama": Provider(
            base_url=settings.ollama_base_url,
            chat_model="paperexplorer-chat",  # the alias from scripts/setup-tower.sh
            chat_context=73728,  # its Modelfile's num_ctx
            embedding_model="bge-m3",
            thinking_field="reasoning",
            no_key_placeholder="ollama",
        ),
        # Probed 2026-11-03 (docs/research/2026-11-melious-api.md). Its bge-m3 shares Ollama's name,
        # and so its stored Vectors, because the two were measured to agree (ADR 0013). A Provider
        # whose model of that name has not been measured against them must name it differently.
        "melious": Provider(
            base_url="https://api.melious.ai/v1",
            chat_model="deepseek-v4-flash-0731",
            chat_context=1_000_000,
            embedding_model="bge-m3",
            thinking_field="reasoning_content",
        ),
    }


def _provider(settings: Settings) -> Provider:
    providers = _providers(settings)
    provider = providers.get(settings.provider)
    if provider is None:
        known = ", ".join(sorted(providers))
        raise ProviderError(f"PE_PROVIDER is {settings.provider!r}; it must be one of: {known}")
    return provider


def _key(settings: Settings, provider: Provider) -> str:
    # The key belongs to the hosted Provider: Ollama, often over plain HTTP, never gets it.
    if provider.no_key_placeholder is not None:
        return provider.no_key_placeholder
    if settings.provider_api_key is None:
        raise ProviderError(f"PE_PROVIDER {settings.provider!r} needs PE_PROVIDER_API_KEY")
    return settings.provider_api_key.get_secret_value()


def chat_context(settings: Settings) -> int:
    """The chat model's context in tokens; a whole Seed's budget must leave room within it."""
    context = settings.chat_num_ctx or _provider(settings).chat_context
    if settings.whole_seed_max_tokens >= context:
        raise ProviderError(
            f"PE_WHOLE_SEED_MAX_TOKENS ({settings.whole_seed_max_tokens}) must stay below the chat"
            f" context ({context}): the whole Seed would leave no room for the prompt, the"
            " Conversation and the Answer"
        )
    return context


def chat_model(settings: Settings, *, http: httpx2.AsyncClient | None = None) -> Model:
    """The chat model of `PE_PROVIDER`; `http` is for tests."""
    provider = _provider(settings)
    chat_context(settings)
    # Ollama's pydantic-ai profile also fits Melious: tool schemas without `strict`, and a profile
    # by the model's name. Only where thinking travels differs.
    # A stream silent for `chat_timeout_s` fails the turn, rather than at the client's 600 s.
    return OpenAIChatModel(
        settings.chat_model or provider.chat_model,
        provider=OllamaProvider(
            base_url=provider.base_url, api_key=_key(settings, provider), http_client=http
        ),
        profile=OpenAIModelProfile(openai_chat_thinking_field=provider.thinking_field),
        settings=ModelSettings(timeout=settings.chat_timeout_s),
    )


def embedder(
    settings: Settings, tracing: Tracing, *, transport: httpx.BaseTransport | None = None
) -> Embedder:
    """The Embedder of `PE_PROVIDER`; `transport` is for tests."""
    provider = _provider(settings)
    model = settings.embedding_model or provider.embedding_model
    http = httpx.Client(
        base_url=provider.base_url,
        headers={"Authorization": f"Bearer {_key(settings, provider)}"},
        timeout=_EMBEDDING_TIMEOUT_S,
        transport=transport,
    )
    return OpenAICompatibleEmbedder(
        http,
        model,
        batch_size=settings.embedding_batch_size,
        tracing=tracing,
    )
