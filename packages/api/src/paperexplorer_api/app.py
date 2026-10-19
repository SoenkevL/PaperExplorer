"""FastAPI application factory."""

import psycopg
from fastapi import FastAPI

from paperexplorer import __version__
from paperexplorer.config import Settings
from paperexplorer_api.routes import Connections, citations, works
from paperexplorer_api.schemas import Health


def create_app(connections: Connections) -> FastAPI:
    """Build the API app; each request opens a store connection through `connections`."""
    app = FastAPI(title="PaperExplorer API", version=__version__)

    @app.get("/health")
    def health() -> Health:  # pyright: ignore[reportUnusedFunction]
        return Health(version=__version__)

    app.include_router(works.router(connections))
    app.include_router(citations.router(connections))
    return app


def app_from_env() -> FastAPI:
    """ASGI factory for uvicorn: `uvicorn --factory paperexplorer_api.app:app_from_env`."""
    url = Settings().database_url
    return create_app(lambda: psycopg.connect(url, autocommit=True))
