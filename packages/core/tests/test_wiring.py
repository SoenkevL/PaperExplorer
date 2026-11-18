import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import psycopg
import pytest
from psycopg.rows import TupleRow
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel

from paperexplorer.asking import Reading
from paperexplorer.config import Settings
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.library import InMemoryLibrary, LibraryNotConfiguredError, ZoteroLibrary
from paperexplorer.resolution import DoiOrgRegistry, InMemoryDoiRegistry, Registries
from paperexplorer.tracing import Tracing
from paperexplorer.wiring import Wiring

ZOTERO_KEYS = {"zotero_library_id": "12345", "zotero_api_key": "k"}


def _settings(**values: object) -> Settings:
    values = {"langfuse_public_key": "", "langfuse_secret_key": "", **values}
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue, reportArgumentType]


def _unused(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    raise AssertionError("no test here asks the model")


def test_what_is_given_is_wired_as_given() -> None:
    tracing, embedder, model = Tracing.off(), FakeEmbedder(), FunctionModel(_unused)

    wiring = Wiring.from_settings(_settings(), tracing=tracing, embedder=embedder, model=model)

    assert wiring.tracing is tracing
    assert wiring.embedder is embedder
    assert wiring.model is model


def test_what_is_not_given_comes_from_the_settings() -> None:
    wiring = Wiring.from_settings(_settings(embedding_model="bge-m3", chat_model="gemma4:e4b"))

    assert wiring.tracing.base_url is None  # no Langfuse keys: tracing is off
    assert wiring.embedder.model == "bge-m3"
    assert wiring.model.model_name == "gemma4:e4b"


def test_a_turns_context_holds_its_connection_and_the_wiring(
    store_db: psycopg.Connection[TupleRow],
) -> None:
    settings = _settings()
    wiring = Wiring.from_settings(settings, embedder=FakeEmbedder())

    ctx = wiring.asking(store_db, reading=Reading.RETRIEVAL)

    assert ctx.conn is store_db
    assert (ctx.embedder, ctx.model, ctx.tracing) == (wiring.embedder, wiring.model, wiring.tracing)
    assert ctx.settings is settings
    assert ctx.reading is Reading.RETRIEVAL


def test_a_turns_context_reads_the_seed_as_fits_by_default(
    store_db: psycopg.Connection[TupleRow],
) -> None:
    ctx = Wiring.from_settings(_settings(), embedder=FakeEmbedder()).asking(store_db)

    assert ctx.reading is Reading.AUTO


def test_the_library_doi_registry_and_registries_given_are_held_as_given() -> None:
    library, doi_registry = InMemoryLibrary([], {}), InMemoryDoiRegistry()

    def registries() -> Registries:
        return Registries(_settings())

    wiring = Wiring.from_settings(
        _settings(), library=library, doi_registry=doi_registry, registries=registries
    )

    assert wiring.library is library
    assert wiring.doi_registry is doi_registry
    assert wiring.registries is registries


def test_without_zoteros_keys_there_is_no_library() -> None:
    assert Wiring.from_settings(_settings()).library is None


def test_with_zoteros_keys_the_library_is_zotero() -> None:
    wiring = Wiring.from_settings(_settings(**ZOTERO_KEYS))

    assert isinstance(wiring.library, ZoteroLibrary)
    assert isinstance(wiring.doi_registry, DoiOrgRegistry)


def test_each_registry_check_gets_fresh_registries_within_one_set_of_limits() -> None:
    registries = Wiring.from_settings(_settings()).registries

    first, second = registries(), registries()

    assert first is not second
    # The limits are Registries' own; their sharing across Checks is shown on the wire in
    # test_resolution_check, so here it is enough that both Checks hold the same limiters.
    assert first._crossref is second._crossref  # pyright: ignore[reportPrivateUsage]
    assert first._crossref_single is second._crossref_single  # pyright: ignore[reportPrivateUsage]
    assert first._openalex is second._openalex  # pyright: ignore[reportPrivateUsage]


def test_a_stages_context_holds_its_connection_and_every_adapter(
    store_db: psycopg.Connection[TupleRow],
) -> None:
    settings = _settings()
    library, doi_registry = InMemoryLibrary([], {}), InMemoryDoiRegistry()
    tracing, embedder = Tracing.off(), FakeEmbedder()
    wiring = Wiring.from_settings(
        settings, tracing=tracing, embedder=embedder, library=library, doi_registry=doi_registry
    )

    ctx = wiring.stages(store_db)

    assert ctx.conn is store_db
    assert ctx.settings is settings
    assert ctx.library is library
    assert ctx.embedder is embedder
    assert ctx.tracing is tracing
    assert ctx.doi_registry is doi_registry


def test_a_stages_context_needs_a_library(store_db: psycopg.Connection[TupleRow]) -> None:
    wiring = Wiring.from_settings(_settings(), tracing=Tracing.off(), embedder=FakeEmbedder())

    with pytest.raises(LibraryNotConfiguredError, match="PE_ZOTERO_API_KEY"):
        wiring.stages(store_db)


class _Probes:
    """A Langfuse host that only counts what reaches it."""

    def __init__(self) -> None:
        self.paths: list[str] = []


@pytest.fixture
def langfuse() -> Iterator[tuple[str, _Probes]]:
    probes = _Probes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            probes.paths.append(self.path)
            self.send_response(200)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", probes
    server.shutdown()
    server.server_close()


def test_using_only_the_library_builds_neither_tracing_nor_the_chat_model(
    langfuse: tuple[str, _Probes],
) -> None:
    url, probes = langfuse
    settings = _settings(
        **ZOTERO_KEYS,
        langfuse_public_key=f"pk-lf-test-{uuid.uuid4()}",
        langfuse_secret_key="sk",
        langfuse_base_url=url,
        provider="no-such-provider",  # building the Embedder or the chat model raises
    )

    wiring = Wiring.from_settings(settings)
    assert isinstance(wiring.library, ZoteroLibrary)
    wiring.registries()
    wiring.flush()

    assert probes.paths == []


def test_tracing_is_built_on_first_use_and_then_kept(langfuse: tuple[str, _Probes]) -> None:
    url, probes = langfuse
    settings = _settings(
        langfuse_public_key=f"pk-lf-test-{uuid.uuid4()}",
        langfuse_secret_key="sk",
        langfuse_base_url=url,
    )
    wiring = Wiring.from_settings(settings, embedder=FakeEmbedder())

    tracing = wiring.tracing

    assert tracing.base_url == url
    assert wiring.tracing is tracing
    assert probes.paths == ["/api/public/health"]


class _CountedFlushes(Tracing):
    def __init__(self) -> None:
        off = Tracing.off()
        super().__init__(None, off.tracer_provider, None)
        self.flushes = 0

    def flush(self) -> None:
        self.flushes += 1


def test_flush_flushes_the_tracing_in_use() -> None:
    tracing = _CountedFlushes()
    wiring = Wiring.from_settings(_settings(), tracing=tracing)

    wiring.flush()

    assert tracing.flushes == 1
