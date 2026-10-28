"""The Embedder over any OpenAI-compatible `/v1/embeddings` server; Ollama serves bge-m3."""

import logging
from collections.abc import Sequence
from typing import Self

import httpx
from pydantic import BaseModel, ValidationError

from paperexplorer.config import Settings
from paperexplorer.domain import Vector
from paperexplorer.tracing import Tracing

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    """The server failed, or did not answer with one Vector per text."""


class _Embedding(BaseModel):
    index: int
    embedding: Vector


class _Usage(BaseModel):
    prompt_tokens: int


class _Answer(BaseModel):
    data: list[_Embedding]
    usage: _Usage | None = None


class OpenAICompatibleEmbedder:
    """Texts go to `<base_url>/embeddings` in batches of `batch_size`, one request each."""

    def __init__(
        self, http: httpx.Client, model: str, *, batch_size: int, tracing: Tracing
    ) -> None:
        """`http` is based at the server's API root (`…/v1`) and sends the key."""
        self._http = http
        self._model = model
        self._batch_size = batch_size
        self._tracing = tracing

    @classmethod
    def from_settings(cls, settings: Settings, tracing: Tracing) -> Self:
        """The server and model named by the `PE_EMBEDDING_*` settings."""
        http = httpx.Client(
            base_url=settings.embedding_base_url,
            headers={"Authorization": f"Bearer {settings.embedding_api_key.get_secret_value()}"},
            # The first request after a while waits for the server to load the model.
            timeout=120.0,
        )
        return cls(
            http,
            settings.embedding_model,
            batch_size=settings.embedding_batch_size,
            tracing=tracing,
        )

    @property
    def model(self) -> str:
        """The model the server is asked for."""
        return self._model

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        """One Vector per text, in order; raises `EmbeddingError` if the server fails."""
        size = self._batch_size
        batches = [list(texts[i : i + size]) for i in range(0, len(texts), size)]
        vectors: list[Vector] = []
        for batch in batches:
            vectors += self._embed_batch(batch)
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[Vector]:
        with self._tracing.embedding(self._model, texts) as observation:
            vectors, usage = self._post(texts)
            dimensions = len(vectors[0]) if vectors else 0
            observation.update(
                output={"vectors": len(vectors), "dimensions": dimensions},
                usage=None if usage is None else {"input": usage.prompt_tokens},
            )
        return vectors

    def _post(self, texts: list[str]) -> tuple[list[Vector], _Usage | None]:
        response = self._http.post("/embeddings", json={"model": self._model, "input": texts})
        if response.is_error:
            raise EmbeddingError(
                f"{response.request.url} answered {response.status_code}: {_message(response)}"
            )
        try:
            answer = _Answer.model_validate_json(response.content)
        except ValidationError as e:
            raise EmbeddingError(f"{response.request.url} answered no embeddings: {e}") from e
        if sorted(d.index for d in answer.data) != list(range(len(texts))):
            raise EmbeddingError(
                f"{response.request.url} answered {len(answer.data)} embeddings"
                f" for {len(texts)} texts"
            )
        logger.debug("Embedded %d texts with %s", len(texts), self._model)
        return [d.embedding for d in sorted(answer.data, key=lambda d: d.index)], answer.usage


def _message(response: httpx.Response) -> str:
    """The error message an OpenAI-compatible server puts in its body, else the body itself."""
    try:
        return str(response.json()["error"]["message"])
    except ValueError, KeyError, TypeError:
        return response.text[:200]
