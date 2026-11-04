"""The Provider switch: one setting and one key move the chat model and the Embedder together."""

import asyncio
import json
from typing import Any

import httpx
import httpx2
import pytest
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from paperexplorer.config import Settings
from paperexplorer.inference import ProviderError, chat_context, chat_model, embedder
from paperexplorer.tracing import Tracing


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


def _melious(**values: Any) -> Settings:
    return _settings(provider="melious", provider_api_key="sk-mel-test", **values)


def test_ollama_is_the_default_and_needs_no_key() -> None:
    settings = _settings(ollama_base_url="http://tower:11434/v1")

    model = chat_model(settings)

    assert model.model_name == "paperexplorer-chat"
    assert isinstance(model, OpenAIChatModel)
    assert str(model.client.base_url) == "http://tower:11434/v1/"
    assert embedder(settings, Tracing.off()).model == "bge-m3"
    assert chat_context(settings) == 73728


def test_melious_is_chosen_by_its_name_and_a_key() -> None:
    settings = _melious()

    model = chat_model(settings)

    assert model.model_name == "deepseek-v4-flash-0731"
    assert isinstance(model, OpenAIChatModel)
    assert str(model.client.base_url) == "https://api.melious.ai/v1/"
    assert model.client.api_key == "sk-mel-test"
    assert chat_context(settings) == 1_000_000


def test_melious_needs_a_key() -> None:
    with pytest.raises(ProviderError, match="PE_PROVIDER_API_KEY"):
        chat_model(_settings(provider="melious"))
    with pytest.raises(ProviderError, match="PE_PROVIDER_API_KEY"):
        embedder(_settings(provider="melious"), Tracing.off())


def test_ollama_is_never_sent_the_key_meant_for_another_provider() -> None:
    settings = _settings(provider="ollama", provider_api_key="sk-mel-test")

    model = chat_model(settings)

    assert isinstance(model, OpenAIChatModel)
    assert model.client.api_key == "ollama"


def test_an_unknown_provider_names_the_known_ones() -> None:
    with pytest.raises(ProviderError, match=r"melious, ollama"):
        chat_model(_settings(provider="nonesuch"))


def test_models_and_context_can_be_set_over_the_providers_own() -> None:
    settings = _melious(chat_model="gemma-4-31b", chat_num_ctx=256000, embedding_model="other")

    assert chat_model(settings).model_name == "gemma-4-31b"
    assert chat_context(settings) == 256000
    assert embedder(settings, Tracing.off()).model == "melious/other"


def test_a_whole_seed_budget_beyond_the_providers_context_is_refused() -> None:
    settings = _settings(whole_seed_max_tokens=80000)

    with pytest.raises(ProviderError, match="PE_WHOLE_SEED_MAX_TOKENS"):
        chat_context(settings)
    assert chat_context(_melious(whole_seed_max_tokens=80000)) == 1_000_000


def test_melious_is_asked_for_bge_m3_and_its_vectors_are_stored_apart_from_ollamas() -> None:
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    melious = embedder(_melious(), Tracing.off(), transport=httpx.MockTransport(answer))

    assert melious.embed(["a sentence"]) == [[1.0, 0.0]]
    assert melious.model == "melious/bge-m3"
    assert str(seen[0].url) == "https://api.melious.ai/v1/embeddings"
    assert seen[0].headers["authorization"] == "Bearer sk-mel-test"
    assert json.loads(seen[0].content)["model"] == "bge-m3"


def _stream(*deltas: dict[str, Any]) -> httpx2.Response:
    """A streamed chat answer, field for field as Ollama and Melious (probed 2026-11-03) send it."""
    frames = [
        {
            "id": "x",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        for delta in deltas
    ]
    body = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n"
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)


@pytest.mark.parametrize(
    ("settings", "field"),
    [
        (_settings(), "reasoning"),
        (_melious(), "reasoning_content"),
    ],
)
def test_thinking_goes_back_in_the_field_it_came_in_within_a_turn(
    settings: Settings, field: str
) -> None:
    """Under the wrong field, a Provider's thinking returned as `<think>` text in `content`."""
    requests: list[dict[str, Any]] = []

    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            function = {"name": "search", "arguments": "{}"}
            call = {"index": 0, "id": "call_1", "type": "function", "function": function}
            return _stream({"role": "assistant", field: "I should search."}, {"tool_calls": [call]})
        return _stream({"role": "assistant", "content": "The answer."})

    async def run() -> None:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(answer)) as http:
            agent = Agent(chat_model(settings, http=http))

            @agent.tool_plain
            def search() -> str:
                return "found"

            async with agent.run_stream_events("A Question") as events:
                async for _ in events:
                    pass

    asyncio.run(run())

    assert "strict" not in requests[0]["tools"][0]["function"]
    replayed = next(m for m in requests[1]["messages"] if m["role"] == "assistant")
    assert replayed[field] == "I should search."
    assert "<think>" not in (replayed.get("content") or "")
