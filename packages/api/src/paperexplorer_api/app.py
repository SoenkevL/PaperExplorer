"""FastAPI application factory."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import psycopg
from fastapi import FastAPI

from paperexplorer import __version__
from paperexplorer.asking import AskingContext, chat_model
from paperexplorer.config import Settings
from paperexplorer.embedding import OpenAICompatibleEmbedder
from paperexplorer.logs import configure_logging
from paperexplorer.tracing import configure_tracing
from paperexplorer_api.asking_thread import AskingContexts, AskingThread
from paperexplorer_api.conversations import Conversations
from paperexplorer_api.routes import Connections, citations, works
from paperexplorer_api.routes import conversations as conversation_routes
from paperexplorer_api.schemas import Health


@dataclass(frozen=True)
class Asking:
    """How the API answers Questions: each turn's context, and where Conversations are held."""

    contexts: AskingContexts
    conversations: Conversations


def create_app(connections: Connections, asking: Asking | None = None) -> FastAPI:
    """Build the API app; each request opens a store connection through `connections`.

    With `asking`, it also holds Conversations and answers their Questions, each turn on the
    asking thread with a store connection of its own; without, it serves Works and Citations.
    """
    thread = AskingThread(connections, asking.contexts) if asking else None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        yield
        if thread is not None:
            await asyncio.to_thread(thread.stop)

    app = FastAPI(title="PaperExplorer API", version=__version__, lifespan=lifespan)

    @app.get("/health")
    def health() -> Health:  # pyright: ignore[reportUnusedFunction]
        return Health(version=__version__)

    app.include_router(works.router(connections))
    app.include_router(citations.router(connections))
    if asking is not None and thread is not None:
        app.include_router(
            conversation_routes.router(connections, asking.contexts, asking.conversations, thread)
        )
    return app


def app_from_env() -> FastAPI:
    """ASGI factory for uvicorn: `uvicorn --factory paperexplorer_api.app:app_from_env`.

    The Embedder, the chat model and tracing are built once, here, from the `PE_*` settings.
    """
    settings = Settings()
    configure_logging(settings.log_level, settings.log_format)
    tracing = configure_tracing(settings)
    embedder = OpenAICompatibleEmbedder.from_settings(settings, tracing)
    model = chat_model(settings)
    return create_app(
        lambda: psycopg.connect(settings.database_url, autocommit=True),
        Asking(
            contexts=lambda conn: AskingContext(conn, embedder, model, settings, tracing),
            conversations=Conversations.from_settings(settings),
        ),
    )
