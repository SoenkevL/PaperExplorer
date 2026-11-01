"""The adapters a process works with, built once from Settings: tracing, Embedder, chat model."""

from dataclasses import dataclass
from typing import Self

import psycopg
from psycopg.rows import TupleRow
from pydantic_ai.models import Model

from paperexplorer.asking import AskingContext, Reading, chat_model
from paperexplorer.config import Settings
from paperexplorer.embedding import Embedder, OpenAICompatibleEmbedder
from paperexplorer.tracing import Tracing, configure_tracing


@dataclass(frozen=True)
class Wiring:
    """Tracing, the Embedder and the chat model, with the Settings they were built from."""

    settings: Settings
    tracing: Tracing
    embedder: Embedder
    model: Model

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        tracing: Tracing | None = None,
        embedder: Embedder | None = None,
        model: Model | None = None,
    ) -> Self:
        """Each adapter not given is built from `settings`; the Embedder traces to `tracing`."""
        if tracing is None:
            tracing = configure_tracing(settings)
        if embedder is None:
            embedder = OpenAICompatibleEmbedder.from_settings(settings, tracing)
        return cls(
            settings=settings,
            tracing=tracing,
            embedder=embedder,
            model=chat_model(settings) if model is None else model,
        )

    def asking(
        self,
        conn: psycopg.Connection[TupleRow],
        *,
        reading: Reading = Reading.AUTO,
    ) -> AskingContext:
        """A turn's `AskingContext` around the store connection it answers over."""
        return AskingContext(
            conn=conn,
            embedder=self.embedder,
            model=self.model,
            settings=self.settings,
            tracing=self.tracing,
            reading=reading,
        )
