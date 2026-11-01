import psycopg
from psycopg.rows import TupleRow
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel

from paperexplorer.asking import Reading
from paperexplorer.config import Settings
from paperexplorer.embedding import FakeEmbedder
from paperexplorer.tracing import Tracing
from paperexplorer.wiring import Wiring


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
