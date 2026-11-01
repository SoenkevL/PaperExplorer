"""FastAPI application factory."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import psycopg
from fastapi import FastAPI

from paperexplorer import __version__
from paperexplorer.config import Settings
from paperexplorer.library import ZoteroLibrary
from paperexplorer.logs import configure_logging
from paperexplorer.resolution import Registries
from paperexplorer.wiring import Wiring
from paperexplorer_api.asking_thread import AskingContexts, AskingThread
from paperexplorer_api.conversations import Conversations
from paperexplorer_api.routes import Connections, checks, citations, works
from paperexplorer_api.routes import conversations as conversation_routes
from paperexplorer_api.routes.checks import Checking
from paperexplorer_api.schemas import Health

logger = logging.getLogger(__name__)

__all__ = ["Asking", "Checking", "app_from_env", "create_app"]


@dataclass(frozen=True)
class Asking:
    """How the API answers Questions: each turn's context, and where Conversations are held."""

    contexts: AskingContexts
    conversations: Conversations


def create_app(
    connections: Connections, asking: Asking | None = None, checking: Checking | None = None
) -> FastAPI:
    """Build the API app; each request opens a store connection through `connections`.

    With `asking`, it also holds Conversations and answers their Questions, each turn on the
    asking thread with a store connection of its own; without, it serves Works and Citations.
    With `checking`, it runs Registry Checks, takes Readers' Choices and lists Duplicate Items.
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
    if checking is not None:
        app.include_router(checks.router(connections, checking))
    if asking is not None and thread is not None:
        app.include_router(
            conversation_routes.router(connections, asking.contexts, asking.conversations, thread)
        )
    return app


def app_from_env() -> FastAPI:
    """ASGI factory for uvicorn: `uvicorn --factory paperexplorer_api.app:app_from_env`.

    The Embedder, the chat model and tracing are built once, here, from the `PE_*` settings;
    without Zotero's keys it serves no Registry Checks.
    """
    settings = Settings()
    configure_logging(settings.log_level, settings.log_format)
    wiring = Wiring.from_settings(settings)
    try:
        library = ZoteroLibrary.from_settings(settings)
    except ValueError as e:
        logger.info("No Registry Checks or merges: %s", e)
        library = None
    return create_app(
        lambda: psycopg.connect(settings.database_url, autocommit=True),
        Asking(
            contexts=wiring.asking,
            conversations=Conversations.from_settings(settings),
        ),
        Checking(library=library, registries=lambda: Registries(settings)) if library else None,
    )
