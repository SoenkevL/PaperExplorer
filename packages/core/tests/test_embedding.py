"""The Embedder port: the fake that tests embed with, and the OpenAI-compatible adapter."""

import json
import math
import uuid
from collections.abc import Callable

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from paperexplorer.config import Settings
from paperexplorer.domain import Vector
from paperexplorer.embedding import EmbeddingError, FakeEmbedder, OpenAICompatibleEmbedder
from paperexplorer.tracing import Tracing, configure_tracing


def cosine(a: Vector, b: Vector) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_the_fake_puts_texts_that_share_words_closer_than_texts_that_do_not() -> None:
    entropy, also_entropy, unrelated = FakeEmbedder().embed(
        [
            "Brain entropy rises under psilocybin.",
            "Entropy of the brain signal",
            "Therapists trained in groups.",
        ]
    )

    assert cosine(entropy, also_entropy) > 0.5
    assert cosine(entropy, unrelated) == 0.0


def test_the_fakes_cosine_counts_shared_content_words() -> None:
    four, two, same_two = FakeEmbedder().embed(
        ["brain entropy psilocybin therapy", "Brain entropy", "the ENTROPY of a brain"]
    )

    assert cosine(four, two) == pytest.approx(2 / math.sqrt(4 * 2))
    assert same_two == two


def test_the_fake_records_what_it_embedded() -> None:
    fake = FakeEmbedder(model="fake")

    fake.embed(["one"])
    fake.embed(["two", "three"])

    assert fake.model == "fake"
    assert fake.calls == [["one"], ["two", "three"]]


BASE_URL = "http://tower:11434/v1"


class Server:
    """An OpenAI-compatible `/v1/embeddings` that answers with the fake's Vectors, reversed."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        texts: list[str] = json.loads(request.content)["input"]
        vectors = FakeEmbedder().embed(texts)
        data = [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "bge-m3",
                "data": data[::-1],  # the index, not the order, says which text a Vector is for
                "usage": {"prompt_tokens": 3 * len(texts), "total_tokens": 3 * len(texts)},
            },
        )


def embedder(
    answer: Callable[[httpx.Request], httpx.Response],
    *,
    batch_size: int = 32,
    tracing: Tracing | None = None,
) -> OpenAICompatibleEmbedder:
    http = httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": "Bearer ollama"},
        transport=httpx.MockTransport(answer),
    )
    return OpenAICompatibleEmbedder(
        http, "bge-m3", batch_size=batch_size, tracing=tracing or Tracing.off()
    )


TEXTS = [f"sentence {n} about entropy" for n in range(5)]


def test_the_adapter_embeds_in_batches_and_keeps_the_texts_order() -> None:
    server = Server()

    vectors = embedder(server, batch_size=2).embed(TEXTS)

    assert vectors == FakeEmbedder().embed(TEXTS)
    assert [json.loads(r.content) for r in server.requests] == [
        {"model": "bge-m3", "input": TEXTS[0:2]},
        {"model": "bge-m3", "input": TEXTS[2:4]},
        {"model": "bge-m3", "input": TEXTS[4:5]},
    ]
    for request in server.requests:
        assert request.method == "POST"
        assert request.url == f"{BASE_URL}/embeddings"
        assert request.headers["Authorization"] == "Bearer ollama"


def test_nothing_to_embed_sends_nothing() -> None:
    server = Server()

    assert embedder(server).embed([]) == []
    assert server.requests == []


def test_a_failing_server_says_why() -> None:
    def missing_model(request: httpx.Request) -> httpx.Response:
        error = {"message": 'model "bge-m3" not found, try pulling it first', "type": "api_error"}
        return httpx.Response(404, json={"error": error})

    with pytest.raises(EmbeddingError, match='404: model "bge-m3" not found'):
        embedder(missing_model).embed(TEXTS)


def test_an_answer_without_one_vector_per_text_is_an_error() -> None:
    def one_short(request: httpx.Request) -> httpx.Response:
        answer = Server()(request)
        body = answer.json()
        body["data"] = body["data"][1:]
        return httpx.Response(200, json=body)

    with pytest.raises(EmbeddingError, match="4 embeddings for 5 texts"):
        embedder(one_short).embed(TEXTS)


def traced(exporter: InMemorySpanExporter) -> Tracing:
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        # A fresh key per test: the Langfuse SDK keeps one client per public key and process.
        langfuse_public_key=f"pk-lf-test-{uuid.uuid4()}",
        langfuse_secret_key="sk-lf-test",
        langfuse_base_url=None,
    )
    return configure_tracing(settings, span_exporter=exporter)


def test_each_batch_is_one_embedding_observation_in_the_current_trace() -> None:
    exporter = InMemorySpanExporter()
    tracing = traced(exporter)

    with tracing.trace("question", tags=["asking"]):
        embedder(Server(), batch_size=2, tracing=tracing).embed(TEXTS)
    tracing.flush()

    spans = exporter.get_finished_spans()
    (root,) = [s for s in spans if s.name == "question"]
    batches = [s for s in spans if s.name == "embeddings bge-m3"]
    assert root.context is not None
    assert [s.parent and s.parent.span_id for s in batches] == [root.context.span_id] * 3
    attributes = [s.attributes or {} for s in batches]
    assert [a["langfuse.observation.type"] for a in attributes] == ["embedding"] * 3
    assert [a["langfuse.observation.model.name"] for a in attributes] == ["bge-m3"] * 3
    assert [json.loads(str(a["langfuse.observation.input"])) for a in attributes] == [
        TEXTS[0:2], TEXTS[2:4], TEXTS[4:5],
    ]  # fmt: skip
    assert [json.loads(str(a["langfuse.observation.usage_details"])) for a in attributes] == [
        {"input": 6}, {"input": 6}, {"input": 3},
    ]  # fmt: skip
    assert [a["langfuse.trace.name"] for a in attributes] == ["question"] * 3


def test_outside_a_trace_embedding_records_nothing() -> None:
    exporter = InMemorySpanExporter()
    tracing = traced(exporter)

    embedder(Server(), batch_size=2, tracing=tracing).embed(TEXTS)
    tracing.flush()

    assert exporter.get_finished_spans() == ()
