"""The adapters a process works with, built once from Settings, and the contexts built from them."""

import logging
from collections.abc import Callable
from typing import Self

import psycopg
from psycopg.rows import TupleRow
from pydantic_ai.models import Model

from paperexplorer import inference
from paperexplorer.asking import AskingContext, Reading
from paperexplorer.config import Settings
from paperexplorer.embedding import Embedder
from paperexplorer.library import Library, LibraryNotConfiguredError, ZoteroLibrary
from paperexplorer.pipeline.stage import StageContext
from paperexplorer.resolution import DoiOrgRegistry, DoiRegistry, Registries, shared_registries
from paperexplorer.tracing import Tracing, configure_tracing

logger = logging.getLogger(__name__)


class Wiring:
    """Every adapter a process works with, and the Settings they were built from.

    The Library, the DOI registry and the source of `Registries` are built with the `Wiring`:
    none touches the network. Tracing (which probes Langfuse), the Embedder and the chat model
    (which can raise `ProviderError`) are built on first use and then kept, so a command that
    needs none of them never builds them.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        library: Library | None,
        doi_registry: DoiRegistry,
        registries: Callable[[], Registries],
        tracing: Tracing | None = None,
        embedder: Embedder | None = None,
        model: Model | None = None,
    ) -> None:
        """Prefer `from_settings`; an adapter left `None` here is built from `settings` on use."""
        self._settings = settings
        self._library = library
        self._doi_registry = doi_registry
        self._registries = registries
        self._tracing = tracing
        self._embedder = embedder
        self._model = model

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        tracing: Tracing | None = None,
        embedder: Embedder | None = None,
        model: Model | None = None,
        library: Library | None = None,
        doi_registry: DoiRegistry | None = None,
        registries: Callable[[], Registries] | None = None,
    ) -> Self:
        """Each adapter not given is built from `settings`; the Library is None without Zotero."""
        if library is None:
            try:
                library = ZoteroLibrary.from_settings(settings)
            except LibraryNotConfiguredError as error:
                logger.info("No Library: %s", error)
        if doi_registry is None:
            doi_registry = DoiOrgRegistry.from_settings(settings)
        if registries is None:
            registries = shared_registries(settings)
        return cls(
            settings,
            library=library,
            doi_registry=doi_registry,
            registries=registries,
            tracing=tracing,
            embedder=embedder,
            model=model,
        )

    @property
    def settings(self) -> Settings:
        """The Settings every adapter not given was built from."""
        return self._settings

    @property
    def library(self) -> Library | None:
        """The Library; None when Zotero's keys are absent."""
        return self._library

    @property
    def doi_registry(self) -> DoiRegistry:
        """The DOI registry `verify_dois` asks."""
        return self._doi_registry

    @property
    def registries(self) -> Callable[[], Registries]:
        """Fresh `Registries` for each Registry Check, all within one process-wide set of limits."""
        return self._registries

    @property
    def tracing(self) -> Tracing:
        """Tracing, configured on first use: that probes the tower's Langfuse."""
        if self._tracing is None:
            self._tracing = configure_tracing(self.settings)
        return self._tracing

    @property
    def embedder(self) -> Embedder:
        """The Embedder of `PE_PROVIDER`, built on first use; it traces to `tracing`."""
        if self._embedder is None:
            self._embedder = inference.embedder(self.settings, self.tracing)
        return self._embedder

    @property
    def model(self) -> Model:
        """The chat model of `PE_PROVIDER`, built on first use."""
        if self._model is None:
            self._model = inference.chat_model(self.settings)
        return self._model

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

    def stages(self, conn: psycopg.Connection[TupleRow]) -> StageContext:
        """A Stage's `StageContext` around a store connection; every Stage needs the Library."""
        if self.library is None:
            raise LibraryNotConfiguredError
        return StageContext(
            conn=conn,
            settings=self.settings,
            library=self.library,
            embedder=self.embedder,
            tracing=self.tracing,
            doi_registry=self.doi_registry,
        )

    def flush(self) -> None:
        """Send every finished trace now, if tracing was built; otherwise do nothing."""
        if self._tracing is not None:
            self._tracing.flush()
