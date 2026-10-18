"""FastAPI application factory."""

from fastapi import FastAPI

from paperexplorer import __version__
from paperexplorer_api.schemas import Health


def create_app() -> FastAPI:
    """Build the API app; routes are registered here."""
    app = FastAPI(title="PaperExplorer API", version=__version__)

    @app.get("/health")
    def health() -> Health:  # pyright: ignore[reportUnusedFunction]
        return Health(version=__version__)

    return app
